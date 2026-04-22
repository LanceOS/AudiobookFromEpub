from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class JobStopped(Exception):
    pass


def save_job_snapshot(job_id: str, deps: Any) -> None:
    with deps.JOBS_LOCK:
        job = deps.JOBS.get(job_id)
        if not job:
            return
        payload = dict(job)

    # Keep snapshots compact so terminal jobs do not retain full chapter text
    # or other large transient fields on disk.
    payload.pop("chapters", None)

    out = deps.JOB_META_DIR / f"{job_id}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def get_job(job_id: str, deps: Any) -> Optional[Dict]:
    with deps.JOBS_LOCK:
        job = deps.JOBS.get(job_id)
        return dict(job) if job else None


def update_job(job_id: str, deps: Any, **fields: object) -> None:
    with deps.JOBS_LOCK:
        if job_id not in deps.JOBS:
            return
        deps.JOBS[job_id].update(fields)
        deps.JOBS[job_id]["updated_at"] = deps.now_iso()

    deps.save_job_snapshot(job_id)


def register_worker(job_id: str, worker, deps: Any) -> None:
    with deps.WORKERS_LOCK:
        deps.WORKERS[job_id] = worker


def unregister_worker(job_id: str, deps: Any, worker=None) -> None:
    with deps.WORKERS_LOCK:
        current = deps.WORKERS.get(job_id)
        if current is None:
            return
        if worker is not None and current is not worker:
            return
        deps.WORKERS.pop(job_id, None)


def is_job_active(job_id: str, deps: Any, job: Optional[Dict] = None) -> bool:
    current_job = job or deps.get_job(job_id)

    with deps.WORKERS_LOCK:
        worker = deps.WORKERS.get(job_id)
        if worker is None:
            return False

        if worker.is_alive():
            return True

        if current_job and current_job.get("status") in deps.ACTIVE_JOB_STATUSES:
            return True

        deps.WORKERS.pop(job_id, None)
        return False


def job_has_generated_output(job: Dict) -> bool:
    if job.get("generated_files"):
        return True

    run_folder = str(job.get("run_folder") or "").strip()
    if not run_folder:
        return False

    run_path = Path(run_folder)
    return run_path.exists() and any(run_path.glob("*.wav"))


def job_files_cleared(job: Dict) -> bool:
    return bool(job.get("status") == "stopped" and job.get("stop_requested") and not job_has_generated_output(job))


def can_clear_job_files(job: Dict, deps: Any) -> bool:
    return bool(job.get("status") == "stopped" and not deps.is_job_active(job["id"], job) and job_has_generated_output(job))


def list_generated_files(job: Dict) -> List[Dict]:
    run_folder = job.get("run_folder")
    if not run_folder:
        return []

    run_path = Path(run_folder)
    if not run_path.exists():
        return []

    files: List[Dict] = []
    for wav_path in sorted(run_path.glob("*.wav")):
        stat = wav_path.stat()
        files.append(
            {
                "name": wav_path.name,
                "path": str(wav_path),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
                "url": f"/api/jobs/{job['id']}/file/{wav_path.name}",
                "download_url": f"/api/jobs/{job['id']}/download/{wav_path.name}",
            }
        )

    return files


def serialize_job_status(job: Dict, deps: Any) -> Dict:
    elapsed_seconds = job.get("elapsed_seconds")
    if elapsed_seconds is None and job.get("started_at"):
        elapsed_seconds = deps.calculate_elapsed_seconds(job.get("started_at"), job.get("finished_at"))

    active = deps.is_job_active(job["id"], job)

    return {
        "id": job["id"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "error": job["error"],
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "elapsed_seconds": elapsed_seconds,
        "estimated_seconds": job.get("estimated_seconds"),
        "device": job.get("device") or job.get("config", {}).get("device", "auto"),
        "hf_model_id": job.get("config", {}).get("hf_model_id"),
        "model_id": job.get("config", {}).get("model_id") or deps.LOCAL_DEFAULT_MODEL_ID,
        "model_type": job.get("config", {}).get("model_type") or "kokoro",
        "detected_title": job["detected_title"],
        "chapters_count": job["chapters_count"],
        "run_folder": job["run_folder"],
        "updated_at": job["updated_at"],
        "stop_requested": bool(job.get("stop_requested")),
        "stop_requested_at": job.get("stop_requested_at"),
        "active": active,
        "can_stop": bool(job.get("status") in deps.ACTIVE_JOB_STATUSES and active and not job.get("stop_requested")),
        "can_clear_files": deps.can_clear_job_files(job),
        "files_cleared": job_files_cleared(job),
    }


def finalize_stopped_job(job_id: str, started_at: Optional[str], generated_files: List[str], deps: Any) -> None:
    job = deps.get_job(job_id)
    if not job:
        return

    files = deps.list_generated_files(job)
    final_files = [entry["name"] for entry in files] or list(generated_files)
    progress = int(job.get("progress") or 0)
    message = "Generation stopped."
    if final_files:
        message = f"Generation stopped. Kept {len(final_files)} generated file(s)."

    deps.update_job(
        job_id,
        status="stopped",
        progress=min(100, max(progress, 0)),
        message=message,
        error=None,
        finished_at=deps.now_iso(),
        elapsed_seconds=deps.calculate_elapsed_seconds(started_at or job.get("started_at")),
        generated_files=final_files,
    )


def raise_if_stop_requested(job_id: str, started_at: Optional[str], generated_files: List[str], deps: Any) -> None:
    job = deps.get_job(job_id)
    if not job or not job.get("stop_requested"):
        return

    deps.finalize_stopped_job(job_id, started_at, generated_files)
    raise JobStopped()


def run_generation_job(job_id: str, deps: Any) -> None:
    job = deps.get_job(job_id)
    if not job:
        return

    started_at = deps.now_iso()
    generated_files: List[str] = []

    try:
        deps.update_job(job_id, status="running", progress=5, message="Preparing generation job...", started_at=started_at)
        deps.raise_if_stop_requested(job_id, started_at, generated_files)

        output_dir = Path(job["config"]["output_dir"])
        run_folder = deps.create_run_folder(output_dir, job["config"]["output_name"])
        deps.update_job(job_id, run_folder=str(run_folder), progress=15, message="Run folder created.")
        deps.raise_if_stop_requested(job_id, started_at, generated_files)

        upload_path = Path(job["upload_path"])
        voice = str(job["config"].get("voice", "")).strip()
        if not voice:
            raise RuntimeError("No voice configured for this generation job.")
        mode = str(job["config"].get("mode", "single"))
        model_type = str(job["config"].get("model_type", "kokoro"))
        hf_model_id = deps.normalize_hf_model_id(job["config"].get("hf_model_id"))
        output_name = deps.slugify(str(job["config"].get("output_name", "audiobook")), fallback="audiobook")
        device = deps.detect_device(str(job["config"].get("device", "cpu")))
        deps.update_job(job_id, device=device)

        chapters = job.get("chapters") or []
        if not chapters:
            try:
                chapters = deps.extract_chapters_from_epub(upload_path)
            except Exception:
                if not deps.is_test_mode():
                    raise

                try:
                    chapter_count = int(job.get("chapters_count") or 0)
                except (TypeError, ValueError):
                    chapter_count = 0

                chapter_count = max(1, chapter_count)
                chapters = [
                    {
                        "index": str(idx),
                        "title": f"Chapter {idx}",
                        "text": "Test content.",
                    }
                    for idx in range(1, chapter_count + 1)
                ]
                deps.update_job(job_id, message="Using test-mode placeholder chapters for EPUB parsing fallback.")
        total_chapters = len(chapters)
        deps.update_job(job_id, progress=25, message=f"Loaded {total_chapters} chapters from EPUB.")
        deps.raise_if_stop_requested(job_id, started_at, generated_files)

        if deps.is_test_mode():
            sample = deps.BASE_DIR / "sample_kokoro_cpu.wav"
            if not sample.exists():
                raise RuntimeError("Test mode enabled but sample_kokoro_cpu.wav is missing")

            deps.update_job(job_id, progress=35, message="Test mode: preparing sample audio files...")

            if mode == "single":
                deps.raise_if_stop_requested(job_id, started_at, generated_files)
                out_path = run_folder / f"{output_name}.wav"
                deps.shutil.copyfile(str(sample), str(out_path))
                generated_files.append(out_path.name)
                deps.raise_if_stop_requested(job_id, started_at, generated_files)
                deps.update_job(job_id, progress=95, message="Test-mode single file ready.")
            else:
                for idx, chapter in enumerate(chapters, start=1):
                    deps.raise_if_stop_requested(job_id, started_at, generated_files)
                    chapter_slug = deps.slugify(chapter["title"], fallback=f"chapter_{idx:03d}")
                    out_path = run_folder / f"{idx:03d}_{chapter_slug}.wav"
                    deps.shutil.copyfile(str(sample), str(out_path))
                    generated_files.append(out_path.name)
                    deps.raise_if_stop_requested(job_id, started_at, generated_files)
                    prog = int(40 + idx / max(1, total_chapters) * 50)
                    deps.update_job(job_id, progress=prog, message=f"Test-mode chapter {idx}/{total_chapters} ready.")

            deps.raise_if_stop_requested(job_id, started_at, generated_files)
            deps.update_job(
                job_id,
                status="completed",
                progress=100,
                message=f"Test-mode generation complete. Created {len(generated_files)} file(s).",
                generated_files=generated_files,
                error=None,
                finished_at=deps.now_iso(),
                elapsed_seconds=deps.calculate_elapsed_seconds(started_at),
            )
            return

        if hf_model_id:
            deps.update_job(job_id, progress=30, message=f"Preparing download for model '{hf_model_id}'...")

            def report_download_progress(percent: int, message: str) -> None:
                mapped_progress = 30 + int(max(0, min(100, percent)) * 15 / 100)
                deps.update_job(job_id, progress=min(45, mapped_progress), message=message)
                deps.raise_if_stop_requested(job_id, started_at, generated_files)

            deps.get_pipeline_bundle(
                voice,
                device,
                hf_model_id=hf_model_id,
                model_type=model_type,
                progress_callback=report_download_progress,
            )
            deps.update_job(job_id, progress=45, message="Model ready. Generating files...")
            deps.raise_if_stop_requested(job_id, started_at, generated_files)

        if mode == "single":
            deps.raise_if_stop_requested(job_id, started_at, generated_files)
            combined_text = "\n\n".join(ch["text"] for ch in chapters)
            out_path = run_folder / f"{output_name}.wav"
            deps.update_job(job_id, progress=45, message="Generating single audiobook file...")
            deps.synthesize_text_to_wav(
                combined_text,
                voice=voice,
                output_path=out_path,
                device=device,
                hf_model_id=hf_model_id,
                model_type=model_type,
            )
            generated_files.append(out_path.name)
            deps.raise_if_stop_requested(job_id, started_at, generated_files)
            deps.update_job(job_id, progress=95, message="Single file generation complete.")
        else:
            for idx, chapter in enumerate(chapters, start=1):
                deps.raise_if_stop_requested(job_id, started_at, generated_files)
                chapter_slug = deps.slugify(chapter["title"], fallback=f"chapter_{idx:03d}")
                out_path = run_folder / f"{idx:03d}_{chapter_slug}.wav"
                base_progress = 45 if hf_model_id else 30
                progress_span = 45 if hf_model_id else 60
                progress_start = int(base_progress + (idx - 1) / total_chapters * progress_span)
                deps.update_job(
                    job_id,
                    progress=progress_start,
                    message=f"Generating chapter {idx}/{total_chapters}: {chapter['title']}",
                )
                deps.synthesize_text_to_wav(
                    chapter["text"],
                    voice=voice,
                    output_path=out_path,
                    device=device,
                    hf_model_id=hf_model_id,
                    model_type=model_type,
                )
                generated_files.append(out_path.name)
                deps.raise_if_stop_requested(job_id, started_at, generated_files)

        deps.raise_if_stop_requested(job_id, started_at, generated_files)
        deps.update_job(
            job_id,
            status="completed",
            progress=100,
            message=f"Generation complete. Created {len(generated_files)} file(s).",
            generated_files=generated_files,
            error=None,
            finished_at=deps.now_iso(),
            elapsed_seconds=deps.calculate_elapsed_seconds(started_at),
        )
    except JobStopped:
        return
    except Exception as exc:
        logging.exception("Generation job failed: %s", exc)
        deps.update_job(
            job_id,
            status="failed",
            progress=100,
            message="Generation failed.",
            error=str(exc),
            finished_at=deps.now_iso(),
            elapsed_seconds=deps.calculate_elapsed_seconds(started_at),
        )
    finally:
        deps.unregister_worker(job_id)
