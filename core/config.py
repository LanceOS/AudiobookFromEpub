from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent
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
MODEL_TYPE_OPTIONS = {"kokoro", "other"}
MODEL_TYPE_LABELS: Dict[str, str] = {
    "kokoro": "Kokoro",
    "other": "Other",
}
MODEL_VOICE_OPTIONS: Dict[str, List[str]] = {
    "kokoro": list(VOICE_OPTIONS),
    "other": ["default"],
}
PREDEFINED_MODEL_CATALOG = [
    {
        "id": "hexgrad/Kokoro-82M",
        "display_name": "Kokoro 82M (Hugging Face)",
        "model_type": "kokoro",
        "description": "Official Kokoro model repository.",
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
