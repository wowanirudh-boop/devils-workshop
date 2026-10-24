"""Using the app from a phone: the password, remembered phones, wrong-password limits, addresses.

The PC stays the engine (stories, the API key, the requests); a phone opens the
same screens in its browser over home Wi-Fi or Tailscale. Nothing here touches
NiceGUI; devils_workshop/ui/phone_access.py puts it in front of every page.

- The password is stored only as a salted PBKDF2-SHA256 hash in settings.yaml.
- A phone that signs in gets a random token in a first-party, HttpOnly,
  SameSite=Lax cookie; only the token's SHA-256 is kept, in phones.json.
- The desktop window (a loopback request that didn't come through the phone
  listener) never needs the password.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from devils_workshop.project.files import read_text, write_text

ITERATIONS = 600_000
MIN_PASSWORD = 8
COOKIE = "dw_phone"
REMEMBER_DAYS = 400          # the longest a browser keeps a cookie (Chrome caps Max-Age at 400 days)
SESSION_HOURS = 12           # "remember this phone" unticked
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


# --- the password ---------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    """'pbkdf2_sha256$<iterations>$<salt>$<hash>'; a new random salt every time."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        expected = _unb64(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _unb64(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def password_problem(password: str, again: str) -> str:
    """Why this new password can't be used ('' if it can)."""
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if password != again:
        return "The two passwords don't match."
    return ""


# --- wrong-password limits --------------------------------------------------------------------

@dataclass
class _Tries:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0


class Throttle:
    """A few free tries per address, then waits that double (30 s, 1 min, 2 min ... up to 15 min).
    Every address together also has a limit, so changing address doesn't give endless tries."""

    def __init__(self, free: int = 5, overall: int = 30, window_s: float = 900.0, first_wait_s: float = 30.0,
                 longest_wait_s: float = 900.0, clock=time.monotonic):
        self.free, self.overall, self.window = free, overall, window_s
        self.first_wait, self.longest_wait = first_wait_s, longest_wait_s
        self.clock = clock
        self._tries: dict[str, _Tries] = {}
        self._lock = threading.Lock()

    def wait(self, who: str) -> float:
        """Seconds before this address may try again (0: now)."""
        now = self.clock()
        with self._lock:
            return max(0.0, max(self._get(key).locked_until for key in (who, "*")) - now)

    def failed(self, who: str) -> None:
        now = self.clock()
        with self._lock:
            for key, free in ((who, self.free), ("*", self.overall)):
                tries = self._get(key)
                tries.failures = [t for t in tries.failures if now - t < self.window] + [now]
                over = len(tries.failures) - free
                if over >= 0:
                    tries.locked_until = now + min(self.first_wait * 2 ** over, self.longest_wait)

    def succeeded(self, who: str) -> None:
        with self._lock:
            self._tries.pop(who, None)

    def _get(self, key: str) -> _Tries:
        return self._tries.setdefault(key, _Tries())


# --- remembered phones ---------------------------------------------------------------------------

class Devices:
    """Signed-in phones: the SHA-256 of each one's cookie token, with when it expires."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._items: list[dict] | None = None

    def _load(self) -> list[dict]:
        if self._items is None:
            try:
                data = json.loads(read_text(self.path)[0]) if self.path.is_file() else {}
                self._items = [d for d in data.get("devices", []) if isinstance(d, dict) and "id" in d]
            except (ValueError, OSError, AttributeError):
                self._items = []
        return self._items

    def _save(self) -> None:
        write_text(self.path, json.dumps({"devices": self._items or []}, indent=2))

    @staticmethod
    def _id(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def add(self, name: str, remember: bool, now: float | None = None) -> str:
        """A new token for a phone that just signed in (only its hash is kept)."""
        now = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        life = REMEMBER_DAYS * 86400 if remember else SESSION_HOURS * 3600
        with self._lock:
            items = [d for d in self._load() if d.get("expires", 0) > now]
            items.append({"id": self._id(token), "name": name[:80], "created": now, "last_seen": now,
                          "expires": now + life})
            self._items = items
            self._save()
        return token

    def known(self, token: str | None, now: float | None = None) -> bool:
        if not token:
            return False
        now = time.time() if now is None else now
        wanted = self._id(token)
        with self._lock:
            for item in self._load():
                if hmac.compare_digest(item["id"], wanted) and item.get("expires", 0) > now:
                    if now - item.get("last_seen", 0) > 3600:  # write at most once an hour
                        item["last_seen"] = now
                        self._save()
                    return True
        return False

    def forget(self, token: str | None) -> None:
        if not token:
            return
        wanted = self._id(token)
        with self._lock:
            self._items = [d for d in self._load() if d["id"] != wanted]
            self._save()

    def forget_all(self) -> None:
        with self._lock:
            self._items = []
            self._save()

    def count(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            return sum(1 for d in self._load() if d.get("expires", 0) > now)


def device_name(user_agent: str) -> str:
    """A rough name for the sign-in list, from the browser's User-Agent."""
    agent = user_agent or ""
    device = next((d for d in ("iPhone", "iPad", "Android", "Windows", "Macintosh", "Linux") if d in agent), "Device")
    browser = ("Chrome" if "CriOS" in agent or "Chrome" in agent
               else "Firefox" if "FxiOS" in agent or "Firefox" in agent
               else "Safari" if "Safari" in agent else "browser")
    return f"{'Mac' if device == 'Macintosh' else device} · {browser}"


# --- who is asking ------------------------------------------------------------------------------------

def _address(host: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address((host or "").split("%")[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def is_tailscale(host: str | None) -> bool:
    address = _address(host)
    return isinstance(address, ipaddress.IPv4Address) and address in TAILSCALE_NET


def is_loopback(host: str | None) -> bool:
    address = _address(host)
    return bool(address and address.is_loopback)


FORWARDED = (b"x-forwarded-for", b"forwarded", b"x-real-ip")


def is_desktop(scope: dict, phone_port: int | None) -> bool:
    """The desktop window: a loopback request that didn't arrive through the phone listener
    and wasn't passed on by a proxy (e.g. 'tailscale serve' forwarding to this PC)."""
    client = scope.get("client") or (None, None)
    if not is_loopback(client[0]):
        return False
    server = scope.get("server") or (None, None)
    if phone_port is not None and server[1] == phone_port:
        return False
    return not any(name in FORWARDED for name, _ in scope.get("headers") or [])


def cookie_from(scope: dict, name: str = COOKIE) -> str | None:
    for header, value in scope.get("headers") or []:
        if header == b"cookie":
            for part in value.decode("latin-1").split(";"):
                key, _, item = part.strip().partition("=")
                if key == name:
                    return item
    return None


# --- this PC's addresses -------------------------------------------------------------------------------

@dataclass
class Addresses:
    lan: list[str] = field(default_factory=list)        # home Wi-Fi / network addresses
    tailscale: list[str] = field(default_factory=list)  # 100.x addresses
    tailscale_name: str = ""                            # e.g. my-pc.tail1234.ts.net (MagicDNS)


def find_addresses() -> Addresses:
    """This PC's addresses that a phone could open (can take a moment: run it off the UI thread)."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # no packet is sent; this only picks the main network
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        found += [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    result = Addresses()
    for host in dict.fromkeys(found):
        address = _address(host)
        if address is None or address.is_loopback or address.is_link_local:
            continue
        if is_tailscale(host):
            result.tailscale.append(host)
        elif address.is_private:
            result.lan.append(host)
    name, ips = _tailscale_status()
    result.tailscale_name = name
    result.tailscale += [ip for ip in ips if ip not in result.tailscale]
    return result


def _tailscale_cli() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base and (Path(base) / "Tailscale" / "tailscale.exe").is_file():
                return str(Path(base) / "Tailscale" / "tailscale.exe")
    return None


def _tailscale_status() -> tuple[str, list[str]]:
    """This PC's Tailscale name and 100.x addresses, if Tailscale is installed and signed in."""
    cli = _tailscale_cli()
    if not cli:
        return "", []
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flashing up from the windowed app
        done = subprocess.run([cli, "status", "--json"], capture_output=True, timeout=4, creationflags=flags)
        me = json.loads(done.stdout.decode("utf-8", "replace") or "{}").get("Self") or {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return "", []
    name = str(me.get("DNSName") or "").rstrip(".")
    ips = [str(ip) for ip in me.get("TailscaleIPs") or [] if is_tailscale(str(ip))]
    return name, ips
