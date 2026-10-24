"""Starting with Windows, in the background, so the phone can reach the app after the PC restarts.

Settings → Start with Windows writes the app into the current user's Run key
(HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run), the way any app starts when you sign in; no
administrator is needed. It starts as "DevilsWorkshop.exe --background": the server only, without a window,
with phone access as set in Settings.

Only one copy serves at a time. The running one writes its local address to running.json in the app's folder;
starting the app again while it runs (from the Start menu, say) opens a window onto that copy instead of
starting a second one, and closing that window leaves the background copy running.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from devils_workshop import APP_NAME
from devils_workshop.settings import settings_dir

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DevilsWorkshop"
RUNNING_FILE = "running.json"

log = logging.getLogger(__name__)


def app_command(*args: str) -> list[str]:
    """How to start the app again: the .exe, or (from source) pythonw with the app module."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    return [str(windowless if windowless.is_file() else python), "-m", "devils_workshop", "app", *args]


def command() -> str:
    """What Windows runs at sign-in."""
    return subprocess.list2cmdline(app_command("--background"))


def launch(*args: str) -> None:
    """Start another copy of the app on its own: it goes on running when this one ends."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(app_command(*args), creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_until_stopped(timeout: float = 30.0) -> None:
    """Wait for the copy that is running to stop answering (it is closing), so this one can take its place."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and running_url(timeout=1.0):
        time.sleep(0.5)
    time.sleep(1.0)  # and for its phone port to be let go


def start_background(timeout: float = 45.0) -> str | None:
    """Start the app in the background (Settings → Run hidden) and wait for it: its address, or None."""
    launch("--background")
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        url = running_url(timeout=1.0)
        if url:
            return url
        time.sleep(0.5)
    return None


def supported() -> bool:
    return sys.platform == "win32"


def enabled() -> bool:
    """True if the app starts when you sign in to Windows."""
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except OSError:
        return False


def enable() -> None:
    """Start in the background at every sign-in (written again each time, so a moved .exe is found)."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())


def disable() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except OSError:
        pass  # not there


# --- the copy that is running -------------------------------------------------------------------------

def running_path() -> Path:
    return settings_dir() / RUNNING_FILE


def note_running(port: int, background: bool) -> None:
    running_path().write_text(json.dumps({"pid": os.getpid(), "port": port, "background": background}),
                              encoding="utf-8")


def forget_running() -> None:
    try:
        data = json.loads(running_path().read_text(encoding="utf-8"))
        if data.get("pid") == os.getpid():
            running_path().unlink()
    except (OSError, ValueError):
        pass


def running_url(timeout: float = 2.0) -> str | None:
    """The local address of a copy of the app already running on this PC, or None. A note left behind by a
    copy that stopped (or another program on its port) doesn't count: the copy must answer as this app."""
    try:
        data = json.loads(running_path().read_text(encoding="utf-8"))
        port = int(data["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    url = f"http://127.0.0.1:{port}"
    try:
        answer = httpx.get(url + "/manifest.webmanifest", timeout=timeout)
        if answer.status_code == 200 and answer.json().get("name") == APP_NAME:
            return url
    except (httpx.HTTPError, ValueError):
        pass
    return None
