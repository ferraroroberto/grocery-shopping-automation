"""Auth middleware for remote FastAPI/PWA access."""

import hmac
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_AUTH_EXEMPT_PREFIXES = ("/static/",)
_AUTH_EXEMPT_EXACT = frozenset(
    {"/", "/manifest.json", "/app-icon.svg", "/healthz", "/api/health", "/api/login"}
)


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
        if client_host in _LOOPBACK_HOSTS:
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
