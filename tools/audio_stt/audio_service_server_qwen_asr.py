"""Qwen3-ASR-1.7B speech-to-text service (a copy running in parallel with audio_service_server.py).

════════════════════════════════════════════════════════════
 What this file is for
════════════════════════════════════════════════════════════

Wraps Qwen3-ASR-1.7B as a WebSocket service; the protocol is identical to the original SenseVoice version:
    client sends PCM chunks -> sends an empty frame b"" -> the service returns text + "AUDIO_PROCESSING_COMPLETE"

Relationship to the original: **audio_service_server.py is untouched, line for line**. This is a parallel
experiment copy, and its default port is offset too, so both can run at once and either can be withdrawn at any time.

────────────────────────────────────────────────────────────
 This model's temperament (know it before deploying; design, not a bug)
────────────────────────────────────────────────────────────

1. **It does not do ITN (no digit conversion); that is the model's remit, not a defect.**
   Qwen3-ASR is a verbatim transcription model. Three things were verified:
     - transcribe(context="please write numbers as Arabic digits") -> the output was unchanged, character for character
       (context is a hotword/contextual bias, not a format instruction; the model does not follow instructions)
     - The community wrapper ComfyUI-QwenASR also relies on an external itn_rules.json for digit and letter merging
     - arXiv 2609.02901: ITN has cascade and unified routes; Qwen3-ASR takes cascade

   * This service **does no digit mapping by default** (laofeng, 2026-11-20: after mapping the experience was
     not better -- mixed case and simplified/traditional soup came out messier -- and he prefers to type digits himself).
     To enable mapping: QWEN_ASR_ITN_POST=1 (goes through cn_num_normalize.py in the same directory).

2. **It splits abbreviations into single letters**: `ASR` may come out as `A S R`, `4.7GB` as `4 dian qi G B`.
   This is **always merged** (pure typesetting, digits untouched) and is not governed by the ITN switch.

3. **Simplified/traditional is unstable**: the same model may output simplified or traditional for different audio
   (measured: the model's own zh.mp3 sample -> traditional; our own TTS -> simplified). Add opencc outside if uniformity is needed.

4. **Korean is unsupported** (it emits Japanese kana garbage). For Korean, switch to Fun-ASR-MLT-Nano (31 languages).

5. **No ITN, but punctuation is fine**: punctuation is normal, digits are not converted; see item 1.

════════════════════════════════════════════════════════════
 Deployment (copy as-is; measured 2026-11-20 on Ubuntu + RTX 3080)
════════════════════════════════════════════════════════════

[0] Hardware requirements
    VRAM: weights are 4.08 GB, measured peak **4.70 GB** -- a 10 GB card (3080) is comfortable.
    CPU works too but is much slower; not recommended.
    Measured speed (3080, 10-second audio): cold start 6.0s (including model load), then 0.23-0.49s (RTF ~ 0.05).

[1] Pick a directory -- do not put large files on the system disk
    Model 4.7 GB + venv about 6 GB. When the system disk is tight, check free space first: df -h
    Measured here in: /home/mat/robertfeng
    Replace <BASE> in the commands below with your own directory.

[2] Download the model (use modelscope, not git clone)
    # When modelscope is not on PATH, use the full path, e.g. ~/.local/bin/modelscope
    modelscope download --model Qwen/Qwen3-ASR-1.7B \\
        --local_dir <BASE>/models/Qwen3-ASR-1.7B
    Only 4.7 GB, two safetensors. In mainland China ModelScope is faster and steadier than HF.
    Alternative: the HF repo Qwen/Qwen3-ASR-1.7B has identical content.

[3] Create a venv (uv or python -m venv both work)
    uv venv <BASE>/venv-qwen-asr --python 3.12
    # or without uv: python3 -m venv <BASE>/venv-qwen-asr

[4] Install dependencies -- three traps here, all hit already

    1) **torch must be pinned to 2.7.0**, otherwise CUDA is unavailable:
       newer drivers/torch install as cu13x wheels, nvcc and the driver mismatch ->
       torch.cuda.is_available() returns False, and the service either runs on CPU or crashes outright.
       Old drivers (e.g. 550 / CUDA 12.4) need cu126 wheels, and PyPI's
       torch==2.7.0 linux wheel happens to be cu126.

    2) **pypi.org is unreachable from some intranet hosts**; use a mirror (Aliyun measured 1.05s, faster than Tsinghua):

    3) **A uv-created venv lacks websockets**. Without it uvicorn silently falls back to plain HTTP,
       every WebSocket request 404s, and the log shows only one easily-missed warning:
       "No supported WebSocket library detected". Install it separately.

    Full command:

    uv pip install --python <BASE>/venv-qwen-asr/bin/python \\
        --index-url https://mirrors.aliyun.com/pypi/simple/ \\
        --reinstall-package torch "torch==2.7.0" "qwen-asr" "websockets" "uvloop"

    Self-check after install (all three must pass):
    <BASE>/venv-qwen-asr/bin/python -c \\
      "import torch;print(torch.__version__, torch.cuda.is_available())"
    # expected: 2.7.0+cu126 True
    <BASE>/venv-qwen-asr/bin/python -c "import qwen_asr, websockets;print('ok')"

[5] Place the code
    This file and the files it depends on in the same directory must sit together (all are required):
        audio_service_server_qwen_asr.py   <- this file
        cn_num_normalize.py                <- Chinese-numeral post-processing (hard dependency)
        _models.py / _logging.py / audio_decode.py   <- VAD, logging, decoding
    Take them from the repo: the whole tools/audio_stt/ directory.

[6] Start the service
    # foreground smoke test
    <BASE>/venv-qwen-asr/bin/python audio_service_server_qwen_asr.py \\
        --host 0.0.0.0 --port 18888

    # long-running (use pm2; avoid nohup -- starting nohup from an automation script
    # takes the child down when the parent shell exits; measured: the service killed itself once at 09:22)
    pm2 start <start-script> --name agx-asr-qwen --interpreter bash --update-env -- run
    pm2 save

[7] Verify (do not just check that the port listens; do one full round trip)
    # server health
    curl -s http://127.0.0.1:18888/docs >/dev/null && echo up

    # Send a real clip: connect to /ws/audio/audio2text/<any id>, send PCM, then an empty frame b"",
    # and you should receive text + "AUDIO_PROCESSING_COMPLETE".
    # * Connect to the [path with a client_id]. In the original code the bare path /ws/audio/audio2text
    #   is matched first by /ws/audio/{client_id}, takes the wrong branch, returns only binary and never text.
    #   This file adds a precise route to fix that, but external docs should still use the form with an id, for compatibility with older versions.

════════════════════════════════════════════════════════════
 How to start it once installed (choose one)
════════════════════════════════════════════════════════════

**Option A: use the one-shot script in the same directory (recommended)**

    ./start_qwen_asr.sh start      # start in the background (writes a PID; logs under logs/)
    ./start_qwen_asr.sh status     # show status
    ./start_qwen_asr.sh log        # follow the log
    ./start_qwen_asr.sh stop       # stop
    ./start_qwen_asr.sh run        # run in the foreground (use this for pm2/systemd)

    The script auto-detects the interpreter and the model directory and tells you how to fix whatever is missing.
    pm2 example:
        pm2 start ./start_qwen_asr.sh --name qwen-asr -- run
        pm2 save

**Option B: run python directly**

    <BASE>/venv-qwen-asr/bin/python audio_service_server_qwen_asr.py \\
        --host 0.0.0.0 --port 18888

    * Do not start it with nohup from an automation script -- the parent shell exit takes the child down.

════════════════════════════════════════════════════════════
 Environment variables
════════════════════════════════════════════════════════════

    QWEN_ASR_MODEL          weights directory. Default ~/.cache/modelscope/hub/models/Qwen/Qwen3-ASR-1.7B
                            * A missing directory is a hard error; this service does not download from the network
    QWEN_ASR_LANG           force the language (Chinese / English). Default empty = auto-detect
    QWEN_ASR_MAX_NEW_TOKENS max generated tokens per call, default 256 (raise it for long audio)
    QWEN_ASR_ITN_POST       1=enable Chinese-numeral -> Arabic digit mapping  0=off (**default 0**)
                            * Off by default. laofeng was not satisfied with the mapped output (mixed case and
                              simplified/traditional soup) and prefers to type digits himself. When on, it goes through cn_num_normalize.py.
                            Letter-by-letter abbreviation merging (A S R -> ASR) is not governed by this switch; always on.
    ASR_DEBUG_AUDIO         1=save the audio into debug_audio/ on every recognition (default 0; do not leave it on)
    ASR_HOST / ASR_PORT     default 127.0.0.1:8888; --host/--port on the command line take precedence

    * If you use an external start script (e.g. asr_service3.sh) that relies on asr.env, note that if asr.env
      already hardcodes the entry file via `: "${ASR_ENTRY:=...}"`, the exported environment overrides it --
      to run this file you must be explicit:
      export ASR_ENTRY=<path>/audio_service_server_qwen_asr.py

════════════════════════════════════════════════════════════
 Rollback / uninstall
════════════════════════════════════════════════════════════

    Back to SenseVoice: stop this service (pm2 stop agx-asr-qwen), start the original (pm2 start agx-asr).
    Drop it entirely: pm2 delete agx-asr-qwen, delete <BASE>/models/Qwen3-ASR-1.7B (4.7G)
    and <BASE>/venv-qwen-asr (6G); in the repo delete this file and asr_service3.sh.

════════════════════════════════════════════════════════════
"""

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
from cn_num_normalize import merge_spaced_acronyms as _merge_spaced_acronyms
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
# Chinese numerals -> Arabic digits post-processing switch.
# * 2026-11-20 laofeng's decision: **off by default**.
#   Measured: the mapped output was not a better experience (mixed case and simplified/traditional soup made it messier), and he prefers to type digits himself.
#   Turn it on explicitly when needed: QWEN_ASR_ITN_POST=1
# Letter-by-letter abbreviation merging (A S R -> ASR, the letters in "4 dian qi G B" -> 4.7GB) is not governed by this switch;
# it is a pure formatting fix that does not affect digit semantics, and is always on.
ITN_POST = os.environ.get("QWEN_ASR_ITN_POST", "0") == "1"


# ============================================================================
# Real ASR engine (speech -> text)
# * This file is the 3rd [parallel experiment copy] of audio_service_server.py: the same WebSocket
#   protocol and the same port, only the ASR engine swapped for **Qwen3-ASR-1.7B** (2026-01).
#   The original audio_service_server.py is untouched line for line, so rollback is always possible.
#
# Three generations compared (measured with the same batch of 16k wav, English containing times/amounts/large numbers):
#   SenseVoiceSmall  'at 345 pm ... budget is $1250,000'   <- separators eaten; 0.33s on 10s audio
#   Fun-ASR-Nano     'at 3:45 pm ... budget is $1,250,000' <- correct; 6.7x slower
#   Qwen3-ASR-1.7B   to be measured (this file)
#
# The most fundamental difference from the other two: this is the only implementation [not built on funasr].
#   SenseVoice / Fun-ASR-Nano both run funasr.AutoModel;
#   Qwen3-ASR runs the official `qwen-asr` package (transformers or vLLM backend),
#   its base is Qwen3-Omni and the output goes through LLM decoding.
#
# Dependency note: pip install -U qwen-asr (it pins transformers==4.57.6, so a dedicated venv is required)
# ============================================================================

# Qwen3-ASR weights directory: override with QWEN_ASR_MODEL. A missing directory is an error; no network download.
DEFAULT_QWEN_ASR_MODEL = "~/.cache/modelscope/hub/models/Qwen/Qwen3-ASR-1.7B"


def qwen_asr_model_path() -> str:
    """Resolve the Qwen3-ASR weights directory (local only; no network download)."""
    raw = os.environ.get("QWEN_ASR_MODEL", DEFAULT_QWEN_ASR_MODEL)
    return os.path.expanduser(os.path.expandvars(raw.strip()))


class ASREngine:
    """Local Qwen3-ASR wrapper (lazy-loaded singleton)"""

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

        model_path = qwen_asr_model_path()
        if not os.path.isdir(model_path):
            raise RuntimeError(
                f"Qwen3-ASR 权重目录不存在: {model_path}\n"
                f"下载: modelscope download --model Qwen/Qwen3-ASR-1.7B --local_dir {model_path}\n"
                f"或用 QWEN_ASR_MODEL=<别的目录> 指定。"
            )

        import torch
        from qwen_asr import Qwen3ASRModel

        device = self._pick_device()
        logger.info(f"[ASR] loading Qwen3-ASR: {model_path} device={device}")
        # attn_implementation is not passed: when flash-attn is absent, transformers falls back to eager/sdpa on its own.
        # forced_aligner is not loaded: this service only needs text, not timestamps (which would cost a chunk of VRAM).
        self._model = Qwen3ASRModel.from_pretrained(
            model_path,
            dtype=torch.bfloat16,
            device_map=device,
            max_inference_batch_size=1,
            max_new_tokens=int(os.environ.get("QWEN_ASR_MAX_NEW_TOKENS", "256")),
        )
        logger.info("[ASR] Qwen3-ASR loaded")
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

        # 16-bit PCM -> float32, scaled to [-1, 1].
        # qwen-asr takes an (np.ndarray, sample_rate) pair; no need to write a file.
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

        # language=None: let the model detect the language. With mixed Chinese/English this is steadier than hard-coding "Chinese".
        # To force it, use QWEN_ASR_LANG=Chinese/English.
        lang = os.environ.get("QWEN_ASR_LANG") or None
        results = model.transcribe(audio=(audio, 16000), language=lang)
        text = results[0].text
        logger.info(f"[ASR] transcribe ok (lang={getattr(results[0], 'language', '?')}): {text!r}")
        if ITN_POST:
            normalized = _normalize_cn_numbers(text)
            if normalized != text:
                logger.info(f"[ASR] itn post -> {normalized!r}")
            text = normalized
        else:
            # Digits stay as spoken; letter-by-letter abbreviations (A S R) are still merged back to ASR --
            # that is pure typesetting and changes no digit semantics.
            text = _merge_spaced_acronyms(text)
        return text


# * Qwen3-ASR has no hotwords parameter (that is a funasr-family interface), so this file offers no hotwords.


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
