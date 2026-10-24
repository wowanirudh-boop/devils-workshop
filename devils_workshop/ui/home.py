"""All stories (home): first-run setup, starting a story, and your library (ui/library_view.py)."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

from nicegui import ui

from devils_workshop.llm.livecheck import live_check
from devils_workshop.project.file_ops import (
    move_to_recycle_bin,
    own_files_inside,
    story_folder_problem,
    trash_folder,
)
from devils_workshop.planner.output import create_planner_story
from devils_workshop.planner.state import DONE, PlannerState, is_planner_story
from devils_workshop.project.newproject import ProjectExistsError, create_project
from devils_workshop.project.project import NotAProjectError
from devils_workshop.project.story_settings import is_story_folder
from devils_workshop.settings import PLANNER, WRITER, key_place, set_api_key, settings_dir
from devils_workshop.ui.components import (
    card,
    model_name,
    notice,
    page,
    page_header,
    primary,
    quiet,
    secondary,
    toast,
    smooth_reload,
)
from devils_workshop.ui.layout import frame, native_window_available, pick_folder
from devils_workshop.ui.library_view import library_section
from devils_workshop.ui.state import STATE


def default_stories_folder() -> Path:
    documents = Path.home() / "Documents"
    return (documents if documents.is_dir() else Path.home()) / "Devil's Workshop"


def clean_path(path: str) -> str:
    """Windows' "Copy as path" adds quotes; pasted paths may have spaces around them."""
    return (path or "").strip().strip('"\'').strip()


def stories_inside(folder: Path) -> list[Path]:
    """Story folders directly inside this folder (when the folder holding your stories was picked)."""
    try:
        return sorted(p for p in folder.iterdir() if p.is_dir() and is_story_folder(p))
    except OSError:
        return []


def open_and_go(path: str) -> None:
    folder = Path(clean_path(path))
    if not clean_path(path):
        toast("Choose a folder first", "warning")
        return
    if folder.is_dir() and not is_story_folder(folder):
        inside = stories_inside(folder)
        if inside:
            choose_story_dialog(folder, inside)
        else:
            STATE.root = None  # a new story folder: set it up first
            ui.navigate.to(f"/setup?folder={quote(str(folder))}")
        return
    try:
        STATE.open_story(folder)
    except NotAProjectError as exc:
        toast(str(exc), "negative")
        return
    planning = is_planner_story(folder) and PlannerState.load(folder).stage != DONE
    ui.navigate.to("/story/planner" if planning else "/story")


# --- the page --------------------------------------------------------------------------------------

def home_page(tab: str = "", sort: str = "", view: str = "", q: str = "") -> None:
    STATE.root = None  # the sidebar shows no story here
    frame("/")
    has_key = STATE.has_a_key()
    recent = STATE.app_settings().recent_projects
    with page("medium"):
        if not has_key:
            with page_header("Welcome to Devil's Workshop",
                             "Write a novel chapter by chapter with AI models, from story files you control."):
                pass
            first_run_steps()
        else:
            with page_header("Your stories", "Pick up where you left off, or start something new." if recent
                             else "Start your first story below."):
                secondary("Open a folder", open_folder_dialog, "folder_open")

        if recent and has_key:
            library_section(open_and_go, delete_story_dialog, tab, sort, view, q)

        ui.label("Start a story" if not recent or not has_key else "Start something new").classes("dw-h2 mt-2")
        with ui.element("div").classes("grid gap-4 w-full").style(
                "grid-template-columns: repeat(auto-fit, minmax(240px, 1fr))"):
            start_card("psychology", "Plan a new story", "Brainstorm from a rough idea, files and pictures; the "
                       "planner then breaks it into chapters and scenes, ready to write.", plan_story_dialog)
            start_card("edit_note", "New story", "A blank story with templates for characters, places and an "
                       "outline.", lambda: new_story_dialog(False))
            start_card("folder_open", "Use my own files", "A folder of your own notes (any text file, PDF or Word). "
                       "You choose what each file is for.", open_folder_dialog)
            start_card("auto_stories", "Try the sample story", "A short invented mystery that shows how "
                       "everything works.", lambda: new_story_dialog(True))

        if recent and not has_key:
            ui.label("Your stories").classes("dw-h2 mt-2")
            library_section(open_and_go, delete_story_dialog, tab, sort, view, q)


def first_run_steps() -> None:
    settings = STATE.app_settings()
    with card(classes="gap-5"):
        with ui.row().classes("no-wrap gap-4 w-full"):
            _step_number("1")
            with ui.column().classes("gap-2 grow"):
                ui.label("Connect to OpenRouter").classes("dw-h3")
                ui.label("Your chapters are written by AI models reached through OpenRouter, which you pay per "
                         "use (a chapter usually costs a few cents). Create a key at openrouter.ai/keys and paste "
                         f"it here. It is kept in {key_place()}.").classes("dw-small")
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    key = ui.input("OpenRouter key (starts with sk-or-)", password=True,
                                   password_toggle_button=True).classes("grow").mark("first-run-key")

                    def save() -> None:
                        value = (key.value or "").strip()
                        if not value.startswith("sk-"):
                            toast("That doesn't look like an OpenRouter key (they start with sk-or-)", "warning")
                            return
                        try:
                            set_api_key(value)
                        except Exception as exc:
                            toast(f"Couldn't store the key: {exc}", "negative")
                            return
                        toast("Key saved")
                        smooth_reload()

                    primary("Save key", save)
        with ui.row().classes("no-wrap gap-4 w-full"):
            _step_number("2")
            with ui.column().classes("gap-1 grow"):
                ui.label("Models").classes("dw-h3")
                for role, doing in ((WRITER, "Writing"), (PLANNER, "Planning")):
                    main, fallback = settings.models_of(role)
                    also = f", falling back to {model_name(fallback)}" if fallback else ""
                    ui.label(f"{doing} with {model_name(main)}{also}.").classes("dw-small")
                ui.label("The writer and the planner each have their own models; change them any time in "
                         "Settings.").classes("dw-small")
        with ui.row().classes("no-wrap gap-4 w-full"):
            _step_number("3")
            with ui.column().classes("gap-1 grow"):
                ui.label("Start a story").classes("dw-h3")
                ui.label("Below: a new story, a folder of your own files, or the sample story to look "
                         "around first.").classes("dw-small")


def _step_number(text: str) -> None:
    ui.label(text).classes("flex-none text-center").style(
        "width:28px;height:28px;line-height:28px;border-radius:50%;background:var(--dw-accent-soft);"
        "color:var(--dw-accent-text);font-weight:600")


def start_card(icon: str, title: str, text: str, on_click) -> None:
    with ui.card().classes("dw-card clickable gap-2").props("flat").on("click", on_click) as box:
        ui.icon(icon).classes("text-2xl").style("color: var(--dw-accent)")
        ui.label(title).classes("dw-h3")
        ui.label(text).classes("dw-small")
    box.mark(f"start-{title.lower().replace(' ', '-')}")


# --- dialogs ----------------------------------------------------------------------------------------

def new_story_dialog(sample: bool) -> None:
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[34rem] gap-4"):
        ui.label("Try the sample story" if sample else "New story").classes("dw-h2")
        ui.label("A short invented mystery, The Saltmarsh Clock, with chapter 1 already written." if sample else
                 "A story folder with a style guide, story bible, outline and example character and place "
                 "files to fill in.").classes("dw-small")
        title = ui.input("Title", value="The Saltmarsh Clock" if sample else "").classes("w-full")
        if sample:
            title.disable()
        with ui.row().classes("w-full items-end no-wrap gap-2"):
            parent = ui.input("Save it in", value=str(default_stories_folder())).classes("grow")
            if native_window_available():
                async def browse() -> None:
                    chosen = await pick_folder()
                    if chosen:
                        parent.value = chosen
                secondary("Browse…", browse)

        def create() -> None:
            name = (title.value or "").strip()
            if not name:
                toast("Give the story a title first", "warning")
                return
            folder_name = "".join(c for c in name if c not in '<>:"/\\|?*').strip() or "Story"
            target = Path(parent.value or default_stories_folder()) / folder_name
            try:
                create_project(target, sample=sample, title=None if sample else name)
            except ProjectExistsError:
                if sample and is_story_folder(target):
                    dialog.close()
                    open_and_go(str(target))  # the sample was created before: just open it
                    return
                toast(f"There is already a folder called {folder_name} there", "negative")
                return
            dialog.close()
            open_and_go(str(target))

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary("Open the sample" if sample else "Create story", create)
    dialog.open()


def plan_story_dialog() -> None:
    """A new story folder for the planner: it fills it with the plan, chapters and scenes."""
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[34rem] gap-4"):
        ui.label("Plan a new story").classes("dw-h2")
        ui.label("You brainstorm the story with the planner from a rough idea, files and reference pictures. When "
                 "you approve the plan, it designs the chapters and writes a brief for every scene into this "
                 "folder, ready to write.").classes("dw-small")
        title = ui.input("Working title").classes("w-full").mark("plan-title")
        with ui.row().classes("w-full items-end no-wrap gap-2"):
            parent = ui.input("Save it in", value=str(default_stories_folder())).classes("grow") \
                .mark("plan-parent")
            if native_window_available():
                async def browse() -> None:
                    chosen = await pick_folder()
                    if chosen:
                        parent.value = chosen
                secondary("Browse…", browse)
        ui.label("A new folder named after the title is made there for the plan and the story files.") \
            .classes("dw-small")

        def create() -> None:
            name = (title.value or "").strip()
            if not name:
                toast("Give the story a working title first", "warning")
                return
            folder_name = "".join(c for c in name if c not in '<>:"/\\|?*').strip() or "Story"
            target = Path(clean_path(parent.value) or default_stories_folder()) / folder_name
            try:
                create_planner_story(target, name)
            except ProjectExistsError:
                toast(f"There is already a folder called {folder_name} there", "negative")
                return
            except OSError as exc:
                toast(f"Couldn't make the folder: {exc}", "negative")
                return
            dialog.close()
            open_and_go(str(target))

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary("Start planning", create, "psychology").mark("plan-create")
    dialog.open()


def open_folder_dialog() -> None:
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[36rem] gap-4"):
        ui.label("Open a folder").classes("dw-h2")
        ui.label("A story you made here, or a folder of your own story files (any text file, PDF or Word).").classes(
            "dw-small")
        with ui.row().classes("w-full items-end no-wrap gap-2"):
            path = ui.input("Folder").classes("grow").mark("folder-path")
            if native_window_available():
                async def browse() -> None:
                    chosen = await pick_folder()
                    if chosen:
                        path.value = chosen
                secondary("Browse…", browse)
        if not native_window_available():
            ui.label("Tip: in File Explorer, Shift + right-click the folder → Copy as path, then paste it here.") \
                .classes("dw-small")
        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary("Open", lambda: (dialog.close(), open_and_go(path.value or ""))).mark("open-folder")
    dialog.open()


def delete_story_dialog(path: str, title: str, own: bool) -> None:
    """Move a story's folder to the Recycle Bin (e.g. one made by mistake), or just take it off the list."""
    folder = Path(path)
    problem = story_folder_problem(folder)
    yours = own_files_inside(folder) if own and not problem else []
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[34rem] gap-3"):
        ui.label(f"Delete {title}?").classes("dw-h2")
        if problem:
            notice("problem", problem + " You can still take it off this list; nothing is deleted then.")
        else:
            ui.label(f"The folder {folder} and everything in it go to the Recycle Bin. You can get it back "
                     "from there until the Recycle Bin is emptied." if sys.platform == "win32" else
                     f"The folder {folder} and everything in it move to {trash_folder()}, where you can get it "
                     "back until you empty that folder.").classes("dw-small")
            if yours:
                shown = ", ".join(yours[:5]) + (f" and {len(yours) - 5} more" if len(yours) > 5 else "")
                notice("warning", f"That includes your own files in it ({len(yours)}): {shown}. To keep them, "
                                  "use 'Just remove it from the list' instead.")

        def forget() -> None:
            STATE.forget_recent(path)
            dialog.close()
            toast(f"{title} is off the list; its folder is still there")
            smooth_reload()

        def go() -> None:
            try:
                moved = move_to_recycle_bin(folder)
            except OSError as exc:
                toast(f"Couldn't delete it: {exc}", "negative")
                return
            STATE.forget_recent(path)
            if STATE.root and Path(STATE.root).resolve() == folder.resolve():
                STATE.root = None
            dialog.close()
            toast(f"{title} was moved to {moved}" if moved else f"{title} was moved to the Recycle Bin")
            smooth_reload()

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            if problem:
                primary("Remove it from the list", forget)
            else:
                secondary("Just remove it from the list", forget).mark("story-forget-only")
                primary("Move to Recycle Bin", go, "delete_outline").props("color=negative").mark("story-delete-go")
    dialog.open()


def choose_story_dialog(folder: Path, stories: list[Path]) -> None:
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[36rem] gap-3"):
        ui.label("This folder holds these stories").classes("dw-h2")
        with ui.element("div").classes("dw-list"):
            for story in stories:
                with ui.element("div").classes("dw-row"):
                    ui.label(story.name).classes("grow")
                    primary("Open", lambda s=story: (dialog.close(), open_and_go(str(s)))).mark("open-story")
        with ui.row().classes("w-full justify-end"):
            quiet("Use this folder itself for a story", lambda: (
                dialog.close(), ui.navigate.to(f"/setup?folder={quote(str(folder))}")))
    dialog.open()


async def check_connection(report_area) -> None:
    """Shared by Settings: one tiny request, answered in plain words."""
    report_area.clear()
    with report_area:
        with ui.row().classes("items-center gap-2"):
            ui.spinner(size="18px")
            ui.label("Checking… (one tiny request, well under $0.01)").classes("dw-small")
    result = await live_check(STATE.client().openrouter, STATE.app_settings(), settings_dir() / "logs",
                              STATE.api_key())
    text = result.text()
    report_area.clear()
    with report_area:
        ok = result.ok
        notice("success" if ok else "problem",
               "Connected. Your key works and the models answered." if ok else
               "The connection test didn't pass. The details below say what went wrong.")
        with ui.expansion("Technical details").classes("w-full"):
            ui.label(text).classes("dw-raw")
