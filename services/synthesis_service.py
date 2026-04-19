from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np


def get_pipeline_bundle(
    voice: str,
    device: str,
    deps: Any,
    hf_model_id: Optional[str] = None,
    progress_callback=None,
) -> Dict:
    if not deps.HAS_KOKORO:
        raise RuntimeError(f"kokoro import failed: {deps.KOKORO_IMPORT_ERROR}")

    lang_code = deps.voice_to_lang_code(voice)
    requested_model_id = deps.normalize_hf_model_id(hf_model_id)
    model_cache_key = requested_model_id or "local_default"
    cache_key = f"{lang_code}:{device}:{model_cache_key}"

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
        default_bundle = get_pipeline_bundle(voice, device, deps, hf_model_id=None)
        with deps.PIPELINE_LOCK:
            deps.PIPELINE_CACHE[cache_key] = default_bundle
        return default_bundle

    if model_instance is not None:
        pipeline_kwargs: Dict[str, object] = {
            "lang_code": lang_code,
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
                default_bundle = get_pipeline_bundle(voice, device, deps, hf_model_id=None)
                with deps.PIPELINE_LOCK:
                    deps.PIPELINE_CACHE[cache_key] = default_bundle
                return default_bundle
            raise
    else:
        pipeline_kwargs: Dict[str, object] = {
            "lang_code": lang_code,
            "device": device,
        }
        try:
            pipeline = deps.KPipeline(**pipeline_kwargs)
        except TypeError:
            pipeline_kwargs.pop("device", None)
            pipeline = deps.KPipeline(**pipeline_kwargs)

    bundle = {
        "pipeline": pipeline,
        "model": model_instance,
    }

    with deps.PIPELINE_LOCK:
        deps.PIPELINE_CACHE[cache_key] = bundle

    return bundle


def synthesize_text_to_wav(
    text: str,
    voice: str,
    output_path: Path,
    deps: Any,
    device: str = "cpu",
    hf_model_id: Optional[str] = None,
) -> None:
    chunks = deps.split_text_into_chunks(text)
    if not chunks:
        raise ValueError("No text chunks available for synthesis.")

    bundle = deps.get_pipeline_bundle(voice, device, hf_model_id=hf_model_id)
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
