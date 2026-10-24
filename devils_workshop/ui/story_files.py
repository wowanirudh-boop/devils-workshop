"""Story files: Files · System prompt · Problems · What's sent (stories in your own files)."""

from __future__ import annotations

from nicegui import ui

from devils_workshop.project.issues import ERROR, WARNING
from devils_workshop.tools.plancheck import run_plan_check
from devils_workshop.ui.components import page, page_header
from devils_workshop.ui.files import files_panel
from devils_workshop.ui.layout import frame, need_story
from devils_workshop.ui.prompts import prompt_panel
from devils_workshop.ui.story_details import settings_panel
from devils_workshop.ui.records import problems_panel
from devils_workshop.ui.setup import your_files_panel


def story_files_page(path: str = "", tab: str = "") -> None:
    project = frame("/story/files")
    if project is None:
        need_story()
        return
    report = run_plan_check(project)
    problems = sum(1 for i in report.issues if i.severity in (ERROR, WARNING))
    tab = tab or "files"
    if tab == "sent" and not project.own_files:
        tab = "files"
    with page("wide"):
        subtitle = ("Your own files, used as they are, and what each one is for." if project.own_files else
                    "The files the model is given: outline, story bible, characters, places and more.")
        with page_header("Story files", subtitle):
            pass
        with ui.tabs(value=tab).classes("w-full").props("align=left inline-label no-caps active-color=primary "
                                                         "indicator-color=primary") as tabs:
            ui.tab("files", "Files", icon="description")
            if project.own_files:
                ui.tab("sent", "What's sent", icon="checklist")
            ui.tab("settings", "Settings", icon="tune")
            ui.tab("prompt", "Prompts", icon="chat")
            ui.tab("problems", f"Problems ({problems})" if problems else "Problems", icon="fact_check")
        ui.separator().classes("-mt-5")
        with ui.tab_panels(tabs, value=tab).classes("w-full").style("background: transparent"):
            with ui.tab_panel("files").classes("p-0 pt-2"):
                files_panel(project, path)
            if project.own_files:
                with ui.tab_panel("sent").classes("p-0 pt-2 gap-4"):
                    your_files_panel(project.root, project.config)
            with ui.tab_panel("settings").classes("p-0 pt-2 gap-4"):
                settings_panel(project)
            with ui.tab_panel("prompt").classes("p-0 pt-2 gap-4"):
                prompt_panel(project)
            with ui.tab_panel("problems").classes("p-0 pt-2 gap-3"):
                problems_panel(project)
