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

# Migration change: the original code did `from config.cfg import Syscfg` +
#   `cfg = Syscfg()`, yet never used cfg; but constructing Syscfg requires APP_ROOT_DIR
#   + config/config.json in place -- tying a whole service to someone else's
#   configuration system for one useless line, so it was removed.
logger = get_logger("reasoning_websocket")

# Debug audio is off by default: the original wrote a wav into debug_audio/ on every recognition,
#   and the original project had accumulated 178MB / 417 files. Off by default; set ASR_DEBUG_AUDIO=1 when needed.
SAVE_DEBUG_AUDIO = os.environ.get("ASR_DEBUG_AUDIO", "0") == "1"


# ============================================================================
# Real ASR engine (speech -> text)
# Reuses the source project's SenseVoiceSmall (funasr) + fsmn_vad, inferred locally offline.
# Behaves the same as the source project's audio_conv_text, except:
#   - automatic device selection: mps (Apple) > cuda > cpu (the source hardcoded cuda:0, and this host has no CUDA)
#   - lazy loading + resident in memory (the model loads once; later recognitions take milliseconds)
# ============================================================================
class ASREngine:
    """Local SenseVoice ASR wrapper (lazy-loaded singleton)"""

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

        # Explicit config (asr.env) wins; when the local directory is missing, fall back to the model id and let funasr download it;
        # the log prints "original config value + why + download size" -- see _models.py
        voice_model_path = voice_model()
        vad_model_path = vad_model()
        device = self._pick_device()
        logger.info(f"[ASR] loading SenseVoice model: {voice_model_path} device={device}")
        self._model = AutoModel(
            model=voice_model_path,
            vad_model=vad_model_path,
            vad_kwargs={"max_single_segment_time": 30000},
            device=device,
            disable_update=True,
            model_kwargs={"language": "zh"},
        )
        logger.info("[ASR] model loaded")
        return self._model

    def transcribe_bytes(self, pcm_bytes: bytes) -> str:
        """Receive audio bytes, normalize them to 16k/16bit/mono PCM, recognize and return text.

                Supports several input formats (the server sniffs automatically; the client need not declare):
                  - raw PCM (16k/mono/s16le) -> used as-is
                  - CAF (iOS)               -> ffmpeg normalization (falls back to manual header stripping)
                  - m4a/aac/amr/webm/ogg    -> ffmpeg decoding
                  - wav                     -> ffmpeg decoding
        """
        from funasr.utils.postprocess_utils import rich_transcription_postprocess

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

        res = model.generate(
            input=audio,
            cache={},
            language="zh",
            use_itn=True,
            batch_size_s=60,
            merge_vad=True,
            merge_length_s=15,
        )
        text = rich_transcription_postprocess(res[0]["text"])
        logger.info(f"[ASR] transcribe ok: {text!r}")
        return text


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
