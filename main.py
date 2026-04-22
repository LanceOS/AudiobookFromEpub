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
import logging
import os
import secrets
import shutil
import subprocess
import threading
import time
import sys 
import signal
from datetime import datetime, timezone
from pathlib import Path
from pathlib import Path as _Path
import sys as _sys
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Ensure `src/` is on sys.path so moved packages remain importable by their
# original top-level names (e.g. `core`, `services`, `routes`, `utils`).
_ROOT = _Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if _SRC.exists():
    _sys.path.insert(0, str(_SRC))

_APP_DATA = _ROOT / ".app_data"
_CACHE_ROOT = _APP_DATA / ".cache"
os.environ.setdefault("HOME", str(_APP_DATA))
os.environ.setdefault("XDG_CACHE_HOME", str(_CACHE_ROOT))
os.environ.setdefault("HF_HOME", str(_CACHE_ROOT / "huggingface"))
os.environ.setdefault("HF_HUB_CACHE", str(_CACHE_ROOT / "huggingface" / "hub"))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(_CACHE_ROOT / "huggingface" / "hub"))
os.environ.setdefault("HF_TOKEN_PATH", str(_CACHE_ROOT / "huggingface" / "token"))
os.environ.setdefault("TRANSFORMERS_CACHE", str(_CACHE_ROOT / "transformers"))
os.environ.setdefault("TORCH_HOME", str(_CACHE_ROOT / "torch"))

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
from services import synthesis_service
from services import model_service as model_feature
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

# Heavy ML imports (kokoro / qwen_tts) are lazily loaded to avoid
# initializing CUDA/drivers at module import time. Call `lazy_load_heavy_deps()`
# from the functions that actually need these libraries.
KPipeline = None
KModel = None
HAS_KOKORO = False
KOKORO_IMPORT_ERROR = ""

Qwen3TTSModel = None
HAS_QWEN_TTS = False
QWEN_TTS_IMPORT_ERROR = ""

_HEAVY_DEPS_LOADED = False

def lazy_load_heavy_deps() -> None:
    """Attempt to import heavy ML libraries (kokoro, qwen_tts) once.

    This prevents GPU/driver initialization at process start and confines
    potentially unsafe native initialization to on-demand code paths.
    """
    global _HEAVY_DEPS_LOADED, KPipeline, KModel, HAS_KOKORO, KOKORO_IMPORT_ERROR
    global Qwen3TTSModel, HAS_QWEN_TTS, QWEN_TTS_IMPORT_ERROR

    if _HEAVY_DEPS_LOADED:
        return
    _HEAVY_DEPS_LOADED = True

    try:
        from kokoro import KPipeline as _KPipeline  # type: ignore[reportMissingImports]
        from kokoro.model import KModel as _KModel  # type: ignore[reportMissingImports]
        KPipeline = _KPipeline
        KModel = _KModel
        HAS_KOKORO = True
        KOKORO_IMPORT_ERROR = ""
    except Exception as exc:
        KPipeline = None
        KModel = None
        HAS_KOKORO = False
        KOKORO_IMPORT_ERROR = str(exc)

    try:
        from qwen_tts import Qwen3TTSModel as _Qwen3TTSModel  # type: ignore[reportMissingImports]
        Qwen3TTSModel = _Qwen3TTSModel
        HAS_QWEN_TTS = True
        QWEN_TTS_IMPORT_ERROR = ""
    except Exception as exc:
        Qwen3TTSModel = None
        HAS_QWEN_TTS = False
        QWEN_TTS_IMPORT_ERROR = str(exc)

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
STOP_EVENT = threading.Event()


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


def _model_feature_deps() -> SimpleNamespace:
    return SimpleNamespace(
        DEFAULT_HF_MODEL_CACHE_DIR=DEFAULT_HF_MODEL_CACHE_DIR,
        HF_MODEL_CACHE_DIR_SEGMENT_RE=HF_MODEL_CACHE_DIR_SEGMENT_RE,
        LOCAL_DEFAULT_MODEL_ID=LOCAL_DEFAULT_MODEL_ID,
        MODEL_DOWNLOADS=MODEL_DOWNLOADS,
        MODEL_DOWNLOADS_LOCK=MODEL_DOWNLOADS_LOCK,
        MODEL_DOWNLOAD_WORKERS=MODEL_DOWNLOAD_WORKERS,
        MODEL_DOWNLOAD_WORKERS_LOCK=MODEL_DOWNLOAD_WORKERS_LOCK,
        MODEL_TYPE_LABELS=MODEL_TYPE_LABELS,
        PREDEFINED_MODEL_CATALOG=PREDEFINED_MODEL_CATALOG,
        build_model_catalog_entry=build_model_catalog_entry,
        discover_qwen_supported_speakers=discover_qwen_supported_speakers,
        download_hf_model_snapshot=download_hf_model_snapshot,
        find_predefined_model=find_predefined_model,
        get_hf_model_cache_path=get_hf_model_cache_path,
        get_hf_model_cache_root=get_hf_model_cache_root,
        get_model_catalog_entry=get_model_catalog_entry,
        get_model_download_state=get_model_download_state,
        infer_model_type_for_model=infer_model_type_for_model,
        is_hf_model_cached=is_hf_model_cached,
        is_model_download_active=is_model_download_active,
        list_available_models=list_available_models,
        local_default_model_entry=local_default_model_entry,
        make_manual_model_entry=make_manual_model_entry,
        merge_model_download_state=merge_model_download_state,
        model_voices_for_type=model_voices_for_type,
        model_voice_status=model_voice_status,
        normalize_model_type=normalize_model_type,
        now_iso=now_iso,
        register_model_download_worker=register_model_download_worker,
        run_model_download=run_model_download,
        set_model_download_state=set_model_download_state,
        supports_generation_for_model_type=supports_generation_for_model_type,
        unregister_model_download_worker=unregister_model_download_worker,
    )


def is_hf_model_cached(model_id: str) -> bool:
    return model_feature.is_hf_model_cached(model_id, _model_feature_deps())


def build_model_catalog_entry(
    model_id: str,
    display_name: str,
    model_type: str,
    *,
    description: str,
    predefined: bool,
    voices: Optional[object] = None,
    default_voice: Optional[object] = None,
    supports_generation: Optional[bool] = None,
) -> Dict:
    return model_feature.build_model_catalog_entry(
        model_id,
        display_name,
        model_type,
        _model_feature_deps(),
        description=description,
        predefined=predefined,
        voices=voices,
        default_voice=default_voice,
        supports_generation=supports_generation,
    )


def local_default_model_entry() -> Dict:
    return model_feature.local_default_model_entry(_model_feature_deps())


def find_predefined_model(model_id: str) -> Optional[Dict]:
    return model_feature.find_predefined_model(model_id, _model_feature_deps())


def make_manual_model_entry(
    model_id: str,
    model_type: str,
    *,
    voices: Optional[object] = None,
    default_voice: Optional[object] = None,
    supports_generation: Optional[bool] = None,
) -> Dict:
    return model_feature.make_manual_model_entry(
        model_id,
        model_type,
        _model_feature_deps(),
        voices=voices,
        default_voice=default_voice,
        supports_generation=supports_generation,
    )


def get_model_download_state(model_id: str) -> Optional[Dict]:
    return model_feature.get_model_download_state(model_id, _model_feature_deps())


def set_model_download_state(model_id: str, **fields: object) -> Dict:
    return model_feature.set_model_download_state(model_id, _model_feature_deps(), **fields)


def merge_model_download_state(entry: Dict) -> Dict:
    return model_feature.merge_model_download_state(entry, _model_feature_deps())


def list_available_models() -> List[Dict]:
    return model_feature.list_available_models(_model_feature_deps())


def get_model_catalog_entry(model_id: str, model_type: Optional[str] = None) -> Dict:
    return model_feature.get_model_catalog_entry(model_id, _model_feature_deps(), model_type)


def get_hf_model_cache_root() -> Path:
    return model_feature.get_hf_model_cache_root(_model_feature_deps())


def get_hf_model_cache_path(model_id: str) -> Path:
    return model_feature.get_hf_model_cache_path(model_id, _model_feature_deps())


def download_hf_model_snapshot(
    model_id: str,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Tuple[Optional[Path], Optional[str]]:
    return model_feature.download_hf_model_snapshot(model_id, _model_feature_deps(), progress_callback=progress_callback)


def register_model_download_worker(model_id: str, worker: threading.Thread) -> None:
    model_feature.register_model_download_worker(model_id, worker, _model_feature_deps())


def unregister_model_download_worker(model_id: str, worker: Optional[threading.Thread] = None) -> None:
    model_feature.unregister_model_download_worker(model_id, _model_feature_deps(), worker)


def is_model_download_active(model_id: str) -> bool:
    return model_feature.is_model_download_active(model_id, _model_feature_deps())


def infer_model_type_for_model(model_id: str, fallback: str = "kokoro") -> str:
    return model_feature.infer_model_type_for_model(model_id, _model_feature_deps(), fallback=fallback)


def run_model_download(model_id: str, model_type: str) -> None:
    model_feature.run_model_download(model_id, model_type, _model_feature_deps())


def start_model_download(model_id: str, model_type: str) -> Tuple[Dict, bool]:
    return model_feature.start_model_download(model_id, model_type, _model_feature_deps())


def model_download_status(model_id: str, model_type: Optional[str] = None) -> Dict:
    return model_feature.model_download_status(model_id, _model_feature_deps(), model_type)


def model_voice_status(model_id: Optional[str], model_type: Optional[str]) -> Dict:
    return model_feature.model_voice_status(model_id, model_type, _model_feature_deps())


def resolve_local_kokoro_assets(model_cache_path: Path) -> Tuple[Optional[Path], Optional[Path], Optional[str]]:
    return model_feature.resolve_local_kokoro_assets(model_cache_path)


def get_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def is_same_origin_request() -> bool:
    request_host = (request.host or "").lower()
    # Compare both host:port and hostnames. Allow common loopback aliases
    # (e.g. localhost vs 127.0.0.1) to be considered equivalent for local dev.
    request_hostname = request_host.split(":")[0]
    for header_name in ("Origin", "Referer"):
        header_value = request.headers.get(header_name)
        if not header_value:
            continue
        parsed = urlparse(header_value)
        if not parsed.netloc:
            continue
        header_netloc = parsed.netloc.lower()
        header_hostname = header_netloc.split(":")[0]

        # Exact host:port match is allowed
        if hmac.compare_digest(header_netloc, request_host):
            continue

        # Hostname match (ignoring port) is allowed
        if hmac.compare_digest(header_hostname, request_hostname):
            continue

        # Treat common loopback names as equivalent
        loopback_aliases = {"127.0.0.1", "localhost", "::1"}
        if header_hostname in loopback_aliases and request_hostname in loopback_aliases:
            continue

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
    chapter_count = len(chapters)
    if chapter_count <= 0:
        try:
            chapter_count = int(job.get("chapters_count") or 0)
        except (TypeError, ValueError):
            chapter_count = 0

    chapter_count = max(1, chapter_count)
    total_chars = sum(len(str(chapter.get("text", ""))) for chapter in chapters)
    if total_chars <= 0:
        total_chars = chapter_count * 1800
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


def ensure_generation_backend_ready(model_type: str) -> Optional[str]:
    # Ensure heavy ML libraries are available lazily before checking availability
    lazy_load_heavy_deps()
    resolved_type = normalize_model_type(model_type, default="other")

    if resolved_type == "kokoro" and not HAS_KOKORO:
        return (
            "Kokoro is unavailable in this Python environment "
            f"({KOKORO_IMPORT_ERROR}). Activate kokoro_venv (Python 3.12) "
            "and install requirements, or run in test mode with AUDIOBOOK_TEST_MODE=1."
        )

    if resolved_type == "qwen3_customvoice" and not HAS_QWEN_TTS:
        return (
            "Qwen3 CustomVoice backend is unavailable in this Python environment "
            f"({QWEN_TTS_IMPORT_ERROR}). Install qwen-tts and its runtime dependencies, "
            "or run in test mode with AUDIOBOOK_TEST_MODE=1."
        )

    return None


def _synthesis_feature_deps() -> SimpleNamespace:
    # Ensure heavy ML libraries are loaded lazily when synthesis deps are requested
    lazy_load_heavy_deps()

    return SimpleNamespace(
        BASE_DIR=BASE_DIR,
        HAS_KOKORO=HAS_KOKORO,
        HAS_QWEN_TTS=HAS_QWEN_TTS,
        KModel=KModel,
        KPipeline=KPipeline,
        Qwen3TTSModel=Qwen3TTSModel,
        KOKORO_IMPORT_ERROR=KOKORO_IMPORT_ERROR,
        QWEN_TTS_IMPORT_ERROR=QWEN_TTS_IMPORT_ERROR,
        PIPELINE_CACHE=PIPELINE_CACHE,
        PIPELINE_LOCK=PIPELINE_LOCK,
        choose_voice_reference=choose_voice_reference,
        download_hf_model_snapshot=download_hf_model_snapshot,
        get_hf_model_cache_path=get_hf_model_cache_path,
        get_pipeline_bundle=get_pipeline_bundle,
        infer_model_type_for_model=infer_model_type_for_model,
        is_lfs_pointer=is_lfs_pointer,
        normalize_hf_model_id=normalize_hf_model_id,
        normalize_model_type=normalize_model_type,
        resolve_local_kokoro_assets=resolve_local_kokoro_assets,
        split_text_into_chunks=split_text_into_chunks,
        voice_to_lang_code=voice_to_lang_code,
    )


def get_pipeline_bundle(
    voice: str,
    device: str,
    hf_model_id: Optional[str] = None,
    model_type: Optional[str] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Dict:
    return synthesis_service.get_pipeline_bundle(
        voice,
        device,
        _synthesis_feature_deps(),
        hf_model_id=hf_model_id,
        model_type=model_type,
        progress_callback=progress_callback,
    )


def discover_qwen_supported_speakers(
    model_id: Optional[str],
    *,
    allow_download: bool = False,
    device: str = "cpu",
) -> Tuple[List[str], Optional[str]]:
    return synthesis_service.discover_qwen_supported_speakers(
        model_id,
        _synthesis_feature_deps(),
        allow_download=allow_download,
        device=device,
    )


def synthesize_text_to_wav(
    text: str,
    voice: str,
    output_path: Path,
    device: str = "cpu",
    hf_model_id: Optional[str] = None,
    model_type: Optional[str] = None,
) -> None:
    synthesis_service.synthesize_text_to_wav(
        text,
        voice,
        output_path,
        _synthesis_feature_deps(),
        device=device,
        hf_model_id=hf_model_id,
        model_type=model_type,
    )


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
    can_clear_job_files=can_clear_job_files,
    client_identifier=client_identifier,
    default_requested_device=default_requested_device,
    detect_device=detect_device,
    device_resolution_note=device_resolution_note,
    ensure_generation_backend_ready=ensure_generation_backend_ready,
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


def graceful_shutdown(signum, frame) -> None:
    """Signal handler to attempt a graceful shutdown of background workers.

    This does best-effort joining of worker threads and attempts to free CUDA
    memory if PyTorch is available. It then exits the process.
    """
    try:
        print("Signal received: shutting down gracefully...")
    except Exception:
        pass

    STOP_EVENT.set()

    # Join any registered workers
    try:
        with WORKERS_LOCK:
            workers = list(WORKERS.values())
    except Exception:
        workers = []

    for w in workers:
        try:
            if w is not None and getattr(w, "is_alive", lambda: False)():
                w.join(timeout=5)
        except Exception:
            pass

    # Join model download workers
    try:
        with MODEL_DOWNLOAD_WORKERS_LOCK:
            mdws = list(MODEL_DOWNLOAD_WORKERS.values())
    except Exception:
        mdws = []

    for w in mdws:
        try:
            if w is not None and getattr(w, "is_alive", lambda: False)():
                w.join(timeout=5)
        except Exception:
            pass

    # Best-effort: free CUDA resources if available
    try:
        import torch as _torch  # type: ignore[reportMissingImports]

        if getattr(_torch, "cuda", None) and _torch.cuda.is_available():
            try:
                _torch.cuda.synchronize()
            except Exception:
                pass
            try:
                _torch.cuda.empty_cache()
            except Exception:
                pass
    except Exception:
        pass

    # Short pause for cleanup then exit
    try:
        time.sleep(0.1)
    except Exception:
        pass

    try:
        sys.exit(0)
    except SystemExit:
        os._exit(0)


signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)


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
