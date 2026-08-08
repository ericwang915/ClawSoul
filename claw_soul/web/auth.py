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
from jwt import PyJWKClient
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..core import tenancy

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


def cookie_domain() -> str | None:
    """Optional cookie ``Domain`` attribute.

    Set ``COOKIE_DOMAIN=.herandhim.ai`` so the dashboard cookie is shared with
    the marketing site / login page on the same registrable domain. Leave
    empty for a host-only cookie (default).
    """
    return os.environ.get("COOKIE_DOMAIN", "").strip() or None


def _multi_tenant_signal() -> bool:
    """Any sign this process is a hosted / multi-tenant deployment rather than
    a laptop self-host: a Supabase backend, or the SaaS role/tenant env vars."""
    return bool(
        os.environ.get("SUPABASE_JWT_SECRET", "").strip()
        or os.environ.get("SUPABASE_URL", "").strip()
        or os.environ.get("CLAW_ROLE", "").strip()
        or os.environ.get("CLAW_USER_ID", "").strip()
    )


def dev_no_auth() -> bool:
    """Whether the web dashboard runs without login (open local access).

    Two ways in:
      1. Explicit ``CLAW_DEV_NO_AUTH=1``.
      2. **Standalone self-host** — no auth backend AND no multi-tenant signal
         at all. This is the open-source single-user case: run it on your
         laptop with zero cloud env vars and the dashboard just opens.

    A hosted deploy always carries a Supabase / role signal, so a *misconfigured*
    multi-tenant deploy (cleared secret, botched rotation) still fails closed in
    ``verify_auth_config`` rather than silently sharing one namespace.
    """
    if os.environ.get("CLAW_DEV_NO_AUTH", "").strip().lower() in (
        "1", "true", "yes",
    ):
        return True
    return not _multi_tenant_signal() and not (jwt_secret() or supabase_url())


def auth_enabled() -> bool:
    # Secure by default: auth is enforced unless explicitly disabled for
    # local dev via CLAW_DEV_NO_AUTH.  When enabled, decode_jwt verifies
    # against the shared HS256 secret or the project's JWKS public key —
    # whichever the token's `alg` header indicates.
    return not dev_no_auth()


def verify_auth_config() -> None:
    """Fail fast at startup if auth is required but unconfigured.

    Called once when the web app boots.  Without this a deploy that
    forgot / cleared ``SUPABASE_URL`` + ``SUPABASE_JWT_SECRET`` would
    400/401 every request (or, with the old fail-open logic, serve every
    user as a single shared tenant).  We'd rather refuse to boot and
    surface the misconfig.
    """
    if dev_no_auth():
        import logging
        logging.getLogger(__name__).warning(
            "[auth] auth DISABLED — open local access (standalone self-host / "
            "no Supabase backend). Do NOT expose this dashboard to the public "
            "internet without putting a login in front of it."
        )
        return
    if not (jwt_secret() or supabase_url()):
        raise RuntimeError(
            "Auth is enabled but neither SUPABASE_JWT_SECRET nor SUPABASE_URL "
            "is set — refusing to boot to avoid serving all tenants unguarded. "
            "Configure Supabase auth, or set CLAW_DEV_NO_AUTH=1 for local dev."
        )


def login_url() -> str:
    """Where to send an unauthenticated browser.

    Defaults to the dashboard's built-in ``/login`` (the bundled fallback
    page). If ``LOGIN_REDIRECT_URL`` is configured, redirect there instead
    — usually the marketing site's branded login (e.g. herandhim.ai/login)
    which has OAuth providers, so users don't see a duplicate login form
    sitting on the dashboard subdomain.
    """
    return os.environ.get("LOGIN_REDIRECT_URL", "").strip() or "/login"


# ── JWKS cache for asymmetric (ES256 / RS256) Supabase projects ─────────────

_jwks_client: PyJWKClient | None = None
_jwks_url_cached: str | None = None


def _jwks_client_for_supabase() -> PyJWKClient | None:
    """Lazy-build (and cache) a JWKS client for the current SUPABASE_URL."""
    global _jwks_client, _jwks_url_cached
    base = supabase_url()
    if not base:
        return None
    url = f"{base}/auth/v1/.well-known/jwks.json"
    if _jwks_url_cached != url:
        _jwks_url_cached = url
        # PyJWKClient caches keys in-process and respects HTTP cache headers.
        _jwks_client = PyJWKClient(url, cache_keys=True, max_cached_keys=8)
    return _jwks_client


# ── JWT decode + validation ─────────────────────────────────────────────────

def decode_jwt(token: str) -> Optional[dict]:
    """Verify a Supabase access token and return its payload.

    Supports two flavours of Supabase JWT signing:

    * **HS256** — legacy projects with a shared ``SUPABASE_JWT_SECRET``.
    * **ES256 / RS256** — current Supabase default. Public key is fetched
      from ``<SUPABASE_URL>/auth/v1/.well-known/jwks.json`` and cached.

    Returns the payload on success, ``None`` on any failure. The reason is
    captured on ``decode_jwt.last_error`` so callers can surface it.
    """
    decode_jwt.last_error = None  # type: ignore[attr-defined]

    if not token:
        decode_jwt.last_error = "missing token"  # type: ignore[attr-defined]
        return None

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        decode_jwt.last_error = f"malformed JWT header: {exc}"  # type: ignore[attr-defined]
        return None

    alg = header.get("alg") or ""

    try:
        if alg == "HS256":
            secret = jwt_secret()
            if not secret:
                decode_jwt.last_error = (
                    "JWT signed with HS256 but server has no SUPABASE_JWT_SECRET"
                )  # type: ignore[attr-defined]
                return None
            payload = jwt.decode(
                token, secret, algorithms=["HS256"], audience="authenticated"
            )
        elif alg in ("ES256", "RS256"):
            client = _jwks_client_for_supabase()
            if client is None:
                decode_jwt.last_error = (
                    f"JWT signed with {alg} but server has no SUPABASE_URL "
                    "to fetch JWKS from"
                )  # type: ignore[attr-defined]
                return None
            signing_key = client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=[alg],
                audience="authenticated",
            )
        else:
            decode_jwt.last_error = f"unsupported JWT algorithm: {alg!r}"  # type: ignore[attr-defined]
            return None
    except jwt.ExpiredSignatureError:
        decode_jwt.last_error = "token expired"  # type: ignore[attr-defined]
        return None
    except jwt.InvalidAudienceError:
        decode_jwt.last_error = "wrong audience (expected 'authenticated')"  # type: ignore[attr-defined]
        return None
    except jwt.InvalidSignatureError:
        decode_jwt.last_error = (
            "signature mismatch — verification key on the server doesn't match Supabase"
        )  # type: ignore[attr-defined]
        return None
    except jwt.PyJWTError as exc:
        decode_jwt.last_error = f"jwt error: {type(exc).__name__}: {exc}"  # type: ignore[attr-defined]
        return None
    except Exception as exc:
        decode_jwt.last_error = f"jwks/key error: {type(exc).__name__}: {exc}"  # type: ignore[attr-defined]
        return None

    allowed = allowed_emails()
    if allowed:
        email = (payload.get("email") or "").lower()
        if email not in allowed:
            decode_jwt.last_error = f"email {email!r} not on ALLOWED_EMAILS"  # type: ignore[attr-defined]
            return None
    return payload


decode_jwt.last_error = None  # type: ignore[attr-defined]


def extract_token(conn: HTTPConnection) -> Optional[str]:
    """Pull the JWT from Authorization or cookie. Works for both HTTP + WS."""
    auth = conn.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return conn.cookies.get(COOKIE_NAME)


def authorize_websocket(token: Optional[str]) -> Optional[dict]:
    """Same validation as the HTTP middleware, but called from the WS handler.

    Returns the decoded payload if valid, else None.
    """
    if not auth_enabled():
        return {"sub": "dev"}  # auth disabled → permit
    return decode_jwt(token) if token else None


# ── Middleware ──────────────────────────────────────────────────────────────

class AuthMiddleware:
    """Pure-ASGI middleware. Gates requests AND sets the tenancy contextvar.

    Implemented as raw ASGI (not Starlette's ``BaseHTTPMiddleware``) because
    that base class spawns the downstream app in a separate task, which
    breaks contextvar propagation — our per-user tenancy contextvar would
    not be visible to route handlers. See encode/starlette#1715.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_public = (
            path in PUBLIC_PATHS
            or any(path.startswith(p) for p in PUBLIC_PREFIXES)
        )

        # Fast path 1: auth gate is off altogether (local dev). No contextvar
        # binding; everything runs single-tenant.
        if not auth_enabled():
            await self.app(scope, receive, send)
            return

        # In all other cases we have to inspect the token, because we want to
        # bind tenancy even for "public" routes (e.g. an authed /api/status
        # call should still see the user's own state). The gate ENFORCEMENT
        # still skips public routes — fly health checks reach /api/status
        # without a token and get the daemon-wide stats.
        # NB: must use HTTPConnection (not Request) so this works for both
        # HTTP and WebSocket scopes — Request asserts scope['type']=='http'.
        conn = HTTPConnection(scope)
        token_str = extract_token(conn)
        payload = decode_jwt(token_str) if token_str else None

        if not payload:
            if is_public:
                await self.app(scope, receive, send)
                return
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4401})
                return
            if path.startswith(("/api/", "/ws/")):
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
            else:
                response = RedirectResponse(url=login_url(), status_code=302)
            await response(scope, receive, send)
            return

        # Have a valid token — bind the tenancy contextvar so all route
        # handlers, agent lookups, and storage calls see the right user_id.
        user_id = payload.get("sub")
        cv_token = tenancy.set_current_user(user_id) if user_id else None

        try:
            await self.app(scope, receive, send)
        finally:
            if cv_token is not None:
                tenancy.reset_current_user(cv_token)
