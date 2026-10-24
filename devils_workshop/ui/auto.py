"""Auto-write: write a range of chapters, whole or scene by scene, without review stops,
several requests at the same time.

The run continues in the background while you look at other screens (for
example, reading chapters as they finish). Closing the app stops it; nothing
from an unfinished request is saved.
"""

from __future__ import annotations

import math
import time

from nicegui import background_tasks, ui

from devils_workshop.chat.auto import (
    AS_SET,
    DONE,
    FAILED,
    FINISHED,
    RUNNING,
    SCENES,
    SKIPPED,
    WAITING,
    WHOLE,
    AutoOptions,
)
from devils_workshop.chat.auto import AutoRun
from devils_workshop.chat.session import ChapterChat
from devils_workshop import eta
from devils_workshop.context.assemble import AssemblyError, assemble
from devils_workshop.project.project import Project
from devils_workshop.settings import settings_dir
from devils_workshop.planner.state import IN_ORDER, TOGETHER
from devils_workshop.ui.components import (
    card,
    model_name,
    money,
    notice,
    page,
    page_header,
    plural,
    primary,
    quiet,
    secondary,
    smooth_reload,
    tail,
)
from devils_workshop.ui.duplicates import duplicates_notice
from devils_workshop.ui.layout import frame, need_story
from devils_workshop.ui.state import STATE

CURRENT: dict[str, AutoRun] = {}  # story folder -> its latest run
STATUS_TEXT = {RUNNING: "Writing", FINISHED: "Finished", "stopped": "Stopped", FAILED: "Stopped by a problem"}
STEP_ICONS = {DONE: ("check_circle", "var(--dw-final)"), SKIPPED: ("remove_circle_outline", "var(--dw-faint)"),
              WAITING: ("hourglass_empty", "var(--dw-faint)"),
              "writing": ("autorenew", "var(--dw-writing)"), "stopped": ("pause_circle", "var(--dw-draft)"),
              FAILED: ("error", "var(--dw-problem)")}


def current_run(project: Project) -> AutoRun | None:
    return CURRENT.get(str(project.root))


def busy(project: Project) -> bool:
    run = current_run(project)
    return run is not None and run.running


def start(project: Project, options: AutoOptions) -> AutoRun:
    run = AutoRun(project.root, options, settings=STATE.app_settings(), catalog=STATE.catalog,
                  client=STATE.client, secret=STATE.api_key(), calibration_dir=settings_dir(),
                  sizes=STATE.sizes_for, estimate=STATE.estimate_cost, name=model_name)
    CURRENT[str(project.root)] = run
    background_tasks.create(run.run(), name="auto-write")
    return run


def complete(project: Project, number: int) -> bool:
    """Written, and in scene mode with every outlined scene written."""
    written = project.chapters.get(number)
    if written is None or not (written.text or written.scenes):
        return False
    return not (written.scenes and ChapterChat.load(project.root, number).next_scene(project))


async def auto_page() -> None:
    project = frame("/story/auto")
    if project is None:
        need_story()
        return
    run = current_run(project)
    if run is not None:
        progress(project, run)
        return
    if not STATE.catalog.models:
        await STATE.refresh_catalog()  # prices, for the estimate
    form(project)


# --- starting a run -----------------------------------------------------------------------------

MODE_HELP = {
    AS_SET: "Each chapter the way it's set in the outline (whole, or scene by scene).",
    WHOLE: "Each chapter in one go.",
    SCENES: "Each chapter scene by scene.",
}
AT_ONCE = {1: "1 (one after another)", **{n: str(n) for n in (2, 3, 4, 5, 6, 8, 10, 12, 16, 20)}}


def form(project: Project) -> None:
    numbers = [c.number for c in project.outline.chapters]
    settings = STATE.app_settings()
    with page("narrow"):
        with page_header("Auto-write", "Writes chapters without stopping for review, several at the same time. "
                                       "Every request is still saved, with its cost, in Activity."):
            pass
        duplicates_notice(project)
        if not numbers:
            notice("info", "There are no chapters to write yet.")
            return
        first = next((n for n in numbers if not complete(project, n)), numbers[0])
        labels = {c.number: f"{c.number}. {c.title}" if c.title else str(c.number) for c in project.outline.chapters}
        with card("Which chapters"):
            with ui.row().classes("gap-3 w-full no-wrap dw-wrap-phone"):
                start_at = ui.select(labels, value=first, label="From").classes("flex-1 dw-full-phone") \
                    .mark("auto-from")
                end_at = ui.select(labels, value=numbers[-1], label="To").classes("flex-1 dw-full-phone") \
                    .mark("auto-to")
            rewrite = ui.checkbox("Also write chapters that already have text (their current text is kept as an "
                                  "earlier version)")
        with card("How"):
            mode = ui.toggle({AS_SET: "As set in the outline", WHOLE: "Whole chapters", SCENES: "Scene by scene"},
                             value=AS_SET)
            mode_help = ui.label(MODE_HELP[AS_SET]).classes("dw-small")
            with ui.column().classes("gap-1 w-full") as scene_options:
                independent = project.config.in_chapter_context == "independent"
                planned = _planner_state(project)
                if planned is not None:  # the planner's scenes: in order and checked one by one, or all at once
                    together = ui.checkbox("Write a chapter's scenes at the same time, then check them together",
                                           value=planned.scene_order == TOGETHER).mark("auto-together")
                else:
                    together = ui.checkbox("Write a chapter's scenes at the same time, then smooth the joins between "
                                           "them", value=settings.scenes_together or independent).mark("auto-together")
                    if independent:
                        together.disable()
                together_help = ui.label().classes("dw-small").style("margin-left: 34px")
                plan = ui.checkbox("If a chapter has no scenes yet, let the model plan them first", value=True)
            carry_on = ui.checkbox("If a reply comes back short, ask the model to carry on once", value=True)
            pause = ui.checkbox("Pause after each chapter, so I can read it and ask for changes first",
                                value=independent).mark("auto-pause")
            ui.label("Recommended for stories made with the planner: each chapter's briefs are written from what "
                     "was actually written before it.").classes("dw-small").style("margin-left: 34px")
        with card("Speed"):
            at_once = ui.select(AT_ONCE, value=settings.at_once, label="Requests at the same time") \
                .classes("w-64").mark("auto-at-once")
            ui.label("More at once finishes sooner and costs the same. Chapters never see each other's text, so "
                     "writing them side by side changes nothing in them; earlier chapters go first.") \
                .classes("dw-small")
        with card("Spending limit"):
            estimate_label = ui.label().classes("dw-body")
            cap = ui.number("Stop before spending more than ($)", min=0.01, step=0.5, format="%.2f") \
                .classes("w-72")
            ui.label("Leave it empty for no limit. The run stops before any request that could take it past the "
                     "limit, counting the requests already under way.").classes("dw-small")

            def chosen() -> list[int]:
                low, high = sorted((start_at.value, end_at.value))
                return [n for n in numbers if low <= n <= high]

            def update_estimate(set_cap: bool = False) -> None:
                mode_help.text = MODE_HELP[mode.value]
                scene_options.set_visibility(mode.value != WHOLE)
                together_help.text = (
                    ("Faster: every scene is written from its own brief at once, then the planner checks them all "
                     "in one request and anything to put right is changed once. A scene can't follow how the one "
                     "before it actually turned out." if together.value else
                     "Each scene is written after the one before it, from its own brief; the planner checks it as "
                     "written, and if it departed from its brief, the next brief is brought in line first, so "
                     "every scene follows on from what actually happened. Slower: one scene at a time.")
                    if planned is not None else
                    "This story's scenes are each written from their own brief, which says how the scene begins "
                    "and ends, so a chapter's scenes are always written at the same time, with no joins to smooth."
                    if independent else
                    "Faster, but the chapter may not read as one continuous piece: each scene is written from the "
                    "scene plan without seeing the text of the scene before it. One small extra request per "
                    "chapter then rewrites only the opening of each scene so it follows on from the one before."
                    if together.value else
                    "Each scene is written in the chapter's one chat, after the scene before it, so it follows on "
                    "from its actual text. Slower: one scene at a time per chapter.")
                result = estimate(project, chosen(), mode.value, rewrite.value, bool(together.value))
                if result is None:
                    estimate_label.text = "No cost estimate yet (the list of models and prices hasn't loaded)."
                    return
                low, high, count, words = result
                skipped = len(chosen()) - count
                if not count:
                    estimate_label.text = "Every chapter in this range is already written."
                    return
                note = f" ({skipped} already written, left as {'it is' if skipped == 1 else 'they are'})" \
                    if skipped else ""
                estimate_label.text = (f"{plural(count, 'chapter')} to write{note} · roughly {money(low)}–"
                                       f"{money(high)} (the higher figure allows for the model thinking at length)"
                                       f" · about {eta.duration(eta.writing_left(words))}, at your usual pace")
                if set_cap:
                    cap.value = max(1.0, math.ceil(high * 1.5 * 2) / 2)

            for element in (start_at, end_at, mode, rewrite, together):
                element.on_value_change(lambda: update_estimate(set_cap=True))
            update_estimate(set_cap=True)

        def go() -> None:
            if not STATE.has_a_key():
                ui.notify("Add a key in Settings first (OpenRouter, Venice, Experiential Labs or another service)", type="warning", position="top")
                return
            settings.at_once = int(at_once.value or 1)
            if planned is not None:  # this story's own choice
                state = _planner_state(project)
                state.scene_order = TOGETHER if together.value else IN_ORDER
                state.save()
            else:
                settings.scenes_together = bool(together.value)
            STATE.save_settings()
            options = AutoOptions(chosen(), mode=mode.value, rewrite=bool(rewrite.value),
                                  continue_short=bool(carry_on.value), plan_scenes=bool(plan.value),
                                  spend_cap=float(cap.value) if cap.value else None,
                                  at_once=settings.at_once, scenes_together=settings.scenes_together,
                                  pause_after_chapter=bool(pause.value))
            start(project, options)
            smooth_reload()

        with ui.row().classes("w-full justify-end"):
            primary("Start auto-write", go, "bolt")


def words_left(project: Project, run: AutoRun) -> int:
    """The words this run still has to write, as the chapters stand now."""
    left = 0
    for number in sorted(set(run.options.chapters)):
        try:
            target = assemble(project, number, mode="whole").target_words
        except AssemblyError:
            continue
        have = project.chapters.get(number)
        left += max(target - (0 if run.options.rewrite or have is None else have.words), 0)
    return left


def estimate(project: Project, numbers: list[int], mode: str, rewrite: bool,
             together: bool = False) -> tuple[float, float, int, int] | None:
    """(low, high, chapters, words) in dollars for the chapters that will be written, and the words to write.
    Rough: scene mode sends the reference material once per scene, so its estimate is scaled up; smoothing the
    joins adds a little."""
    primary_model, _ = STATE.models_for(project)
    low = high = 0.0
    count = words = 0
    for number in numbers:
        if complete(project, number) and not rewrite:
            continue
        try:
            request = assemble(project, number, mode="whole")
        except AssemblyError:
            continue
        cost = STATE.estimate_cost(primary_model, request.size.tokens, request.target_words)
        if cost is None:
            return None
        outline = project.outline.chapter(number)
        chapter_mode = (outline.settings.get("mode") or project.config.mode) if mode == AS_SET else mode
        factor = 1.0 if chapter_mode == WHOLE else 1.0 + 0.6 * max(len(outline.scenes), 3) + (0.1 if together else 0)
        low, high, count = low + cost[0], high + cost[1] * factor, count + 1
        have = project.chapters.get(number)
        words += max(request.target_words - (0 if rewrite or have is None else have.words), 0)
    return low, high, count, words


# --- a run in progress (or just ended) ------------------------------------------------------------

def progress(project: Project, run: AutoRun) -> None:
    chapters = run.options.chapters
    span = f"Chapters {min(chapters)}–{max(chapters)}" if len(chapters) > 1 else f"Chapter {chapters[0]}"
    at_once = run.options.at_once
    with page("medium"):
        with page_header("Auto-write", span + (f" · up to {at_once} at the same time" if at_once > 1 else "")):
            buttons = ui.row().classes("gap-2")
        with card(classes="gap-3"):
            with ui.row().classes("items-center w-full gap-3"):
                spinner = ui.spinner(size="20px")
                status = ui.label().classes("dw-h2")
                ui.space()
                spent = ui.label().classes("dw-small")
            bar = ui.linear_progress(value=0, show_value=False).props("rounded size=8px color=primary")
            time_left = ui.label().classes("dw-small").mark("auto-time-left")
            reason = ui.label().classes("dw-small dw-pre")
        live = ui.element("div").classes("grid gap-3 w-full").style(
            "grid-template-columns: repeat(auto-fit, minmax(320px, 1fr))")
        steps = ui.element("div").classes("dw-list")
    shown = {"status": None, "steps": None, "writing": None}
    live_labels: dict[int, tuple[ui.label, ui.label]] = {}
    to_write = words_left(project, run)  # as things stand now, and what the run has written so far
    written_then = run.written_words()

    def draw_buttons() -> None:
        buttons.clear()
        with buttons:
            if run.running:
                secondary("Stop after these", lambda: (run.stop(), ui.notify(
                    "Auto-write will stop when the requests under way are done", position="top")), "pause")
                quiet("Stop now", lambda: run.stop(now=True), "stop").classes("dw-danger")
            else:
                secondary("Start another run", lambda: (CURRENT.pop(str(project.root), None),
                                                        smooth_reload()), "bolt")
                primary("Read the story", lambda: ui.navigate.to("/story/read"), "auto_stories")

    def draw_steps() -> None:
        steps.clear()
        order = {id(step): index for index, step in enumerate(run.steps)}
        with steps:
            for step in sorted(run.steps, key=lambda s: (s.chapter, order[id(s)])):
                icon, colour = STEP_ICONS.get(step.status, ("radio_button_unchecked", "var(--dw-faint)"))
                with ui.element("div").classes("dw-row clickable").on(
                        "click", lambda s=step: ui.navigate.to(f"/story/chapter/{s.chapter}")).mark("auto-step"):
                    ui.icon(icon).classes("text-xl").style(f"color: {colour}")
                    with ui.column().classes("gap-0 grow min-w-0"):
                        ui.label(step.label).classes("dw-body")
                        note = step.note or ("Waiting for a free slot" if step.status == WAITING else "")
                        if note:
                            ui.label(note).classes("dw-small")
                    if step.words:
                        ui.label(f"{step.words:,} words").classes("dw-small")
                    if step.cost:
                        ui.label(money(step.cost)).classes("dw-small").style("width: 60px; text-align: right")
        steps.set_visibility(bool(run.steps))

    def draw_live(writing) -> None:
        live.clear()
        live_labels.clear()
        with live:
            for step in writing:
                with ui.card().classes("dw-card gap-2").props("flat").mark("auto-live"):
                    ui.label(step.label).classes("dw-h3")
                    activity = ui.label().classes("dw-small")
                    height = "34vh" if len(writing) == 1 else "22vh"
                    with ui.scroll_area().classes("w-full").style(f"height: {height}") as scroll:
                        text = ui.label().classes("dw-prose dw-pre").style("font-size: 15.5px")
                live_labels[id(step)] = (activity, text, scroll)

    def update() -> None:
        if shown["status"] == RUNNING and not run.running:
            smooth_reload()  # the run ended: refresh the sidebar's progress too
            return
        if run.running and run.stop_after_current:
            status.text = "Finishing the requests under way"
        else:
            status.text = STATUS_TEXT.get(run.status, run.status)
        spinner.set_visibility(run.running)
        spent.text = f"{money(run.spent)} spent this run"
        written = run.written_words()
        time_left.text = eta.left_text(eta.writing_left(to_write - (written - written_then), written,
                                                        time.monotonic() - run.began)) if run.running else ""
        finished = len({s.chapter for s in run.steps if s.status in (DONE, SKIPPED)}
                       - {s.chapter for s in run.steps if s.status in (WAITING, "writing")})
        bar.value = min(1.0, finished / max(1, len(chapters))) if run.running else 1.0
        bar.props(f"color={'primary' if run.status in (RUNNING, FINISHED) else 'warning'}")
        reason.text = "" if run.running or run.status == FINISHED else run.reason
        state = [(s.status, s.words, s.note) for s in run.steps]
        if shown["steps"] != state:
            shown["steps"] = state
            draw_steps()
        writing = run.writing if run.running else []
        if shown["writing"] != [id(s) for s in writing]:
            shown["writing"] = [id(s) for s in writing]
            draw_live(writing)
        for step in writing:
            activity, text, scroll = live_labels[id(step)]
            activity.text = step.activity
            shown_text = tail(step.live)
            if shown_text != text.text:  # the newest text stays in view, and nothing moves while nothing comes
                text.text = shown_text
                scroll.scroll_to(percent=1.0)
        if shown["status"] != run.status:
            shown["status"] = run.status
            draw_buttons()

    update()
    ui.timer(1.0, update)


def _planner_state(project):
    """The planner's state, for a story the planner planned (its scenes written from their briefs alone)."""
    from devils_workshop.planner.state import PlannerState, is_planner_story
    if project.config.in_chapter_context != "independent" or not is_planner_story(project.root):
        return None
    return PlannerState.load(project.root)
