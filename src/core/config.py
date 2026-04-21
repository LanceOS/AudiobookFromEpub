from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List

# Compute repository root (BASE_DIR) robustly so module can live under `src/`.
p = Path(__file__).resolve()
if "src" in p.parts:
    # repo root is the path up to but not including the `src` segment
    src_index = p.parts.index("src")
    BASE_DIR = Path(*p.parts[:src_index])
else:
    BASE_DIR = p.parents[1]

APP_DATA_DIR = BASE_DIR / ".app_data"
UPLOADS_DIR = APP_DATA_DIR / "uploads"
JOB_META_DIR = APP_DATA_DIR / "jobs"
DEFAULT_OUTPUT_DIR = BASE_DIR / "generated_audio"
DEFAULT_HF_MODEL_CACHE_DIR = APP_DATA_DIR / "hf_models"
ALLOWED_UPLOAD_EXTENSIONS = {".epub"}
JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")
HF_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)?$")
HF_MODEL_CACHE_DIR_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")

def discover_kokoro_voice_options() -> List[str]:
    voices_dir = BASE_DIR / "Kokoro-82M" / "voices"
    if not voices_dir.exists() or not voices_dir.is_dir():
        return []

    discovered = sorted({path.stem for path in voices_dir.glob("*.pt") if path.is_file() and path.stem})
    return discovered


KOKORO_VOICE_OPTIONS = discover_kokoro_voice_options()
QWEN3_CUSTOMVOICE_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
QWEN3_CUSTOMVOICE_SPEAKERS = [
    "Vivian",
    "Serena",
    "Uncle_Fu",
    "Dylan",
    "Eric",
    "Ryan",
    "Aiden",
    "Ono_Anna",
    "Sohee",
]

LOCAL_DEFAULT_MODEL_ID = "__local_kokoro_default__"
MODEL_TYPE_OPTIONS = {"kokoro", "qwen3_customvoice", "voxcpm2", "other"}
MODEL_TYPE_LABELS: Dict[str, str] = {
    "kokoro": "Kokoro",
    "qwen3_customvoice": "Qwen3 CustomVoice",
    "voxcpm2": "VoxCPM2",
    "other": "Other",
}
MODEL_TYPE_ALIASES = {
    "vox": "voxcpm2",
    "voxcpm2": "voxcpm2",
    "openbmb/voxcpm2": "voxcpm2",
    "qwen": "qwen3_customvoice",
    "qwen3": "qwen3_customvoice",
    "qwen3_customvoice": "qwen3_customvoice",
    "qwen3-customvoice": "qwen3_customvoice",
    "qwen/qwen3-tts-12hz-1.7b-customvoice": "qwen3_customvoice",
}
MODEL_VOICE_OPTIONS: Dict[str, List[str]] = {
    "kokoro": list(KOKORO_VOICE_OPTIONS),
    "qwen3_customvoice": list(QWEN3_CUSTOMVOICE_SPEAKERS),
    "voxcpm2": [],
    "other": [],
}
PREDEFINED_MODEL_CATALOG = [
    {
        "id": "hexgrad/Kokoro-82M",
        "display_name": "Kokoro 82M (Hugging Face)",
        "model_type": "kokoro",
        "description": "Official Kokoro model repository.",
        "voices": list(KOKORO_VOICE_OPTIONS),
        "default_voice": KOKORO_VOICE_OPTIONS[0] if KOKORO_VOICE_OPTIONS else None,
        "supports_generation": True,
    },
    {
        "id": QWEN3_CUSTOMVOICE_MODEL_ID,
        "display_name": "Qwen3 TTS 1.7B CustomVoice",
        "model_type": "qwen3_customvoice",
        "description": "Qwen3-TTS model with predefined multi-language speakers.",
        "voices": list(QWEN3_CUSTOMVOICE_SPEAKERS),
        "default_voice": QWEN3_CUSTOMVOICE_SPEAKERS[0],
        "supports_generation": True,
    },
]

ACTIVE_JOB_STATUSES = {"queued", "running", "stopping"}


def max_upload_bytes_from_env() -> int:
    default_mb = 50
    raw = os.getenv("AUDIOBOOK_MAX_UPLOAD_MB", str(default_mb)).strip()
    try:
        mb = int(raw)
    except ValueError:
        mb = default_mb
    mb = max(1, min(mb, 512))
    return mb * 1024 * 1024
