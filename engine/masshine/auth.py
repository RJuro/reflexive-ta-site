"""PIN gate for shared deployments (Coolify). Two ways in, same PINs:

- A styled in-app PIN screen (cookie-based): unauthenticated BROWSER navigations get a small
  login page in the app's design language; a correct PIN sets an HttpOnly cookie via
  POST /auth/pin. No WWW-Authenticate header on HTML responses, so the browser's ugly native
  Basic-auth dialog never appears.
- Plain HTTP Basic (curl/scripts/tests): the Authorization header keeps working exactly as
  before, and non-HTML 401s still advertise it.

Enabled only when MASSHINE_PIN is set; unset (local dev) means no auth at all.

P3.8 role model (unchanged): an optional second, weaker PIN (MASSHINE_VIEW_PIN) grants a
read-only "viewer" role — GET/HEAD only, everything else 403s. Resolution order:

    MASSHINE_PIN unset                         -> editor (no auth at all)
    presented pin == MASSHINE_PIN              -> editor (full access)
    MASSHINE_VIEW_PIN set and pin matches
        (and does not also match MASSHINE_PIN) -> viewer (read-only)
    anything else                              -> 401
"""
from __future__ import annotations

import base64
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.responses import Response as StarletteResponse

_UNAUTHED_PATHS = {"/health", "/auth/pin", "/auth/logout"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
COOKIE_NAME = "masshine_auth"


def _basic_password(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return ""
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
        _, password = decoded.split(":", 1)
        return password
    except Exception:
        return ""


def role_for_pin(presented: str) -> str | None:
    """'editor' | 'viewer' | None for one presented PIN string (constant-time compares)."""
    pin = os.environ.get("MASSHINE_PIN")
    if not pin:
        return "editor"  # no auth configured at all
    if presented and secrets.compare_digest(presented, pin):
        return "editor"
    view_pin = os.environ.get("MASSHINE_VIEW_PIN")
    if view_pin and presented and secrets.compare_digest(presented, view_pin):
        return "viewer"
    return None


def resolve_role(request: Request) -> str | None:
    """Return 'editor' | 'viewer' | None. Checks the Authorization header first (explicit
    always wins), then the session cookie set by POST /auth/pin."""
    if not os.environ.get("MASSHINE_PIN"):
        return "editor"
    basic = _basic_password(request)
    if basic:
        return role_for_pin(basic)
    cookie = request.cookies.get(COOKIE_NAME, "")
    if cookie:
        return role_for_pin(cookie)
    return None


def wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def set_auth_cookie(response, request: Request, pin: str) -> None:
    secure = (request.url.scheme == "https"
              or request.headers.get("x-forwarded-proto", "") == "https")
    response.set_cookie(COOKIE_NAME, pin, max_age=30 * 24 * 3600, httponly=True,
                        samesite="lax", secure=secure, path="/")


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MASSHINE</title>
<style>
  :root { color-scheme: light; }
  body {
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    background: oklch(98.3% 0.008 80); color: oklch(25% 0.018 50);
    font: 13px/1.5 -apple-system, BlinkMacSystemFont, 'SF Pro Text', system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  .card { width: 300px; text-align: center; padding: 0 20px 6vh; }
  h1 {
    font-family: 'Tiempos Headline', 'Newsreader', 'Iowan Old Style', Georgia, serif;
    font-size: 26px; font-weight: 600; letter-spacing: -0.015em; margin: 0 0 4px;
  }
  .sub { color: oklch(52% 0.015 55); margin: 0 0 26px; }
  form { display: flex; flex-direction: column; gap: 10px; }
  input {
    text-align: center; font: 16px/1 inherit; letter-spacing: 0.25em; padding: 11px 12px;
    border: 1px solid oklch(91% 0.008 75); border-radius: 10px;
    background: oklch(99.5% 0.003 80); color: inherit; outline: none;
  }
  input:focus { border-color: oklch(56% 0.115 32); }
  button {
    border: 0; border-radius: 10px; padding: 10px; cursor: pointer;
    background: oklch(56% 0.115 32); color: #fff; font: 600 13px inherit;
  }
  button:hover { filter: brightness(0.95); }
  .err { color: oklch(55% 0.15 25); font-size: 12px; height: 16px; margin: 2px 0 0; }
  .shake { animation: shake 300ms; }
  @keyframes shake { 25% { transform: translateX(-5px); } 75% { transform: translateX(5px); } }
</style></head>
<body>
  <div class="card">
    <h1>MASSHINE</h1>
    <p class="sub">Enter the PIN to continue</p>
    <form id="f">
      <input id="pin" type="password" autocomplete="current-password" autofocus
             placeholder="&#8226;&#8226;&#8226;&#8226;" aria-label="PIN">
      <button type="submit">Enter</button>
      <p class="err" id="err"></p>
    </form>
  </div>
  <script>
    const f = document.getElementById('f'), pin = document.getElementById('pin'),
          err = document.getElementById('err');
    f.addEventListener('submit', async e => {
      e.preventDefault();
      err.textContent = '';
      const r = await fetch('/auth/pin', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({pin: pin.value})
      }).catch(() => null);
      if (r && r.ok) { location.reload(); return; }
      err.textContent = 'That PIN isn\\u2019t right — try again.';
      pin.value = ''; pin.focus();
      f.classList.remove('shake'); void f.offsetWidth; f.classList.add('shake');
    });
  </script>
</body></html>"""


class PinAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        pin = os.environ.get("MASSHINE_PIN")
        if not pin or request.url.path in _UNAUTHED_PATHS:
            return await call_next(request)
        role = resolve_role(request)
        if role is None:
            if wants_html(request):  # browser navigation → the styled PIN screen, no native dialog
                return HTMLResponse(LOGIN_PAGE, status_code=401)
            return StarletteResponse(status_code=401,
                                     headers={"WWW-Authenticate": 'Basic realm="MASSHINE"'})
        if role == "viewer" and request.method not in _SAFE_METHODS:
            return JSONResponse(status_code=403, content={"detail": "view-only access"})
        request.state.role = role
        return await call_next(request)
