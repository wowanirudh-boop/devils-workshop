"""Overview: where the story stands and the next step."""

from __future__ import annotations

from nicegui import ui

from devils_workshop.library import family_of, set_completed
from devils_workshop.llm.costs import read_costs, summarize
from devils_workshop.project.issues import ERROR, WARNING
from devils_workshop.project.project import Project
from devils_workshop.settings import PLANNER, WRITER
from devils_workshop.tools.plancheck import run_plan_check
from devils_workshop.ui.components import (
    bar,
    card,
    chip,
    model_name,
    money,
    page,
    page_header,
    plural,
    primary,
    quiet,
    secondary,
    smooth_reload,
)
from devils_workshop.ui.duplicates import duplicates_notice
from devils_workshop.ui.layout import frame, need_story
from devils_workshop.ui.state import STATE

SUBPLOT_WORDS = {"I": "introduced", "A": "active", "C": "climax", "R": "resolved"}


def chapter_state(project: Project, number: int) -> str | None:
    written = project.chapters.get(number)
    if written is None or not (written.text or written.scenes):
        return None
    return "final" if written.status == "final" else "draft"


def dashboard_page() -> None:
    project = frame("/story")
    if project is None:
        need_story()
        return
    report = run_plan_check(project)
    costs = summarize(read_costs(project.root / "logs"))
    chapters = project.outline.chapters
    words = sum(c.words for c in project.chapters.values())
    written = [c for c in chapters if chapter_state(project, c.number)]
    final = [c for c in chapters if chapter_state(project, c.number) == "final"]

    with page("wide"):
        facts = [plural(len(chapters), "chapter") + " planned", f"{len(written)} written", f"{len(final)} final"]
        if words:
            facts.append(f"{words:,} words")
        eyebrow = "Overview · Completed" if project.config.status == "completed" else "Overview"
        with page_header(project.config.title, " · ".join(facts), eyebrow=eyebrow):
            completed = project.config.status == "completed"
            quiet("Back to in progress" if completed else "Mark as completed",
                  lambda: (set_completed(str(project.root), not completed), smooth_reload()),
                  "undo" if completed else "task_alt").mark("overview-completed")                 .tooltip("Completed stories are listed under Completed on All stories")
            secondary("Read", lambda: ui.navigate.to("/story/read"), "auto_stories")
        duplicates_notice(project)

        with ui.element("div").classes("dw-cols main-side"):
            with ui.column().classes("gap-5 min-w-0"):
                next_step(project, chapters)
                chapter_list(project)
                if report.subplot_matrix and report.chapters:
                    subplots(report)
            with ui.column().classes("gap-5"):
                problems_card(report)
                versions = family_of(str(project.root))
                with card("Spending"):
                    ui.label(money(sum(v.cost for v in versions) or costs.total)).classes("dw-stat-value")
                    if len(versions) > 1:
                        ui.label(f"on this story's {len(versions)} versions; {money(costs.total)} on this one, "
                                 f"in {plural(costs.calls, 'request')}").classes("dw-small")
                    else:
                        ui.label(f"on {plural(costs.calls, 'request')} for this story").classes("dw-small")
                    quiet("See activity", lambda: ui.navigate.to("/story/activity"), "receipt_long").classes(
                        "self-start -ml-2")
                with card("Models"):
                    for role, (main, fallback) in story_models(project).items():
                        with ui.column().classes("gap-0").mark(f"overview-models-{role}"):
                            ui.label(ROLE_TITLES[role]).classes("dw-eyebrow")
                            ui.label(model_name(main)).classes("dw-body")
                            if fallback:
                                ui.label(f"falls back to {model_name(fallback)}").classes("dw-small")
                    quiet("Change in Settings", lambda: ui.navigate.to("/settings"), "tune").classes(
                        "self-start -ml-2")


ROLE_TITLES = {PLANNER: "Planner", WRITER: "Writer"}


def story_models(project: Project) -> dict[str, tuple[str, str | None]]:
    """The planner's models (for a story started with the planner), then the writer's."""
    from devils_workshop.planner.run import models
    from devils_workshop.planner.state import PlannerState, is_planner_story
    found = {}
    if is_planner_story(project.root):
        found[PLANNER] = models(PlannerState.load(project.root), STATE.app_settings())
    found[WRITER] = STATE.models_for(project)
    return found


def next_step(project: Project, chapters) -> None:
    with card(classes="gap-3 accent"):
        ui.label("Next step").classes("dw-eyebrow")
        if not chapters:
            ui.label("Plan your chapters first").classes("dw-serif").style("font-size: 22px")
            from devils_workshop.planner.state import PlannerState, is_planner_story
            if is_planner_story(project.root):
                ui.label(PlannerState.load(project.root).summary() + ". The chapters appear here as the planner "
                         "plans them.").classes("dw-small")
                with ui.row().classes("gap-2"):
                    primary("Open the planner", lambda: ui.navigate.to("/story/planner"), "psychology")
            elif project.own_files:
                ui.label("No chapter instructions were found in your files. Mark a file as 'Chapter outline' or "
                         "'Chapter 3 only'.").classes("dw-small")
                with ui.row().classes("gap-2"):
                    primary("Choose what your files are for", lambda: ui.navigate.to("/story/files?tab=sent"))
            else:
                ui.label("Add chapters to the outline: a heading like '# Chapter 1: Title' with what should "
                         "happen.").classes("dw-small")
                with ui.row().classes("gap-2"):
                    primary("Open the outline", lambda: ui.navigate.to("/story/files?path=outline.md"))
            return
        upcoming = next((c for c in chapters if chapter_state(project, c.number) is None), None)
        if upcoming is not None:
            title = f"Chapter {upcoming.number}" + (f" · {upcoming.title}" if upcoming.title else "")
            ui.label(title).classes("dw-serif").style("font-size: 22px; line-height: 1.3")
            brief = " ".join(upcoming.text.split())[:220]
            if brief:
                ui.label(brief + ("…" if len(upcoming.text) > 220 else "")).classes("dw-small")
            with ui.row().classes("gap-2 mt-1"):
                primary(f"Write chapter {upcoming.number}",
                        lambda: ui.navigate.to(f"/story/chapter/{upcoming.number}"), "edit")
                secondary("Auto-write several chapters", lambda: ui.navigate.to("/story/auto"), "bolt")
            return
        drafts = [c for c in chapters if chapter_state(project, c.number) == "draft"]
        if drafts:
            ui.label("Every chapter has a draft").classes("dw-serif").style("font-size: 22px")
            ui.label("Read them through, ask for changes where needed, and mark each one final when you're "
                     "happy.").classes("dw-small")
            with ui.row().classes("gap-2 mt-1"):
                primary("Read the story", lambda: ui.navigate.to("/story/read"), "auto_stories")
                secondary(f"Open chapter {drafts[0].number}",
                          lambda: ui.navigate.to(f"/story/chapter/{drafts[0].number}"))
            return
        ui.label("Every chapter is final").classes("dw-serif").style("font-size: 22px")
        with ui.row().classes("gap-2 mt-1"):
            primary("Read the story", lambda: ui.navigate.to("/story/read"), "auto_stories")


def chapter_list(project: Project) -> None:
    chapters = project.outline.chapters
    if not chapters:
        return
    with ui.column().classes("gap-2 w-full"):
        ui.label("Chapters").classes("dw-h2")
        with ui.element("div").classes("dw-list").mark("chapter-list"):
            for chapter in chapters:
                state = chapter_state(project, chapter.number)
                written = project.chapters.get(chapter.number)
                target = chapter.settings.get("words", project.config.chapter_words)
                with ui.element("div").classes("dw-row clickable").on(
                        "click", lambda n=chapter.number: ui.navigate.to(f"/story/chapter/{n}")):
                    ui.label(str(chapter.number)).classes("dw-chapter-num")
                    with ui.column().classes("gap-1 grow min-w-0"):
                        ui.label(chapter.title or f"Chapter {chapter.number}").classes("dw-body")
                        if written:
                            with ui.row().classes("items-center gap-3 no-wrap w-full"):
                                bar(written.words / target if target else 0, state or "").style("max-width: 220px")
                                ui.label(f"{written.words:,} of {target:,} words").classes("dw-small whitespace-nowrap")
                        else:
                            extra = f" · {plural(len(chapter.scenes), 'scene')}" if chapter.scenes else ""
                            ui.label(f"about {target:,} words{extra}").classes("dw-small")
                    chip(state)
                    ui.icon("chevron_right").classes("text-xl").style("color: var(--dw-faint)")


def problems_card(report) -> None:
    serious = [i for i in report.issues if i.severity in (ERROR, WARNING)]
    with card("Problems in your story files" if serious else "Story files"):
        if not serious:
            ui.label("No problems found.").classes("dw-small")
            quiet("Open story files", lambda: ui.navigate.to("/story/files"), "folder_open").classes(
                "self-start -ml-2")
            return
        errors = sum(1 for i in serious if i.severity == ERROR)
        ui.label(plural(len(serious), "thing") + " to look at" + (f", {errors} serious" if errors else "")) \
            .classes("dw-small")
        for issue in serious[:3]:
            with ui.row().classes("no-wrap gap-2 items-start"):
                ui.icon("error_outline" if issue.severity == ERROR else "warning_amber").style(
                    "color: var(--dw-problem)" if issue.severity == ERROR else "color: var(--dw-draft)")
                where = f"{issue.file}: " if issue.file else ""
                ui.label(where + issue.message).classes("dw-small").style("color: var(--dw-text)")
        quiet("See all", lambda: ui.navigate.to("/story/files?tab=problems"), "arrow_forward").classes(
            "self-start -ml-2")


def subplots(report) -> None:
    numbers = [r.number for r in report.chapters]
    colours = {"I": "var(--dw-writing)", "A": "var(--dw-draft)", "C": "var(--dw-problem)", "R": "var(--dw-final)"}
    with ui.column().classes("gap-2 w-full"):
        ui.label("Subplots").classes("dw-h2")
        with ui.element("div").classes("dw-list"):
            with ui.element("div").classes("dw-row"):
                ui.label("").classes("grow")
                for n in numbers:
                    ui.label(str(n)).classes("dw-small text-center").style("width: 28px")
            for name, letters in report.subplot_matrix:
                with ui.element("div").classes("dw-row"):
                    ui.label(name).classes("dw-body grow")
                    for n, letter in zip(numbers, letters):
                        colour = colours.get(letter)
                        style = (f"background:{colour}" if colour else "border:1.5px solid var(--dw-border-strong)")
                        with ui.element("div").classes("flex justify-center").style("width: 28px"):
                            ui.element("span").classes("dw-dot").style(style).tooltip(
                                f"Chapter {n}: {SUBPLOT_WORDS.get(letter, 'not started or resting')}")
            with ui.element("div").classes("dw-row"):
                with ui.row().classes("gap-4"):
                    with ui.row().classes("items-center gap-1"):
                        ui.element("span").classes("dw-dot").style("border:1.5px solid var(--dw-border-strong)")
                        ui.label("not started or resting").classes("dw-small")
                    for letter, word in SUBPLOT_WORDS.items():
                        with ui.row().classes("items-center gap-1"):
                            ui.element("span").classes("dw-dot").style(f"background:{colours[letter]}")
                            ui.label(word).classes("dw-small")
