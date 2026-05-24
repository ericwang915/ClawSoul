"""
Supabase JWT auth gate for the ClawSoul web dashboard.

When the `SUPABASE_JWT_SECRET` env var is set, every request to the dashboard
must carry a valid Supabase access token (HS256). Browsers send it as an
HttpOnly cookie set by /api/auth/session; programmatic callers send it as a
Bearer header.

When `SUPABASE_JWT_SECRET` is empty (local dev), the middleware is a no-op
so `claw_soul start` on a laptop keeps working unchanged.

`ALLOWED_EMAILS` is an optional comma-separated allowlist — only those emails
can authenticate. If empty, any signed-in Supabase user is allowed.
"""

from __future__ import annotations

import os
from typing import Optional

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

# Routes that bypass auth entirely
PUBLIC_PATHS = {
    "/login",
    "/api/status",          # fly health check
    "/api/auth/session",    # sets the cookie from a JWT
    "/api/auth/logout",     # clears the cookie
    "/api/auth/config",     # serves SUPABASE_URL + anon key to the login page
    "/favicon.ico",
}
PUBLIC_PREFIXES = ("/static/",)

COOKIE_NAME = "sb-access-token"
COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


# ── env helpers ─────────────────────────────────────────────────────────────

def jwt_secret() -> str:
    return os.environ.get("SUPABASE_JWT_SECRET", "")


def supabase_url() -> str:
    return os.environ.get("SUPABASE_URL", "")


def supabase_anon_key() -> str:
    return os.environ.get("SUPABASE_ANON_KEY", "")


def allowed_emails() -> set[str]:
    raw = os.environ.get("ALLOWED_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def allowed_origins() -> list[str]:
    """Origins permitted to call the API cross-site (e.g. the marketing site)."""
    raw = os.environ.get("ALLOWED_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


def auth_enabled() -> bool:
    return bool(jwt_secret())


# ── JWT decode + validation ─────────────────────────────────────────────────

def decode_jwt(token: str) -> Optional[dict]:
    secret = jwt_secret()
    if not secret or not token:
        return None
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.PyJWTError:
        return None

    allowed = allowed_emails()
    if allowed:
        email = (payload.get("email") or "").lower()
        if email not in allowed:
            return None
    return payload


def extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME)


def authorize_websocket(token: Optional[str]) -> Optional[dict]:
    """Same validation as the HTTP middleware, but called from the WS handler.

    Returns the decoded payload if valid, else None.
    """
    if not auth_enabled():
        return {"sub": "dev"}  # auth disabled → permit
    return decode_jwt(token) if token else None


# ── Middleware ──────────────────────────────────────────────────────────────

class AuthMiddleware(BaseHTTPMiddleware):
    """Reject unauthenticated requests when SUPABASE_JWT_SECRET is set."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        if not auth_enabled():
            return await call_next(request)

        token = extract_token(request)
        payload = decode_jwt(token) if token else None

        if not payload:
            if path.startswith(("/api/", "/ws/")):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return RedirectResponse(url="/login", status_code=302)

        request.state.user = payload
        return await call_next(request)
