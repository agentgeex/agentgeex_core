"""
Audio format sniffing + decoding -> unified PCM (16k / mono / s16le)

Background
----------
The ASR engine (SenseVoice/funasr) only accepts raw PCM (int16, 16k, mono).
Clients record in different formats though:
  - iOS    : LINEARPCM (*.caf, with a CAF header) -> just strip the header
  - Web    : MediaRecorder webm/opus              -> needs decoding
  - Android: MediaRecorder m4a/aac                -> needs decoding (Android cannot record PCM directly)

This module is responsible for:
  1. sniffing the byte header to detect the real container format (not trusting what the client claims)
  2. normalizing to 16k / mono / s16le PCM bytes
  3. returning unrecognized formats unchanged as raw PCM (backward compatible)

Design principles
-----------------
  - auto sniffing: clients need not declare a format
  - backward compatible: raw PCM passes straight through, so the existing iPad pipeline is unaffected
  - observable failures: a failed decode raises AudioDecodeError instead of silently emitting garbage
"""

from __future__ import annotations

import logging
import os
import struct
import subprocess

logger = logging.getLogger("audio_decode")

# Target format (the ASR engine's only requirement)
TARGET_RATE = 16000
TARGET_CHANNELS = 1
TARGET_SAMPLE_WIDTH = 2  # bytes, s16le

# ffmpeg executable (overridable via environment variable)
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")


class AudioDecodeError(Exception):
    """Audio decoding failed"""


# ---------------------------------------------------------------------------
# 1) Format sniffing
# ---------------------------------------------------------------------------
def sniff_format(data: bytes) -> str:
    """Sniff the audio format from its magic number.

        Returns: 'wav' | 'aiff' | 'caf' | 'm4a' | 'amr' | 'webm' | 'ogg' | 'mp3' | 'raw'
    """
    if not data or len(data) < 4:
        return "raw"

    head = data[:16]

    # CAF (Core Audio Format) - Apple
    if head[:4] == b"caff":
        return "caf"

    # RIFF/WAVE
    if head[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"

    # AIFF / AIFC (default output of macOS `say`; some hardware recorders)
    # * 2026-11-06 added: this branch was missing, so AIFF fell through to "raw" and was treated as raw PCM;
    #   the result was "runs fine, no error, empty text" -- looking like "nobody spoke" when decoding was wrong.
    if head[:4] == b"FORM" and data[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"

    # MP4 / M4A : ....ftyp
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "m4a"

    # AMR
    if head[:5] == b"#!AMR":
        return "amr"

    # WebM / Matroska (EBML)
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"

    # OGG
    if head[:4] == b"OggS":
        return "ogg"

    # MP3 (ID3 tag or frame sync)
    if head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"

    # 3GP is also in the ftyp family (isom/3gp) -- already covered by the m4a branch (ftyp)
    # Everything else is treated as raw PCM
    return "raw"


# ---------------------------------------------------------------------------
# 2) Strip the CAF header -> raw PCM
# ---------------------------------------------------------------------------
def _extract_pcm_from_caf(data: bytes) -> bytes:
    """Extract raw PCM from a CAF container (locate the 'data' chunk, skip the header).

        CAF data chunk layout:
            [4B] 'data'
            [4B] chunk size (big-endian; -1 may mean unknown)
            [4B] edit count (usually 0)
            [..] raw PCM
    """
    if data[:4] != b"caff":
        return data  # Not CAF; return unchanged

    idx = data.find(b"data")
    if idx < 0:
        logger.warning("[decode] CAF 无 data chunk,原样返回")
        return data

    # After the 'data' chunk tag come 4 bytes of size, then 4 bytes of edit count
    pcm_start = idx + 4 + 4 + 4
    if pcm_start >= len(data):
        logger.warning("[decode] CAF data chunk 越界,原样返回")
        return data
    return data[pcm_start:]


# ---------------------------------------------------------------------------
# 3) Decode any format with ffmpeg -> 16k/mono/s16le PCM
# ---------------------------------------------------------------------------
def _ffmpeg_to_pcm(data: bytes, fmt: str) -> bytes:
    """Decode `data` into 16k/mono/s16le PCM via ffmpeg.

        Uses stdin/stdout pipes; no temporary files.
    """
    cmd = [
        FFMPEG_BIN,
        "-hide_banner",
        "-loglevel", "error",
        "-i", "pipe:0",             # Input from stdin
        "-f", "s16le",              # Output raw PCM
        "-acodec", "pcm_s16le",
        "-ar", str(TARGET_RATE),
        "-ac", str(TARGET_CHANNELS),
        "pipe:1",                   # Write to stdout
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=data,
            capture_output=True,
            timeout=30,
        )
    except FileNotFoundError as e:
        raise AudioDecodeError(f"ffmpeg 未找到({FFMPEG_BIN}),无法解码 {fmt}") from e
    except subprocess.TimeoutExpired as e:
        raise AudioDecodeError(f"ffmpeg 解码 {fmt} 超时") from e

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "ignore")[:300]
        raise AudioDecodeError(f"ffmpeg 解码 {fmt} 失败: {err}")

    return proc.stdout


# ---------------------------------------------------------------------------
# 4) Public entry point: any bytes -> 16k/mono/s16le PCM
# ---------------------------------------------------------------------------
def decode_to_pcm(data: bytes, hint: str | None = None) -> bytes:
    """Normalize arbitrary audio bytes into 16k/mono/s16le raw PCM.

        Args:
            data: raw bytes (may be PCM / CAF / WAV / AIFF / M4A / AMR / WebM / OGG / MP3)
            hint: optional format hint (e.g. explicitly reported by the client); takes precedence over sniffing

        Returns:
            raw PCM bytes

        Raises:
            AudioDecodeError: when decoding is required but fails
    """
    if not data:
        return b""

    fmt = (hint or sniff_format(data)).lower()
    logger.info(f"[decode] 输入 {len(data)}B, 嗅探格式={fmt}")

    # Raw PCM: return unchanged
    if fmt == "raw":
        return data

    # CAF: prefer unifying through ffmpeg (it resamples and converts channels);
    #      on failure fall back to manual header stripping (the zero-ffmpeg-dependency path)
    if fmt == "caf":
        try:
            pcm = _ffmpeg_to_pcm(data, "caf")
            logger.info(f"[decode] CAF(ffmpeg) → PCM {len(pcm)}B")
            return pcm
        except AudioDecodeError as e:
            logger.warning(f"[decode] CAF ffmpeg 失败({e}), 退回手工剥头")
            pcm = _extract_pcm_from_caf(data)
            logger.info(f"[decode] CAF 剥头 → PCM {len(pcm)}B(假定源=16k/mono)")
            return pcm

    # Other container formats: let ffmpeg decode them
    if fmt in ("wav", "aiff", "m4a", "amr", "webm", "ogg", "mp3"):
        pcm = _ffmpeg_to_pcm(data, fmt)
        logger.info(f"[decode] {fmt} → PCM {len(pcm)}B")
        return pcm

    # Fallback: treat unknown formats as raw PCM
    logger.warning(f"[decode] 未知格式 {fmt},按裸 PCM 处理")
    return data


def rms_of_pcm(pcm: bytes) -> float:
    """Compute the RMS (0..1) of s16le PCM, used for silence detection and debugging."""
    if not pcm or len(pcm) < 2:
        return 0.0
    n = len(pcm) // 2
    try:
        samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    except struct.error:
        return 0.0
    if not samples:
        return 0.0
    # Compute with int to avoid f32 precision issues
    acc = 0.0
    for s in samples:
        acc += float(s) * float(s)
    mean = acc / n
    return (mean ** 0.5) / 32768.0
