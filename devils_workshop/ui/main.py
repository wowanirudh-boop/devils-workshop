"""Starting the desktop app (and the --self-test used by the Windows build check).

    DevilsWorkshop.exe               open the app window
    DevilsWorkshop.exe --browser     open in your web browser instead
    DevilsWorkshop.exe --background  run without a window, for the phone (Settings → Start with Windows)
    DevilsWorkshop.exe --self-test --self-test-output result.txt
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import sys
import tempfile
import traceback
from pathlib import Path

from devils_workshop import APP_NAME, __version__
from devils_workshop.settings import settings_dir

WEBVIEW2_KEYS = [  # where Windows records the Edge WebView2 runtime
    r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
]
# pywebview's window blocks selecting text unless asked (text_select=False puts user-select: none on the page),
# which stopped you copying anything in the app window. The browser and phone never had this.
WINDOW_OPTIONS = {"text_select": True}


def window_options(app) -> dict:
    """Set the app window's options (selecting and copying text, its colour before a page shows) on NiceGUI's
    app; returns them."""
    app.native.window_args.update(WINDOW_OPTIONS, background_color=_window_colour())
    return app.native.window_args


def _window_colour() -> str:
    """The window's colour while a page loads: the page's own, so opening or moving between pages never
    flashes white in Dark."""
    from devils_workshop.ui.theme import page_colour
    try:
        from devils_workshop.settings import load_settings
        return page_colour(load_settings().appearance)
    except Exception:  # noqa: BLE001 - unreadable settings: the light page colour
        return page_colour("light")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="DevilsWorkshop", description=APP_NAME)
    parser.add_argument("--browser", action="store_true", help="open in the web browser instead of an app window")
    parser.add_argument("--port", type=int, help="port for the local server (default: any free one)")
    parser.add_argument("--background", action="store_true",
                        help="run without a window, so the phone can use it (as when started with Windows)")
    parser.add_argument("--after-close", action="store_true", help=argparse.SUPPRESS)  # see _hide_on_close
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-test-output", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    _give_windowed_app_somewhere_to_print()
    _log_to_file()
    if args.self_test:
        return self_test(args.self_test_output)

    from devils_workshop import startup

    log = logging.getLogger(__name__)
    if args.after_close:
        startup.wait_until_stopped()  # the window's copy is still shutting down: let it go first
    running = startup.running_url()
    if running:  # one copy serves at a time (the phone listener's port can only be taken once)
        if args.background:
            log.info("%s is already running at %s", APP_NAME, running)
            return 0
        log.info("%s is already running: opening a window onto it", APP_NAME)
        return open_window(running, browser=args.browser)
    if not args.background and not args.browser and _run_hidden():
        started = startup.start_background()  # Settings → Run hidden: the app runs on after the window closes
        if started:
            return open_window(started)
        log.warning("The background copy didn't start; opening the app in its own window instead")

    from nicegui import app, native, ui

    from devils_workshop.ui.app import register_pages
    from devils_workshop.ui.phone_access import RECONNECT_TIMEOUT_S
    from devils_workshop.ui.state import STATE

    use_window = not args.browser and not args.background and _window_supported()
    STATE.background = args.background
    port = args.port or native.find_open_port()
    register_pages()
    window_options(app)
    app.on_startup(lambda: startup.note_running(port, args.background))
    app.on_shutdown(startup.forget_running)
    from devils_workshop import resume
    app.on_startup(lambda: asyncio.get_running_loop().create_task(resume.carry_on()))  # runs cut off last time
    app.on_startup(lambda: asyncio.get_running_loop().create_task(_load_model_list(STATE)))
    if args.background:
        from devils_workshop.tray import TRAY
        app.on_startup(lambda: TRAY.start() if STATE.app_settings().tray else None)
        app.on_shutdown(TRAY.stop)
    elif use_window:
        app.on_shutdown(lambda: _hide_on_close(STATE.app_settings().tray))
    log.info("Starting %s %s (%s)", APP_NAME, __version__,
             "background" if args.background else "window" if use_window else "browser")
    ui.run(title=APP_NAME, native=use_window, reload=False, host="127.0.0.1",
           port=port, window_size=(1400, 900) if use_window else None,
           show_welcome_message=False, show=not use_window and not args.background, favicon="📖",
           # phones: sleeping or switching apps for a few minutes returns to the same page, and
           # viewport-fit=cover lets the page reach the screen edges (it pads for the notch itself)
           reconnect_timeout=RECONNECT_TIMEOUT_S,
           viewport="width=device-width, initial-scale=1, viewport-fit=cover")
    return 0


def open_window(url: str, browser: bool = False) -> int:
    """A window onto the copy already running (started with Windows, or hidden): closing it leaves that copy
    running. With a passcode, the window opens locked (ui/lock.py)."""
    import httpx
    try:
        httpx.post(url + "/lock", timeout=3)
    except httpx.HTTPError:
        pass
    if not browser and _window_supported():
        import webview
        webview.create_window(APP_NAME, url, width=1400, height=900, background_color=_window_colour(),
                              **WINDOW_OPTIONS)
        webview.start()
    else:
        import webbrowser
        webbrowser.open(url)
    return 0


async def _load_model_list(state) -> None:
    """The model list (from the saved copy, or fresh once a day) while the app starts, so the first screen that
    needs it (Settings, Planner, Auto-write) finds it ready."""
    try:
        if not state.catalog.models:
            await state.refresh_catalog()
    except Exception:  # noqa: BLE001 - the screens load it themselves when it isn't there
        logging.getLogger(__name__).exception("Couldn't load the model list at start-up")


def _hide_on_close(run_hidden: bool) -> None:
    """The app's own window closed. With Settings → Run hidden on (switched on while this window was open), the
    app goes on hidden: a background copy takes over once this one has stopped (--after-close)."""
    if run_hidden and sys.platform == "win32":
        from devils_workshop import startup
        logging.getLogger(__name__).info("Window closed with Run hidden on: carrying on in the background")
        startup.launch("--background", "--after-close")


def _run_hidden() -> bool:
    """Settings → Run hidden (Windows only)."""
    if sys.platform != "win32":
        return False
    try:
        from devils_workshop.settings import load_settings
        return load_settings().tray
    except Exception:  # noqa: BLE001 - unreadable settings: open as usual
        return False


def _window_supported() -> bool:
    """The app window needs pywebview and, on Windows, the Edge WebView2 runtime."""
    try:
        import webview  # noqa: F401
    except ImportError:
        return False
    if sys.platform != "win32":
        return True
    import winreg
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key in WEBVIEW2_KEYS:
            try:
                with winreg.OpenKey(hive, key) as handle:
                    version, _ = winreg.QueryValueEx(handle, "pv")
                    if version and version != "0.0.0.0":
                        return True
            except OSError:
                continue
    logging.getLogger(__name__).warning("WebView2 not found; opening in the browser instead")
    return False


def _give_windowed_app_somewhere_to_print() -> None:
    """A windowed .exe has no console; libraries that print would crash without this."""
    log_dir = settings_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = open(log_dir / "output.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = sys.stdout


def _log_to_file() -> None:
    handler = logging.handlers.RotatingFileHandler(settings_dir() / "app.log", maxBytes=2_000_000, backupCount=2,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def self_test(output: str | None) -> int:
    """Check that everything the app needs is inside the build, without opening a window."""
    lines = [f"{APP_NAME} {__version__} self-test", f"python {sys.version.split()[0]} on {sys.platform}"]
    ok = True
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DW_SETTINGS_DIR"] = str(Path(tmp) / "settings")
            from devils_workshop.context.assemble import assemble
            from devils_workshop.project.newproject import create_project
            from devils_workshop.project.project import load_project
            from devils_workshop.settings import load_settings
            from devils_workshop.tools.plancheck import run_plan_check

            root = create_project(Path(tmp) / "sample", sample=True)
            project = load_project(root)
            report = run_plan_check(project)
            lines.append(f"sample story: {len(project.entries)} entries, plan-check ok={report.ok}")
            ok &= report.ok
            request = assemble(project, 4, mode="whole")
            lines.append(f"chapter 4 request: {request.size.tokens} tokens, {len(request.messages)} messages")
            from devils_workshop.project.files import write_text
            from devils_workshop.project.own_files import settings_text

            own = Path(tmp) / "own"
            write_text(own / "World.md", "Mara Venn keeps the harbour clock.")
            write_text(own / "Outline.txt", "Chapter 1: Arrival\nShe arrives.\n\nChapter Two\nThe clock stops.\n")
            write_text(own / "Story settings.md", settings_text({"World.md": "every chapter", "Outline.txt": "chapter outline"},
                                                      "Own files", 3000))
            own_project = load_project(own)
            own_request = assemble(own_project, 2)
            lines.append(f"your own files: {len(own_project.outline.chapters)} chapters, chapter 2 request "
                         f"{own_request.size.tokens} tokens")
            ok &= len(own_project.outline.chapters) == 2 and "The clock stops." in own_request.full_text()
            from devils_workshop.chat.joins import prepare_join
            from devils_workshop.project.default_instructions import folder

            write_text(folder() / "House style.md", "DEFAULT-CHECK Short sentences.")
            with_default = assemble(load_project(root), 4, mode="whole").full_text()
            join = prepare_join(project, 2, {1: "End of one.\n\nThe last line.", 2: "Start of two.\n\nMore."})
            lines.append(f"default instructions: sent={'DEFAULT-CHECK' in with_default}; joins request "
                         f"{join.size.tokens} tokens")
            ok &= "DEFAULT-CHECK" in with_default and "<opening>" in join.messages[1].content
            from devils_workshop.tools.reading import standalone_html

            reading = standalone_html(project)
            lines.append(f"reading copy: {len(reading):,} characters")
            ok &= 'id="ch1"' in reading
            from devils_workshop.tools.pdf import _font_files, story_pdf

            book = story_pdf(project)
            pages = len(pypdf_reader(book).pages)
            lines.append(f"PDF: {len(book):,} bytes, {pages} pages, font "
                         f"{(_font_files() or {}).get('', 'built-in Times')}")
            ok &= book.startswith(b"%PDF") and pages >= 2
            from devils_workshop.project.revisions import ExportError, verify
            try:
                verify(book, "pdf", project)
                lines.append("export check: the PDF holds every chapter as written")
            except ExportError as exc:
                lines.append(f"export check FAILED: {exc}")
                ok = False
            settings = load_settings()
            lines.append(f"settings: primary={settings.primary}")

            import httpx
            import keyring
            import nicegui
            import pypdf
            import yaml

            lines.append(f"libraries: nicegui {nicegui.__version__}, httpx {httpx.__version__}, "
                         f"pyyaml {yaml.__version__}, pypdf {pypdf.__version__}")
            lines.append(f"credential store: {type(keyring.get_keyring()).__name__}")
            try:
                import webview
                lines.append(f"pywebview {getattr(webview, '__version__', '?')}, window supported: "
                             f"{_window_supported()}")
            except ImportError as exc:
                lines.append(f"pywebview missing: {exc}")
                ok = False
            from devils_workshop.ui.app import register_pages  # noqa: F401  (imports every screen)
            lines.append("screens: imported")
            from nicegui import app as nicegui_app

            selectable = window_options(nicegui_app).get("text_select") is True
            lines.append(f"app window: text can be selected and copied={selectable}")
            ok &= selectable
            import segno

            from devils_workshop.phone import check_password, hash_password
            from devils_workshop.ui.phone_access import icon_png

            hashed = hash_password("self-test", iterations=1000)
            phone_ok = check_password("self-test", hashed) and not check_password("wrong", hashed)
            phone_ok &= icon_png(180).startswith(b"\x89PNG") and "<svg" in segno.make("http://a:8730").svg_inline()
            lines.append(f"phone access: password check and icon ok={phone_ok}, segno {segno.__version__}")
            ok &= phone_ok
            import pystray

            from devils_workshop.tray import icon_image

            tray_ok = icon_image().size == (64, 64) and pystray.Icon.__module__ == "pystray._win32"
            lines.append(f"tray icon: picture and Windows backend ok={tray_ok}")
            ok &= tray_ok
            from devils_workshop.planner.webpage import node, read_page

            saved = Path(tmp) / "page.html"
            write_text(saved, "<html><body><article><h1>Self-test</h1><p>WEB-CHECK Mara climbed the forty-one iron "
                              "steps of the lighthouse at dusk and found the great lens dark for the first time in "
                              "thirty years.</p></article><nav>MENU-CHECK</nav></body></html>")
            page = read_page("https://example.com/self-test", saved=saved)  # the extractor, with the app's Node
            web_ok = "WEB-CHECK" in page.markdown and "MENU-CHECK" not in page.markdown
            lines.append(f"web page reader: {node()}, ok={web_ok}")
            ok &= web_ok
    except Exception:
        ok = False
        lines.append(traceback.format_exc())
    lines.append("RESULT: " + ("OK" if ok else "FAILED"))
    text = "\n".join(lines) + "\n"
    if output:
        Path(output).write_text(text, encoding="utf-8")
    print(text)
    return 0 if ok else 1


def pypdf_reader(data: bytes):
    """A PDF made in memory, read back (for the self-test)."""
    import io

    from pypdf import PdfReader
    return PdfReader(io.BytesIO(data))
