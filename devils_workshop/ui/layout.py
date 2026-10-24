"""The frame around every screen: the sidebar with the open story and its sections."""

from __future__ import annotations

import html

from nicegui import app, ui

from devils_workshop import APP_NAME
from devils_workshop.project.project import NotAProjectError, Project
from devils_workshop.ui.phone_access import from_phone
from devils_workshop.ui.state import STATE
from devils_workshop.ui.theme import apply_theme

NAV = [  # (label, path, icon); paths that belong to a section are matched by prefix
    ("Overview", "/story", "space_dashboard"),
    ("Chapters", "/story/chapter", "article"),
    ("Auto-write", "/story/auto", "bolt"),
    ("Read", "/story/read", "auto_stories"),
    ("Story files", "/story/files", "folder_open"),
]
NAV_MORE = [
    ("Activity", "/story/activity", "receipt_long"),
    ("History", "/story/history", "history"),
]


def frame(active: str = "") -> Project | None:
    """Draw the sidebar (with the open story's sections) and return the open story, if any."""
    apply_theme(STATE.app_settings().appearance)
    try:
        project = STATE.project()
    except NotAProjectError:
        STATE.root, project = None, None

    # Wide windows show the sidebar (an icon rail below 1200px); phones get a top bar whose menu
    # button opens it over the page (Quasar's own behaviour below the 600px breakpoint).
    with ui.left_drawer(fixed=True).classes("dw-sidebar").props("width=236 breakpoint=600") as drawer:
        with ui.element("div").classes("dw-brand").on("click", lambda: ui.navigate.to("/")):
            ui.html('<div class="dw-brand-mark">D</div>', sanitize=False)
            ui.label(APP_NAME).classes("dw-brand-name")
        if project:
            _story_block(project)
            from devils_workshop.planner.state import is_planner_story
            if is_planner_story(project.root):
                _nav("Planner", "/story/planner", "psychology", active == "/story/planner")
            for label, path, icon in NAV:
                _nav(label, path, icon, active == path)
            ui.label("Records").classes("dw-nav-group")
            for label, path, icon in NAV_MORE:
                _nav(label, path, icon, active == path)
            _running_pill(project)
        ui.space()
        ui.separator().classes("my-2")
        _nav("All stories", "/", "library_books", active == "/")
        _nav("Default instructions", "/defaults", "rule", active == "/defaults")
        _nav("Prompts", "/prompts", "notes", active == "/prompts")
        _nav("Settings", "/settings", "settings", active == "/settings")
        if from_phone():
            _nav("Sign out of this device", "/logout", "logout", False)
        else:
            from devils_workshop.ui import lock
            if lock.enabled():
                lock.on_page()
                with ui.button("Lock now", icon="lock", on_click=lock.lock_now, color=None) \
                        .props("flat no-caps align=left").classes("dw-nav").mark("lock-now"):
                    ui.tooltip("Lock now").props("delay=800 anchor='center right' self='center left'")
    _top_bar(drawer, project, active)
    return project


def _top_bar(drawer, project: Project | None, active: str) -> None:
    """Only shown on phones (see theme.py): the menu button, then where you are."""
    names = {path: label for label, path, _ in NAV + NAV_MORE}
    names.update({"/": "All stories", "/defaults": "Default instructions", "/prompts": "Prompts",
                  "/settings": "Settings", "/story/planner": "Planner"})
    with ui.element("div").classes("dw-topbar"):
        ui.button(icon="menu", on_click=drawer.toggle, color=None).props('flat round aria-label="Menu"') \
            .classes("dw-quiet").mark("menu")
        with ui.column().classes("gap-0 min-w-0 grow"):
            if project and active.startswith("/story"):
                ui.label(project.config.title).classes("dw-topbar-story")
            ui.label(names.get(active, APP_NAME)).classes("dw-topbar-title")


def _nav(label: str, path: str, icon: str, active: bool) -> None:
    with ui.button(label, icon=icon, on_click=lambda p=path: ui.navigate.to(p), color=None) \
            .props("flat no-caps align=left").classes("dw-nav" + (" active" if active else "")):
        ui.tooltip(label).props("delay=800 anchor='center right' self='center left'")  # for the narrow sidebar


def _story_block(project: Project) -> None:
    planned = len(project.outline.chapters)
    written = sum(1 for c in project.chapters.values() if c.text or c.scenes)
    words = sum(c.words for c in project.chapters.values())
    with ui.element("div").classes("dw-story-block cursor-pointer").on("click", lambda: ui.navigate.to("/story")):
        ui.label(project.config.title).classes("dw-story-title")
        share = written / planned if planned else 0
        ui.html(f'<div class="dw-bar" style="margin:10px 0 6px"><div style="width:{share * 100:.0f}%"></div></div>',
                sanitize=False).classes("w-full")
        facts = f"{written} of {planned} chapters" if planned else "No chapters planned yet"
        ui.label(facts + (f" · {words:,} words" if words else "")).classes("dw-small")


def _running_pill(project: Project) -> None:
    from devils_workshop.ui import auto  # the auto-write screen imports this module

    run = auto.current_run(project)
    if run is None or not run.running:
        return
    with ui.element("div").classes("dw-running-pill").on("click", lambda: ui.navigate.to("/story/auto")) as pill:
        ui.spinner(size="14px")
        label = ui.label()

    def update() -> None:
        writing = run.writing
        label.text = "Auto-writing · " + (writing[0].label if len(writing) == 1 else
                                           f"{len(writing)} at the same time" if writing else "starting")
        pill.set_visibility(run.running)

    update()
    ui.timer(1.5, update)


def page_title(text: str, subtitle: str = "") -> None:
    """Kept for screens that only need a plain title."""
    ui.label(text).classes("dw-title")
    if subtitle:
        ui.label(subtitle).classes("dw-subtitle")


def need_story() -> None:
    """Shown on story screens when no story is open."""
    from devils_workshop.ui.components import empty_state, page, primary

    with page("narrow"):
        with empty_state("menu_book", "No story is open.", "Choose a story, or start a new one."):
            primary("Choose a story", lambda: ui.navigate.to("/"))


def prose(text: str):
    """Prose shown as paragraphs, safely escaped."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    body = "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs)
    return ui.html(f'<div class="dw-prose">{body or "<p class=dw-muted>Nothing written yet.</p>"}</div>',
                   sanitize=False)


def mono(text: str):
    """Monospace text that wraps long lines (requests, reports, logs)."""
    return ui.label(text).classes("dw-raw")


def native_window_available() -> bool:
    """The PC's own file dialogs; never for a phone (the dialog would open on the PC's screen)."""
    return getattr(app.native, "main_window", None) is not None and not from_phone()


async def pick_files(extensions: tuple[str, ...] = (), description: str = "") -> list[str] | None:
    """A native file picker (several files) in the desktop window: the chosen paths ([] if cancelled),
    or None if it couldn't be shown. With no extensions, any file can be chosen."""
    if from_phone():
        return None
    try:
        import webview
        window = app.native.main_window
        if window is None:
            return None
        kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None) or getattr(webview, "OPEN_DIALOG")
        pattern = ";".join(f"*{ext}" for ext in extensions)
        types = ((f"{description} ({pattern})",) if extensions else ()) + ("All files (*.*)",)
        result = await window.create_file_dialog(kind, allow_multiple=True, file_types=types)
        return list(result or [])
    except Exception:
        return None


async def pick_folder() -> str | None:
    """A native folder picker in the desktop window; None if unavailable (e.g. browser mode)."""
    if from_phone():
        return None
    try:
        import webview
        window = app.native.main_window
        if window is None:
            return None
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or getattr(webview, "FOLDER_DIALOG")
        result = await window.create_file_dialog(kind)
        return result[0] if result else None
    except Exception:
        return None
