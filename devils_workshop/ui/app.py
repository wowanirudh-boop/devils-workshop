"""Registering the screens (docs/ui-redesign.md, "Information architecture")."""

from __future__ import annotations

from nicegui import app, ui

from devils_workshop import APP_NAME
from devils_workshop.ui import (
    activity,
    auto,
    chapter,
    dashboard,
    defaults,
    home,
    lock,
    phone_access,
    planner,
    prompts,
    reader,
    records,
    settings_page,
    setup,
    story_files,
)
from devils_workshop.ui.theme import FirstPaint, set_component_defaults


PAGE_TIMEOUT_S = 20.0


def register_pages() -> None:
    """Called once per app start (and per test), so every screen is registered fresh."""
    set_component_defaults()
    phone_access.install()
    app.add_middleware(FirstPaint)  # every page starts in the app's colour: no white flash in Dark
    lock.install()
    # A screen may wait for the model list (Settings, Planner, Auto-write): fetched fresh once a day from every
    # service, that can take longer than NiceGUI's 3 seconds, which would show an error page instead.
    page = lambda path: ui.page(path, title=APP_NAME, response_timeout=PAGE_TIMEOUT_S)  # noqa: E731
    page("/")(home.home_page)
    page("/story")(dashboard.dashboard_page)
    page("/story/chapter")(chapter.chapter_index_page)
    page("/story/chapter/{number}")(chapter.chapter_page)
    page("/story/auto")(auto.auto_page)
    page("/story/planner")(planner.planner_page)
    page("/story/read")(reader.reader_page)
    page("/story/files")(story_files.story_files_page)
    page("/story/activity")(activity.activity_page)
    page("/story/history")(records.history_page)
    page("/settings")(settings_page.settings_page)
    page("/defaults")(defaults.defaults_page)
    page("/prompts")(prompts.prompts_page)
    page("/setup")(setup.setup_page)
    # Earlier addresses, now tabs of other screens.
    page("/story/prompts")(lambda: story_files.story_files_page(tab="prompt"))
    page("/story/check")(lambda: story_files.story_files_page(tab="problems"))
    page("/story/setup")(lambda: story_files.story_files_page(tab="sent"))
    page("/story/costs")(activity.activity_page)
    page("/story/logs")(activity.activity_page)
