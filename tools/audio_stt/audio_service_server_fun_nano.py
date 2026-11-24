import asyncio
import os
import wave
from contextlib import asynccontextmanager
from datetime import datetime

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Same-directory import: works for a direct run (`python audio_service_server.py`)
# and for package use (`from tools.audio_stt.audio_service_server import app`).
try:
    from ._logging import get_logger
    from ._models import vad_model, voice_model
    from .audio_decode import AudioDecodeError, decode_to_pcm
except ImportError:  # Run directly as a script
    from _logging import get_logger
    from _models import vad_model, voice_model
    from audio_decode import AudioDecodeError, decode_to_pcm
from cn_num_normalize import normalize as _normalize_cn_numbers

# Migration change: the original code did `from config.cfg import Syscfg` +
#   `cfg = Syscfg()`, yet never used cfg; but constructing Syscfg requires APP_ROOT_DIR
#   + config/config.json in place -- tying a whole service to someone else's
#   configuration system for one useless line, so it was removed.
logger = get_logger("reasoning_websocket")

# Debug audio is off by default: the original wrote a wav into debug_audio/ on every recognition,
#   and the original project had accumulated 178MB / 417 files. Off by default; set ASR_DEBUG_AUDIO=1 when needed.
SAVE_DEBUG_AUDIO = os.environ.get("ASR_DEBUG_AUDIO", "0") == "1"

# Chinese numerals -> Arabic digits (post-processing).
#   Why post-process: this model decodes with Qwen3-0.6B and its ITN ability is unstable -- on the same clip
#   5 runs gave identical output (greedy decoding, not random), yet ITN behavior differs across clips:
#   some come out as "3/15" and others as the spelled-out Chinese form. Tweaking the prompt (language=None /
#   instructions inside language) is a coin flip, and clips move in opposite directions, so it cannot be a mechanism.
#   So deterministic rule-based post-processing covers it: convert only "numeral + explicit quantifier";
#   anything without a quantifier anchor (altogether / together / by all means / very / below zero) is left alone.
#   Set FUN_NANO_ITN_POST=0 to disable.
ITN_POST = os.environ.get("FUN_NANO_ITN_POST", "1") == "1"


# ============================================================================
# Real ASR engine (speech -> text)
# * This file is a [parallel experiment copy] of audio_service_server.py: the same WebSocket protocol,
#   the same port, only the ASR engine swapped for Fun-ASR-Nano-2512 (2025-12).
#   The original audio_service_server.py is untouched line for line, so rollback is always possible.
#
# Why switch (measured 2026-11-20, same Mac MPS, same batch of audio):
#   SenseVoiceSmall:  'at 345 pm ... budget is $1250,000'     <- separators lost in time/amount
#   Fun-ASR-Nano    : 'at 3:45 pm ... budget is $1,250,000'    <- both correct
#   Cost: about 6-7x slower (0.33s -> 2.18s on an 11-second clip)
#
# Structural differences (read from its config.yaml, not guessed):
#   audio_encoder = SenseVoiceEncoderSmall (freeze=true)   <- the same encoder as SenseVoice
#   llm           = Qwen3-0.6b            (freeze=true)   <- the newly added decoder
#   ctc_decoder   = Transformer n_layer=5 (freeze=false)  <- the only trained part
#   -> In short: the same ears, with a new brain that can write.
# ============================================================================

# Fun-ASR-Nano weights directory: override with the FUN_NANO_MODEL environment variable.
# No silent fallback when the directory is missing (switching models is a deliberate decision, not a fallback); fail loudly and explain what to do.
DEFAULT_FUN_NANO_MODEL = "~/.cache/modelscope/hub/models/FunAudioLLM/Fun-ASR-Nano-2512"


def fun_nano_model_path() -> str:
    """Resolve the Fun-ASR-Nano weights directory (local only; no network download)."""
    raw = os.environ.get("FUN_NANO_MODEL", DEFAULT_FUN_NANO_MODEL)
    path = os.path.expanduser(os.path.expandvars(raw.strip()))
    return path


class ASREngine:
    """Local Fun-ASR-Nano ASR wrapper (lazy-loaded singleton)"""

    def __init__(self):
        self._model = None

    @staticmethod
    def _pick_device() -> str:
        try:
            import torch

            if torch.backends.mps.is_available():
                return "mps"
            if torch.cuda.is_available():
                return "cuda:0"
        except Exception:
            pass
        return "cpu"

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        from funasr import AutoModel

        model_path = fun_nano_model_path()
        if not os.path.isdir(model_path):
            raise RuntimeError(
                f"Fun-ASR-Nano 权重目录不存在: {model_path}\n"
                f"下载: modelscope download --model FunAudioLLM/Fun-ASR-Nano-2512 --local_dir {model_path}\n"
                f"或用 FUN_NANO_MODEL=<别的目录> 指定。"
            )
        if not os.path.isfile(os.path.join(model_path, "model.pt")):
            raise RuntimeError(f"{model_path} 里没有 model.pt，目录可能没下完。")

        vad_model_path = vad_model()
        device = self._pick_device()
        logger.info(f"[ASR] loading Fun-ASR-Nano: {model_path} device={device}")
        # trust_remote_code is not passed: funasr>=1.4.22 already ships fun_asr_nano,
        # so the built-in implementation suffices; no need to clone a repo just for model.py.
        # Note: this model computes CTC timestamps inside generate, which requires torchaudio.
        self._model = AutoModel(
            model=model_path,
            vad_model=vad_model_path,
            vad_kwargs={"max_single_segment_time": 30000},
            device=device,
            disable_update=True,
        )
        logger.info("[ASR] Fun-ASR-Nano loaded")
        return self._model

    def transcribe_bytes(self, pcm_bytes: bytes) -> str:
        """Receive audio bytes, normalize them to 16k/16bit/mono PCM, recognize and return text.

                Supports several input formats (the server sniffs automatically; the client need not declare):
                  - raw PCM (16k/mono/s16le) -> used as-is
                  - CAF (iOS)               -> ffmpeg normalization (falls back to manual header stripping)
                  - m4a/aac/amr/webm/ogg    -> ffmpeg decoding
                  - wav                     -> ffmpeg decoding
        """
        if not pcm_bytes:
            return ""

        # --- Format sniffing + normalization -> 16k/mono/s16le PCM ---
        try:
            pcm_bytes = decode_to_pcm(pcm_bytes)
        except AudioDecodeError as e:
            logger.error(f"[ASR] 音频解码失败: {e}")
            raise
        if not pcm_bytes:
            return ""

        model = self._ensure_model()

        # 16-bit PCM -> float32, scaled to [-1, 1] (the input format funasr expects)
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # The arguments differ from the SenseVoice set; one by one:
        #   language: "zh" -> "Chinese" (this model uses the language name, not an ISO code)
        #   use_itn -> itn (same switch, renamed)
        #   batch_size_s=60 + merge_vad -> batch_size=1 (this model's generate cannot batch by seconds)
        res = model.generate(
            input=audio,
            cache={},
            batch_size=1,
            language=os.environ.get("FUN_NANO_LANG", "中文"),
            itn=True,
            hotwords=_hotwords(),
        )
        # * rich_transcription_postprocess is not called: that function decodes SenseVoice's
        #   <|zh|><|NEUTRAL|><|Speech|> rich-text markers, which this model does not emit.
        text = res[0]["text"]
        logger.info(f"[ASR] transcribe ok: {text!r}")
        if ITN_POST:
            normalized = _normalize_cn_numbers(text)
            if normalized != text:
                logger.info(f"[ASR] itn post -> {normalized!r}")
            text = normalized
        return text


def _hotwords() -> list[str]:
    """Domain hotwords: FUN_NANO_HOTWORDS='w1,w2' -> ["w1","w2"]. Empty means not passed."""
    raw = os.environ.get("FUN_NANO_HOTWORDS", "").strip()
    if not raw:
        return []
    return [w.strip() for w in raw.split(",") if w.strip()]


asr_engine = ASREngine()


class AudioBufferManager:
    """Manages per-client audio buffers"""

    def __init__(self):
        self.buffers: dict[str, bytes] = {}  # client_id: audio_data
        self.last_update: dict[str, float] = {}  # Last update timestamp

    def append(self, client_id: str, data: bytes):
        """Append audio data to the given client's buffer"""
        if client_id not in self.buffers:
            self.buffers[client_id] = b""
        self.buffers[client_id] += data
        self.last_update[client_id] = asyncio.get_event_loop().time()

    def get(self, client_id: str) -> bytes:
        """Fetch and clear the buffer"""
        return self.buffers.pop(client_id, b"")

    def clear_expired(self, timeout: float = 300.0):
        """Drop buffers that have not been updated within the timeout"""
        current_time = asyncio.get_event_loop().time()
        expired = [client_id for client_id, last_time in self.last_update.items() if current_time - last_time > timeout]
        for client_id in expired:
            self.buffers.pop(client_id, None)
            self.last_update.pop(client_id, None)
            logger.warning(f"Cleared expired buffer for {client_id}")


class ConnectionManager:
    """Manages WebSocket connections"""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, client_id: str, websocket: WebSocket):
        """Register a new connection"""
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"Client {client_id} connected")

    def disconnect(self, client_id: str):
        """Remove a disconnected connection"""
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.info(f"Client {client_id} disconnected")

    async def send_bytes(self, client_id: str, data: bytes):
        """Send binary data"""
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_bytes(data)

    async def send_text(self, client_id: str, message: str):
        """Send a text message"""
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_text(message)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    # Initialization
    app.state.manager = ConnectionManager()
    app.state.audio_buffer = AudioBufferManager()

    # Start the background cleanup task
    cleanup_task = asyncio.create_task(periodic_cleanup(app))

    logger.info("Server started")
    yield

    # Shut down cleanup
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Server stopped")


async def periodic_cleanup(app: FastAPI, interval: float = 60.0):
    """Periodically clean up expired buffers"""
    while True:
        await asyncio.sleep(interval)
        app.state.audio_buffer.clear_expired()


app = FastAPI(lifespan=lifespan)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def process_complete_audio(audio_data: bytes) -> bytes:
    """
        Process a complete chunk of audio data
        In a real project this should be replaced with the actual ASR/TTS logic
    """
    if not audio_data:
        return b""

    # Simulate processing time (scaled with the data size)
    # processing_time = max(0.1, len(audio_data) / 16000)  # assuming a 16k sample rate
    await asyncio.sleep(0.1)

    # Sample processing: simply reverse the audio data
    return audio_data[::-1]


# * Route registration order pitfall (hit in practice 2026-11-20):
#   `/ws/audio/{client_id}` is registered before `/ws/audio/audio2text/{client_id}`,
#   so `/ws/audio/audio2text` (without a client_id) is swallowed by it (client_id="audio2text")
#   and goes to process_complete_audio (which reverses the bytes), returning a pile of binary + AUDIO_PROCESSING_COMPLETE,
#   **never any text**. Meanwhile the local address written in asr.env is exactly this client_id-less form,
#   so anyone copying it will assume the service is broken. Hence a precise route is added in front, pointing the bare path at the real transcription endpoint.
@app.websocket("/ws/audio/audio2text")
async def audio_text_endpoint_no_client_id(websocket: WebSocket):
    """Compatibility for connections without a client_id (see the comment above)."""
    await audio_text_endpoint(websocket, client_id="audio2text-default")


@app.websocket("/ws/audio/{client_id}")
async def audio_stream_endpoint(websocket: WebSocket, client_id: str):
    """Audio stream processing endpoint"""
    manager = app.state.manager
    buffer = app.state.audio_buffer

    await manager.connect(client_id, websocket)

    try:
        while True:
            audio_data = await websocket.receive_bytes()

            # Received the end marker
            if len(audio_data) == 0:
                complete_data = buffer.get(client_id)
                if not complete_data:
                    await manager.send_text(client_id, "ERROR: Empty audio data")
                    continue

                # Process the audio and return the result
                processed_data = await process_complete_audio(complete_data)

                # Key response logic: send the processing result + end acknowledgement
                await manager.send_bytes(client_id, processed_data)
                await manager.send_text(client_id, "AUDIO_PROCESSING_COMPLETE")  # End-of-protocol marker
                logger.info(f"Sent final result to {client_id}")
                continue

            # Normal data-block handling
            buffer.append(client_id, audio_data)
            if len(buffer.buffers[client_id]) % 10000 == 0:  # Progress feedback
                await manager.send_text(client_id, f"BUFFERED:{len(buffer.buffers[client_id])}")

    except WebSocketDisconnect:
        manager.disconnect(client_id)
        buffer.buffers.pop(client_id, None)
    except Exception as e:
        logger.error(f"Error with {client_id}: {str(e)}")
        manager.disconnect(client_id)
        buffer.buffers.pop(client_id, None)


async def save_audio(data: bytes):
    """Save the received audio (simulated playback)"""
    timestamp = datetime.now().strftime("%H%M%S")
    debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_audio")
    os.makedirs(debug_dir, exist_ok=True)
    with wave.open(os.path.join(debug_dir, f"server_{timestamp}.wav"), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(data)


@app.websocket("/ws/audio/audio2text/{client_id}")
async def audio_text_endpoint(websocket: WebSocket, client_id: str):
    """Receive an audio file and return text"""
    manager = app.state.manager
    buffer = app.state.audio_buffer

    await manager.connect(client_id, websocket)

    try:
        while True:
            audio_data = await websocket.receive_bytes()

            # Received the end marker
            if len(audio_data) == 0:
                complete_data = buffer.get(client_id)
                if not complete_data:
                    await manager.send_text(client_id, "ERROR: Empty audio data")
                    continue

                # Process the audio and return the result: real ASR (speech -> text)
                try:
                    # funasr inference blocks synchronously; run it in a thread pool so the event loop stays free
                    text = await asyncio.to_thread(asr_engine.transcribe_bytes, complete_data)
                except Exception as e:
                    logger.error(f"[ASR] transcribe failed: {e}", exc_info=True)
                    await manager.send_text(client_id, "ERROR: ASR failed")
                    continue

                # Keep a copy of the audio for debugging (the normalized PCM, so the wav is always playable)
                # Not written to disk by default: see the SAVE_DEBUG_AUDIO note at the top of the file
                if SAVE_DEBUG_AUDIO:
                    try:
                        await save_audio(decode_to_pcm(complete_data))
                    except Exception:
                        pass

                # Key response logic: send the recognized text + end acknowledgement
                await manager.send_text(client_id, text)  # The real text result
                await manager.send_text(client_id, "AUDIO_PROCESSING_COMPLETE")  # End-of-protocol marker
                logger.info(f"Sent final result to {client_id}: {text!r}")
                continue

            # Normal data-block handling
            buffer.append(client_id, audio_data)
            # if len(buffer.buffers[client_id]) % 10000 == 0:  # progress feedback
            #     await manager.send_text(client_id, f"BUFFERED:{len(buffer.buffers[client_id])}")

    except WebSocketDisconnect:
        manager.disconnect(client_id)
        buffer.buffers.pop(client_id, None)
    except Exception as e:
        logger.error(f"Error with {client_id}: {str(e)}")
        manager.disconnect(client_id)
        buffer.buffers.pop(client_id, None)


def generate_audio_chunks(file_path: str | None = None):
    """Simulated real-time audio stream generator

        Migration change: the original hardcoded an absolute path to `motozolo_gpt_tools/test1.wav`,
        so the file was guaranteed to be missing on another machine. Now it is an environment
        variable plus a path relative to this directory.
    """
    if file_path is None:
        file_path = os.environ.get(
            "ASR_TEST_AUDIO",
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "debug_audio", "test1.wav"),
        )
    with wave.open(file_path, "rb") as wf:
        while True:
            data = wf.readframes(1600)
            if not data:
                break
            yield data


@app.websocket("/ws/audio/string2audio/{client_id}")
async def text_audio_endpoint(websocket: WebSocket, client_id: str):
    """Receive text but return an audio file"""
    manager = app.state.manager
    await manager.connect(client_id, websocket)

    try:
        while True:
            message = await websocket.receive_text()
            logger.info(f"Received from {client_id}: {message}")
            # response = f"server received: {message}"

            # The processing step here should convert speech into text
            # await manager.send_text(client_id, response)
            # Here the audio information needs to be sent back to the client

            for chunk in generate_audio_chunks():
                await websocket.send_bytes(chunk)

            await manager.send_text(client_id, "AUDIO_PROCESSING_COMPLETE")  # End-of-protocol marker

    except WebSocketDisconnect:
        manager.disconnect(client_id)
    except Exception as e:
        logger.error(f"Text error with {client_id}: {str(e)}")
        logger.error(e, exc_info=True)
        manager.disconnect(client_id)


@app.websocket("/ws/string/{client_id}")
async def text_endpoint(websocket: WebSocket, client_id: str):
    """Text interaction endpoint"""
    manager = app.state.manager
    await manager.connect(client_id, websocket)

    try:
        while True:
            message = await websocket.receive_text()
            logger.info(f"Received from {client_id}: {message}")

            response = f"服务器已收到：{message}"
            await manager.send_text(client_id, response)

    except WebSocketDisconnect:
        manager.disconnect(client_id)
    except Exception as e:
        logger.error(f"Text error with {client_id}: {str(e)}")
        manager.disconnect(client_id)


def main() -> None:
    """Startup entry point.

        Migration change 1: binds 127.0.0.1 by default (the original hardcoded 0.0.0.0).
          This is a "receive audio, return text" endpoint with no authentication. Binding 0.0.0.0
          would let anyone on the same subnet use this machine's compute for free, and would hand
          "what is said upstairs" to others. Exposing it requires an explicit --host 0.0.0.0, so it
          becomes a deliberate decision.

        Migration change 2: uvloop is now optional. The original unconditionally called `uvloop.install()`,
          but uvloop does not support Windows -- on Windows it could not even start.
    """
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="ASR WebSocket 服务（:8888）")
    ap.add_argument("--host", default=os.environ.get("ASR_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("ASR_PORT", "8888")))
    args = ap.parse_args()

    try:
        import uvloop

        uvloop.install()
        logger.info("uvloop installed")
    except ImportError:
        logger.info("uvloop 不可用，使用默认事件循环")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        ws_ping_interval=10,
        ws_ping_timeout=30,
        timeout_keep_alive=60,
    )


if __name__ == "__main__":
    main()
