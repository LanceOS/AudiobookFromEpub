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

VOICE_OPTIONS = [
    "af_heart",
    "af_bella",
    "af_nicole",
    "am_adam",
    "am_michael",
    "bf_emma",
    "bm_george",
    "ef_dora",
    "ff_siwis",
    "hf_alpha",
    "if_sara",
    "pf_dora",
]

LOCAL_DEFAULT_MODEL_ID = "__local_kokoro_default__"
MODEL_TYPE_OPTIONS = {"kokoro", "voxcpm2", "other"}
MODEL_TYPE_LABELS: Dict[str, str] = {
    "kokoro": "Kokoro",
    "voxcpm2": "VoxCPM2",
    "other": "Other",
}
MODEL_TYPE_ALIASES = {
    "vox": "voxcpm2",
    "voxcpm2": "voxcpm2",
    "openbmb/voxcpm2": "voxcpm2",
}
MODEL_VOICE_OPTIONS: Dict[str, List[str]] = {
    "kokoro": list(VOICE_OPTIONS),
    "voxcpm2": [],
    "other": [],
}
PREDEFINED_MODEL_CATALOG = [
    {
        "id": "hexgrad/Kokoro-82M",
        "display_name": "Kokoro 82M (Hugging Face)",
        "model_type": "kokoro",
        "description": "Official Kokoro model repository.",
        "voices": list(VOICE_OPTIONS),
        "default_voice": VOICE_OPTIONS[0],
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
