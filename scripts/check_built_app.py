"""Start the built app and check that it really works (used by the Windows build).

    python scripts/check_built_app.py dist/DevilsWorkshop.exe browser
    python scripts/check_built_app.py dist/DevilsWorkshop.exe window

browser: the home page renders, every script it loads is inside the build, the setup
         screen lists a folder of your own files, the settings page can fetch the
         model list from OpenRouter (so HTTPS works), and with phone access on, the
         phone listener answers with the sign-in page (and Add to Home Screen works).
window:  the app is still running 20 seconds after start. In window mode the app
         quits when its window process ends, so this means the window opened.
Only the standard library is used. Exit code 0 means every check passed.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PORTS = {"browser": 8770, "window": 8771}
PHONE_PORT = 8772
STARTUP_TIMEOUT_S = 90  # the one-file .exe unpacks itself first
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never send localhost through a proxy


def fetch(url: str, timeout: float = 30) -> tuple[int, str]:
    try:
        with OPENER.open(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def wait_for(base: str, app: subprocess.Popen) -> bool:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if app.poll() is not None:
            return False
        try:
            if fetch(base + "/", timeout=5)[0] == 200:
                return True
        except OSError:
            pass
        time.sleep(1)
    return False


def stop(app: subprocess.Popen, base: str) -> None:
    if app.poll() is None:
        if os.name == "nt":  # the .exe starts a second process; /T stops both
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)
        else:
            os.killpg(app.pid, signal.SIGTERM)
        try:
            app.wait(timeout=15)
        except subprocess.TimeoutExpired:
            app.kill()
    # The second process can outlive the first by a moment: wait until the app stops answering.
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            fetch(base + "/", timeout=2)
        except OSError:
            return
        time.sleep(0.5)


def check_browser(base: str, settings: Path, app: subprocess.Popen) -> list[str]:
    problems = []
    status, home = fetch(base + "/")
    if "New story" not in home:
        problems.append(f"home page (HTTP {status}) doesn't show 'New story'")
    assets = sorted(set(re.findall(r'(?:src|href)="(/_nicegui/[^"]+)"', home)))
    if not assets:
        problems.append("home page loads no scripts")
    for asset in assets:
        code = fetch(base + asset)[0]
        print(f"  {code} {asset}")
        if code != 200:
            problems.append(f"missing from the build: {asset}")
    own = settings.parent / "My Story"
    own.mkdir()
    (own / "Plot outline.md").write_text("Chapter 1: Arrival\nShe arrives.\n\nChapter 2\nThe clock stops.\n",
                                         encoding="utf-8")
    status, page = fetch(base + "/setup?folder=" + urllib.parse.quote(str(own)))
    if "Plot outline.md" not in page or "Chapters: 1–2" not in page:
        problems.append(f"setup screen (HTTP {status}) doesn't list a folder of your own files")
    else:
        print("  setup screen: lists your own files")
    defaults = settings / "Default instructions"
    defaults.mkdir(parents=True, exist_ok=True)
    (defaults / "House style.md").write_text("Short sentences.\n", encoding="utf-8")
    status, page = fetch(base + "/defaults")
    if "House style.md" not in page:
        problems.append(f"default instructions screen (HTTP {status}) doesn't list its files")
    else:
        print("  default instructions screen: lists its files")
    status, page = fetch(base + "/settings", timeout=60)
    if status != 200 or "Couldn't get the model list" in page or "couldn't be loaded from OpenRouter" in page:
        problems.append(f"settings page (HTTP {status}) couldn't get the model list from OpenRouter")
    elif not (settings / "models_cache.json").is_file():
        problems.append("settings page didn't save the model list")
    else:
        print("  settings page: model list fetched over HTTPS")
    for path in ("/manifest.webmanifest", "/apple-touch-icon.png"):
        if fetch(base + path)[0] != 200:
            problems.append(f"{path} is missing (Add to Home Screen needs it)")
    status, page = fetch(f"http://127.0.0.1:{PHONE_PORT}/story")  # redirected to the sign-in page
    if "Remember this phone" not in page:
        problems.append(f"the phone listener on port {PHONE_PORT} (HTTP {status}) doesn't show the sign-in page")
    else:
        print(f"  phone access: port {PHONE_PORT} asks a phone to sign in")
    if app.poll() is not None:
        problems.append(f"the app stopped (exit code {app.returncode})")
    return problems


def phone_access_on(settings: Path) -> None:
    """settings.yaml with phone access on (the rest stays default), as the Settings screen saves it."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", b"build-check-password", salt, 1000)
    b64 = lambda data: base64.urlsafe_b64encode(data).decode().rstrip("=")  # noqa: E731
    settings.mkdir(parents=True, exist_ok=True)
    hashed = f"pbkdf2_sha256$1000${b64(salt)}${b64(digest)}"
    (settings / "settings.yaml").write_text(
        f"phone:\n  enabled: true\n  port: {PHONE_PORT}\n  password_hash: {hashed}\n", encoding="utf-8")


def check_window(settings: Path, app: subprocess.Popen) -> list[str]:
    time.sleep(20)
    if app.poll() is not None:
        return [f"the app quit (exit code {app.returncode}): the window didn't open or closed itself"]
    log = settings / "app.log"
    if "(window)" not in (log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""):
        return ["the app started in browser mode instead of a window (see app.log)"]
    print("  window mode: still running after 20 seconds")
    return []


def main() -> int:
    exe, mode = sys.argv[1], sys.argv[2]
    port = PORTS[mode]
    base = f"http://127.0.0.1:{port}"
    # Windows may hold the app's log file open briefly after it stops; a leftover temporary
    # folder on the build machine doesn't matter, so it isn't treated as a failure.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        settings = Path(tmp) / "settings"
        env = dict(os.environ, DW_SETTINGS_DIR=str(settings), USERPROFILE=tmp, HOME=tmp)
        args = [exe, "--port", str(port)] + (["--browser"] if mode == "browser" else [])
        if mode == "browser":
            phone_access_on(settings)
        print(f"starting: {' '.join(args)}")
        app = subprocess.Popen(args, env=env, cwd=tmp, start_new_session=os.name != "nt")
        try:
            if not wait_for(base, app):
                problems = [f"the app didn't start within {STARTUP_TIMEOUT_S} s (exit code {app.poll()})"]
            elif mode == "browser":
                problems = check_browser(base, settings, app)
            else:
                problems = check_window(settings, app)
        finally:
            stop(app, base)
        for name in ("app.log", "output.log"):
            path = settings / name
            if path.is_file():
                print(f"----- {name} -----")
                print(path.read_text(encoding="utf-8", errors="replace")[-6000:])
    for problem in problems:
        print(f"PROBLEM: {problem}")
    print("RESULT: " + ("FAILED" if problems else "OK"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
