"""The icon in the notification area (beside the clock), for when the app runs hidden (Settings → Run hidden).

With Run hidden on, the app runs in the background, without a window or a taskbar button, and only this icon
shows it is there; Windows 11 puts a new icon among the hidden ones (the ^ beside the clock) unless you drag it
out. Its menu: Open (a window onto the app; closing the window hides it again), Lock now (with a passcode, see
ui/lock.py) and Quit. Its name is a plain "Workshop", so a glance at the taskbar doesn't say what it is.

pystray runs the icon's own message loop on a thread of its own; what the menu does to the app is handed to the
app's event loop.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading

from devils_workshop import startup

log = logging.getLogger(__name__)

TOOLTIP = "Workshop"


def supported() -> bool:
    return sys.platform == "win32"


def icon_image(size: int = 64):
    """A plain icon: a dark rounded square with an open page."""
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    unit = size / 64
    draw.rounded_rectangle((2 * unit, 2 * unit, 62 * unit, 62 * unit), radius=12 * unit, fill=(63, 59, 54, 255))
    draw.rectangle((16 * unit, 14 * unit, 48 * unit, 50 * unit), fill=(236, 232, 225, 255))
    for row in range(4):
        y = (22 + row * 7) * unit
        draw.line((21 * unit, y, 43 * unit, y), fill=(111, 106, 99, 255), width=max(1, int(2 * unit)))
    return image


class Tray:
    def __init__(self) -> None:
        self.icon = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None

    @property
    def showing(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> None:
        """Show the icon. Call it on the app's event loop (its menu hands work back to it)."""
        if not supported() or self.showing:
            return
        self.loop = asyncio.get_running_loop()
        self.thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001 - the icon goes when the app does anyway
                log.exception("Couldn't remove the tray icon")
        self.icon = None
        self.thread = None

    def _run(self) -> None:
        try:
            import pystray
            self.icon = pystray.Icon("devils-workshop", icon_image(), TOOLTIP, menu=self.menu(pystray))
            log.info("Tray icon shown")
            self.icon.run()
        except Exception:  # noqa: BLE001 - the app keeps running without its icon
            log.exception("Couldn't show the tray icon")

    def menu(self, pystray):
        from devils_workshop.ui import lock
        return pystray.Menu(
            pystray.MenuItem("Open", lambda: self.open(), default=True),
            pystray.MenuItem("Lock now", lambda: self.on_app(lock.lock_now), visible=lambda _item: lock.enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: self.quit()),
        )

    def open(self) -> None:
        startup.launch()  # a new copy finds this one running and opens a window onto it (ui/main.py)

    def quit(self) -> None:
        from nicegui import app
        self.on_app(app.shutdown)

    def on_app(self, action) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(action)


TRAY = Tray()
