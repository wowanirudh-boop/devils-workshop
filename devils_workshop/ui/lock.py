"""The passcode lock: a code that closes this PC's screens (the app window, or a browser on this PC) until it is
typed again.

Settings → Passcode lock sets the code (only a scrambled form of it is saved, as for the phone password) and how
long the app may go unused before it locks. It locks when the app starts, whenever its window is opened (ui/main.py
asks before opening one), after that time without use, and when you choose Lock now (in the sidebar, or on the icon
in the notification area). While it is locked, every page on this PC is the unlock page; writing and planning that
are under way carry on. Phones aren't affected: they have their own password (Settings → Phone and other devices).

It keeps your screens private from someone at your PC; it doesn't encrypt the story files on the disk.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import time
import urllib.parse

from nicegui import app, ui
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from devils_workshop import APP_NAME
from devils_workshop.phone import Throttle, check_password, hash_password, is_desktop
from devils_workshop.ui.state import STATE

log = logging.getLogger(__name__)

MIN_CODE = 4
OPEN_PATHS = {"/unlock", "/manifest.webmanifest", "/favicon.ico", "/icon-192.png", "/icon-512.png",
              "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"}
CHECK_EVERY_S = 10.0
THROTTLE = Throttle()
WHO = "this PC"  # one person at one PC: the wrong-code limit is shared by every window

_locked = True            # locked when the app starts (if there is a code)
_last_active = time.monotonic()
clock = time.monotonic    # tests replace it


def enabled() -> bool:
    return bool(STATE.app_settings().lock.code_hash)


def is_locked() -> bool:
    return enabled() and _locked


def touch() -> None:
    """Someone is using the app on this PC."""
    global _last_active
    _last_active = clock()


def lock_now() -> None:
    """Close every screen on this PC (if there is a code). Call it on the app's event loop."""
    global _locked
    if not enabled() or _locked:
        return
    _locked = True
    log.info("Locked")
    for client in list(app.clients()):  # open pages show the unlock page
        request = getattr(client, "request", None)
        if request is not None and _desktop(request.scope) and client.has_socket_connection:
            with contextlib.suppress(Exception):  # a page that has gone away: it meets the lock when it comes back
                client.run_javascript("location.reload()")


def unlock() -> None:
    global _locked
    _locked = False
    touch()
    log.info("Unlocked")


def check_idle() -> None:
    """Lock when the app has gone unused on this PC for longer than Settings allows."""
    minutes = STATE.app_settings().lock.idle_minutes
    if enabled() and not _locked and minutes and clock() - _last_active > minutes * 60:
        lock_now()


def code_problem(code: str, again: str) -> str:
    """Why this new code can't be used ('' if it can)."""
    if len(code) < MIN_CODE:
        return f"Use at least {MIN_CODE} characters (digits are fine)."
    if code != again:
        return "The two codes don't match."
    return ""


def set_code(code: str, iterations: int | None = None) -> None:
    """Save a new code. The app stays open for you: you are the one setting it."""
    settings = STATE.app_settings()
    settings.lock.code_hash = hash_password(code) if iterations is None else hash_password(code, iterations)
    STATE.save_settings()
    unlock()


def remove_code() -> None:
    STATE.app_settings().lock.code_hash = ""
    STATE.save_settings()
    unlock()


def _desktop(scope: dict) -> bool:
    from devils_workshop.ui.phone_access import SERVER  # phone_access asks this module at every request
    return is_desktop(scope, SERVER.listening_port())


def locked_out(scope: dict) -> bool:
    """True for a request from this PC that the lock keeps out."""
    return scope.get("path") not in OPEN_PATHS and _desktop(scope) and is_locked()


async def refuse(scope, receive, send) -> None:
    """What a request kept out by the lock gets: the unlock page for a page, nothing for the rest."""
    if scope["type"] == "websocket":
        await receive()  # websocket.connect
        await send({"type": "websocket.close", "code": 1008})
        return
    path = scope.get("path") or "/"
    if scope.get("method") == "GET" and not path.startswith("/_nicegui"):
        query = scope.get("query_string", b"").decode("latin-1")
        target = path + ("?" + query if query else "")
        response: Response = RedirectResponse("/unlock?next=" + urllib.parse.quote(target, safe=""), status_code=303)
    else:
        response = PlainTextResponse("Locked.", status_code=401)
    await response(scope, receive, send)


# --- the unlock page -------------------------------------------------------------------------------------

async def unlock_page(request: Request) -> Response:
    from devils_workshop.ui.phone_access import _safe_next
    target = _safe_next(request.query_params.get("next"))
    if not is_locked():
        return RedirectResponse(target, status_code=303)
    return _unlock_html(target)


async def unlock_submit(request: Request) -> Response:
    from devils_workshop.ui.phone_access import _safe_next
    form = urllib.parse.parse_qs((await request.body()).decode("utf-8", "replace"))
    code = (form.get("code") or [""])[0]
    target = _safe_next((form.get("next") or ["/"])[0])
    if not is_locked():
        return RedirectResponse(target, status_code=303)
    if not _desktop(request.scope):
        return _unlock_html(target, "This PC's lock can only be opened on this PC.", 403)
    wait = THROTTLE.wait(WHO)
    if wait > 0:
        return _unlock_html(target, f"Too many wrong codes. Try again in {_seconds(wait)}.", 429)
    if not await asyncio.to_thread(check_password, code, STATE.app_settings().lock.code_hash):
        THROTTLE.failed(WHO)
        wait = THROTTLE.wait(WHO)
        log.warning("Lock: wrong code")
        return _unlock_html(target, "That code isn't right." + (f" Try again in {_seconds(wait)}." if wait > 0
                                                                 else ""), 401)
    THROTTLE.succeeded(WHO)
    unlock()
    return RedirectResponse(target, status_code=303)


async def lock_request(request: Request) -> Response:
    """POST /lock: lock now (this PC only; the window asks for it before it opens)."""
    if _desktop(request.scope):
        lock_now()
    return PlainTextResponse("", status_code=204)


async def active_request(request: Request) -> Response:
    """POST /lock/active: a page on this PC is being used (sent at most every 20 seconds)."""
    if _desktop(request.scope) and not is_locked():
        touch()
    return PlainTextResponse("", status_code=204)


def _seconds(wait: float) -> str:
    wait = int(wait + 0.999)
    return f"{wait} seconds" if wait < 120 else f"{(wait + 59) // 60} minutes"


def _unlock_html(target: str, message: str = "", status: int = 200) -> HTMLResponse:
    from devils_workshop.ui.phone_access import HEAD_LINKS, gate_style
    error = f'<p class="error" role="alert">{html.escape(message)}</p>' if message else ""
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Locked</title>
{HEAD_LINKS}
<style>{gate_style()}</style></head>
<body><main>
<div class="mark">D</div>
<h1>Locked</h1>
<p>Type your passcode to open {html.escape(APP_NAME)}.</p>
{error}
<form method="post" action="/unlock">
<input type="hidden" name="next" value="{html.escape(target)}">
<label for="code">Passcode</label>
<input id="code" name="code" type="password" autocomplete="off" required autofocus>
<p></p>
<button type="submit">Unlock</button>
</form>
</main></body></html>"""
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


# --- on every page of this PC ------------------------------------------------------------------------------

# Tells the app the page is in use (at most every 20 s), and reloads it if the app has locked meanwhile.
ACTIVE_JS = """<script>
(() => {
  let last = 0;
  const seen = () => {
    const now = Date.now();
    if (now - last < 20000) return;
    last = now;
    fetch('/lock/active', {method: 'POST'}).then(r => { if (r.status === 401) location.reload(); }).catch(() => {});
  };
  ['mousemove', 'mousedown', 'keydown', 'wheel', 'touchstart'].forEach(e => addEventListener(e, seen, {passive: true}));
  seen();
})();
</script>"""


def on_page() -> None:
    """Called by every screen (ui/layout.frame) on this PC while there is a code."""
    ui.add_body_html(ACTIVE_JS)


async def _watch_idle() -> None:
    while True:
        await asyncio.sleep(CHECK_EVERY_S)
        with contextlib.suppress(Exception):  # the lock's timer must never stop
            check_idle()


def install() -> None:
    """Called with register_pages(), before the app starts."""
    app.add_api_route("/unlock", unlock_page, methods=["GET"], include_in_schema=False)
    app.add_api_route("/unlock", unlock_submit, methods=["POST"], include_in_schema=False)
    app.add_api_route("/lock", lock_request, methods=["POST"], include_in_schema=False)
    app.add_api_route("/lock/active", active_request, methods=["POST"], include_in_schema=False)
    app.on_startup(lambda: asyncio.get_running_loop().create_task(_watch_idle()))
