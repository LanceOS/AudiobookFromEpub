#!/usr/bin/env python3
"""
Localhost EPUB -> audiobook web application.

Run:
    python main.py
Then open:
    http://127.0.0.1:5000
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import numpy as np
from core.config import (
    ACTIVE_JOB_STATUSES,
    ALLOWED_UPLOAD_EXTENSIONS,
    APP_DATA_DIR,
    BASE_DIR,
    DEFAULT_HF_MODEL_CACHE_DIR,
    DEFAULT_OUTPUT_DIR,
    HF_MODEL_CACHE_DIR_SEGMENT_RE,
    JOB_META_DIR,
    LOCAL_DEFAULT_MODEL_ID,
    MODEL_TYPE_LABELS,
    PREDEFINED_MODEL_CATALOG,
    UPLOADS_DIR,
    VOICE_OPTIONS,
    max_upload_bytes_from_env,
)
from features import jobs as job_feature
from services.epub_service import (
    _normalize_filter_level,
    _should_skip_chapter,
    extract_book_title,
    extract_chapters_from_epub,
    looks_like_valid_epub,
)
from routes.jobs import register_job_routes
from routes.middleware import register_middleware
from routes.models import register_model_routes
from routes.ui import register_ui_routes
from routes.upload import register_upload_routes
from utils.helpers import (
    calculate_elapsed_seconds,
    create_run_folder,
    get_allowed_output_root,
    is_valid_job_id,
    iso_to_epoch,
    model_voices_for_type,
    normalize_hf_model_id,
    normalize_model_type,
    now_iso,
    slugify,
    split_text_into_chunks,
    supports_generation_for_model_type,
    validate_hf_model_id,
    validate_output_directory,
    voice_to_lang_code,
)
# Lazy-import heavy audio/torch libraries when necessary to allow test-mode
# Lazy-import EPUB parsing and HTML parsing libraries when needed
try:
    from flask import Flask, jsonify, render_template, request, send_from_directory, session  # type: ignore[reportMissingImports]
    from werkzeug.utils import secure_filename  # type: ignore[reportMissingImports]
except Exception as exc:  # pragma: no cover - helpful user-facing error
    sys.stderr.write("Missing required Python package 'flask' or 'werkzeug'.\n")
    sys.stderr.write("Fix: activate kokoro_venv and install requirements:\n")
    sys.stderr.write("  source kokoro_venv/bin/activate\n")
    sys.stderr.write("  pip install -r requirements.txt\n")
    sys.stderr.write("Or install Flask directly: pip install flask\n")
    sys.stderr.write("Then re-run: python main.py\n")
    raise

try:
    from kokoro import KPipeline  # type: ignore[reportMissingImports]
    from kokoro.model import KModel  # type: ignore[reportMissingImports]

    HAS_KOKORO = True
    KOKORO_IMPORT_ERROR = ""
except Exception as exc:
    KPipeline = None
    KModel = None
    HAS_KOKORO = False
    KOKORO_IMPORT_ERROR = str(exc)

JOBS: Dict[str, Dict] = {}
JOBS_LOCK = threading.Lock()
WORKERS: Dict[str, threading.Thread] = {}
WORKERS_LOCK = threading.Lock()
PIPELINE_CACHE: Dict[str, Dict] = {}
PIPELINE_LOCK = threading.Lock()
RATE_LIMITS = {
    "upload": (30, 60),
    "generate": (30, 60),
    "jobs": (600, 60),
}
RATE_LIMIT_STATE: Dict[str, List[float]] = {}
RATE_LIMIT_LOCK = threading.Lock()
CLEANUP_LOCK = threading.Lock()
CLEANUP_LAST_RUN = 0.0
MODEL_DOWNLOADS: Dict[str, Dict] = {}
MODEL_DOWNLOADS_LOCK = threading.Lock()
MODEL_DOWNLOAD_WORKERS: Dict[str, threading.Thread] = {}
MODEL_DOWNLOAD_WORKERS_LOCK = threading.Lock()


JobStopped = job_feature.JobStopped

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = max_upload_bytes_from_env()
app.secret_key = os.getenv("AUDIOBOOK_SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.getenv("AUDIOBOOK_COOKIE_SECURE", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def ensure_app_dirs() -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    JOB_META_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    get_hf_model_cache_root().mkdir(parents=True, exist_ok=True)
    maybe_cleanup_stale_data()


def is_hf_model_cached(model_id: str) -> bool:
    if not model_id:
        return False

    cache_path = get_hf_model_cache_path(model_id)
    if not cache_path.exists() or not cache_path.is_dir():
        return False

    for path in cache_path.rglob("*"):
        if path.is_file():
            return True
    return False


def build_model_catalog_entry(
    model_id: str,
    display_name: str,
    model_type: str,
    *,
    description: str,
    predefined: bool,
) -> Dict:
    normalized_type = normalize_model_type(model_type)
    downloaded = is_hf_model_cached(model_id)
    status = "downloaded" if downloaded else "not_downloaded"
    supports_generation = supports_generation_for_model_type(normalized_type)
    return {
        "id": model_id,
        "display_name": display_name,
        "model_type": normalized_type,
        "model_type_label": MODEL_TYPE_LABELS.get(normalized_type, MODEL_TYPE_LABELS["other"]),
        "description": description,
        "predefined": bool(predefined),
        "download_required": True,
        "downloaded": downloaded,
        "status": status,
        "progress": 100 if downloaded else 0,
        "message": "Model is available locally." if downloaded else "Model is not downloaded yet.",
        "error": None,
        "supports_generation": supports_generation,
        "voices": model_voices_for_type(normalized_type),
    }


def local_default_model_entry() -> Dict:
    return {
        "id": LOCAL_DEFAULT_MODEL_ID,
        "display_name": "Built-in Kokoro (default)",
        "model_type": "kokoro",
        "model_type_label": MODEL_TYPE_LABELS["kokoro"],
        "description": "Bundled Kokoro model shipped with this app.",
        "predefined": True,
        "download_required": False,
        "downloaded": True,
        "status": "ready",
        "progress": 100,
        "message": "Built-in model is ready.",
        "error": None,
        "supports_generation": True,
        "voices": model_voices_for_type("kokoro"),
    }


def find_predefined_model(model_id: str) -> Optional[Dict]:
    target = str(model_id or "").strip()
    if not target:
        return None

    for item in PREDEFINED_MODEL_CATALOG:
        if str(item.get("id", "")).strip() == target:
            return dict(item)
    return None


def make_manual_model_entry(model_id: str, model_type: str) -> Dict:
    normalized_type = normalize_model_type(model_type)
    downloaded = is_hf_model_cached(model_id)
    status = "downloaded" if downloaded else "not_downloaded"
    supports_generation = supports_generation_for_model_type(normalized_type)
    return {
        "id": model_id,
        "display_name": f"Manual model ({model_id})",
        "model_type": normalized_type,
        "model_type_label": MODEL_TYPE_LABELS.get(normalized_type, MODEL_TYPE_LABELS["other"]),
        "description": "User-specified Hugging Face model.",
        "predefined": False,
        "download_required": True,
        "downloaded": downloaded,
        "status": status,
        "progress": 100 if downloaded else 0,
        "message": "Model is available locally." if downloaded else "Model is not downloaded yet.",
        "error": None,
        "supports_generation": supports_generation,
        "voices": model_voices_for_type(normalized_type),
    }


def get_model_download_state(model_id: str) -> Optional[Dict]:
    with MODEL_DOWNLOADS_LOCK:
        entry = MODEL_DOWNLOADS.get(model_id)
        return dict(entry) if entry else None


def set_model_download_state(model_id: str, **fields: object) -> Dict:
    with MODEL_DOWNLOADS_LOCK:
        current = dict(MODEL_DOWNLOADS.get(model_id) or {})
        current.update(fields)
        current["id"] = model_id
        current["updated_at"] = now_iso()
        MODEL_DOWNLOADS[model_id] = current
        return dict(current)


def merge_model_download_state(entry: Dict) -> Dict:
    merged = dict(entry)
    runtime = get_model_download_state(str(entry.get("id", "")))
    if not runtime:
        return merged

    for key in (
        "display_name",
        "model_type",
        "model_type_label",
        "description",
        "predefined",
        "download_required",
        "downloaded",
        "status",
        "progress",
        "message",
        "error",
        "supports_generation",
        "voices",
        "updated_at",
    ):
        if key in runtime:
            merged[key] = runtime[key]

    normalized_type = normalize_model_type(merged.get("model_type", "kokoro"))
    merged["model_type"] = normalized_type
    merged["model_type_label"] = MODEL_TYPE_LABELS.get(normalized_type, MODEL_TYPE_LABELS["other"])
    merged["supports_generation"] = supports_generation_for_model_type(normalized_type)
    merged["voices"] = model_voices_for_type(normalized_type)

    if bool(merged.get("downloaded")):
        merged["progress"] = 100
        if str(merged.get("status", "")) in {"not_downloaded", "downloading", "failed", ""}:
            merged["status"] = "downloaded"
        if not str(merged.get("message", "")).strip():
            merged["message"] = "Model is available locally."

    return merged


def list_available_models() -> List[Dict]:
    models = [local_default_model_entry()]
    known_ids = {LOCAL_DEFAULT_MODEL_ID}

    for item in PREDEFINED_MODEL_CATALOG:
        model_id = str(item["id"])
        known_ids.add(model_id)
        models.append(
            build_model_catalog_entry(
                model_id,
                str(item["display_name"]),
                str(item["model_type"]),
                description=str(item.get("description", "")),
                predefined=True,
            )
        )

    with MODEL_DOWNLOADS_LOCK:
        extra_entries = [
            dict(entry)
            for model_id, entry in MODEL_DOWNLOADS.items()
            if model_id not in known_ids and model_id
        ]

    for entry in extra_entries:
        model_id = str(entry.get("id", "")).strip()
        if not model_id:
            continue

        model_type = normalize_model_type(entry.get("model_type", "kokoro"))
        manual_entry = make_manual_model_entry(model_id, model_type)
        manual_entry.update(
            {
                "display_name": str(entry.get("display_name") or manual_entry["display_name"]),
                "description": str(entry.get("description") or manual_entry["description"]),
                "status": str(entry.get("status") or manual_entry["status"]),
                "progress": int(entry.get("progress", manual_entry["progress"])),
                "message": str(entry.get("message") or manual_entry["message"]),
                "error": entry.get("error"),
                "downloaded": bool(entry.get("downloaded", manual_entry["downloaded"])),
            }
        )
        models.append(manual_entry)

    models = [merge_model_download_state(entry) for entry in models]
    return models


def get_model_catalog_entry(model_id: str, model_type: Optional[str] = None) -> Dict:
    if model_id == LOCAL_DEFAULT_MODEL_ID:
        return local_default_model_entry()

    for entry in list_available_models():
        if str(entry.get("id", "")).strip() == model_id:
            return dict(entry)

    fallback_type = normalize_model_type(model_type or "kokoro")
    return make_manual_model_entry(model_id, fallback_type)


def get_hf_model_cache_root() -> Path:
    cache_root = os.getenv("AUDIOBOOK_HF_MODEL_CACHE_DIR", str(DEFAULT_HF_MODEL_CACHE_DIR)).strip()
    return Path(cache_root or str(DEFAULT_HF_MODEL_CACHE_DIR)).expanduser().resolve(strict=False)


def get_hf_model_cache_path(model_id: str) -> Path:
    normalized = str(model_id or "").strip()
    safe_segment = HF_MODEL_CACHE_DIR_SEGMENT_RE.sub("__", normalized).strip("._")
    if not safe_segment:
        safe_segment = "model"
    return get_hf_model_cache_root() / safe_segment


def download_hf_model_snapshot(
    model_id: str,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Tuple[Optional[Path], Optional[str]]:
    cache_path = get_hf_model_cache_path(model_id)

    def emit_progress(percent: int, message: str) -> None:
        if progress_callback is None:
            return
        bounded = max(0, min(100, int(percent)))
        progress_callback(bounded, message)

    emit_progress(0, f"Preparing download for model '{model_id}'...")

    try:
        cache_path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return None, f"Unable to prepare local model cache: {exc}"

    try:
        from huggingface_hub import hf_hub_download, snapshot_download  # type: ignore[reportMissingImports]
    except Exception as exc:
        return None, f"huggingface_hub is unavailable: {exc}"

    token = (os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_HUB_TOKEN") or "").strip() or None

    def download_single_file(filename: str) -> Optional[str]:
        kwargs: Dict[str, object] = {
            "repo_id": model_id,
            "filename": filename,
            "local_dir": str(cache_path),
        }
        if token:
            kwargs["token"] = token

        try:
            hf_hub_download(**kwargs)
            return None
        except TypeError:
            legacy_kwargs: Dict[str, object] = {
                "repo_id": model_id,
                "filename": filename,
                "local_dir": str(cache_path),
            }
            if token:
                legacy_kwargs["use_auth_token"] = token
            try:
                hf_hub_download(**legacy_kwargs)
                return None
            except Exception as exc:
                return str(exc)
        except Exception as exc:
            return str(exc)

    primary_kwargs: Dict[str, object] = {
        "repo_id": model_id,
        "local_dir": str(cache_path),
    }
    if token:
        primary_kwargs["token"] = token

    if progress_callback is None:
        try:
            snapshot_download(**primary_kwargs)
        except TypeError:
            legacy_kwargs: Dict[str, object] = {
                "repo_id": model_id,
                "local_dir": str(cache_path),
            }
            if token:
                legacy_kwargs["use_auth_token"] = token
            try:
                snapshot_download(**legacy_kwargs)
            except Exception as exc:
                return None, f"Unable to download Hugging Face model '{model_id}': {exc}"
        except Exception as exc:
            return None, f"Unable to download Hugging Face model '{model_id}': {exc}"

        return cache_path, None

    dry_run_files: List[object] = []
    try:
        dry_run_result = snapshot_download(dry_run=True, **primary_kwargs)
        if isinstance(dry_run_result, list):
            dry_run_files = list(dry_run_result)
    except TypeError:
        # Older huggingface_hub may not support dry_run.
        dry_run_files = []
    except Exception as exc:
        return None, f"Unable to inspect Hugging Face model '{model_id}': {exc}"

    if dry_run_files:
        total_bytes = sum(max(0, int(getattr(entry, "file_size", 0) or 0)) for entry in dry_run_files)
        if total_bytes <= 0:
            total_bytes = max(1, len(dry_run_files))

        downloaded_bytes = 0
        completed_files = 0
        total_files = len(dry_run_files)

        for entry in dry_run_files:
            filename = str(getattr(entry, "filename", "")).strip()
            if not filename:
                continue

            file_size = max(0, int(getattr(entry, "file_size", 0) or 0))
            will_download = bool(getattr(entry, "will_download", True))
            if will_download:
                download_err = download_single_file(filename)
                if download_err:
                    return None, f"Unable to download Hugging Face model '{model_id}': {download_err}"

            completed_files += 1
            downloaded_bytes += file_size if file_size > 0 else 1
            ratio = downloaded_bytes / max(1, total_bytes)
            percent = int(min(100, ratio * 100))
            emit_progress(percent, f"Downloading model files... {percent}% ({completed_files}/{total_files})")

        emit_progress(100, f"Model download complete ({completed_files}/{total_files} files).")
        return cache_path, None

    try:
        snapshot_download(**primary_kwargs)
    except TypeError:
        legacy_kwargs: Dict[str, object] = {
            "repo_id": model_id,
            "local_dir": str(cache_path),
        }
        if token:
            legacy_kwargs["use_auth_token"] = token
        try:
            snapshot_download(**legacy_kwargs)
        except Exception as exc:
            return None, f"Unable to download Hugging Face model '{model_id}': {exc}"
    except Exception as exc:
        return None, f"Unable to download Hugging Face model '{model_id}': {exc}"

    emit_progress(100, "Model download complete.")
    return cache_path, None


def register_model_download_worker(model_id: str, worker: threading.Thread) -> None:
    with MODEL_DOWNLOAD_WORKERS_LOCK:
        MODEL_DOWNLOAD_WORKERS[model_id] = worker


def unregister_model_download_worker(model_id: str, worker: Optional[threading.Thread] = None) -> None:
    with MODEL_DOWNLOAD_WORKERS_LOCK:
        current = MODEL_DOWNLOAD_WORKERS.get(model_id)
        if current is None:
            return
        if worker is not None and worker is not current:
            return
        MODEL_DOWNLOAD_WORKERS.pop(model_id, None)


def is_model_download_active(model_id: str) -> bool:
    with MODEL_DOWNLOAD_WORKERS_LOCK:
        worker = MODEL_DOWNLOAD_WORKERS.get(model_id)
        if worker is None:
            return False
        if worker.is_alive():
            return True
        MODEL_DOWNLOAD_WORKERS.pop(model_id, None)
        return False


def infer_model_type_for_model(model_id: str, fallback: str = "kokoro") -> str:
    predefined = find_predefined_model(model_id)
    if predefined:
        return normalize_model_type(predefined.get("model_type"), default=fallback)

    state_entry = get_model_download_state(model_id)
    if state_entry:
        return normalize_model_type(state_entry.get("model_type"), default=fallback)

    return normalize_model_type(fallback)


def run_model_download(model_id: str, model_type: str) -> None:
    try:
        def report_progress(percent: int, message: str) -> None:
            set_model_download_state(
                model_id,
                progress=max(0, min(100, int(percent))),
                message=str(message),
                status="downloading",
                error=None,
            )

        cache_path, download_err = download_hf_model_snapshot(model_id, progress_callback=report_progress)
        if download_err:
            set_model_download_state(
                model_id,
                status="failed",
                progress=0,
                downloaded=False,
                message="Model download failed.",
                error=download_err,
                supports_generation=supports_generation_for_model_type(model_type),
                voices=model_voices_for_type(model_type),
            )
            return

        set_model_download_state(
            model_id,
            status="downloaded",
            progress=100,
            downloaded=True,
            message="Model download complete.",
            error=None,
            cache_path=str(cache_path) if cache_path else None,
            supports_generation=supports_generation_for_model_type(model_type),
            voices=model_voices_for_type(model_type),
        )
    except Exception as exc:
        set_model_download_state(
            model_id,
            status="failed",
            progress=0,
            downloaded=False,
            message="Model download failed.",
            error=str(exc),
            supports_generation=supports_generation_for_model_type(model_type),
            voices=model_voices_for_type(model_type),
        )
    finally:
        unregister_model_download_worker(model_id)


def start_model_download(model_id: str, model_type: str) -> Tuple[Dict, bool]:
    normalized_type = infer_model_type_for_model(model_id, fallback=model_type)
    if model_id == LOCAL_DEFAULT_MODEL_ID:
        return local_default_model_entry(), False

    predefined = find_predefined_model(model_id)
    if predefined:
        display_name = str(predefined.get("display_name") or model_id)
        description = str(predefined.get("description") or "")
        normalized_type = normalize_model_type(predefined.get("model_type"), default=normalized_type)
        predefined_flag = True
    else:
        display_name = f"Manual model ({model_id})"
        description = "User-specified Hugging Face model."
        predefined_flag = False

    if is_hf_model_cached(model_id):
        cached_entry = make_manual_model_entry(model_id, normalized_type)
        cached_entry.update(
            {
                "display_name": display_name,
                "description": description,
                "predefined": predefined_flag,
                "status": "downloaded",
                "progress": 100,
                "downloaded": True,
                "message": "Model is available locally.",
                "error": None,
                "supports_generation": supports_generation_for_model_type(normalized_type),
                "voices": model_voices_for_type(normalized_type),
            }
        )
        set_model_download_state(model_id, **cached_entry)
        return get_model_catalog_entry(model_id, normalized_type), False

    existing_state = get_model_download_state(model_id)
    if existing_state and bool(existing_state.get("downloaded")):
        return get_model_catalog_entry(model_id, normalized_type), False

    if existing_state and str(existing_state.get("status", "")) == "downloading" and is_model_download_active(model_id):
        return get_model_catalog_entry(model_id, normalized_type), False

    set_model_download_state(
        model_id,
        id=model_id,
        display_name=display_name,
        model_type=normalized_type,
        model_type_label=MODEL_TYPE_LABELS.get(normalized_type, MODEL_TYPE_LABELS["other"]),
        description=description,
        predefined=predefined_flag,
        download_required=True,
        downloaded=False,
        status="downloading",
        progress=0,
        message=f"Preparing download for model '{model_id}'...",
        error=None,
        supports_generation=supports_generation_for_model_type(normalized_type),
        voices=model_voices_for_type(normalized_type),
    )

    worker = threading.Thread(target=run_model_download, args=(model_id, normalized_type), daemon=True)
    register_model_download_worker(model_id, worker)
    worker.start()
    return get_model_catalog_entry(model_id, normalized_type), True


def model_download_status(model_id: str, model_type: Optional[str] = None) -> Dict:
    entry = get_model_catalog_entry(model_id, model_type)
    entry["active_download"] = bool(model_id != LOCAL_DEFAULT_MODEL_ID and is_model_download_active(model_id))
    return entry


def model_voice_status(model_id: Optional[str], model_type: Optional[str]) -> Dict:
    target_model_id = str(model_id or "").strip() or None
    requested_type = normalize_model_type(model_type, default="kokoro")

    if target_model_id:
        entry = get_model_catalog_entry(target_model_id, requested_type)
        if not model_type:
            requested_type = normalize_model_type(entry.get("model_type"), default=requested_type)

    supports_generation = supports_generation_for_model_type(requested_type)
    voices = model_voices_for_type(requested_type)
    return {
        "model_id": target_model_id,
        "model_type": requested_type,
        "model_type_label": MODEL_TYPE_LABELS.get(requested_type, MODEL_TYPE_LABELS["other"]),
        "voices": voices,
        "supports_generation": supports_generation,
    }


def resolve_local_kokoro_assets(model_cache_path: Path) -> Tuple[Optional[Path], Optional[Path], Optional[str]]:
    config_path = model_cache_path / "config.json"
    if not config_path.exists() or not config_path.is_file():
        return None, None, "missing config.json"

    try:
        config_payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, None, f"invalid config.json ({exc})"

    if not isinstance(config_payload, dict) or "vocab" not in config_payload:
        return None, None, "config.json is not Kokoro-compatible (missing 'vocab')"

    candidates: List[Path] = []
    for candidate in model_cache_path.rglob("*.pth"):
        if ".cache" in candidate.parts:
            continue
        if candidate.is_file():
            candidates.append(candidate)

    if not candidates:
        return None, None, "no .pth weight file found"

    candidates.sort(key=lambda item: (0 if "kokoro" in item.name.lower() else 1, len(item.name), item.name.lower()))
    return config_path, candidates[0], None


def get_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def is_same_origin_request() -> bool:
    request_host = request.host.lower()
    for header_name in ("Origin", "Referer"):
        header_value = request.headers.get(header_name)
        if not header_value:
            continue
        parsed = urlparse(header_value)
        if not parsed.netloc:
            continue
        if not hmac.compare_digest(parsed.netloc.lower(), request_host):
            return False
    return True


def validate_generated_file_request(job: Dict, filename: str) -> Tuple[Optional[str], Optional[str]]:
    run_folder = str(job.get("run_folder") or "").strip()
    if not run_folder:
        return None, "No generated files for this job yet."

    if not filename or "/" in filename or "\\" in filename:
        return None, "Invalid file name."

    safe_name = Path(filename).name
    if safe_name in {"", ".", ".."} or safe_name != filename:
        return None, "Invalid file name."

    allowed_files = set(job.get("generated_files") or [])
    if not allowed_files:
        allowed_files = {entry["name"] for entry in list_generated_files(job)}
    if safe_name not in allowed_files:
        return None, "File not found."

    run_path = Path(run_folder).resolve(strict=False)
    candidate = (run_path / safe_name).resolve(strict=False)
    try:
        candidate.relative_to(run_path)
    except ValueError:
        return None, "Invalid file path."

    if not candidate.exists() or not candidate.is_file():
        return None, "File not found."

    return safe_name, None


def estimate_generation_seconds(job: Dict, mode: str) -> int:
    chapters = job.get("chapters") or []
    chapter_count = max(1, len(chapters))
    total_chars = sum(len(str(chapter.get("text", ""))) for chapter in chapters)
    mode_factor = 1 if mode == "single" else 2
    estimated = 15 + (chapter_count * (6 * mode_factor)) + int(total_chars / (900 / mode_factor))
    return max(15, estimated)


def should_enable_cleanup() -> bool:
    return os.getenv("AUDIOBOOK_ENABLE_CLEANUP", "1").strip().lower() in {"1", "true", "yes", "on"}


def cleanup_max_age_seconds() -> int:
    raw = os.getenv("AUDIOBOOK_CLEANUP_AGE_HOURS", "168").strip()
    try:
        hours = int(raw)
    except ValueError:
        hours = 168
    hours = max(1, min(hours, 24 * 365))
    return hours * 3600


def cleanup_interval_seconds() -> int:
    raw = os.getenv("AUDIOBOOK_CLEANUP_INTERVAL_SECONDS", "600").strip()
    try:
        interval = int(raw)
    except ValueError:
        interval = 600
    return max(60, min(interval, 86400))


def maybe_cleanup_stale_data() -> None:
    global CLEANUP_LAST_RUN

    if not should_enable_cleanup():
        return

    now = time.time()
    interval = cleanup_interval_seconds()
    with CLEANUP_LOCK:
        if now - CLEANUP_LAST_RUN < interval:
            return
        CLEANUP_LAST_RUN = now

    cutoff = now - cleanup_max_age_seconds()

    for upload_dir in UPLOADS_DIR.iterdir():
        try:
            if upload_dir.stat().st_mtime < cutoff:
                shutil.rmtree(upload_dir, ignore_errors=True)
        except FileNotFoundError:
            continue

    for meta_file in JOB_META_DIR.glob("*.json"):
        try:
            if meta_file.stat().st_mtime < cutoff:
                meta_file.unlink(missing_ok=True)
        except FileNotFoundError:
            continue


def rate_limit_bucket_for_request() -> Tuple[Optional[str], int, int]:
    if request.path == "/api/upload":
        limit, window = RATE_LIMITS["upload"]
        return "upload", limit, window
    if request.path == "/api/generate":
        limit, window = RATE_LIMITS["generate"]
        return "generate", limit, window
    if request.path.startswith("/api/jobs/"):
        limit, window = RATE_LIMITS["jobs"]
        return "jobs", limit, window
    return None, 0, 0


def client_identifier() -> str:
    forwarded_for = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if forwarded_for:
        return forwarded_for
    return request.remote_addr or "unknown"


def is_lfs_pointer(path: Path) -> bool:
    if not path.exists():
        return False

    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:256]
    except Exception:
        return False

    return "git-lfs.github.com/spec/v1" in head


def detect_device(preferred: str = "cpu") -> str:
    pref = (preferred or "cpu").strip().lower()

    if pref == "auto":
        try:
            import torch as _torch  # type: ignore[reportMissingImports]

            has_cuda_build = bool(getattr(_torch.version, "cuda", None))
            if has_cuda_build and _torch.cuda.is_available():
                return "cuda"
            return "cpu"
        except Exception:
            return "cpu"

    if pref.startswith("cuda"):
        try:
            import torch as _torch  # type: ignore[reportMissingImports]

            has_cuda_build = bool(getattr(_torch.version, "cuda", None))
            if not has_cuda_build or not _torch.cuda.is_available():
                return "cpu"
        except Exception:
            return "cpu"

    if pref != "cpu" and not pref.startswith("cuda"):
        return "cpu"

    return pref


def nvidia_gpu_visible() -> bool:
    try:
        probe = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except Exception:
        return False

    return probe.returncode == 0 and "GPU " in (probe.stdout or "")


def device_resolution_note(requested_device: str, resolved_device: str) -> str:
    if str(resolved_device).lower().startswith("cuda"):
        return "CUDA acceleration is enabled."

    gpu_visible = nvidia_gpu_visible()
    try:
        import torch as _torch  # type: ignore[reportMissingImports]
    except Exception:
        if gpu_visible:
            return "NVIDIA GPU detected, but PyTorch is unavailable in this environment."
        return "Using CPU in the current environment."

    if not bool(getattr(_torch.version, "cuda", None)):
        if gpu_visible:
            return "NVIDIA GPU detected, but PyTorch in this environment is CPU-only."
        return "Using CPU because PyTorch was installed without CUDA support."

    if not _torch.cuda.is_available():
        if gpu_visible:
            return "NVIDIA GPU detected, but CUDA is not available to PyTorch in this environment."
        return "CUDA is not available to PyTorch in this environment."

    requested = (requested_device or "auto").strip().lower()
    if requested in {"auto", "cuda"} or requested.startswith("cuda"):
        return "Using CPU because CUDA selection could not be activated."

    return "Using CPU in the current environment."


def default_requested_device() -> str:
    configured = os.getenv("AUDIOBOOK_DEVICE", "").strip().lower()
    return configured or "auto"


def is_test_mode() -> bool:
    val = os.getenv("AUDIOBOOK_TEST_MODE", "")
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def choose_voice_reference(voice: str) -> str:
    local_voice = BASE_DIR / "Kokoro-82M" / "voices" / f"{voice}.pt"
    if local_voice.exists() and not is_lfs_pointer(local_voice):
        return str(local_voice)
    return voice


def get_pipeline_bundle(
    voice: str,
    device: str,
    hf_model_id: Optional[str] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Dict:
    if not HAS_KOKORO:
        raise RuntimeError(f"kokoro import failed: {KOKORO_IMPORT_ERROR}")

    lang_code = voice_to_lang_code(voice)
    requested_model_id = normalize_hf_model_id(hf_model_id)
    model_cache_key = requested_model_id or "local_default"
    cache_key = f"{lang_code}:{device}:{model_cache_key}"

    with PIPELINE_LOCK:
        cached = PIPELINE_CACHE.get(cache_key)
        if cached:
            return cached

    model_instance = None
    checkpoint = BASE_DIR / "Kokoro-82M" / "kokoro-v1_0.pth"
    config_file = BASE_DIR / "Kokoro-82M" / "config.json"
    fallback_reason: Optional[str] = None

    if KModel:
        if requested_model_id:
            model_cache_path, download_err = download_hf_model_snapshot(
                requested_model_id,
                progress_callback=progress_callback,
            )
            if download_err:
                fallback_reason = download_err
            elif model_cache_path is not None:
                custom_config, custom_weights, asset_err = resolve_local_kokoro_assets(model_cache_path)
                if asset_err:
                    fallback_reason = asset_err
                else:
                    try:
                        model_instance = KModel(
                            repo_id=requested_model_id,
                            config=str(custom_config),
                            model=str(custom_weights),
                        ).to(device).eval()
                    except Exception as exc:
                        fallback_reason = f"failed to initialize downloaded model ({exc})"
        elif checkpoint.exists() and not is_lfs_pointer(checkpoint):
            config_arg = None
            if config_file.exists() and not is_lfs_pointer(config_file):
                config_arg = str(config_file)
            model_instance = KModel(config=config_arg, model=str(checkpoint)).to(device).eval()

    if requested_model_id and model_instance is None:
        reason = fallback_reason or "model could not be initialized"
        logging.warning(
            "Falling back to default Kokoro model for '%s' (%s).",
            requested_model_id,
            reason,
        )
        default_bundle = get_pipeline_bundle(voice, device, hf_model_id=None)
        with PIPELINE_LOCK:
            PIPELINE_CACHE[cache_key] = default_bundle
        return default_bundle

    if model_instance is not None:
        pipeline_kwargs: Dict[str, object] = {
            "lang_code": lang_code,
            "model": model_instance,
        }
        if requested_model_id:
            pipeline_kwargs["repo_id"] = requested_model_id
        try:
            pipeline = KPipeline(**pipeline_kwargs)
        except Exception as exc:
            if requested_model_id:
                logging.warning(
                    "Custom HF model pipeline failed for '%s' (%s). Falling back to default Kokoro model.",
                    requested_model_id,
                    exc,
                )
                default_bundle = get_pipeline_bundle(voice, device, hf_model_id=None)
                with PIPELINE_LOCK:
                    PIPELINE_CACHE[cache_key] = default_bundle
                return default_bundle
            raise
    else:
        pipeline_kwargs: Dict[str, object] = {
            "lang_code": lang_code,
            "device": device,
        }
        try:
            pipeline = KPipeline(**pipeline_kwargs)
        except TypeError:
            pipeline_kwargs.pop("device", None)
            pipeline = KPipeline(**pipeline_kwargs)

    bundle = {
        "pipeline": pipeline,
        "model": model_instance,
    }

    with PIPELINE_LOCK:
        PIPELINE_CACHE[cache_key] = bundle

    return bundle


def synthesize_text_to_wav(
    text: str,
    voice: str,
    output_path: Path,
    device: str = "cpu",
    hf_model_id: Optional[str] = None,
) -> None:
    chunks = split_text_into_chunks(text)
    if not chunks:
        raise ValueError("No text chunks available for synthesis.")

    bundle = get_pipeline_bundle(voice, device, hf_model_id=hf_model_id)
    pipeline = bundle["pipeline"]
    model = bundle["model"]
    voice_reference = choose_voice_reference(voice)

    generated_audio: List[np.ndarray] = []
    for chunk in chunks:
        if model is not None:
            generator = pipeline(chunk, voice=voice_reference, model=model)
        else:
            generator = pipeline(chunk, voice=voice_reference)

        for result in generator:
            if hasattr(result, "audio"):
                audio = result.audio
            else:
                audio = result[2]

            if audio is None:
                continue

            audio_np = np.asarray(audio, dtype=np.float32).flatten()
            if audio_np.size:
                generated_audio.append(audio_np)

    if not generated_audio:
        raise RuntimeError("Kokoro did not return any audio frames.")

    combined = np.concatenate(generated_audio)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import soundfile as sf  # type: ignore[reportMissingImports]
    except Exception:
        raise RuntimeError("soundfile (pysoundfile) is required to write WAV output")

    sf.write(str(output_path), combined, 24000)


def _job_feature_deps() -> SimpleNamespace:
    return SimpleNamespace(
        ACTIVE_JOB_STATUSES=ACTIVE_JOB_STATUSES,
        BASE_DIR=BASE_DIR,
        JOBS=JOBS,
        JOBS_LOCK=JOBS_LOCK,
        JOB_META_DIR=JOB_META_DIR,
        LOCAL_DEFAULT_MODEL_ID=LOCAL_DEFAULT_MODEL_ID,
        WORKERS=WORKERS,
        WORKERS_LOCK=WORKERS_LOCK,
        calculate_elapsed_seconds=calculate_elapsed_seconds,
        can_clear_job_files=can_clear_job_files,
        create_run_folder=create_run_folder,
        detect_device=detect_device,
        extract_chapters_from_epub=extract_chapters_from_epub,
        finalize_stopped_job=finalize_stopped_job,
        get_job=get_job,
        get_pipeline_bundle=get_pipeline_bundle,
        is_job_active=is_job_active,
        is_test_mode=is_test_mode,
        list_generated_files=list_generated_files,
        normalize_hf_model_id=normalize_hf_model_id,
        now_iso=now_iso,
        raise_if_stop_requested=raise_if_stop_requested,
        save_job_snapshot=save_job_snapshot,
        shutil=shutil,
        slugify=slugify,
        synthesize_text_to_wav=synthesize_text_to_wav,
        unregister_worker=unregister_worker,
        update_job=update_job,
    )


def save_job_snapshot(job_id: str) -> None:
    job_feature.save_job_snapshot(job_id, _job_feature_deps())


def get_job(job_id: str) -> Optional[Dict]:
    return job_feature.get_job(job_id, _job_feature_deps())


def update_job(job_id: str, **fields: object) -> None:
    job_feature.update_job(job_id, _job_feature_deps(), **fields)


def register_worker(job_id: str, worker: threading.Thread) -> None:
    job_feature.register_worker(job_id, worker, _job_feature_deps())


def unregister_worker(job_id: str, worker: Optional[threading.Thread] = None) -> None:
    job_feature.unregister_worker(job_id, _job_feature_deps(), worker)


def is_job_active(job_id: str, job: Optional[Dict] = None) -> bool:
    return job_feature.is_job_active(job_id, _job_feature_deps(), job=job)


def job_has_generated_output(job: Dict) -> bool:
    return job_feature.job_has_generated_output(job)


def job_files_cleared(job: Dict) -> bool:
    return job_feature.job_files_cleared(job)


def can_clear_job_files(job: Dict) -> bool:
    return job_feature.can_clear_job_files(job, _job_feature_deps())


def serialize_job_status(job: Dict) -> Dict:
    return job_feature.serialize_job_status(job, _job_feature_deps())


def finalize_stopped_job(job_id: str, started_at: Optional[str], generated_files: List[str]) -> None:
    job_feature.finalize_stopped_job(job_id, started_at, generated_files, _job_feature_deps())


def raise_if_stop_requested(job_id: str, started_at: Optional[str], generated_files: List[str]) -> None:
    job_feature.raise_if_stop_requested(job_id, started_at, generated_files, _job_feature_deps())


def list_generated_files(job: Dict) -> List[Dict]:
    return job_feature.list_generated_files(job)


def run_generation_job(job_id: str) -> None:
    job_feature.run_generation_job(job_id, _job_feature_deps())


ROUTE_DEPS = SimpleNamespace(
    ALLOWED_UPLOAD_EXTENSIONS=ALLOWED_UPLOAD_EXTENSIONS,
    ACTIVE_JOB_STATUSES=ACTIVE_JOB_STATUSES,
    DEFAULT_OUTPUT_DIR=DEFAULT_OUTPUT_DIR,
    HAS_KOKORO=HAS_KOKORO,
    JOBS=JOBS,
    JOBS_LOCK=JOBS_LOCK,
    KOKORO_IMPORT_ERROR=KOKORO_IMPORT_ERROR,
    LOCAL_DEFAULT_MODEL_ID=LOCAL_DEFAULT_MODEL_ID,
    RATE_LIMIT_LOCK=RATE_LIMIT_LOCK,
    RATE_LIMIT_STATE=RATE_LIMIT_STATE,
    UPLOADS_DIR=UPLOADS_DIR,
    VOICE_OPTIONS=VOICE_OPTIONS,
    can_clear_job_files=can_clear_job_files,
    client_identifier=client_identifier,
    default_requested_device=default_requested_device,
    detect_device=detect_device,
    device_resolution_note=device_resolution_note,
    ensure_app_dirs=ensure_app_dirs,
    estimate_generation_seconds=estimate_generation_seconds,
    extract_book_title=extract_book_title,
    extract_chapters_from_epub=extract_chapters_from_epub,
    finalize_stopped_job=finalize_stopped_job,
    get_allowed_output_root=get_allowed_output_root,
    get_csrf_token=get_csrf_token,
    get_job=get_job,
    infer_model_type_for_model=infer_model_type_for_model,
    is_job_active=is_job_active,
    is_same_origin_request=is_same_origin_request,
    is_test_mode=is_test_mode,
    is_valid_job_id=is_valid_job_id,
    job_files_cleared=job_files_cleared,
    jsonify=jsonify,
    list_available_models=list_available_models,
    list_generated_files=list_generated_files,
    looks_like_valid_epub=looks_like_valid_epub,
    model_download_status=model_download_status,
    model_voice_status=model_voice_status,
    model_voices_for_type=model_voices_for_type,
    normalize_hf_model_id=normalize_hf_model_id,
    normalize_model_type=normalize_model_type,
    now_iso=now_iso,
    rate_limit_bucket_for_request=rate_limit_bucket_for_request,
    register_worker=register_worker,
    render_template=render_template,
    request=request,
    run_generation_job=run_generation_job,
    save_job_snapshot=save_job_snapshot,
    secure_filename=secure_filename,
    send_from_directory=send_from_directory,
    serialize_job_status=serialize_job_status,
    session=session,
    slugify=slugify,
    start_model_download=start_model_download,
    supports_generation_for_model_type=supports_generation_for_model_type,
    update_job=update_job,
    validate_generated_file_request=validate_generated_file_request,
    validate_hf_model_id=validate_hf_model_id,
    validate_output_directory=validate_output_directory,
)

register_middleware(app, ROUTE_DEPS)
register_ui_routes(app, ROUTE_DEPS)
register_upload_routes(app, ROUTE_DEPS)
register_model_routes(app, ROUTE_DEPS)
register_job_routes(app, ROUTE_DEPS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local EPUB-to-audiobook web app")
    parser.add_argument("--host", default="127.0.0.1", help="Host bind address")
    parser.add_argument("--port", type=int, default=5000, help="Port")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    return parser.parse_args()


def main() -> None:
    ensure_app_dirs()
    args = parse_args()
    if args.debug and os.getenv("AUDIOBOOK_ALLOW_DEBUG", "").strip().lower() not in {"1", "true", "yes", "on"}:
        raise SystemExit("Refusing --debug startup. Set AUDIOBOOK_ALLOW_DEBUG=1 to override.")
    print(f"Open the app at http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
