from __future__ import annotations

from typing import Any


def register_ui_routes(app, deps: Any) -> None:
    @app.get("/")
    def index() -> str:
        deps.ensure_app_dirs()
        allowed_root, _ = deps.get_allowed_output_root()
        requested_device = deps.default_requested_device()
        detected_device = deps.detect_device(requested_device)
        detected_device_note = deps.device_resolution_note(requested_device, detected_device)
        return deps.render_template(
            "index.html",
            default_output_dir=str(allowed_root or deps.DEFAULT_OUTPUT_DIR),
            csrf_token=deps.get_csrf_token(),
            detected_device=detected_device,
            detected_device_note=detected_device_note,
        )

    @app.get("/health")
    def health():
        return deps.jsonify({"ok": True, "time": deps.now_iso()})
