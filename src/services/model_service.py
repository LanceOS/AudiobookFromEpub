from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def _normalize_voice_options(raw_voices: object) -> List[str]:
    if raw_voices is None:
        return []

    if isinstance(raw_voices, str):
        values = [raw_voices]
    elif isinstance(raw_voices, (list, tuple, set)):
        values = list(raw_voices)
    else:
        return []

    normalized: List[str] = []
    seen = set()
    for value in values:
        voice = str(value or "").strip()
        if voice and voice not in seen:
            normalized.append(voice)
            seen.add(voice)
    return normalized


def _resolve_voice_profile(
    voices: Optional[object],
    default_voice: Optional[object],
    fallback_voices: Optional[List[str]] = None,
) -> Tuple[List[str], Optional[str]]:
    resolved_voices = _normalize_voice_options(voices) if voices is not None else list(fallback_voices or [])
    resolved_default = str(default_voice or "").strip() or None

    if not resolved_voices and resolved_default:
        resolved_voices = [resolved_default]

    if resolved_voices:
        if resolved_default not in resolved_voices:
            resolved_default = resolved_voices[0]
    else:
        resolved_default = None

    return resolved_voices, resolved_default


def is_hf_model_cached(model_id: str, deps: Any) -> bool:
    if not model_id:
        return False

    cache_path = deps.get_hf_model_cache_path(model_id)
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
    deps: Any,
    *,
    description: str,
    predefined: bool,
    voices: Optional[object] = None,
    default_voice: Optional[object] = None,
    supports_generation: Optional[bool] = None,
) -> Dict:
    normalized_type = deps.normalize_model_type(model_type)
    downloaded = deps.is_hf_model_cached(model_id)
    status = "downloaded" if downloaded else "not_downloaded"
    resolved_voices, resolved_default_voice = _resolve_voice_profile(
        voices,
        default_voice,
        fallback_voices=deps.model_voices_for_type(normalized_type),
    )
    resolved_supports_generation = (
        bool(supports_generation)
        if supports_generation is not None
        else deps.supports_generation_for_model_type(normalized_type)
    )
    return {
        "id": model_id,
        "display_name": display_name,
        "model_type": normalized_type,
        "model_type_label": deps.MODEL_TYPE_LABELS.get(normalized_type, deps.MODEL_TYPE_LABELS["other"]),
        "description": description,
        "predefined": bool(predefined),
        "download_required": True,
        "downloaded": downloaded,
        "status": status,
        "progress": 100 if downloaded else 0,
        "message": "Model is available locally." if downloaded else "Model is not downloaded yet.",
        "error": None,
        "supports_generation": resolved_supports_generation,
        "voices": resolved_voices,
        "default_voice": resolved_default_voice,
    }


def local_default_model_entry(deps: Any) -> Dict:
    voices = deps.model_voices_for_type("kokoro")
    return {
        "id": deps.LOCAL_DEFAULT_MODEL_ID,
        "display_name": "Built-in Kokoro (default)",
        "model_type": "kokoro",
        "model_type_label": deps.MODEL_TYPE_LABELS["kokoro"],
        "description": "Bundled Kokoro model shipped with this app.",
        "predefined": True,
        "download_required": False,
        "downloaded": True,
        "status": "ready",
        "progress": 100,
        "message": "Built-in model is ready.",
        "error": None,
        "supports_generation": True,
        "voices": voices,
        "default_voice": voices[0] if voices else None,
    }


def find_predefined_model(model_id: str, deps: Any) -> Optional[Dict]:
    target = str(model_id or "").strip()
    if not target:
        return None

    for item in deps.PREDEFINED_MODEL_CATALOG:
        if str(item.get("id", "")).strip() == target:
            return dict(item)
    return None


def make_manual_model_entry(
    model_id: str,
    model_type: str,
    deps: Any,
    *,
    voices: Optional[object] = None,
    default_voice: Optional[object] = None,
    supports_generation: Optional[bool] = None,
) -> Dict:
    normalized_type = deps.normalize_model_type(model_type)
    downloaded = deps.is_hf_model_cached(model_id)
    status = "downloaded" if downloaded else "not_downloaded"
    resolved_voices, resolved_default_voice = _resolve_voice_profile(
        voices,
        default_voice,
        fallback_voices=deps.model_voices_for_type(normalized_type),
    )
    resolved_supports_generation = (
        bool(supports_generation)
        if supports_generation is not None
        else deps.supports_generation_for_model_type(normalized_type)
    )
    return {
        "id": model_id,
        "display_name": f"Manual model ({model_id})",
        "model_type": normalized_type,
        "model_type_label": deps.MODEL_TYPE_LABELS.get(normalized_type, deps.MODEL_TYPE_LABELS["other"]),
        "description": "User-specified Hugging Face model.",
        "predefined": False,
        "download_required": True,
        "downloaded": downloaded,
        "status": status,
        "progress": 100 if downloaded else 0,
        "message": "Model is available locally." if downloaded else "Model is not downloaded yet.",
        "error": None,
        "supports_generation": resolved_supports_generation,
        "voices": resolved_voices,
        "default_voice": resolved_default_voice,
    }


def get_model_download_state(model_id: str, deps: Any) -> Optional[Dict]:
    with deps.MODEL_DOWNLOADS_LOCK:
        entry = deps.MODEL_DOWNLOADS.get(model_id)
        return dict(entry) if entry else None


def set_model_download_state(model_id: str, deps: Any, **fields: object) -> Dict:
    with deps.MODEL_DOWNLOADS_LOCK:
        current = dict(deps.MODEL_DOWNLOADS.get(model_id) or {})
        current.update(fields)
        current["id"] = model_id
        current["updated_at"] = deps.now_iso()
        deps.MODEL_DOWNLOADS[model_id] = current
        return dict(current)


def merge_model_download_state(entry: Dict, deps: Any) -> Dict:
    merged = dict(entry)
    runtime = deps.get_model_download_state(str(entry.get("id", "")))
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

    normalized_type = deps.normalize_model_type(merged.get("model_type", "other"))
    merged["model_type"] = normalized_type
    merged["model_type_label"] = deps.MODEL_TYPE_LABELS.get(normalized_type, deps.MODEL_TYPE_LABELS["other"])
    if "supports_generation" in merged:
        merged["supports_generation"] = bool(merged.get("supports_generation"))
    else:
        merged["supports_generation"] = deps.supports_generation_for_model_type(normalized_type)

    voices_source = merged.get("voices") if "voices" in merged else None
    default_voice_source = merged.get("default_voice") if "default_voice" in merged else None
    merged["voices"], merged["default_voice"] = _resolve_voice_profile(
        voices_source,
        default_voice_source,
        fallback_voices=None if voices_source is not None else deps.model_voices_for_type(normalized_type),
    )

    if bool(merged.get("downloaded")):
        merged["progress"] = 100
        if str(merged.get("status", "")) in {"not_downloaded", "downloading", "failed", ""}:
            merged["status"] = "downloaded"
        if not str(merged.get("message", "")).strip():
            merged["message"] = "Model is available locally."

    return merged


def list_available_models(deps: Any) -> List[Dict]:
    models = [deps.local_default_model_entry()]
    known_ids = {deps.LOCAL_DEFAULT_MODEL_ID}

    for item in deps.PREDEFINED_MODEL_CATALOG:
        model_id = str(item["id"])
        known_ids.add(model_id)
        models.append(
            deps.build_model_catalog_entry(
                model_id,
                str(item["display_name"]),
                str(item["model_type"]),
                description=str(item.get("description", "")),
                predefined=True,
                voices=item["voices"] if "voices" in item else None,
                default_voice=item["default_voice"] if "default_voice" in item else None,
                supports_generation=item["supports_generation"] if "supports_generation" in item else None,
            )
        )

    with deps.MODEL_DOWNLOADS_LOCK:
        extra_entries = [
            dict(entry)
            for model_id, entry in deps.MODEL_DOWNLOADS.items()
            if model_id not in known_ids and model_id
        ]

    for entry in extra_entries:
        model_id = str(entry.get("id", "")).strip()
        if not model_id:
            continue

        model_type = deps.normalize_model_type(entry.get("model_type", "other"), default="other")
        manual_entry = deps.make_manual_model_entry(model_id, model_type)
        manual_entry.update(
            {
                "display_name": str(entry.get("display_name") or manual_entry["display_name"]),
                "description": str(entry.get("description") or manual_entry["description"]),
                "status": str(entry.get("status") or manual_entry["status"]),
                "progress": int(entry.get("progress", manual_entry["progress"])),
                "message": str(entry.get("message") or manual_entry["message"]),
                "error": entry.get("error"),
                "downloaded": bool(entry.get("downloaded", manual_entry["downloaded"])),
                "supports_generation": bool(entry["supports_generation"]) if "supports_generation" in entry else manual_entry["supports_generation"],
                "voices": entry["voices"] if "voices" in entry else manual_entry["voices"],
                "default_voice": entry["default_voice"] if "default_voice" in entry else manual_entry.get("default_voice"),
            }
        )
        models.append(manual_entry)

    models = [deps.merge_model_download_state(entry) for entry in models]
    return models


def get_model_catalog_entry(model_id: str, deps: Any, model_type: Optional[str] = None) -> Dict:
    if model_id == deps.LOCAL_DEFAULT_MODEL_ID:
        return deps.local_default_model_entry()

    for entry in deps.list_available_models():
        if str(entry.get("id", "")).strip() == model_id:
            return dict(entry)

    fallback_type = deps.infer_model_type_for_model(model_id, fallback=model_type or "other")
    return deps.make_manual_model_entry(model_id, fallback_type)


def get_hf_model_cache_root(deps: Any) -> Path:
    cache_root = os.getenv("AUDIOBOOK_HF_MODEL_CACHE_DIR", str(deps.DEFAULT_HF_MODEL_CACHE_DIR)).strip()
    return Path(cache_root or str(deps.DEFAULT_HF_MODEL_CACHE_DIR)).expanduser().resolve(strict=False)


def get_hf_model_cache_path(model_id: str, deps: Any) -> Path:
    normalized = str(model_id or "").strip()
    safe_segment = deps.HF_MODEL_CACHE_DIR_SEGMENT_RE.sub("__", normalized).strip("._")
    if not safe_segment:
        safe_segment = "model"
    return deps.get_hf_model_cache_root() / safe_segment


def download_hf_model_snapshot(
    model_id: str,
    deps: Any,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Tuple[Optional[Path], Optional[str]]:
    cache_path = deps.get_hf_model_cache_path(model_id)

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


def register_model_download_worker(model_id: str, worker: threading.Thread, deps: Any) -> None:
    with deps.MODEL_DOWNLOAD_WORKERS_LOCK:
        deps.MODEL_DOWNLOAD_WORKERS[model_id] = worker


def unregister_model_download_worker(model_id: str, deps: Any, worker: Optional[threading.Thread] = None) -> None:
    with deps.MODEL_DOWNLOAD_WORKERS_LOCK:
        current = deps.MODEL_DOWNLOAD_WORKERS.get(model_id)
        if current is None:
            return
        if worker is not None and worker is not current:
            return
        deps.MODEL_DOWNLOAD_WORKERS.pop(model_id, None)


def is_model_download_active(model_id: str, deps: Any) -> bool:
    with deps.MODEL_DOWNLOAD_WORKERS_LOCK:
        worker = deps.MODEL_DOWNLOAD_WORKERS.get(model_id)
        if worker is None:
            return False
        if worker.is_alive():
            return True
        deps.MODEL_DOWNLOAD_WORKERS.pop(model_id, None)
        return False


def infer_model_type_for_model(model_id: str, deps: Any, fallback: str = "other") -> str:
    aliased_type = deps.normalize_model_type(model_id, default="")
    if aliased_type:
        return deps.normalize_model_type(aliased_type, default=fallback)

    predefined = deps.find_predefined_model(model_id)
    if predefined:
        return deps.normalize_model_type(predefined.get("model_type"), default=fallback)

    state_entry = deps.get_model_download_state(model_id)
    if state_entry:
        return deps.normalize_model_type(state_entry.get("model_type"), default=fallback)

    return deps.normalize_model_type(fallback)


def run_model_download(model_id: str, model_type: str, deps: Any) -> None:
    voice_status = deps.model_voice_status(model_id, model_type)
    resolved_model_type = deps.normalize_model_type(voice_status.get("model_type"), default=model_type)
    resolved_voices = list(voice_status.get("voices") or [])
    resolved_default_voice = voice_status.get("default_voice")
    resolved_supports_generation = bool(voice_status.get("supports_generation"))

    if resolved_model_type == "qwen3_customvoice" and callable(getattr(deps, "discover_qwen_supported_speakers", None)):
        discovered_voices, discovered_default_voice = deps.discover_qwen_supported_speakers(
            model_id,
            allow_download=False,
        )
        if discovered_voices:
            resolved_voices = discovered_voices
            resolved_default_voice = discovered_default_voice or discovered_voices[0]

    try:
        def report_progress(percent: int, message: str) -> None:
            deps.set_model_download_state(
                model_id,
                progress=max(0, min(100, int(percent))),
                message=str(message),
                status="downloading",
                error=None,
            )

        cache_path, download_err = deps.download_hf_model_snapshot(model_id, progress_callback=report_progress)
        if download_err:
            deps.set_model_download_state(
                model_id,
                status="failed",
                progress=0,
                downloaded=False,
                message="Model download failed.",
                error=download_err,
                supports_generation=resolved_supports_generation,
                voices=resolved_voices,
                default_voice=resolved_default_voice,
            )
            return

        deps.set_model_download_state(
            model_id,
            status="downloaded",
            progress=100,
            downloaded=True,
            message="Model download complete.",
            error=None,
            cache_path=str(cache_path) if cache_path else None,
            supports_generation=resolved_supports_generation,
            voices=resolved_voices,
            default_voice=resolved_default_voice,
        )
    except Exception as exc:
        deps.set_model_download_state(
            model_id,
            status="failed",
            progress=0,
            downloaded=False,
            message="Model download failed.",
            error=str(exc),
            supports_generation=resolved_supports_generation,
            voices=resolved_voices,
            default_voice=resolved_default_voice,
        )
    finally:
        deps.unregister_model_download_worker(model_id)


def start_model_download(model_id: str, model_type: str, deps: Any) -> Tuple[Dict, bool]:
    normalized_type = deps.infer_model_type_for_model(model_id, fallback=model_type)
    if model_id == deps.LOCAL_DEFAULT_MODEL_ID:
        return deps.local_default_model_entry(), False

    base_entry = dict(deps.get_model_catalog_entry(model_id, normalized_type))
    predefined_flag = bool(base_entry.get("predefined"))
    display_name = str(base_entry.get("display_name") or (model_id if predefined_flag else f"Manual model ({model_id})"))
    description = str(base_entry.get("description") or ("User-specified Hugging Face model." if not predefined_flag else ""))
    normalized_type = deps.normalize_model_type(base_entry.get("model_type"), default=normalized_type)
    voice_profile = {
        "voices": base_entry.get("voices") if "voices" in base_entry else None,
        "default_voice": base_entry.get("default_voice") if "default_voice" in base_entry else None,
        "supports_generation": bool(base_entry.get("supports_generation"))
        if "supports_generation" in base_entry
        else deps.supports_generation_for_model_type(normalized_type),
    }

    if deps.is_hf_model_cached(model_id):
        cached_entry = dict(base_entry)
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
                "supports_generation": voice_profile["supports_generation"],
                "voices": voice_profile["voices"],
                "default_voice": voice_profile["default_voice"],
            }
        )
        deps.set_model_download_state(model_id, **cached_entry)
        return deps.get_model_catalog_entry(model_id, normalized_type), False

    existing_state = deps.get_model_download_state(model_id)
    if existing_state and bool(existing_state.get("downloaded")):
        return deps.get_model_catalog_entry(model_id, normalized_type), False

    if existing_state and str(existing_state.get("status", "")) == "downloading" and deps.is_model_download_active(model_id):
        return deps.get_model_catalog_entry(model_id, normalized_type), False

    deps.set_model_download_state(
        model_id,
        id=model_id,
        display_name=display_name,
        model_type=normalized_type,
        model_type_label=deps.MODEL_TYPE_LABELS.get(normalized_type, deps.MODEL_TYPE_LABELS["other"]),
        description=description,
        predefined=predefined_flag,
        download_required=True,
        downloaded=False,
        status="downloading",
        progress=0,
        message=f"Preparing download for model '{model_id}'...",
        error=None,
        supports_generation=voice_profile["supports_generation"],
        voices=voice_profile["voices"],
        default_voice=voice_profile["default_voice"],
    )

    worker = threading.Thread(target=deps.run_model_download, args=(model_id, normalized_type), daemon=True)
    deps.register_model_download_worker(model_id, worker)
    worker.start()
    return deps.get_model_catalog_entry(model_id, normalized_type), True


def model_download_status(model_id: str, deps: Any, model_type: Optional[str] = None) -> Dict:
    entry = deps.get_model_catalog_entry(model_id, model_type)
    entry["active_download"] = bool(model_id != deps.LOCAL_DEFAULT_MODEL_ID and deps.is_model_download_active(model_id))
    return entry


def model_voice_status(model_id: Optional[str], model_type: Optional[str], deps: Any) -> Dict:
    target_model_id = str(model_id or "").strip() or None
    requested_type = deps.normalize_model_type(model_type, default="other")
    entry: Optional[Dict] = None

    if target_model_id:
        entry = deps.get_model_catalog_entry(target_model_id, requested_type)
        requested_type = deps.normalize_model_type(entry.get("model_type"), default=requested_type)

    if entry is not None:
        supports_generation = bool(entry.get("supports_generation")) if "supports_generation" in entry else deps.supports_generation_for_model_type(requested_type)
        voices_source = entry.get("voices") if "voices" in entry else None
        default_voice_source = entry.get("default_voice") if "default_voice" in entry else None
        voices, default_voice = _resolve_voice_profile(
            voices_source,
            default_voice_source,
            fallback_voices=None if voices_source is not None else deps.model_voices_for_type(requested_type),
        )

        if requested_type == "qwen3_customvoice" and target_model_id and callable(getattr(deps, "discover_qwen_supported_speakers", None)):
            discovered_voices, discovered_default_voice = deps.discover_qwen_supported_speakers(
                target_model_id,
                allow_download=False,
            )
            if discovered_voices:
                voices = discovered_voices
                default_voice = discovered_default_voice or discovered_voices[0]

        model_type_label = str(entry.get("model_type_label") or deps.MODEL_TYPE_LABELS.get(requested_type, requested_type.replace("_", " ").title()))
    else:
        supports_generation = deps.supports_generation_for_model_type(requested_type)
        voices, default_voice = _resolve_voice_profile(None, None, fallback_voices=deps.model_voices_for_type(requested_type))
        model_type_label = deps.MODEL_TYPE_LABELS.get(requested_type, requested_type.replace("_", " ").title())

    return {
        "model_id": target_model_id,
        "model_type": requested_type,
        "model_type_label": model_type_label,
        "voices": voices,
        "default_voice": default_voice,
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
