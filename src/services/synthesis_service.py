from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def _directory_has_files(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    return any(item.is_file() for item in path.rglob("*"))


def _resolve_model_type(hf_model_id: Optional[str], model_type: Optional[str], deps: Any) -> str:
    requested_model_id = deps.normalize_hf_model_id(hf_model_id)
    if requested_model_id:
        fallback_type = deps.normalize_model_type(model_type, default="other")
        return deps.infer_model_type_for_model(requested_model_id, fallback=fallback_type)
    return deps.normalize_model_type(model_type, default="kokoro")


def _build_cache_key(model_type: str, voice: str, device: str, model_cache_key: str, deps: Any) -> str:
    if model_type == "kokoro":
        lang_code = deps.voice_to_lang_code(voice)
        return f"{model_type}:{lang_code}:{device}:{model_cache_key}"
    return f"{model_type}:{device}:{model_cache_key}"


def _write_wav(output_path: Path, audio: np.ndarray, sample_rate: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import soundfile as sf  # type: ignore[reportMissingImports]
    except Exception:
        raise RuntimeError("soundfile (pysoundfile) is required to write WAV output")
    sf.write(str(output_path), audio, sample_rate)


def _normalize_generated_audio(audio: object) -> np.ndarray:
    audio_np = np.asarray(audio, dtype=np.float32).flatten()
    if audio_np.size:
        return audio_np
    return np.array([], dtype=np.float32)


def _load_qwen_model(load_target: str, device: str, deps: Any) -> object:
    last_error: Optional[Exception] = None
    candidate_kwargs: List[Dict[str, object]] = [
        {"device_map": device},
        {"device_map": "cpu" if not str(device).lower().startswith("cuda") else device},
        {},
    ]

    for kwargs in candidate_kwargs:
        try:
            if kwargs:
                return deps.Qwen3TTSModel.from_pretrained(load_target, **kwargs)
            return deps.Qwen3TTSModel.from_pretrained(load_target)
        except TypeError:
            continue
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise RuntimeError(f"unable to initialize Qwen3 model ({last_error})")

    return deps.Qwen3TTSModel.from_pretrained(load_target)


def _get_kokoro_pipeline_bundle(
    voice: str,
    device: str,
    deps: Any,
    requested_model_id: Optional[str],
    cache_key: str,
    progress_callback=None,
) -> Dict:
    if not deps.HAS_KOKORO:
        raise RuntimeError(f"kokoro import failed: {deps.KOKORO_IMPORT_ERROR}")

    with deps.PIPELINE_LOCK:
        cached = deps.PIPELINE_CACHE.get(cache_key)
        if cached:
            return cached

    model_instance = None
    checkpoint = deps.BASE_DIR / "Kokoro-82M" / "kokoro-v1_0.pth"
    config_file = deps.BASE_DIR / "Kokoro-82M" / "config.json"
    fallback_reason: Optional[str] = None

    if deps.KModel:
        if requested_model_id:
            model_cache_path, download_err = deps.download_hf_model_snapshot(
                requested_model_id,
                progress_callback=progress_callback,
            )
            if download_err:
                fallback_reason = download_err
            elif model_cache_path is not None:
                custom_config, custom_weights, asset_err = deps.resolve_local_kokoro_assets(model_cache_path)
                if asset_err:
                    fallback_reason = asset_err
                else:
                    try:
                        model_instance = deps.KModel(
                            repo_id=requested_model_id,
                            config=str(custom_config),
                            model=str(custom_weights),
                        ).to(device).eval()
                    except Exception as exc:
                        fallback_reason = f"failed to initialize downloaded model ({exc})"
        elif checkpoint.exists() and not deps.is_lfs_pointer(checkpoint):
            config_arg = None
            if config_file.exists() and not deps.is_lfs_pointer(config_file):
                config_arg = str(config_file)
            model_instance = deps.KModel(config=config_arg, model=str(checkpoint)).to(device).eval()

    if requested_model_id and model_instance is None:
        reason = fallback_reason or "model could not be initialized"
        logging.warning(
            "Falling back to default Kokoro model for '%s' (%s).",
            requested_model_id,
            reason,
        )
        default_cache_key = _build_cache_key("kokoro", voice, device, "local_default", deps)
        default_bundle = _get_kokoro_pipeline_bundle(
            voice,
            device,
            deps,
            requested_model_id=None,
            cache_key=default_cache_key,
            progress_callback=None,
        )
        with deps.PIPELINE_LOCK:
            deps.PIPELINE_CACHE[cache_key] = default_bundle
        return default_bundle

    if model_instance is not None:
        pipeline_kwargs: Dict[str, object] = {
            "lang_code": deps.voice_to_lang_code(voice),
            "model": model_instance,
        }
        if requested_model_id:
            pipeline_kwargs["repo_id"] = requested_model_id
        try:
            pipeline = deps.KPipeline(**pipeline_kwargs)
        except Exception as exc:
            if requested_model_id:
                logging.warning(
                    "Custom HF model pipeline failed for '%s' (%s). Falling back to default Kokoro model.",
                    requested_model_id,
                    exc,
                )
                default_cache_key = _build_cache_key("kokoro", voice, device, "local_default", deps)
                default_bundle = _get_kokoro_pipeline_bundle(
                    voice,
                    device,
                    deps,
                    requested_model_id=None,
                    cache_key=default_cache_key,
                    progress_callback=None,
                )
                with deps.PIPELINE_LOCK:
                    deps.PIPELINE_CACHE[cache_key] = default_bundle
                return default_bundle
            raise
    else:
        pipeline_kwargs: Dict[str, object] = {
            "lang_code": deps.voice_to_lang_code(voice),
            "device": device,
        }
        try:
            pipeline = deps.KPipeline(**pipeline_kwargs)
        except TypeError:
            pipeline_kwargs.pop("device", None)
            pipeline = deps.KPipeline(**pipeline_kwargs)

    bundle = {
        "backend": "kokoro",
        "pipeline": pipeline,
        "model": model_instance,
    }

    with deps.PIPELINE_LOCK:
        deps.PIPELINE_CACHE[cache_key] = bundle
    return bundle


def _get_qwen_pipeline_bundle(
    device: str,
    deps: Any,
    requested_model_id: Optional[str],
    cache_key: str,
    progress_callback=None,
) -> Dict:
    if not deps.HAS_QWEN_TTS:
        raise RuntimeError(f"qwen-tts import failed: {deps.QWEN_TTS_IMPORT_ERROR}")
    if not requested_model_id:
        raise RuntimeError("Qwen3 CustomVoice generation requires a selected Hugging Face model ID.")

    with deps.PIPELINE_LOCK:
        cached = deps.PIPELINE_CACHE.get(cache_key)
        if cached:
            return cached

    model_cache_path, download_err = deps.download_hf_model_snapshot(
        requested_model_id,
        progress_callback=progress_callback,
    )
    if download_err:
        raise RuntimeError(download_err)

    load_target = requested_model_id
    if model_cache_path is not None and _directory_has_files(model_cache_path):
        load_target = str(model_cache_path)

    qwen_model = _load_qwen_model(load_target, device, deps)
    bundle = {
        "backend": "qwen3_customvoice",
        "pipeline": None,
        "model": qwen_model,
    }
    with deps.PIPELINE_LOCK:
        deps.PIPELINE_CACHE[cache_key] = bundle
    return bundle


def get_pipeline_bundle(
    voice: str,
    device: str,
    deps: Any,
    hf_model_id: Optional[str] = None,
    model_type: Optional[str] = None,
    progress_callback=None,
) -> Dict:
    requested_model_id = deps.normalize_hf_model_id(hf_model_id)
    resolved_model_type = _resolve_model_type(requested_model_id, model_type, deps)
    model_cache_key = requested_model_id or "local_default"
    cache_key = _build_cache_key(resolved_model_type, voice, device, model_cache_key, deps)

    if resolved_model_type == "qwen3_customvoice":
        return _get_qwen_pipeline_bundle(
            device,
            deps,
            requested_model_id=requested_model_id,
            cache_key=cache_key,
            progress_callback=progress_callback,
        )

    return _get_kokoro_pipeline_bundle(
        voice,
        device,
        deps,
        requested_model_id=requested_model_id,
        cache_key=cache_key,
        progress_callback=progress_callback,
    )


def discover_qwen_supported_speakers(
    model_id: Optional[str],
    deps: Any,
    *,
    allow_download: bool = False,
    device: str = "cpu",
) -> Tuple[List[str], Optional[str]]:
    requested_model_id = deps.normalize_hf_model_id(model_id)
    if not requested_model_id or not deps.HAS_QWEN_TTS:
        return [], None

    cache_path = deps.get_hf_model_cache_path(requested_model_id)
    if not _directory_has_files(cache_path):
        if not allow_download:
            return [], None
        model_cache_path, download_err = deps.download_hf_model_snapshot(requested_model_id)
        if download_err:
            logging.warning("Unable to discover Qwen speakers for '%s': %s", requested_model_id, download_err)
            return [], None
        if model_cache_path is not None and _directory_has_files(model_cache_path):
            cache_path = model_cache_path

    load_target = str(cache_path) if _directory_has_files(cache_path) else requested_model_id
    try:
        model = _load_qwen_model(load_target, device, deps)
        getter = getattr(model, "get_supported_speakers", None)
        if not callable(getter):
            return [], None
        raw_speakers = getter()
    except Exception as exc:
        logging.warning("Unable to discover Qwen speakers for '%s': %s", requested_model_id, exc)
        return [], None

    if isinstance(raw_speakers, str):
        speakers = [raw_speakers]
    elif isinstance(raw_speakers, (list, tuple, set)):
        speakers = [str(item or "").strip() for item in raw_speakers]
    else:
        speakers = []

    deduped: List[str] = []
    seen = set()
    for speaker in speakers:
        if speaker and speaker not in seen:
            deduped.append(speaker)
            seen.add(speaker)

    default_speaker = deduped[0] if deduped else None
    return deduped, default_speaker


def _synthesize_with_kokoro(
    text: str,
    voice: str,
    output_path: Path,
    deps: Any,
    device: str,
    hf_model_id: Optional[str],
) -> None:
    chunks = deps.split_text_into_chunks(text)
    if not chunks:
        raise ValueError("No text chunks available for synthesis.")

    bundle = deps.get_pipeline_bundle(
        voice,
        device,
        hf_model_id=hf_model_id,
    )
    pipeline = bundle["pipeline"]
    model = bundle["model"]
    voice_reference = deps.choose_voice_reference(voice)

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

            audio_np = _normalize_generated_audio(audio)
            if audio_np.size:
                generated_audio.append(audio_np)

    if not generated_audio:
        raise RuntimeError("Kokoro did not return any audio frames.")

    _write_wav(output_path, np.concatenate(generated_audio), 24000)


def _extract_qwen_waveform(payload: object) -> Tuple[List[np.ndarray], int]:
    if isinstance(payload, tuple) and len(payload) >= 2:
        raw_wavs = payload[0]
        sample_rate = int(payload[1] or 24000)
    else:
        raise RuntimeError("Unexpected Qwen3 generation response format.")

    if isinstance(raw_wavs, np.ndarray):
        wav_items = [raw_wavs]
    elif isinstance(raw_wavs, (list, tuple)):
        wav_items = list(raw_wavs)
    else:
        wav_items = []

    frames = [_normalize_generated_audio(item) for item in wav_items]
    frames = [frame for frame in frames if frame.size]
    return frames, sample_rate


def _synthesize_with_qwen_customvoice(
    text: str,
    voice: str,
    output_path: Path,
    deps: Any,
    device: str,
    hf_model_id: Optional[str],
) -> None:
    chunks = deps.split_text_into_chunks(text)
    if not chunks:
        raise ValueError("No text chunks available for synthesis.")

    bundle = deps.get_pipeline_bundle(
        voice,
        device,
        hf_model_id=hf_model_id,
        model_type="qwen3_customvoice",
    )
    model = bundle.get("model")
    if model is None:
        raise RuntimeError("Qwen3 CustomVoice model is unavailable.")

    if not hasattr(model, "generate_custom_voice"):
        raise RuntimeError("Loaded Qwen3 model does not expose generate_custom_voice().")

    generated_audio: List[np.ndarray] = []
    sample_rate = 24000
    for chunk in chunks:
        result = model.generate_custom_voice(
            text=chunk,
            language="Auto",
            speaker=voice,
        )
        frames, chunk_sr = _extract_qwen_waveform(result)
        if frames:
            generated_audio.extend(frames)
            sample_rate = chunk_sr

    if not generated_audio:
        raise RuntimeError("Qwen3 CustomVoice did not return any audio frames.")

    _write_wav(output_path, np.concatenate(generated_audio), sample_rate)


def synthesize_text_to_wav(
    text: str,
    voice: str,
    output_path: Path,
    deps: Any,
    device: str = "cpu",
    hf_model_id: Optional[str] = None,
    model_type: Optional[str] = None,
) -> None:
    resolved_model_type = _resolve_model_type(hf_model_id, model_type, deps)
    if resolved_model_type == "qwen3_customvoice":
        _synthesize_with_qwen_customvoice(
            text,
            voice,
            output_path,
            deps,
            device=device,
            hf_model_id=hf_model_id,
        )
        return

    _synthesize_with_kokoro(
        text,
        voice,
        output_path,
        deps,
        device=device,
        hf_model_id=hf_model_id,
    )
