"""Phone access: a second listener on the local network, the password gate in front of every
page, the sign-in page, and what "Add to Home Screen" needs (manifest, icons).

The desktop window keeps its own listener on 127.0.0.1. When phone access is on, the
same app also listens on 0.0.0.0:<port> (Settings, "Phone and other devices"); this
starts and stops while the app runs. Every request that isn't from the desktop window
needs a signed-in phone (devils_workshop/phone.py). The OpenRouter key never leaves
the PC: phones only see the screens, and the key and phone settings are desktop-only.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import socket
import struct
import sys
import urllib.parse
import zlib
from functools import lru_cache

import uvicorn
from nicegui import app, core, ui
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from devils_workshop import APP_NAME
from devils_workshop.phone import (
    COOKIE,
    REMEMBER_DAYS,
    Devices,
    Throttle,
    check_password,
    cookie_from,
    device_name,
    is_desktop,
)
from devils_workshop.settings import settings_dir
from devils_workshop.ui.state import STATE

log = logging.getLogger(__name__)

RECONNECT_TIMEOUT_S = 300.0  # a phone that sleeps or switches apps for a while comes back to the same page
OPEN_PATHS = {"/login", "/logout", "/manifest.webmanifest", "/apple-touch-icon.png",
              "/apple-touch-icon-precomposed.png", "/icon-192.png", "/icon-512.png", "/favicon.ico"}
THROTTLE = Throttle()
_DEVICES: dict[str, Devices] = {}


def devices() -> Devices:
    path = settings_dir() / "phones.json"
    return _DEVICES.setdefault(str(path), Devices(path))


# --- the second listener ---------------------------------------------------------------------------

class _Listener(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield  # the desktop listener handles closing the app


class PhoneServer:
    """Listening on the local network while phone access is on (no restart needed)."""

    def __init__(self) -> None:
        self.server: _Listener | None = None
        self.task: asyncio.Task | None = None
        self.port: int | None = None
        self.problem = ""

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done() and self.server is not None and self.server.started

    def listening_port(self) -> int | None:
        return self.port if self.task is not None and not self.task.done() else None

    @staticmethod
    def bind(port: int) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform == "win32":  # don't share the port with another program
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        return sock

    async def start(self, port: int) -> bool:
        await self.stop()
        self.problem = ""
        try:
            sock = self.bind(port)
        except OSError as exc:
            self.problem = (f"Port {port} is in use by another program. Choose another port."
                            if getattr(exc, "winerror", None) in (10048, 10013) or exc.errno in (98, 48, 10048)
                            else f"Couldn't listen on port {port}: {exc}")
            log.warning("Phone access: %s", self.problem)
            return False
        config = uvicorn.Config(core.app, host="0.0.0.0", port=port, lifespan="off", ws="wsproto",
                                log_level="warning", proxy_headers=False, timeout_graceful_shutdown=3)
        self.server, self.port = _Listener(config), port
        self.task = asyncio.create_task(self.server.serve(sockets=[sock]), name="phone listener")
        for _ in range(100):
            if self.server.started or self.task.done():
                break
            await asyncio.sleep(0.05)
        if not self.server.started:
            self.problem = f"Couldn't listen on port {port}."
            await self.stop()
            sock.close()
            return False
        log.info("Phone access: listening on 0.0.0.0:%s", port)
        return True

    async def stop(self) -> None:
        if self.task is None:
            return
        if self.server is not None:
            self.server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(self.task), timeout=8)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001 (stopping must never fail)
            if self.server is not None:
                self.server.force_exit = True
        self.server = self.task = None
        self.port = None
        log.info("Phone access: stopped listening")


SERVER = PhoneServer()


async def apply_settings() -> None:
    """Start, move or stop the listener to match Settings."""
    phone = STATE.app_settings().phone
    wanted = phone.enabled and bool(phone.password_hash)
    if wanted and not (SERVER.running and SERVER.port == phone.port):
        await SERVER.start(phone.port)
    elif not wanted:
        SERVER.problem = ""
        if SERVER.task is not None:
            reload_phones()
            await SERVER.stop()


def reload_phones() -> None:
    """Make every open phone page load again (it then meets the sign-in page, or nothing if off)."""
    for client in list(app.clients()):
        request = getattr(client, "request", None)
        if request is not None and not is_desktop(request.scope, SERVER.listening_port()) \
                and client.has_socket_connection:
            client.run_javascript("location.reload()")


# --- who is asking ---------------------------------------------------------------------------------------

def allowed(scope: dict) -> bool:
    if is_desktop(scope, SERVER.listening_port()):
        return True
    phone = STATE.app_settings().phone
    return phone.enabled and bool(phone.password_hash) and devices().known(cookie_from(scope))


def from_phone() -> bool:
    """True on a page opened from a phone (or any other device), not the desktop window."""
    try:
        request = ui.context.client.request
    except (AttributeError, RuntimeError):
        return False
    return request is not None and not is_desktop(request.scope, SERVER.listening_port())


class PhoneGate:
    """In front of every request: the desktop window (unless the passcode lock is closed) and signed-in phones
    get through."""

    def __init__(self, asgi_app) -> None:
        self.app = asgi_app

    async def __call__(self, scope, receive, send) -> None:
        from devils_workshop.ui import lock  # the passcode lock on this PC's own screens
        if scope["type"] in ("http", "websocket") and lock.locked_out(scope):
            await lock.refuse(scope, receive, send)
            return
        if scope["type"] not in ("http", "websocket") or scope.get("path") in OPEN_PATHS or allowed(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
            return
        path = scope.get("path") or "/"
        if scope.get("method") == "GET" and not path.startswith("/_nicegui"):
            query = scope.get("query_string", b"").decode("latin-1")
            target = path + ("?" + query if query else "")
            response = RedirectResponse("/login?next=" + urllib.parse.quote(target, safe=""), status_code=303)
        else:
            response = PlainTextResponse("Sign in first.", status_code=401)
        await response(scope, receive, send)


# --- signing in ----------------------------------------------------------------------------------------

def _safe_next(target: str | None) -> str:
    target = target or "/"
    if not target.startswith("/") or target.startswith("//") or target.startswith("/\\"):
        return "/"
    return target


def _who(request: Request) -> str:
    return request.client.host if request.client else "?"


async def login_page(request: Request) -> Response:
    return _login_html(_safe_next(request.query_params.get("next")))


async def login_submit(request: Request) -> Response:
    form = urllib.parse.parse_qs((await request.body()).decode("utf-8", "replace"))
    password = (form.get("password") or [""])[0]
    remember = (form.get("remember") or [""])[0] == "on"
    target = _safe_next((form.get("next") or ["/"])[0])
    phone = STATE.app_settings().phone
    if not (phone.enabled and phone.password_hash):
        return _login_html(target, "Phone access is turned off on the PC.", 403)
    who = _who(request)
    wait = THROTTLE.wait(who)
    if wait > 0:
        return _login_html(target, f"Too many wrong passwords. Try again in {_seconds(wait)}.", 429)
    if not await asyncio.to_thread(check_password, password, phone.password_hash):
        THROTTLE.failed(who)
        wait = THROTTLE.wait(who)
        message = "That password isn't right." + (f" Try again in {_seconds(wait)}." if wait > 0 else "")
        log.warning("Phone access: wrong password from %s", who)
        return _login_html(target, message, 401)
    THROTTLE.succeeded(who)
    token = devices().add(device_name(request.headers.get("user-agent", "")), remember)
    log.info("Phone access: %s signed in from %s", device_name(request.headers.get("user-agent", "")), who)
    response = RedirectResponse(target, status_code=303)
    # A first-party cookie set by the server (not by script), so WebKit (Chrome and Safari on iPhone)
    # keeps it for its whole life. Secure only over https: on http it would be thrown away.
    response.set_cookie(COOKIE, token, max_age=REMEMBER_DAYS * 86400 if remember else None, path="/",
                        httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return response


async def logout(request: Request) -> Response:
    devices().forget(request.cookies.get(COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE, path="/")
    return response


def _seconds(wait: float) -> str:
    wait = int(wait + 0.999)
    return f"{wait} seconds" if wait < 120 else f"{(wait + 59) // 60} minutes"


GATE_STYLE = """
:root { --bg:#F7F6F3; --surface:#fff; --border:#D6D2CB; --text:#1F1D1A; --muted:#6F6A63; --accent:#A3253B;
  --problem:#B42318; color-scheme: light dark; }
@media (prefers-color-scheme: dark) { :root { --bg:#151413; --surface:#1E1D1B; --border:#45413B; --text:#ECE8E1;
  --muted:#A39D94; --accent:#E0576F; --problem:#F07167; } }
* { box-sizing: border-box; }
body { margin:0; min-height:100vh; min-height:100dvh; display:grid; place-items:center; background:var(--bg);
  color:var(--text); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  padding: max(16px, env(safe-area-inset-top)) 16px max(16px, env(safe-area-inset-bottom)); }
main { width:100%; max-width:380px; background:var(--surface); border:1px solid var(--border); border-radius:16px;
  padding:28px 22px; }
.mark { width:44px; height:44px; border-radius:11px; background:var(--accent); color:#fff; display:grid;
  place-items:center; font: 700 24px Georgia, serif; margin-bottom:14px; }
h1 { font: 400 26px/1.2 Georgia, serif; margin:0 0 6px; }
p { margin:0 0 18px; color:var(--muted); font-size:15px; }
label { display:block; font-size:14px; font-weight:600; margin-bottom:6px; }
input[type=password] { width:100%; font-size:16px; min-height:48px; padding:10px 14px; border-radius:10px;
  border:1px solid var(--border); background:var(--bg); color:var(--text); }
.remember { display:flex; align-items:center; gap:10px; min-height:44px; margin:12px 0 16px; font-weight:400; }
.remember input { width:22px; height:22px; accent-color: var(--accent); }
button { width:100%; min-height:48px; border:0; border-radius:10px; background:var(--accent); color:#fff;
  font-size:16px; font-weight:600; }
.error { color:var(--problem); font-weight:600; }
"""  # the sign-in page's look, shared with the passcode lock's page (ui/lock.py)


GATE_DARK = """
:root { --bg:#151413; --surface:#1E1D1B; --border:#45413B; --text:#ECE8E1; --muted:#A39D94; --accent:#E0576F;
  --problem:#F07167; color-scheme: dark; }
"""  # with Settings → Appearance → Dark, whatever the phone's own setting


def gate_style() -> str:
    """The sign-in (and unlock) page's look, in the app's appearance: Dark is dark even on a phone set to light;
    Light and Match Windows follow the device."""
    return GATE_STYLE + (GATE_DARK if STATE.app_settings().appearance == "dark" else "")


def _login_html(target: str, message: str = "", status: int = 200) -> HTMLResponse:
    error = f'<p class="error" role="alert">{html.escape(message)}</p>' if message else ""
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Sign in · {html.escape(APP_NAME)}</title>
{HEAD_LINKS}
<style>{gate_style()}</style></head>
<body><main>
<div class="mark">D</div>
<h1>{html.escape(APP_NAME)}</h1>
<p>Enter the password you set on the PC (Settings, Phone and other devices).</p>
{error}
<form method="post" action="/login">
<input type="hidden" name="next" value="{html.escape(target)}">
<label for="password">Password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
<label class="remember"><input type="checkbox" name="remember" checked> Remember this phone</label>
<button type="submit">Sign in</button>
</form>
</main></body></html>"""
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


# --- Add to Home Screen ------------------------------------------------------------------------------

HEAD_LINKS = f"""<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="{html.escape(APP_NAME)}">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="theme-color" content="#F7F6F3" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#151413" media="(prefers-color-scheme: dark)">"""

# Coming back to the app (unlocking the phone, switching back to Chrome): reconnect at once rather than
# waiting for the next retry; a page restored from the back-forward cache is loaded fresh.
RECONNECT_JS = """<script>
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && window.socket && !window.socket.connected) window.socket.connect();
});
window.addEventListener('pageshow', (e) => { if (e.persisted) location.reload(); });
</script>"""


def manifest() -> JSONResponse:
    return JSONResponse({
        "name": APP_NAME, "short_name": "Workshop", "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#F7F6F3", "theme_color": "#F7F6F3",
        "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    }, media_type="application/manifest+json")


@lru_cache(maxsize=4)
def icon_png(size: int) -> bytes:
    """The app's mark (a white D on the accent colour), drawn without an image library."""
    background, ink = (0xA3, 0x25, 0x3B), (0xFF, 0xFF, 0xFF)
    samples = 3 if size <= 256 else 2
    offset = -0.025  # centre the letter

    def inside(x: float, y: float) -> bool:
        x -= offset
        if not 0.25 <= y <= 0.75 or x < 0.30:
            return False
        if x <= 0.50:
            outer = True
            hole = x >= 0.42 and 0.37 <= y <= 0.63
        else:
            outer = ((x - 0.50) / 0.25) ** 2 + ((y - 0.5) / 0.25) ** 2 <= 1
            hole = ((x - 0.50) / 0.13) ** 2 + ((y - 0.5) / 0.13) ** 2 <= 1
        return outer and not hole

    rows = []
    step = 1 / (size * samples)
    for py in range(size):
        row = bytearray(b"\x00")
        for px in range(size):
            hits = sum(inside((px * samples + i + 0.5) * step, (py * samples + j + 0.5) * step)
                       for i in range(samples) for j in range(samples))
            share = hits / samples ** 2
            row += bytes(round(b + (k - b) * share) for b, k in zip(background, ink, strict=True))
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def _png(size: int):
    async def handler(_: Request) -> Response:
        data = await asyncio.to_thread(icon_png, size)
        return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})
    return handler


def head_html() -> None:
    """On every page (see theme.apply_theme)."""
    ui.add_head_html(HEAD_LINKS + RECONNECT_JS)


# --- putting it in place ----------------------------------------------------------------------------------

def install() -> None:
    """Called with register_pages(), before the app starts."""
    app.add_middleware(PhoneGate)
    app.add_api_route("/login", login_page, methods=["GET"], include_in_schema=False)
    app.add_api_route("/login", login_submit, methods=["POST"], include_in_schema=False)
    app.add_api_route("/logout", logout, methods=["GET"], include_in_schema=False)
    app.add_api_route("/manifest.webmanifest", lambda: manifest(), methods=["GET"], include_in_schema=False)
    for path, size in (("/apple-touch-icon.png", 180), ("/apple-touch-icon-precomposed.png", 180),
                       ("/icon-192.png", 192), ("/icon-512.png", 512)):
        app.add_api_route(path, _png(size), methods=["GET"], include_in_schema=False)
    app.on_startup(_on_startup)
    app.on_shutdown(SERVER.stop)


async def _on_startup() -> None:
    # NiceGUI ties the heartbeat to reconnect_timeout; with a long timeout keep a normal heartbeat,
    # so a lost connection is noticed quickly while the page itself waits minutes for the phone.
    core.sio.eio.ping_interval, core.sio.eio.ping_timeout = 20, 20
    phone = STATE.app_settings().phone
    if phone.enabled and phone.password_hash and not await SERVER.start(phone.port):
        asyncio.get_running_loop().create_task(_try_again(phone.port))


async def _try_again(port: int, tries: int = 10, every_s: float = 2.0) -> None:
    """The copy before this one (its window just closed, see ui/main.py) may still hold the port for a moment."""
    for _ in range(tries):
        await asyncio.sleep(every_s)
        if SERVER.running or await SERVER.start(port):
            return

