"""PIN gate for shared deployments (Coolify). Plain HTTP Basic auth where only the password is
checked — any username works, the browser's native prompt is the whole UI. Enabled only when
MASSHINE_PIN is set; unset (local dev) means no auth at all, so nothing changes for the CLI/tests.

P3.8 adds an optional second, weaker PIN (MASSHINE_VIEW_PIN): a coauthor holding it gets a
read-only "viewer" role — GET/HEAD only, everything else 403s with a plain-JSON error. This lets
you hand out a link that can't accidentally trigger a paid LLM run. Role resolution:

    MASSHINE_PIN unset                         -> editor (no auth at all — today's behavior)
    password == MASSHINE_PIN                   -> editor (full access)
    MASSHINE_VIEW_PIN set and password matches
        (and does not also match MASSHINE_PIN) -> viewer (read-only)
    anything else                              -> 401
"""
from __future__ import annotations

import base64
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_UNAUTHED_PATHS = {"/health"}  # Coolify's healthcheck must succeed without credentials
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _password_from(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return ""
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
        _, password = decoded.split(":", 1)
        return password
    except Exception:
        return ""


def resolve_role(request: Request) -> str | None:
    """Return 'editor' | 'viewer' | None (not authenticated). None with no MASSHINE_PIN set
    means auth is off entirely — callers should treat that the same as 'editor'."""
    pin = os.environ.get("MASSHINE_PIN")
    if not pin:
        return "editor"  # no auth configured at all
    password = _password_from(request)
    if secrets.compare_digest(password, pin):
        return "editor"
    view_pin = os.environ.get("MASSHINE_VIEW_PIN")
    if view_pin and secrets.compare_digest(password, view_pin):
        return "viewer"
    return None


class PinAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        pin = os.environ.get("MASSHINE_PIN")
        if not pin or request.url.path in _UNAUTHED_PATHS:
            return await call_next(request)
        role = resolve_role(request)
        if role is None:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="MASSHINE"'})
        if role == "viewer" and request.method not in _SAFE_METHODS:
            return JSONResponse(status_code=403, content={"detail": "view-only access"})
        request.state.role = role
        return await call_next(request)
