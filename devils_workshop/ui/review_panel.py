"""The Reader's side panel: ask for changes to the chapter you're reading, approve the planner's plan, and the
writer makes them; then write the next chapter, all without leaving the Reader (chat/review.py)."""

from __future__ import annotations

import weakref
from pathlib import Path

from nicegui import background_tasks, ui

from devils_workshop.chat.auto import SCENES, AutoOptions
from devils_workshop.chat.review import ReviewTask, ask, make_changes
from devils_workshop.planner.review import REVISE, ChangePlan
from devils_workshop.planner.state import PlannerState, is_planner_story
from devils_workshop.project.project import Project
from devils_workshop.settings import settings_dir
from devils_workshop.ui import auto
from devils_workshop.ui.components import card, money, notice, primary, quiet, toast
from devils_workshop.ui.state import STATE

TASKS: dict[str, ReviewTask] = {}
_LEAVING: weakref.WeakSet = weakref.WeakSet()


def _key(root: Path, number: int) -> str:
    return f"{Path(root).resolve()}#{number}"


def _reload(to: str) -> None:
    client = ui.context.client
    if client in _LEAVING:
        return
    _LEAVING.add(client)
    ui.navigate.to(to)


def review_panel(project: Project, number: int) -> None:
    root = project.root
    if not (is_planner_story(root) and project.config.in_chapter_context == "independent"):
        with card("Ask for changes"):
            ui.label("Changes to this chapter are asked for on its chapter screen.").classes("dw-small")
            quiet("Open the chapter", lambda: ui.navigate.to(f"/story/chapter/{number}"), "edit")
        return
    state = PlannerState.load(root)
    key = _key(root, number)
    task = TASKS.get(key)
    here = f"/story/read?chapter={number}"

    with card("Ask for changes", "Tell the planner what to change. It plans which scenes change and how; you "
                                 "approve; the writer makes the changes. The chapter's text is replaced (History "
                                 "keeps one Undo).") as box:
        box.mark("review-panel")
        with ui.column().classes("w-full gap-2"):
            for turn in state.reviews.get(str(number), [])[-8:]:
                mine = turn["role"] == "user"
                ui.label(("You: " if mine else "Planner: ") + turn["text"]).classes("dw-small dw-pre").style(
                    "padding: 6px 10px; border-radius: 10px; "
                    + ("background: var(--dw-accent-soft)" if mine else "background: var(--dw-sunken, rgba(0,0,0,.04))"))
        raw = state.review_plans.get(str(number))
        if raw:
            _plan_view(ChangePlan.from_dict(raw))
        busy = ui.row().classes("items-center gap-2")
        failure = ui.label().classes("dw-small dw-pre").style("color: var(--dw-problem)")
        request = ui.textarea(placeholder="e.g. Make the argument in scene 3 colder; she shouldn't cry yet") \
            .props("autogrow outlined").classes("w-full").mark("review-request")
        with ui.row().classes("w-full gap-2 items-center"):
            send_button = quiet("Send", lambda: start_ask(), "send").mark("review-send")
            approve_button = primary("Approve and make the changes", lambda: start_make(), "check") \
                .mark("review-approve")
            approve_button.set_visibility(bool(raw))

    from devils_workshop.chat.auto import chapter_finished
    next_chapter = next((c.number for c in project.outline.chapters if c.number > number), None)
    if not chapter_finished(project, number):  # its missing scenes first: the next chapter follows on from them
        missing = [s.number for s in project.outline.chapter(number).scenes
                   if s.number not in (project.chapters[number].scenes or {})]
        with card(f"Chapter {number} isn't finished"):
            ui.label(f"Scene{'s' if len(missing) > 1 else ''} {', '.join(map(str, missing))} "
                     f"{'aren' if len(missing) > 1 else 'isn'}'t written yet. Each is written from its brief and "
                     "checked against the plan, following on from the scenes before it.").classes("dw-small")
            write_next = primary(f"Finish chapter {number}", lambda: start_next(), "edit").mark("review-next")
            progress = ui.label().classes("dw-small")
        next_chapter = number

        def start_next() -> None:
            if not STATE.has_a_key():
                toast("Add a key in Settings first (OpenRouter, Venice, Experiential Labs or another service)", "warning")
                return
            settings = STATE.app_settings()
            auto.start(project, AutoOptions([number], mode=SCENES, continue_short=True, plan_scenes=True,
                                            at_once=settings.at_once, scenes_together=True, pause_after_chapter=True))
            write_next.set_enabled(False)
            shown["auto"] = True
    elif next_chapter and not project.chapters.get(next_chapter):
        with card(f"Happy with chapter {number}?"):
            ui.label(f"The planner follows chapter {number} as it is now, writes chapter {next_chapter}'s scene "
                     "briefs from it, and the writer writes its scenes, each checked against the plan as written."
                     ).classes("dw-small")
            write_next = primary(f"Write chapter {next_chapter}", lambda: start_next(), "edit").mark("review-next")
            progress = ui.label().classes("dw-small")

        def start_next() -> None:
            if not STATE.has_a_key():
                toast("Add a key in Settings first (OpenRouter, Venice, Experiential Labs or another service)", "warning")
                return
            settings = STATE.app_settings()
            auto.start(project, AutoOptions([next_chapter], mode=SCENES, continue_short=True, plan_scenes=True,
                                            at_once=settings.at_once, scenes_together=True, pause_after_chapter=True))
            write_next.set_enabled(False)
            shown["auto"] = True
    else:
        progress = write_next = None

    def start_ask() -> None:
        text = (request.value or "").strip()
        if not text:
            toast("Say what should change first", "warning")
            return
        if not STATE.has_a_key():
            toast("Add a key in Settings first (OpenRouter, Venice, Experiential Labs or another service)", "warning")
            return
        current = TASKS[key] = ReviewTask(number, "ask")
        shown["running"] = True  # so the panel refreshes when it's done, however quick

        async def go() -> None:
            try:
                await ask(root, number, text, settings=STATE.app_settings(), catalog=STATE.catalog,
                          client=STATE.client(), task=current, secret=STATE.api_key())
            except Exception as exc:  # noqa: BLE001 - shown in the panel
                current.failure = str(exc)
            finally:
                current.running = False

        background_tasks.create(go(), name="review-ask")
        request.value = ""

    def start_make() -> None:
        current = TASKS[key] = ReviewTask(number, "make", activity="Updating the plan…")
        shown["running"] = True  # so the panel refreshes when it's done, however quick

        async def go() -> None:
            try:
                await make_changes(root, number, settings=STATE.app_settings(), catalog=STATE.catalog,
                                   client=STATE.client(), task=current, sizes=STATE.sizes_for(
                                       STATE.models_for(STATE.project())[0]),
                                   calibration_dir=settings_dir(), secret=STATE.api_key())
            except Exception as exc:  # noqa: BLE001 - shown in the panel
                current.failure = str(exc)
            finally:
                current.running = False

        background_tasks.create(go(), name="review-make")

    shown = {"running": bool(task and task.running), "auto": bool(auto.busy(project))}

    def update() -> None:
        if ui.context.client in _LEAVING:
            return
        current = TASKS.get(key)
        running = bool(current and current.running)
        if shown["running"] and not running:
            if current and current.kind == "make" and current.done and not current.failure:
                toast(f"Changed scene{'s' if len(current.done) > 1 else ''} {', '.join(map(str, current.done))}")
            _reload(here)
            return
        shown["running"] = running
        busy.clear()
        if running:
            with busy:
                ui.spinner(size="18px")
                ui.label(current.activity).classes("dw-small")
        failure.text = current.failure if current and not running else ""
        for element in (send_button, approve_button):
            element.set_enabled(not running)
        writing = auto.current_run(project)
        if write_next is not None:
            if shown["auto"] and not (writing and writing.running):
                _reload(f"/story/read?chapter={next_chapter}" if STATE.project().chapters.get(next_chapter)
                        else here)
                return
            shown["auto"] = bool(writing and writing.running)
            write_next.set_enabled(not shown["auto"] and not running)
            if writing and writing.running:
                now = writing.steps[-1] if writing.steps else None
                progress.text = (now.label + (f": {now.activity}" if now.activity else "")) if now else "Starting…"
            elif writing and writing.reason:
                progress.text = writing.reason
        if current and not running and current.kind == "ask" and current.cost:
            failure.text = failure.text or f"{money(current.cost)} spent on that reply"

    if task and not task.running:
        TASKS.pop(key, None)
    update()
    ui.timer(0.5, update)


def _plan_view(plan: ChangePlan) -> None:
    with ui.column().classes("w-full gap-2").mark("review-plan"):
        ui.label("The change, waiting for your approval").classes("dw-h3")
        for change in plan.scenes:
            with ui.column().classes("w-full gap-1").style("border: 1px solid var(--dw-border); border-radius: 10px; "
                                                            "padding: 8px 10px"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"Scene {change.number}").classes("dw-body")
                    ui.label("Revise its text" if change.how == REVISE else "Write it again").classes(
                        "dw-chip " + ("draft" if change.how == REVISE else "problem"))
                if change.change:
                    ui.label(change.change).classes("dw-small dw-pre")
                if change.brief:
                    with ui.expansion("Its updated brief", icon="article").classes("w-full dw-small"):
                        ui.label(change.brief).classes("dw-small dw-pre")
        if plan.later:
            notice("info", "Later chapters: " + plan.later)
        for idea in plan.suggestions:
            ui.label("Idea: " + idea).classes("dw-small").style("color: var(--dw-draft)")
