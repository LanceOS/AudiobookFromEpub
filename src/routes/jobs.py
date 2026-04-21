from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any


def register_job_routes(app, deps: Any) -> None:
    @app.post("/api/generate")
    def api_generate():
        data = deps.request.get_json(silent=True) or {}
        job_id = str(data.get("job_id", "")).strip()
        if not job_id:
            return deps.jsonify({"error": "job_id is required."}), 400
        if not deps.is_valid_job_id(job_id):
            return deps.jsonify({"error": "job_id format is invalid."}), 400

        job = deps.get_job(job_id)
        if not job:
            return deps.jsonify({"error": "Job not found."}), 404

        if job.get("status") in deps.ACTIVE_JOB_STATUSES:
            return deps.jsonify({"error": "Job is already active."}), 409

        mode = str(data.get("mode", "single")).strip().lower()
        if mode not in {"single", "chapter"}:
            return deps.jsonify({"error": "mode must be 'single' or 'chapter'."}), 400

        output_dir, output_err = deps.validate_output_directory(str(data.get("output_dir", "")))
        if output_err:
            return deps.jsonify({"error": output_err}), 400

        output_name = deps.slugify(str(data.get("output_name", "")).strip(), fallback="audiobook")

        requested_model_id = deps.normalize_hf_model_id(data.get("model_id"))
        requested_hf_model_id = deps.normalize_hf_model_id(data.get("hf_model_id"))

        if requested_model_id == deps.LOCAL_DEFAULT_MODEL_ID:
            model_id = deps.LOCAL_DEFAULT_MODEL_ID
        elif requested_model_id:
            model_id, selected_model_err = deps.validate_hf_model_id(requested_model_id)
            if selected_model_err:
                return deps.jsonify({"error": selected_model_err}), 400
            model_id = str(model_id)
        elif requested_hf_model_id:
            model_id, selected_model_err = deps.validate_hf_model_id(requested_hf_model_id)
            if selected_model_err:
                return deps.jsonify({"error": selected_model_err}), 400
            model_id = str(model_id)
        else:
            model_id = deps.LOCAL_DEFAULT_MODEL_ID

        default_model_type = "kokoro" if model_id == deps.LOCAL_DEFAULT_MODEL_ID else deps.infer_model_type_for_model(model_id, fallback="other")
        model_type = deps.normalize_model_type(data.get("model_type"), default=default_model_type)
        voice_status = deps.model_voice_status(model_id, model_type)
        model_type = str(voice_status.get("model_type") or model_type)

        available_voices = list(voice_status.get("voices") or [])
        default_voice = str(voice_status.get("default_voice") or "").strip()
        if not available_voices:
            return deps.jsonify({"error": "Selected model does not define any voices yet."}), 400

        voice = str(data.get("voice", default_voice or available_voices[0])).strip() or default_voice or available_voices[0]
        if voice not in available_voices:
            return deps.jsonify({"error": f"Unsupported voice for model type '{model_type}'."}), 400

        if not bool(voice_status.get("supports_generation")):
            return deps.jsonify(
                {
                    "error": (
                        f"Model type '{model_type}' is currently download/select only. "
                        "Generation is not enabled for this model type."
                    )
                }
            ), 400

        if not deps.is_test_mode():
            backend_err = deps.ensure_generation_backend_ready(model_type)
            if backend_err:
                return deps.jsonify({"error": backend_err}), 400

        if model_id != deps.LOCAL_DEFAULT_MODEL_ID:
            selected_model_status = deps.model_download_status(model_id, model_type)
            if not bool(selected_model_status.get("downloaded")):
                return deps.jsonify({"error": "Selected model is not downloaded yet. Download it before generating."}), 400

        hf_model_id = None if model_id == deps.LOCAL_DEFAULT_MODEL_ID else model_id

        estimated_seconds = deps.estimate_generation_seconds(job, mode)
        requested_device = deps.default_requested_device()

        config = {
            "output_dir": str(output_dir),
            "output_name": output_name,
            "mode": mode,
            "voice": voice,
            "device": requested_device,
            "model_id": model_id,
            "model_type": model_type,
            "hf_model_id": hf_model_id,
        }

        deps.update_job(
            job_id,
            status="queued",
            progress=0,
            message="Generation queued.",
            error=None,
            generated_files=[],
            run_folder=None,
            started_at=None,
            finished_at=None,
            elapsed_seconds=None,
            estimated_seconds=estimated_seconds,
            stop_requested=False,
            stop_requested_at=None,
            config=config,
        )

        worker = threading.Thread(target=deps.run_generation_job, args=(job_id,), daemon=True)
        deps.register_worker(job_id, worker)
        worker.start()

        return deps.jsonify({"ok": True, "job_id": job_id})

    @app.post("/api/jobs/<job_id>/stop")
    def api_job_stop(job_id: str):
        if not deps.is_valid_job_id(job_id):
            return deps.jsonify({"error": "job_id format is invalid."}), 400

        job = deps.get_job(job_id)
        if not job:
            return deps.jsonify({"error": "Job not found."}), 404

        if job.get("status") in {"completed", "failed"}:
            return deps.jsonify({"error": "Only active jobs can be stopped."}), 409

        if job.get("status") == "stopped":
            return deps.jsonify({"ok": True, "job": deps.serialize_job_status(job)})

        if not deps.is_job_active(job_id, job):
            deps.finalize_stopped_job(job_id, job.get("started_at"), list(job.get("generated_files") or []))
            stopped_job = deps.get_job(job_id)
            return deps.jsonify({"ok": True, "job": deps.serialize_job_status(stopped_job or job)})

        if not job.get("stop_requested"):
            deps.update_job(
                job_id,
                stop_requested=True,
                stop_requested_at=deps.now_iso(),
                status="stopping",
                message="Stop requested. Waiting for the current step to finish.",
                error=None,
            )

        updated_job = deps.get_job(job_id)
        return deps.jsonify({"ok": True, "job": deps.serialize_job_status(updated_job or job)})

    @app.post("/api/jobs/<job_id>/clear-files")
    def api_job_clear_files(job_id: str):
        if not deps.is_valid_job_id(job_id):
            return deps.jsonify({"error": "job_id format is invalid."}), 400

        job = deps.get_job(job_id)
        if not job:
            return deps.jsonify({"error": "Job not found."}), 404

        if job.get("status") != "stopped":
            return deps.jsonify({"error": "Only stopped jobs can clear generated files."}), 409

        if deps.is_job_active(job_id, job):
            return deps.jsonify({"error": "Stop is still in progress. Wait for the job to finish stopping."}), 409

        files = deps.list_generated_files(job)
        run_folder = str(job.get("run_folder") or "").strip()
        if not run_folder and not files and not job.get("generated_files"):
            deps.update_job(job_id, message="Generated files were already cleared.")
            refreshed = deps.get_job(job_id)
            return deps.jsonify({"ok": True, "job": deps.serialize_job_status(refreshed or job), "files_cleared": []})

        run_path = Path(run_folder).resolve(strict=False) if run_folder else None
        if run_path:
            shutil.rmtree(run_path, ignore_errors=True)

        cleared_names = [entry["name"] for entry in files] or list(job.get("generated_files") or [])
        deps.update_job(
            job_id,
            run_folder=None,
            generated_files=[],
            message="Generated files cleared.",
            error=None,
        )

        refreshed = deps.get_job(job_id)
        return deps.jsonify({"ok": True, "job": deps.serialize_job_status(refreshed or job), "files_cleared": cleared_names})

    @app.get("/api/jobs/<job_id>/status")
    def api_job_status(job_id: str):
        if not deps.is_valid_job_id(job_id):
            return deps.jsonify({"error": "job_id format is invalid."}), 400

        job = deps.get_job(job_id)
        if not job:
            return deps.jsonify({"error": "Job not found."}), 404

        return deps.jsonify(deps.serialize_job_status(job))

    @app.get("/api/jobs/<job_id>/files")
    def api_job_files(job_id: str):
        if not deps.is_valid_job_id(job_id):
            return deps.jsonify({"error": "job_id format is invalid."}), 400

        job = deps.get_job(job_id)
        if not job:
            return deps.jsonify({"error": "Job not found."}), 404

        files = deps.list_generated_files(job)
        deps.update_job(job_id, generated_files=[f["name"] for f in files])

        refreshed = deps.get_job(job_id)
        return deps.jsonify(
            {
                "job_id": job_id,
                "files": files,
                "can_clear_files": deps.can_clear_job_files(refreshed or job),
                "files_cleared": deps.job_files_cleared(refreshed or job),
            }
        )

    @app.get("/api/jobs/<job_id>/file/<path:filename>")
    def api_job_file(job_id: str, filename: str):
        if not deps.is_valid_job_id(job_id):
            return deps.jsonify({"error": "job_id format is invalid."}), 400

        job = deps.get_job(job_id)
        if not job:
            return deps.jsonify({"error": "Job not found."}), 404

        safe_name, file_err = deps.validate_generated_file_request(job, filename)
        if file_err:
            status = 404 if file_err in {"File not found.", "No generated files for this job yet."} else 400
            return deps.jsonify({"error": file_err}), status

        run_folder = str(job["run_folder"])
        return deps.send_from_directory(str(Path(run_folder).resolve(strict=False)), safe_name, as_attachment=False)

    @app.get("/api/jobs/<job_id>/download/<path:filename>")
    def api_job_download(job_id: str, filename: str):
        if not deps.is_valid_job_id(job_id):
            return deps.jsonify({"error": "job_id format is invalid."}), 400

        job = deps.get_job(job_id)
        if not job:
            return deps.jsonify({"error": "Job not found."}), 404

        safe_name, file_err = deps.validate_generated_file_request(job, filename)
        if file_err:
            status = 404 if file_err in {"File not found.", "No generated files for this job yet."} else 400
            return deps.jsonify({"error": file_err}), status

        run_folder = str(job["run_folder"])
        return deps.send_from_directory(str(Path(run_folder).resolve(strict=False)), safe_name, as_attachment=True)
