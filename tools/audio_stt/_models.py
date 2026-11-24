"""Model path resolution: fall back to the model id when the local directory does not exist, letting funasr download it.

Background (2026-11-07, the maintainer's call: "explicit config + fallback"):
    `asr.env` spells VOICE_MODEL / VAD_MODEL as two explicit local directories, which makes
    "which model is used, and where it lives" visible at a glance. The cost is that on a new
    machine, before the models have been downloaded, those two directories do not exist --
    handing a non-existent path straight to AutoModel yields
    "model loading failed", with nothing telling the operator what to do about it.

    How this module handles it:

      directory exists      -> return it as-is (normal path, zero extra cost)
      directory missing     -> log a WARNING with "config value + why + download size + where it lands",
                              return the model id (e.g. iic/SenseVoiceSmall),
                              let funasr/modelscope download it into the default cache; later runs use the local path

Why not just raise:
    Crashing on startup versus taking a few dozen seconds and then working -- the latter fits how
    this tool is used (someone is on site waiting for the transcription).
    The cost (possibly downloading a 900MB model for nothing) is explicitly borne by the WARNING --
    visible in the log with the original config value stated, so it is not a silent trap.

Why not silently fall back to the default path:
    If it is a typo, a silent fallback means "your config was ignored" without the user knowing.
    The log must show both "what you wrote" and "what was actually used".
"""

from __future__ import annotations

import os

try:  # Works both for a direct run (`python xxx.py`) and for package imports (`from tools.audio_stt._models import ...`)
    from ._logging import get_logger
except ImportError:  # pragma: no cover - direct script execution
    from _logging import get_logger

DEFAULT_VOICE_MODEL = "~/.cache/modelscope/hub/models/iic/SenseVoiceSmall"
DEFAULT_VAD_MODEL = "~/.cache/modelscope/hub/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"

VOICE_MODEL_ID = "iic/SenseVoiceSmall"
VAD_MODEL_ID = "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"

logger = get_logger("audio_stt.models")

# modelscope has changed its cache layout; accept both (measured on host 188, 2026-11-07):
#   old (modelscope <= 1.2x):   <root>/hub/models/iic/SenseVoiceSmall
#   new (modelscope 1.40)  :   <root>/models/iic--SenseVoiceSmall/snapshots/master
# The DEFAULT_* above is the old layout. It does not exist on new-layout machines (e.g. 188);
# without recognizing the second layout every run would conclude "directory missing" and repeat
# the "will download on first use" warning even though the files were already local -- a false signal.
def _cache_roots() -> list[str]:
    roots: list[str] = []
    env = os.environ.get("MODELSCOPE_CACHE")
    if env:
        roots.append(os.path.expanduser(os.path.expandvars(env)))
    roots.append(os.path.expanduser("~/.cache/modelscope"))
    return roots


def _cache_candidates(model_id: str) -> list[str]:
    """List the possible model directories under both modelscope cache layouts (offline)."""
    flat = model_id.replace("/", "--")
    out: list[str] = []
    for root in _cache_roots():
        out.append(os.path.join(root, "hub", "models", *model_id.split("/")))
        out.append(os.path.join(root, "models", flat, "snapshots", "master"))
        out.append(os.path.join(root, "models", flat))
    return out


def _has_weights(path: str) -> bool:
    """A directory counts as a model dir when it contains model.pt. Existence alone is not
    enough -- under snapshots there may be only pointer/revision files and no actual weights.
    """
    return os.path.isfile(os.path.join(path, "model.pt"))


def _expand(path: str) -> str:
    """Expand ~ and $VAR -- asr.env writes $HOME, and a direct python run may still carry the literal $HOME."""
    return os.path.expanduser(os.path.expandvars(path.strip()))


def resolve_model_path(
    env_var: str,
    model_id: str,
    default_path: str,
    size_hint: str = "约 1GB",
) -> str:
    """Return the model argument to hand to funasr: an existing local directory, or a model id.

        Args:
            env_var: environment variable name (VOICE_MODEL / VAD_MODEL); used only for logging and lookup
            model_id: modelscope model id used when falling back to download
            default_path: default local path when nothing is configured
            size_hint: download size stated in the log (approximate; no fake MB precision)
    """
    raw = (os.environ.get(env_var) or "").strip()
    configured = bool(raw)
    candidate = _expand(raw or default_path)

    if os.path.isdir(candidate):
        return candidate

    # The configured/default path does not exist, but the model may already be cached locally
    # under the other layout -> use it directly: no warning, no re-download.
    for cached in _cache_candidates(model_id):
        if _has_weights(cached):
            logger.info("[模型] %s 未命中 %s，使用本机已有的 modelscope 缓存: %s", env_var, candidate, cached)
            return cached

    why = "配置的路径不存在" if configured else "默认缓存路径不存在"
    logger.warning(
        "[模型] %s=%s（%s）→ 回落到 model id '%s'，"
        "首次将由 modelscope 下载（%s，慢是正常的），下载后缓存到默认目录，之后不再重复",
        env_var, candidate, why, model_id, size_hint,
    )
    return model_id


def voice_model() -> str:
    """SenseVoice main model: prefer the local directory, fall back to iic/SenseVoiceSmall."""
    return resolve_model_path("VOICE_MODEL", VOICE_MODEL_ID, DEFAULT_VOICE_MODEL, "约 900MB")


def vad_model() -> str:
    """FSMN-VAD voice activity detection model: same as above; very small."""
    return resolve_model_path("VAD_MODEL", VAD_MODEL_ID, DEFAULT_VAD_MODEL, "约 4MB")
