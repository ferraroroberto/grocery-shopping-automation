"""Auth middleware for remote FastAPI/PWA access."""

import hmac
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_AUTH_EXEMPT_PREFIXES = ("/static/",)
_AUTH_EXEMPT_EXACT = frozenset(
    {"/", "/manifest.json", "/healthz", "/api/health", "/api/login"}
)

# Cloudflare's edge stamps these on every request it proxies through a
# tunnel — their presence means the connection genuinely crossed the public
# internet before `cloudflared` handed it to uvicorn over loopback, so it
# must not be treated as a trusted same-machine caller (see
# scripts/run_named_tunnel.py, which binds uvicorn to 127.0.0.1 for the
# named-tunnel launch path). Same signal app-launcher's middleware uses.
_CLOUDFLARE_HEADERS = ("cf-ray", "cf-connecting-ip")


def _via_cloudflare(headers) -> bool:
    return any(h in headers for h in _CLOUDFLARE_HEADERS)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Require a bearer token on non-loopback API calls when configured."""

    def __init__(self, app, get_token: Callable[[], str]) -> None:
        super().__init__(app)
        self._get_token = get_token

    async def dispatch(self, request: Request, call_next):
        token = (self._get_token() or "").strip()
        if not token:
            return await call_next(request)

        client_host = request.client.host if request.client else ""
        if client_host in _LOOPBACK_HOSTS and not _via_cloudflare(request.headers):
            return await call_next(request)

        path = request.url.path
        if path in _AUTH_EXEMPT_EXACT or any(
            path.startswith(prefix) for prefix in _AUTH_EXEMPT_PREFIXES
        ):
            return await call_next(request)

        presented = ""
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            presented = auth_header[7:].strip()
        if not presented:
            presented = request.query_params.get("token", "").strip()

        if presented and hmac.compare_digest(presented, token):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "missing or invalid bearer token"},
            headers={"WWW-Authenticate": 'Bearer realm="grocery"'},
        )
