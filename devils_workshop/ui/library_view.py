"""All stories: your library, in progress and completed, searched, sorted and shown as cards or a list."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from nicegui import ui

from devils_workshop.library import (SORTS, StoryMeta, library, new_version, set_completed, shelf, story_cost,
                                     versions_of)
from devils_workshop.ui.components import money, quiet, smooth_reload, toast
from devils_workshop.ui.state import STATE

VIEWS = {"cards": "Cards", "list": "List"}


def friendly_date(value: str) -> str:
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    days = (datetime.now().date() - moment.date()).days
    return "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago" if days < 7 else \
        moment.strftime("%d %b %Y").lstrip("0")


def library_section(open_story, delete_dialog, tab: str = "", sort: str = "", view: str = "", search: str = "") -> None:
    """open_story(path) opens one; delete_dialog(path, title, own) asks before deleting one."""
    items = library(STATE.app_settings().recent_projects)
    tab = tab if tab in ("progress", "completed") else "progress"
    sort = sort if sort in SORTS else "date"
    view = view if view in VIEWS else "cards"
    progress, completed = shelf(items, False, sort, search), shelf(items, True, sort, search)
    shown = completed if tab == "completed" else progress

    def go(**changes) -> None:
        wanted = {"tab": tab, "sort": sort, "view": view, "q": search, **changes}
        ui.navigate.to("/?" + urlencode({k: v for k, v in wanted.items() if v and not
                                         (k == "sort" and v == "date") and not (k == "view" and v == "cards")
                                         and not (k == "tab" and v == "progress")}))

    with ui.row().classes("w-full items-end gap-3 dw-wrap-phone"):
        with ui.tabs(value=tab, on_change=lambda e: go(tab=e.value)).props(
                "align=left inline-label no-caps active-color=primary indicator-color=primary").mark("library-tabs"):
            ui.tab("progress", f"In progress ({len(shelf(items, False))})", icon="edit_note")
            ui.tab("completed", f"Completed ({len(shelf(items, True))})", icon="task_alt")
        ui.space()
        ui.input(placeholder="Search title, genre, characters", value=search,
                 on_change=lambda e: None).props("dense outlined clearable debounce=400").classes("w-64") \
            .on("keydown.enter", lambda e: go(q=e.sender.value or "")).on("clear", lambda: go(q="")) \
            .mark("library-search")
        ui.select(SORTS, value=sort, label="Sort by", on_change=lambda e: go(sort=e.value)).classes("w-44") \
            .mark("library-sort")
        ui.toggle(VIEWS, value=view, on_change=lambda e: go(view=e.value)).mark("library-view")

    if not shown:
        ui.label("No stories match your search." if search else
                 "No completed stories yet. Mark a story completed from its menu (…) when it's done." if
                 tab == "completed" else "No stories in progress.").classes("dw-small")
        return
    if view == "list":
        with ui.element("div").classes("dw-list").mark("library-list"):
            with ui.element("div").classes("dw-library-row head"):
                for label in ("Story", "Genre", "Key characters", "Chapters", "Words", "Cost",
                              "Completed" if tab == "completed" else "Opened", ""):
                    ui.label(label)
            for meta in shown:
                _row(meta, items, open_story, delete_dialog)
    else:
        with ui.element("div").classes("grid gap-4 w-full").style(
                "grid-template-columns: repeat(auto-fill, minmax(300px, 1fr))"):
            for meta in shown:
                _card(meta, items, open_story, delete_dialog)


def _date_line(meta: StoryMeta) -> str:
    if meta.completed:
        return f"Completed {friendly_date(meta.completed_on)}" if meta.completed_on else "Completed"
    return f"Opened {friendly_date(meta.opened)}" if meta.opened else (f"Started {friendly_date(meta.started)}"
                                                                         if meta.started else "")


def _chapters(meta: StoryMeta) -> str:
    if meta.planning:
        return meta.planning
    if not meta.chapters_planned:
        return "No chapters planned yet"
    return f"{meta.chapters_written} of {meta.chapters_planned} chapters"


def _menu(meta: StoryMeta, items: list[StoryMeta], open_story, delete_dialog) -> None:
    with ui.button(icon="more_horiz", color=None).props('flat round dense aria-label="More actions"') \
            .classes("dw-quiet").on("click.stop", lambda: None).mark("story-actions"):
        with ui.menu():
            if meta.completed:
                ui.menu_item("Back to in progress", lambda: _complete(meta, False)).mark("story-reopen")
                ui.menu_item("Start a new version…", lambda: _version_dialog(meta, open_story)).mark("story-version")
            else:
                ui.menu_item("Mark as completed", lambda: _complete(meta, True)).mark("story-complete")
            for other in versions_of(items, meta):
                if other.path != meta.path:
                    ui.menu_item(f"Open version {other.version}", lambda p=other.path: open_story(p))
            ui.menu_item("Remove from this list",
                         lambda: (STATE.forget_recent(meta.path), smooth_reload())).mark("story-forget")
            ui.menu_item("Delete this story…", lambda: delete_dialog(meta.path, meta.title, _own(meta.path))) \
                .mark("story-delete")


def _own(path: str) -> bool:
    from devils_workshop.project.project import NotAProjectError, load_project
    try:
        return load_project(Path(path)).own_files
    except NotAProjectError:
        return False


def _complete(meta: StoryMeta, completed: bool) -> None:
    set_completed(meta.path, completed)
    toast(f"{meta.title}: " + ("completed" if completed else "back in progress"))
    smooth_reload()


def _version_dialog(meta: StoryMeta, open_story) -> None:
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[34rem] gap-3"):
        ui.label(f"Start a new version of {meta.title}?").classes("dw-h2")
        ui.label("A copy of its foundation (settings, plan, decisions, notes, the writer's guide, the outline and "
                 "the prompts) without its chapters, in a folder next to it. Change the plan first, or not, then "
                 "write it again. This version stays as it is.").classes("dw-small")

        def go() -> None:
            target = new_version(meta.path)
            dialog.close()
            toast(f"Version started: {target.name}")
            open_story(str(target))

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            ui.button("Start the new version", on_click=go, icon="content_copy").props("unelevated no-caps") \
                .mark("story-version-go")
    dialog.open()


def _card(meta: StoryMeta, items: list[StoryMeta], open_story, delete_dialog) -> None:
    with ui.card().classes("dw-card clickable gap-1").props("flat").on("click", lambda: open_story(meta.path)) \
            .mark("story-card"):
        with ui.row().classes("w-full items-start no-wrap gap-1"):
            ui.label(meta.title).classes("dw-serif grow").style("font-size:19px;line-height:1.3")
            if meta.version > 1 or len(versions_of(items, meta)) > 1:
                ui.label(f"Version {meta.version}").classes("dw-chip draft")
            _menu(meta, items, open_story, delete_dialog)
        if meta.genre:
            ui.label(meta.genre).classes("dw-small").style("color: var(--dw-accent-text)")
        if meta.characters:
            ui.label(", ".join(meta.characters[:4]) + ("…" if len(meta.characters) > 4 else "")).classes("dw-small")
        share = meta.chapters_written / meta.chapters_planned if meta.chapters_planned else 0
        ui.html(f'<div class="dw-bar" style="margin-top:6px"><div style="width:{share * 100:.0f}%"></div></div>',
                sanitize=False).classes("w-full")
        cost = story_cost(items, meta)  # every version together
        ui.label(" · ".join(p for p in (_chapters(meta), f"{meta.words:,} words" if meta.words else "",
                                        money(cost) if cost else "", _date_line(meta)) if p)).classes("dw-small")


def _row(meta: StoryMeta, items: list[StoryMeta], open_story, delete_dialog) -> None:
    with ui.element("div").classes("dw-library-row clickable").on("click", lambda: open_story(meta.path)) \
            .mark("story-row"):
        with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
            ui.label(meta.title).classes("dw-body").style("overflow:hidden;text-overflow:ellipsis")
            if meta.version > 1 or len(versions_of(items, meta)) > 1:
                ui.label(f"v{meta.version}").classes("dw-chip draft")
        ui.label(meta.genre or "—").classes("dw-small")
        ui.label(", ".join(meta.characters[:4]) or "—").classes("dw-small")
        ui.label(_chapters(meta)).classes("dw-small")
        ui.label(f"{meta.words:,}" if meta.words else "—").classes("dw-small")
        cost = story_cost(items, meta)
        ui.label(money(cost) if cost else "—").classes("dw-small")
        ui.label(friendly_date(meta.completed_on if meta.completed else meta.opened) or "—").classes("dw-small")
        _menu(meta, items, open_story, delete_dialog)
