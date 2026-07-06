"""PIN gate for shared deployments (Coolify). Plain HTTP Basic auth where only the password is
checked — any username works, the browser's native prompt is the whole UI. Enabled only when
MASSHINE_PIN is set; unset (local dev) means no auth at all, so nothing changes for the CLI/tests.
"""
from __future__ import annotations

import base64
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_UNAUTHED_PATHS = {"/health"}  # Coolify's healthcheck must succeed without credentials


class PinAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        pin = os.environ.get("MASSHINE_PIN")
        if not pin or request.url.path in _UNAUTHED_PATHS:
            return await call_next(request)
        password = ""
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
                _, password = decoded.split(":", 1)
            except Exception:
                password = ""
        if secrets.compare_digest(password, pin):
            return await call_next(request)
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="MASSHINE"'})
