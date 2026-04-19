from __future__ import annotations

import hmac
import time
from typing import Any


def register_middleware(app, deps: Any) -> None:
    @app.before_request
    def enforce_api_csrf():
        if deps.request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        if not deps.request.path.startswith("/api/"):
            return None

        if not deps.is_same_origin_request():
            return deps.jsonify({"error": "Cross-origin requests are not allowed."}), 403

        expected = str(deps.session.get("csrf_token") or "")
        provided = str(deps.request.headers.get("X-CSRF-Token") or "")
        if not expected or not provided or not hmac.compare_digest(provided, expected):
            return deps.jsonify({"error": "Invalid CSRF token."}), 403

        return None

    @app.before_request
    def enforce_rate_limit():
        if not deps.request.path.startswith("/api/"):
            return None

        bucket, limit, window = deps.rate_limit_bucket_for_request()
        if not bucket:
            return None

        now = time.time()
        key = f"{bucket}:{deps.client_identifier()}"
        with deps.RATE_LIMIT_LOCK:
            recent = [stamp for stamp in deps.RATE_LIMIT_STATE.get(key, []) if now - stamp < window]
            if len(recent) >= limit:
                return deps.jsonify({"error": "Rate limit exceeded. Please retry shortly."}), 429
            recent.append(now)
            deps.RATE_LIMIT_STATE[key] = recent

        return None

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; "
            "img-src 'self' data:; script-src 'self'; style-src 'self'; connect-src 'self'",
        )
        if (deps.request.headers.get("X-Forwarded-Proto") or "").strip().lower() == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response
