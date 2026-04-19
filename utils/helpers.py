from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import (
    DEFAULT_OUTPUT_DIR,
    HF_MODEL_ID_RE,
    JOB_ID_RE,
    MODEL_TYPE_OPTIONS,
    MODEL_VOICE_OPTIONS,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def slugify(value: str, fallback: str = "untitled") -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or fallback


def get_allowed_output_root() -> Tuple[Optional[Path], Optional[str]]:
    root_text = os.getenv("AUDIOBOOK_ALLOWED_OUTPUT_ROOT", str(DEFAULT_OUTPUT_DIR))
    root_path = Path(root_text).expanduser()
    try:
        resolved_root = root_path.resolve(strict=False)
        resolved_root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return None, f"Configured output root is invalid: {exc}"
    return resolved_root, None


def validate_output_directory(path_text: str) -> Tuple[Optional[Path], Optional[str]]:
    if not path_text or not str(path_text).strip():
        return None, "Output directory is required."
    output_dir = Path(path_text).expanduser()
    try:
        resolved_output = output_dir.resolve(strict=False)
    except Exception as exc:
        return None, f"Invalid output directory path: {exc}"
    try:
        resolved_output.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return None, f"Unable to create output directory: {exc}"

    if not os.access(resolved_output, os.W_OK):
        return None, "Output directory is not writable."

    return resolved_output, None


def normalize_hf_model_id(raw_value: object) -> Optional[str]:
    text = str(raw_value or "").strip()
    return text or None


def validate_hf_model_id(raw_value: object) -> Tuple[Optional[str], Optional[str]]:
    model_id = normalize_hf_model_id(raw_value)
    if model_id is None:
        return None, None

    if len(model_id) > 128:
        return None, "Hugging Face model ID is too long."

    if not HF_MODEL_ID_RE.fullmatch(model_id):
        return None, "Hugging Face model ID must look like 'namespace/model' or 'model'."

    return model_id, None


def normalize_model_type(raw_value: object, default: str = "kokoro") -> str:
    model_type = str(raw_value or "").strip().lower()
    if model_type in MODEL_TYPE_OPTIONS:
        return model_type
    return default


def supports_generation_for_model_type(model_type: str) -> bool:
    return normalize_model_type(model_type) == "kokoro"


def model_voices_for_type(model_type: str) -> List[str]:
    normalized_type = normalize_model_type(model_type)
    return list(MODEL_VOICE_OPTIONS.get(normalized_type, MODEL_VOICE_OPTIONS["other"]))


def is_valid_job_id(job_id: str) -> bool:
    return bool(JOB_ID_RE.fullmatch((job_id or "").strip()))


def iso_to_epoch(value: Optional[str]) -> Optional[float]:
    if not value:
        return None

    try:
        normalized = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except Exception:
        return None


def calculate_elapsed_seconds(started_at: Optional[str], finished_at: Optional[str] = None) -> Optional[float]:
    start_epoch = iso_to_epoch(started_at)
    if start_epoch is None:
        return None

    end_epoch = iso_to_epoch(finished_at) if finished_at else time.time()
    if end_epoch is None:
        return None

    return round(max(0.0, end_epoch - start_epoch), 1)


def create_run_folder(output_dir: Path, base_name: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = f"{slugify(base_name, fallback='audiobook')}_{stamp}"
    run_dir = output_dir / root
    suffix = 1
    while run_dir.exists():
        run_dir = output_dir / f"{root}_{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def split_text_into_chunks(text: str, max_chars: int = 700) -> List[str]:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return []

    chunks: List[str] = []
    current = ""

    for sentence in re.split(r"(?<=[.!?])\s+", normalized):
        sentence = sentence.strip()
        if not sentence:
            continue

        if len(sentence) > max_chars:
            parts = [sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars)]
        else:
            parts = [sentence]

        for part in parts:
            if not current:
                current = part
                continue

            candidate = f"{current} {part}"
            if len(candidate) <= max_chars:
                current = candidate
            else:
                chunks.append(current)
                current = part

    if current:
        chunks.append(current)

    return chunks


def voice_to_lang_code(voice: str) -> str:
    if "_" not in voice:
        return "a"
    return voice.split("_", 1)[0][0]
