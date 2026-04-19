from __future__ import annotations

from typing import Any


def register_model_routes(app, deps: Any) -> None:
    @app.get("/api/models")
    def api_models():
        deps.ensure_app_dirs()
        return deps.jsonify(
            {
                "default_model_id": deps.LOCAL_DEFAULT_MODEL_ID,
                "default_model_type": "kokoro",
                "models": deps.list_available_models(),
            }
        )

    @app.post("/api/models/download")
    def api_model_download():
        deps.ensure_app_dirs()
        data = deps.request.get_json(silent=True) or {}

        requested_model_id = deps.normalize_hf_model_id(data.get("model_id") or data.get("hf_model_id"))
        if not requested_model_id:
            return deps.jsonify({"error": "model_id is required."}), 400

        if requested_model_id == deps.LOCAL_DEFAULT_MODEL_ID:
            return deps.jsonify({"ok": True, "started": False, "status": deps.model_download_status(requested_model_id)}), 200

        model_id, model_err = deps.validate_hf_model_id(requested_model_id)
        if model_err:
            return deps.jsonify({"error": model_err}), 400

        requested_type = deps.normalize_model_type(
            data.get("model_type"),
            default=deps.infer_model_type_for_model(str(model_id), fallback="kokoro"),
        )
        status, started = deps.start_model_download(str(model_id), requested_type)
        return deps.jsonify({"ok": True, "started": started, "status": status}), (202 if started else 200)

    @app.get("/api/models/download-status")
    def api_model_download_status():
        requested_model_id = deps.normalize_hf_model_id(deps.request.args.get("model_id"))
        if not requested_model_id:
            return deps.jsonify({"error": "model_id is required."}), 400

        model_id = requested_model_id
        if model_id != deps.LOCAL_DEFAULT_MODEL_ID:
            normalized_model_id, model_err = deps.validate_hf_model_id(model_id)
            if model_err:
                return deps.jsonify({"error": model_err}), 400
            model_id = str(normalized_model_id)

        model_type = deps.normalize_model_type(
            deps.request.args.get("model_type"),
            default=deps.infer_model_type_for_model(model_id, fallback="kokoro"),
        )
        return deps.jsonify({"ok": True, "status": deps.model_download_status(model_id, model_type)})

    @app.get("/api/models/voices")
    def api_model_voices():
        requested_model_id = deps.normalize_hf_model_id(deps.request.args.get("model_id"))
        if requested_model_id and requested_model_id != deps.LOCAL_DEFAULT_MODEL_ID:
            normalized_model_id, model_err = deps.validate_hf_model_id(requested_model_id)
            if model_err:
                return deps.jsonify({"error": model_err}), 400
            requested_model_id = str(normalized_model_id)

        requested_model_type = deps.request.args.get("model_type")
        if requested_model_id and not requested_model_type:
            requested_model_type = deps.infer_model_type_for_model(requested_model_id, fallback="kokoro")

        payload = deps.model_voice_status(requested_model_id, requested_model_type)
        return deps.jsonify({"ok": True, "status": payload})
