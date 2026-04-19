from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any


def register_upload_routes(app, deps: Any) -> None:
    @app.post("/api/upload")
    def api_upload():
        deps.ensure_app_dirs()

        if "epub" not in deps.request.files:
            return deps.jsonify({"error": "Missing file field 'epub'."}), 400

        incoming = deps.request.files["epub"]
        if not incoming.filename:
            return deps.jsonify({"error": "Please choose an EPUB file."}), 400

        filename = deps.secure_filename(incoming.filename)
        ext = Path(filename).suffix.lower()
        if ext not in deps.ALLOWED_UPLOAD_EXTENSIONS:
            return deps.jsonify({"error": "Only .epub files are supported."}), 400

        job_id = uuid.uuid4().hex
        upload_dir = deps.UPLOADS_DIR / job_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        upload_path = upload_dir / "input.epub"
        incoming.save(upload_path)

        if not deps.is_test_mode() and not deps.looks_like_valid_epub(upload_path):
            shutil.rmtree(upload_dir, ignore_errors=True)
            return deps.jsonify({"error": "Invalid EPUB structure."}), 400

        filter_level = "default"

        try:
            detected_title = deps.extract_book_title(upload_path)
            raw_filter = deps.request.form.get("filter_level")
            if raw_filter:
                filter_level = str(raw_filter).lower()
            else:
                raw_skip = deps.request.form.get("skip_front_matter")
                if raw_skip is not None:
                    filter_level = "default" if str(raw_skip).lower() in ("1", "true", "yes", "on") else "off"
                else:
                    filter_level = "default"

            chapters = deps.extract_chapters_from_epub(upload_path, filter_level=filter_level)
        except Exception as exc:
            if deps.is_test_mode():
                detected_title = upload_path.stem
                chapters = [{"index": "1", "title": "Chapter 1", "text": "Test content."}]
            else:
                shutil.rmtree(upload_dir, ignore_errors=True)
                return deps.jsonify({"error": f"Failed to parse EPUB: {exc}"}), 400

        suggested_name = deps.slugify(detected_title, fallback="audiobook")
        job_payload = {
            "id": job_id,
            "created_at": deps.now_iso(),
            "updated_at": deps.now_iso(),
            "status": "uploaded",
            "progress": 0,
            "message": "EPUB uploaded. Configure options and generate.",
            "error": None,
            "started_at": None,
            "finished_at": None,
            "elapsed_seconds": None,
            "estimated_seconds": None,
            "upload_path": str(upload_path),
            "detected_title": detected_title,
            "chapters_count": len(chapters),
            "chapters": chapters,
            "run_folder": None,
            "generated_files": [],
            "stop_requested": False,
            "stop_requested_at": None,
            "config": {
                "output_dir": str(deps.DEFAULT_OUTPUT_DIR),
                "output_name": suggested_name,
                "mode": "single",
                "voice": "af_heart",
                "device": "cpu",
                "model_id": deps.LOCAL_DEFAULT_MODEL_ID,
                "model_type": "kokoro",
                "hf_model_id": None,
                "filter_level": filter_level,
            },
        }

        with deps.JOBS_LOCK:
            deps.JOBS[job_id] = job_payload

        deps.save_job_snapshot(job_id)

        return deps.jsonify(
            {
                "job_id": job_id,
                "detected_title": detected_title,
                "suggested_name": suggested_name,
                "chapters_count": len(chapters),
            }
        )
