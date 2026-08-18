"""
CORS and API docs configuration helpers (Phase 12).

Production defaults shrink recon surface (no /docs) and CORS allow-lists to
what the Challazo SPA actually sends, without changing functional API routes.
"""

from __future__ import annotations

import os

from app.utils.auth_cookies import CSRF_HEADER_NAME


DEFAULT_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8000",
    "http://localhost:8080",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    "https://challzo.vercel.app",
]

# Fallback when ALLOWED_ORIGINS is not set in production.
DEFAULT_PROD_ORIGINS = [
    "https://challazo.nxtlab.tech",
]

# Methods used by the SPA against this API (plus OPTIONS preflight / HEAD).
DEFAULT_ALLOW_METHODS = [
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "HEAD",
]

# Headers the Vercel SPA / browsers may send (incl. Phase 5b CSRF + video Range).
DEFAULT_ALLOW_HEADERS = [
    "Authorization",
    "Content-Type",
    "Accept",
    "Origin",
    "X-Requested-With",
    CSRF_HEADER_NAME,
    "Range",
]

# So credentialed clients can read partial-content metadata on video streams.
DEFAULT_EXPOSE_HEADERS = [
    "Content-Range",
    "Accept-Ranges",
    "Content-Length",
]


def is_production() -> bool:
    return os.getenv("ENVIRONMENT", "development").lower() == "production"


def api_docs_enabled() -> bool:
    """
    Whether FastAPI should mount ``/docs``, ``/redoc``, and ``/openapi.json``.

    - Default **off** in production (Phase 12).
    - Default **on** in non-production (local Swagger unchanged).
    - Override with ``ENABLE_API_DOCS=true|false``.
    """
    raw = os.getenv("ENABLE_API_DOCS", "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return not is_production()


def get_allowed_origins() -> list[str]:
    """
    Return CORS allowed origins.

    Production uses ALLOWED_ORIGINS from the environment (required for
    credentialed cross-origin cookie auth). Development merges env origins
    with local defaults.
    """
    env_origins = [
        origin.strip()
        for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ]

    if is_production():
        return env_origins or list(DEFAULT_PROD_ORIGINS)

    return list(dict.fromkeys(DEFAULT_DEV_ORIGINS + env_origins))


def get_cors_allow_methods() -> list[str]:
    """
    CORS allow-methods.

    Default is the SPA method set (covers all current routes). Set
    ``CORS_ALLOW_METHODS=*`` to restore a wildcard allow-list.
    """
    raw = os.getenv("CORS_ALLOW_METHODS", "").strip()
    if raw == "*":
        return ["*"]
    if not raw:
        return list(DEFAULT_ALLOW_METHODS)
    return [m.strip().upper() for m in raw.split(",") if m.strip()] or list(
        DEFAULT_ALLOW_METHODS
    )


def get_cors_allow_headers() -> list[str]:
    """
    CORS allow-headers.

    Default is the SPA header set (Phase 12). Set ``CORS_ALLOW_HEADERS=*`` to
    restore the previous wildcard behaviour. Explicit lists always keep
    ``X-CSRF-Token``.
    """
    raw = os.getenv("CORS_ALLOW_HEADERS", "").strip()
    if raw == "*":
        return ["*"]
    if not raw:
        return list(DEFAULT_ALLOW_HEADERS)

    headers = [h.strip() for h in raw.split(",") if h.strip()]
    lower = {h.lower() for h in headers}
    if CSRF_HEADER_NAME.lower() not in lower:
        headers.append(CSRF_HEADER_NAME)
    return headers or list(DEFAULT_ALLOW_HEADERS)


def get_cors_expose_headers() -> list[str]:
    """
    CORS expose-headers for video Range responses.

    Override with comma-separated ``CORS_EXPOSE_HEADERS`` when needed.
    """
    raw = os.getenv("CORS_EXPOSE_HEADERS", "").strip()
    if not raw:
        return list(DEFAULT_EXPOSE_HEADERS)
    return [h.strip() for h in raw.split(",") if h.strip()] or list(
        DEFAULT_EXPOSE_HEADERS
    )
