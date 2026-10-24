"""Planner: brainstorm a story plan with the model, approve it, then let it plan every chapter and scene.

Stage 1 is a chat beside the story plan: you share an idea, files and pictures; the planner keeps an
agenda of the important decisions still open and asks you about them, most important first, writes the
plan, and suggests improvements. Nothing is final until you approve the plan. Stage 2 runs on its own:
the writer's guide, the chapter architecture, each chapter's scenes, then one self-contained brief per
scene, into Writer guide.md and Chapter outline.md, ready for the writer. Requests carry on while you
look at other screens.
"""

from __future__ import annotations

import asyncio
import re
import weakref
from dataclasses import dataclass, field
from pathlib import Path

from nicegui import background_tasks, ui

from devils_workshop import eta
from devils_workshop.llm.generate import ATTEMPT, DISCARD, SWITCH, TEXT, WAIT, WARNING, Event
from devils_workshop.llm.pictures import picture_limit
from devils_workshop.llm.services import OPENROUTER, service_of
from devils_workshop.planner.inputs import InputError, add_input, is_picture
from devils_workshop.planner.run import (
    FAILED_RUN,
    FILL_GAPS,
    FINISHED,
    RUNNING,
    STOPPED,
    PlanRun,
    all_briefs_now,
    approve,
    back_to_brainstorm,
    briefs_to_come,
    brainstorm,
    current_run,
    models,
    redo_architecture,
    redo_briefs,
    redo_chapter,
    redo_guide,
    redo_scene,
    sees_pictures,
    start_run,
    unwritten_with_briefs,
)
from devils_workshop.planner.state import (
    ACCEPTED,
    BRAINSTORM,
    DECLINED,
    DONE,
    FAILED,
    OPEN,
    PLANNER,
    READY,
    USER,
    PlannerState,
    is_planner_story,
)
from devils_workshop.settings import PLANNER as PLANNER_ROLE
from devils_workshop.ui.copying import copy_button
from devils_workshop.ui.components import (
    card,
    empty_state,
    model_name,
    money,
    notice,
    page,
    page_header,
    primary,
    quiet,
    secondary,
    toast,
    smooth_reload,
    tail,
)
from devils_workshop.ui.layout import frame, native_window_available, need_story, pick_files
from devils_workshop.ui.providers import providers_dialog
from devils_workshop.ui.state import STATE

STATUS_TEXT = {RUNNING: "Planning", FINISHED: "Finished", STOPPED: "Stopped", FAILED_RUN: "Stopped by a problem"}


@dataclass
class Chat:
    """A brainstorm reply on its way (it carries on if you look at another screen)."""
    running: bool = True
    live: str = ""
    activity: str = "Sending…"
    failure: str = ""
    cancel: asyncio.Event = field(default_factory=asyncio.Event)


CHATS: dict[str, Chat] = {}
PENDING: dict[str, list[str]] = {}  # files attached to the message you're writing, by story folder


_LEAVING: weakref.WeakSet = weakref.WeakSet()  # pages that asked to reload, and are about to be replaced


def reload_page() -> None:
    """Reload this page once. A page's timers can tick again before it is replaced; asking twice would
    reload twice (and, in the test browser, step back a page)."""
    client = ui.context.client
    if client in _LEAVING:
        return
    _LEAVING.add(client)
    smooth_reload()


def leaving() -> bool:
    return ui.context.client in _LEAVING


def _key(root: Path) -> str:
    return str(Path(root).resolve())


async def planner_page() -> None:
    project = frame("/story/planner")
    if project is None:
        need_story()
        return
    root = project.root
    if not is_planner_story(root):
        with page("narrow"):
            with empty_state("psychology", "This story wasn't started with the planner.",
                             "To plan a story from an idea, use 'Plan a new story' on the All stories screen."):
                primary("All stories", lambda: ui.navigate.to("/"))
        return
    if not STATE.catalog.models:
        try:
            await STATE.refresh_catalog()  # which models can see pictures, and prices
        except Exception:  # noqa: BLE001 - the planner works without the list; it just can't check
            pass
    state = PlannerState.load(root)
    run = current_run(root)
    if state.stage == BRAINSTORM and not (run and run.running):
        brainstorm_view(root, state)
    else:
        planning_view(root, state, run)


# --- the model ------------------------------------------------------------------------------------

def model_picker(state: PlannerState) -> None:
    """This story's main planner model (the planner's main model in Settings unless you pick another here)."""
    settings = STATE.app_settings()
    current, fallback = models(state, settings)
    options = {m.id: (m.name or m.id) if service_of(m.id) == OPENROUTER else model_name(m.id)  # "Venice · ..."
               for m in sorted(STATE.catalog.models.values(), key=lambda m: m.id)}
    options.setdefault(current, model_name(current))
    with ui.row().classes("w-full items-center gap-x-3 gap-y-1"):
        select = ui.select(options, value=current, with_input=True, label="Planner model").classes("w-72") \
            .mark("planner-model")
        with ui.column().classes("gap-0 min-w-0"):
            seeing = ui.label().classes("dw-small")
            ui.label(f"Falls back to {model_name(fallback)}" if fallback else "No fallback model") \
                .classes("dw-small").mark("planner-fallback")
        choose = quiet("Providers…", lambda: providers_dialog(select.value, PLANNER_ROLE), "dns").props("dense")
        quiet("Planner models…", lambda: ui.navigate.to("/settings"), "tune").props("dense") \
            .tooltip("The planner's main and fallback models, and their providers, in Settings")

    def describe(model: str) -> None:
        sees = sees_pictures(STATE.catalog, model)
        limit = picture_limit(model, STATE.catalog.get(model))
        seeing.text = {True: "Sees pictures" + (f" (at most {limit} per request; the most recent are sent)"
                                                if limit is not None else ""), False: "Can't see pictures (sends their names)",
                       None: "Not known if it sees pictures (sends their names)"}[sees]
        choose.set_visibility(service_of(model) == OPENROUTER)

    def chosen(event) -> None:
        state.model = "" if event.value == settings.planner_primary else (event.value or "")
        state.save()
        describe(event.value)

    select.on_value_change(chosen)
    describe(current)


@ui.refreshable
def kind_picker(root: Path, state: PlannerState) -> None:
    """Original story, or an adaptation of one of the files you attached (or a web page the planner read): an
    adaptation keeps the reference's events, their order, roles and causes, unless you approve a change (Story
    registry.md)."""
    from devils_workshop.planner.inputs import is_picture, list_inputs
    from devils_workshop.planner.registry import ADAPTATION, IMPORT, ORIGINAL
    files = [name for name in list_inputs(root) if not is_picture(name)]
    with ui.row().classes("w-full items-center gap-x-3 gap-y-1"):
        kind = ui.toggle({ORIGINAL: "Original story", ADAPTATION: "Adaptation", IMPORT: "Plan made elsewhere"},
                         value=state.registry.kind).mark("planner-kind")
        reference = ui.select({name: name for name in files}, value=state.registry.reference or None,
                              label="Adapts").classes("w-72").mark("planner-reference")
        plan = ui.select({name: name for name in files}, value=[n for n in state.registry.plan_files if n in files],
                         multiple=True, label="Your plan (in order)").props("use-chips").classes("w-96") \
            .mark("planner-plan-files")
        hint = ui.label().classes("dw-small")

    def show() -> None:
        reference.set_visibility(kind.value == ADAPTATION)
        plan.set_visibility(kind.value == IMPORT)
        hint.text = ("" if kind.value == ORIGINAL else
                     ("Attach your plan's files first, then choose them here." if kind.value == IMPORT else
                      "Attach the story it adapts (or read it from a web page) first, then choose it here.")
                     if not files else
                     "The planner carries your plan in as it is: the same events, in the same chapters and order, "
                     "shown as your instructions want them shown. You review it before you approve."
                     if kind.value == IMPORT else
                     "Its events, in order, are kept: the chapters must carry each one, and the checks hold the "
                     "writing to them, unless you approve a change in Story registry.md.")

    def save() -> None:
        latest = PlannerState.load(root)
        latest.registry.kind = kind.value
        latest.registry.plan_files = list(plan.value or []) if kind.value == IMPORT else []
        latest.registry.reference = (reference.value or "") if kind.value == ADAPTATION else \
            (latest.registry.plan_files[0] if latest.registry.plan_files else "")
        if (latest.registry.reference, latest.registry.plan_files) != (state.registry.reference,
                                                                       state.registry.plan_files):
            latest.registry.sources, latest.registry.map_mark = [], ""  # a new reference: its events again
        latest.save()
        state.registry = latest.registry
        show()

    kind.on_value_change(save)
    reference.on_value_change(save)
    plan.on_value_change(save)
    show()


# --- stage 1: brainstorm --------------------------------------------------------------------------

def brainstorm_view(root: Path, state: PlannerState) -> None:
    key = _key(root)
    pending = PENDING.setdefault(key, [])
    chat = CHATS.get(key)
    with page("wide"):
        with page_header("Planner", "Build the story's foundation with the planner. It keeps a list of the "
                                    "important decisions still open and asks you about them, most important "
                                    "first, writes the plan from your answers, and offers ideas you can take or "
                                    "leave; nothing is final until you approve the plan.",
                         eyebrow="Stage 1 of 2 · Story plan"):
            pass
        model_picker(state)
        kind_picker(root, state)
        with ui.element("div").classes("dw-cols halves"):  # on a phone: the conversation above the plan
            with ui.card().classes("dw-card gap-3").props("flat"):
                ui.label("Conversation").classes("dw-h3")
                with ui.scroll_area().classes("w-full dw-chat-scroll").style("height: 52vh") as scroller:
                    with ui.column().classes("w-full gap-3") as conversation:
                        intro = None
                        if not state.turns:
                            intro = ui.label("Start with your idea: a line or several pages, plus any notes, documents and "
                                     "reference pictures. The planner drafts the plan from it, lists every "
                                     "important decision still open, and asks you about them a few at a time; "
                                     "small details it decides itself (marked 'my choice').") \
                                .classes("dw-small")
                        for turn in state.turns:
                            _bubble(turn.role, turn.text, turn.attachments)
                            if turn.role == PLANNER:
                                _planner_extras(state, turn, lambda: update())
                        live = ui.column().classes("w-full gap-1")
                with live:
                    live_status = ui.label().classes("dw-small")
                    live_text = ui.label().classes("dw-pre dw-body")
                with ui.row().classes("w-full items-start no-wrap gap-1") as failure_row:
                    failure = ui.label().classes("dw-small grow dw-pre").style("color: var(--dw-problem)")
                    copy_button(source=failure, tip="Copy this message").mark("copy-planner-failure")
                with ui.element("div").classes("w-full") as ready_box:
                    notice("success", "The planner says the plan is complete enough to design the chapters. "
                                      "Approve it, or keep refining.", "Approve the plan",
                           lambda: approve_dialog(root, state, editor.value))
                ready_box.mark("planner-ready")
                chips = ui.row().classes("gap-2")
                message = ui.textarea(placeholder="Your idea, answers, or what to change (\"yes to 2 and 4\")…") \
                    .props("autogrow outlined").classes("w-full").mark("planner-message")
                with ui.row().classes("w-full items-center gap-2"):
                    attach_button = quiet("Attach…", lambda: attach(), "attach_file").tooltip(
                        "Text files, PDF, Word, or pictures (.png, .jpg, .webp, .gif)")
                    web_button = quiet("From a web page…", lambda: web_page_dialog(root, store), "language") \
                        .tooltip("Read a story from its web address, as the story this adapts or as a file") \
                        .mark("planner-web-page")
                    gaps_button = quiet("Fill the gaps for me", lambda: send(text=FILL_GAPS), "auto_fix_high") \
                        .tooltip("The planner settles every open decision itself, with its best choices") \
                        .mark("planner-fill-gaps")
                    ui.space()
                    stop_button = quiet("Stop", lambda: CHATS[key].cancel.set(), "stop").classes("dw-danger")
                    retry_button = secondary("Try again", lambda: send(retry=True), "refresh")
                    send_button = primary("Send", lambda: send(), "send").mark("planner-send")
            with ui.card().classes("dw-card gap-3").props("flat"):
                with ui.row().classes("w-full items-center gap-1") as plan_header:
                    ui.label("Story plan").classes("dw-h3")
                    ui.space()
                    edited = ui.label("Your edits are used in the next reply").classes("dw-chip draft")
                editor = ui.codemirror(state.plan, language="Markdown", line_wrapping=True) \
                    .classes("w-full dw-editor dw-plan-editor").style("min-height: 52vh").mark("planner-plan")
                with plan_header:  # copies the plan as it is in the editor, your edits included
                    copy_button(source=editor, label="Copy", tip="Copy the story plan").mark("copy-plan")
                if state.agenda:
                    with ui.expansion(f"Still to decide ({len(state.agenda)})", icon="help_outline", value=True) \
                            .classes("w-full dw-small").mark("planner-agenda"):
                        ui.label("The important decisions still open, most important first. The planner asks "
                                 "you about them next.").classes("dw-small")
                        for item in state.agenda:
                            ui.markdown(f"- {item}").classes("dw-small")
                for items, title, icon, mark in (
                        (state.decisions, "Decided so far", "checklist", "planner-decisions"),
                        (state.preferences, "Your preferences", "favorite_border", "planner-preferences"),
                        (state.ruled_out, "Ruled out", "block", "planner-ruled-out")):
                    if items:
                        with ui.expansion(f"{title} ({len(items)})", icon=icon).classes("w-full dw-small").mark(mark):
                            for item in items:
                                ui.label(f"• {item}").classes("dw-small")
                if state.notes:
                    with ui.expansion(f"Notes on your files ({len(state.notes)})", icon="sticky_note_2") \
                            .classes("w-full dw-small").mark("planner-notes"):
                        ui.label("Made once, when you attached each one; the planner works from these notes, "
                                 "not the files.").classes("dw-small")
                        for name, text in state.notes.items():
                            ui.label(name).classes("dw-h3")
                            ui.label(text).classes("dw-small dw-pre")
                ui.label("The planner's memory is its files in the story folder: Story plan.md, Decisions.md and "
                         "Source notes.md. It works from them, not the whole conversation, and reads your edits "
                         "to them.").classes("dw-small")
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(f"{money(state.spent)} spent on planning so far" if state.spent else "").classes(
                        "dw-small")
                    ui.space()
                    approve_button = primary("Approve the plan", lambda: approve_dialog(root, state, editor.value),
                                             "task_alt").mark("planner-approve")

    def draw_chips() -> None:
        chips.clear()
        with chips:
            for name in pending:
                ui.chip(name, icon="image" if is_picture(name) else "description", removable=True,
                        on_value_change=lambda e, n=name: (pending.remove(n), draw_chips()) if not e.value else None) \
                    .props("dense outline")

    def store(name: str, data: bytes) -> None:
        try:
            pending.append(add_input(root, name, data))
        except InputError as exc:
            toast(str(exc), "warning")
        draw_chips()

    async def attach() -> None:
        if native_window_available():
            chosen = await pick_files()
            if chosen is not None:
                for item in chosen:
                    path = Path(item)
                    try:
                        store(path.name, path.read_bytes())
                    except OSError as exc:
                        toast(f"{path.name} couldn't be read: {exc}", "warning")
                return
        upload_dialog(store)

    def send(retry: bool = False, text: str | None = None) -> None:
        text = (message.value or "").strip() if text is None else text
        if not retry and not text and not pending and not state.untold_answers():
            toast("Write a message, answer a suggestion or attach a file first", "warning")
            return
        if not STATE.has_a_key():
            toast("Add a key in Settings first (OpenRouter, Venice, Experiential Labs or another service)", "warning")
            return
        if editor.value != state.plan:  # your edits to the plan go with this message
            state.plan = editor.value
            state.save()
        files = [] if retry else list(pending)
        if not retry:
            pending.clear()
            message.value = ""
        start_chat(root, state, "" if retry else text, files)
        # Your message joins the conversation where it is, and the reply streams in below it: no reload (the page
        # is shown again once the reply is in, to show the plan as it changed)
        if shown.get("intro") is not None:
            shown.pop("intro").delete()
        if (text or files) and not retry:
            with conversation:
                _bubble(USER, text, files)
            live.move(conversation)
        shown["running"] = True
        update()

    shown = {"running": bool(chat and chat.running), "live": None, "intro": intro}

    def update() -> None:
        if leaving():
            return  # asked to reload already; the timer can tick again before the page is replaced
        current = CHATS.get(key)
        running = bool(current and current.running)
        if shown["running"] and not running:
            reload_page()  # the reply arrived (or failed): show it
            return
        shown["running"] = running
        live.set_visibility(running)
        if running:
            live_status.text = current.activity
            reply = _visible_reply(current.live)
            if reply != shown["live"]:  # follow the reply only as it grows, so the rest can be read meanwhile
                shown["live"] = reply
                live_text.text = reply
                scroller.scroll_to(percent=1.0)
        failure.text = chat.failure if chat and not chat.running else ""
        failure_row.set_visibility(bool(failure.text))
        last_is_yours = bool(state.turns) and state.turns[-1].role != PLANNER
        retry_button.set_visibility(not running and last_is_yours)
        stop_button.set_visibility(running)
        for element in (send_button, attach_button, web_button, approve_button, gaps_button):
            element.set_enabled(not running)
        ready_box.set_visibility(state.ready and not running)
        answered = len(state.untold_answers())
        send_button.set_text("Send" if not answered else f"Send ({answered} answer{'s' if answered > 1 else ''})")
        approve_button.set_enabled(not running and bool(editor.value.strip()))
        edited.set_visibility(editor.value != state.plan)

    if chat and not chat.running:
        CHATS.pop(key, None)  # its outcome is shown once, on this visit
    draw_chips()
    update()
    ui.timer(0.3, update)
    scroller.scroll_to(percent=1.0)


def _bubble(role: str, text: str, attachments: list[str] | None = None) -> None:
    mine = role != PLANNER
    with ui.column().classes("gap-1 w-full").style("align-items: flex-end" if mine else ""):
        with ui.row().classes("items-center gap-1 no-wrap"):
            ui.label("You" if mine else "Planner").classes("dw-small")
            copy_button(text, tip="Copy this message").mark("copy-message")
        with ui.element("div").classes("dw-pre dw-body").style(
                "padding: 10px 14px; border-radius: 12px; max-width: 92%; "
                + ("background: var(--dw-accent-soft)" if mine else "background: var(--dw-sunken, rgba(0,0,0,0.04))")):
            ui.label(text)
            if attachments:
                ui.label("Attached: " + ", ".join(attachments)).classes("dw-small")



def _planner_extras(state: PlannerState, turn, changed_answers) -> None:
    """Under a planner message: which sections it changed, and its new suggestions with Yes / No."""
    if turn.edited:
        ui.label("Changed in the plan: " + ", ".join(turn.edited)).classes("dw-small").style(
            "color: var(--dw-final)").mark("planner-edited")
    for number in turn.suggestions:
        suggestion = state.suggestion(number)
        if suggestion:
            _suggestion_card(state, suggestion, changed_answers)


def _suggestion_card(state: PlannerState, suggestion, changed_answers) -> None:
    @ui.refreshable
    def card_body() -> None:
        with ui.row().classes("w-full items-start no-wrap gap-2").style(
                "border: 1px solid var(--dw-border); border-radius: 10px; padding: 8px 10px; max-width: 92%"):
            ui.icon("lightbulb").classes("text-lg").style("color: var(--dw-draft); margin-top: 2px")
            ui.label(suggestion.text).classes("dw-small grow").style("white-space: pre-wrap")
            if suggestion.told and suggestion.status != OPEN:
                ui.label("Yes" if suggestion.status == ACCEPTED else "No").classes(
                    "dw-chip " + ("final" if suggestion.status == ACCEPTED else "draft"))
                return

            def choose(status: str) -> None:
                state.answer(suggestion.id, OPEN if suggestion.status == status else status)
                card_body.refresh()
                changed_answers()

            yes = ui.button("Yes", on_click=lambda: choose(ACCEPTED)).props(
                "dense no-caps size=sm " + ("color=positive" if suggestion.status == ACCEPTED else "outline"))
            yes.mark(f"suggestion-yes-{suggestion.id}")
            no = ui.button("No", on_click=lambda: choose(DECLINED)).props(
                "dense no-caps size=sm " + ("color=negative" if suggestion.status == DECLINED else "outline"))
            no.mark(f"suggestion-no-{suggestion.id}")

    card_body()


def _visible_reply(live: str) -> str:
    """While a reply streams in, show its message; the plan part is only counted."""
    if "<reply>" in live:
        return live.split("<reply>", 1)[1].replace("</reply>", "").strip()
    sections = re.findall(r"<edit\s+section\s*=\s*[\"']([^\"']+)", live)
    if sections:
        return "Updating the plan: " + ", ".join(dict.fromkeys(sections)) + "…"
    if "<plan>" in live:
        return f"Updating the story plan… ({len(live.split()):,} words so far)"
    return live


def start_chat(root: Path, state: PlannerState, text: str, files: list[str]) -> None:
    from devils_workshop.planner.prompts import converting
    chat = Chat()
    CHATS[_key(root)] = chat
    usually = eta.usually_text(eta.typical(eta.story_roots(root, STATE.app_settings().recent_projects),
                                           "plan-import" if converting(state) else "plan-brainstorm"))

    def on_event(event: Event) -> None:
        if event.kind == TEXT:
            chat.live += event.text
            chat.activity = f"{model_name(event.model)} is replying… ({usually})"
        elif event.kind == DISCARD:
            chat.live = ""
        elif event.kind in (WAIT, SWITCH, WARNING):
            chat.activity = event.message
        elif event.kind == ATTEMPT:
            chat.activity = f"Waiting for {model_name(event.model)}… ({usually})"

    async def go() -> None:
        try:
            result = await brainstorm(state, text, files, settings=STATE.app_settings(), catalog=STATE.catalog,
                                      client=STATE.client(), on_event=on_event, cancel=chat.cancel,
                                      secret=STATE.api_key())
            if not result.ok:
                chat.failure = ("Stopped. Nothing from that reply was kept." if chat.cancel.is_set() else
                                f"{result.failure or 'The request failed'} Try again, or change your message.")
        except Exception as exc:  # noqa: BLE001 - shown on the screen
            chat.failure = f"Something went wrong: {exc}"
        finally:
            chat.running = False

    background_tasks.create(go(), name="planner-chat")


def upload_dialog(store) -> None:
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[34rem] gap-4"):
        ui.label("Attach files").classes("dw-h2")
        ui.label("Any text file (.md, .txt, .json…), PDF or Word, or pictures (.png, .jpg, .webp, .gif; up to 5 MB "
                 "each).").classes("dw-small")

        async def received(event) -> None:
            store(event.file.name, await event.file.read())

        ui.upload(multiple=True, auto_upload=True, on_upload=received).props("flat bordered").classes("w-full") \
            .mark("planner-upload")
        with ui.row().classes("w-full justify-end"):
            primary("Done", dialog.close).mark("planner-upload-done")
    dialog.open()


def web_page_dialog(root: Path, store) -> None:
    """Read a story from a web page with the author's extractor (planner/webpage.py), on this PC, and attach it to
    the next message as a Markdown file; by default it also becomes the story this adapts."""
    from devils_workshop.planner.registry import ADAPTATION
    from devils_workshop.planner.webpage import WebPageError, document, read_page
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[34rem] gap-4"):
        ui.label("Read a story from a web page").classes("dw-h2")
        ui.label("Its main text is read on this PC (ads, comments, menus and opening notices left out, numbered "
                 "pages joined in order) and attached to your next message as a Markdown file. Public pages only: "
                 "not ones behind a sign-in or a paywall.").classes("dw-small")
        address = ui.input("Web address", placeholder="https://…").props("outlined autofocus").classes("w-full") \
            .mark("planner-web-url")
        adapts = ui.checkbox("This is the story the adaptation follows", value=True).mark("planner-web-adapts")
        status = ui.label().classes("dw-small dw-pre")
        with ui.row().classes("w-full justify-end gap-2"):
            secondary("Cancel", dialog.close)
            fetch = primary("Read the page", lambda: go(), "download").mark("planner-web-fetch")

        async def go() -> None:
            url = (address.value or "").strip()
            if not url:
                status.text = "Enter the page's web address first."
                return
            fetch.set_enabled(False)
            status.text = "Reading the page…"
            try:
                page_ = await asyncio.to_thread(read_page, url)
            except WebPageError as exc:
                status.text = str(exc)
                fetch.set_enabled(True)
                return
            name, data = document(page_)
            before = len(PENDING.get(_key(root), []))
            store(name, data)
            added = PENDING.get(_key(root), [])[before:]
            if not added:  # store said why (a toast)
                fetch.set_enabled(True)
                status.text = ""
                return
            if adapts.value:
                latest = PlannerState.load(root)
                latest.registry.kind, latest.registry.reference = ADAPTATION, added[0]
                latest.registry.sources, latest.registry.map_mark = [], ""  # a new reference: its events again
                latest.save()
                kind_picker.refresh(root, latest)
            pages = f" from {page_.pages} pages" if page_.pages > 1 else ""
            toast(f"Read {page_.words:,} words{pages}: {added[0]} goes with your next message"
                  + (", as the story this adapts" if adapts.value else ""), "positive")
            dialog.close()

        address.on("keydown.enter", go)
    dialog.open()


def approve_dialog(root: Path, state: PlannerState, plan: str) -> None:
    replanning = bool(state.chapters)
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[36rem] gap-3"):
        ui.label("Approve the plan and plan the chapters?").classes("dw-h2")
        ui.label("The planner then writes, one request at a time: the writer's guide (the lasting facts sent with "
                 "every scene), the chapters, each chapter's scenes, and a self-contained brief for each scene, "
                 "checked so it gives nothing away, into Writer guide.md and Chapter outline.md. The writer gets only the guide and one scene's brief "
                 "at a time, so the scenes can be written at the same time. You can stop at any time and carry on "
                 "later, and redo the guide, any chapter or any scene afterwards."
                 + (" The chapters planned before are planned again from this plan (the earlier outline stays in "
                    "History)." if replanning else "")).classes("dw-small")
        if state.agenda:
            notice("warning", f"{len(state.agenda)} important decision{'s are' if len(state.agenda) != 1 else ' is'} "
                              "still open. Answer them first, or press 'Fill the gaps for me' to have the planner "
                              "settle them; if you approve now, it decides them as it plans.")
        with ui.row().classes("gap-3"):
            scene_words = ui.number("Words per scene", value=state.scene_words, min=200, max=5000, step=50,
                                    precision=0).classes("w-40").mark("planner-scene-words") \
                .tooltip("Used where the plan doesn't give its own length")
            brief_words = ui.number("Words per scene brief", value=state.brief_words, min=50, max=1500, step=10,
                                    precision=0).classes("w-44").mark("planner-brief-words") \
                .tooltip("For a scene of the length on the left; longer scenes get longer briefs")
        jit = ui.checkbox("Write each chapter's scene briefs just before it is written (recommended)",
                          value=state.jit).mark("planner-jit")
        ui.label("The briefs then follow on from what was actually written, and changes you ask for in the "
                 "Reader don't leave later briefs out of date. Untick to plan every brief now.").classes("dw-small")
        cap = ui.number("Stop before spending more than ($)", min=0.01, step=0.5, format="%.2f").classes("w-72")
        ui.label("Leave it empty for no limit.").classes("dw-small")

        def go() -> None:
            state.plan = plan
            state.scene_words = int(scene_words.value or state.scene_words)
            state.brief_words = int(brief_words.value or state.brief_words)
            state.jit = bool(jit.value)
            if replanning:
                redo_architecture(state)
            approve(state)
            launch(root, spend_cap=float(cap.value) if cap.value else None)
            dialog.close()
            reload_page()

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Not yet", dialog.close)
            primary("Approve and plan", go, "task_alt").mark("planner-approve-go")
    dialog.open()


def launch(root: Path, only: tuple | None = None, spend_cap: float | None = None) -> PlanRun:
    run = PlanRun(root, STATE.app_settings(), STATE.catalog, STATE.client, only=only, spend_cap=spend_cap,
                  secret=STATE.api_key(), estimate=STATE.estimate_cost, name=model_name)
    start_run(run)
    return run


# --- stage 2: chapters and scenes -------------------------------------------------------------------

def planning_view(root: Path, state: PlannerState, run: PlanRun | None) -> None:
    ready, total = state.progress()
    scenes = sum(len(c.scenes) for c in state.chapters)
    subtitle = (f"{len(state.chapters)} chapters, about {state.total_words():,} words, {scenes} scenes"
                if state.chapters else "The chapters haven't been planned yet.")
    with page("medium"):
        with page_header("Planner", subtitle, eyebrow="Stage 2 of 2 · The writer's guide, chapters and scenes"):
            buttons = ui.row().classes("gap-2")
        with card(classes="gap-3"):
            with ui.row().classes("items-center w-full gap-3"):
                spinner = ui.spinner(size="20px")
                status = ui.label().classes("dw-h2")
                ui.space()
                spent = ui.label().classes("dw-small")
            bar = ui.linear_progress(value=0, show_value=False).props("rounded size=8px color=primary")
            time_left = ui.label().classes("dw-small").mark("planner-time-left")
            main, fallback = models(state, STATE.app_settings())
            ui.label(f"Planning with {model_name(main)}"
                     + (f", falling back to {model_name(fallback)}" if fallback else "")).classes("dw-small") \
                .mark("planner-models")
            with ui.row().classes("w-full items-start no-wrap gap-1"):
                reason = ui.label().classes("dw-small dw-pre grow")
                copy_reason = copy_button(source=reason, tip="Copy this message").mark("copy-planner-reason")
        with ui.card().classes("dw-card gap-2").props("flat").mark("planner-live") as live_card:
            live_label = ui.label().classes("dw-h3")
            activity = ui.label().classes("dw-small")
            with ui.scroll_area().classes("w-full").style("height: 26vh") as live_scroll:
                live_text = ui.label().classes("dw-pre dw-small")
        chapters_box = ui.column().classes("w-full gap-2")
    shown = {"status": None, "steps": None}
    opened: set[int] = set()  # the chapters you opened
    usual = eta.usual_times(eta.story_roots(root, STATE.app_settings().recent_projects))

    def draw_buttons() -> None:
        buttons.clear()
        current = current_run(root)
        with buttons:
            if current and current.running:
                secondary("Stop after this", lambda: (current.stop(), toast(
                    "Planning will stop when the request under way is done")), "pause")
                quiet("Stop now", lambda: current.stop(now=True), "stop").classes("dw-danger")
                return
            secondary("Change the plan", lambda: (back_to_brainstorm(state), reload_page()), "edit_note") \
                .tooltip("Back to the conversation and the story plan")
            waiting = briefs_to_come(PlannerState.load(root))
            if waiting:
                secondary("Write all the briefs now", lambda: all_briefs_dialog(root, waiting), "playlist_add_check") \
                    .mark("planner-all-briefs")
            if state.stage == DONE:
                primary("Start writing", lambda: ui.navigate.to("/story"), "edit").mark("planner-start-writing")
            else:
                primary("Continue planning", lambda: (launch(root), reload_page()), "play_arrow") \
                    .mark("planner-continue")

    def draw_chapters() -> None:
        chapters_box.clear()
        latest = PlannerState.load(root)
        busy = bool(current_run(root) and current_run(root).running)
        with chapters_box:
            _guide_row(root, state, latest, busy)
            if latest.chapters:
                with ui.row().classes("w-full items-center"):
                    ui.label("Chapters").classes("dw-h2")
                    ui.space()
                    if not busy:
                        quiet("Re-plan all chapters…", lambda: redo_dialog(
                            "Re-plan all chapters?", "Every chapter, its scenes and their briefs are planned again "
                            "from the story plan. The current outline stays in History.",
                            lambda note: (redo_architecture(state, note), launch(root))), "restart_alt")
                with ui.row().classes("w-full items-center gap-2").mark("planner-briefs-row"):
                    ui.label(f"Briefs: about {latest.brief_words:,} words for a {latest.scene_words:,}-word scene: "
                             "what the writer can't infer, the weight where the scene's is").classes("dw-small")
                    ui.space()
                    if not busy:
                        quiet("Brief length…", lambda: brief_length_dialog(root), "straighten").props("dense") \
                            .mark("planner-brief-length")
                        if unwritten_with_briefs(latest):
                            quiet("Re-plan the briefs…", lambda: replan_briefs_dialog(root), "refresh") \
                                .props("dense").mark("planner-replan-briefs")
            for chapter in latest.chapters:
                done = sum(1 for s in chapter.scenes if s.status == READY)
                later = latest.jit and chapter.number > latest.briefs_through and not done
                caption = (f"{chapter.words:,} words · " if chapter.words else "") + (
                    f"{len(chapter.scenes)} scenes planned · briefs come when you write chapter {chapter.number}"
                    if later and chapter.scenes else
                    f"{done} of {len(chapter.scenes)} scene briefs" if chapter.scenes else "scenes not planned yet")
                with ui.expansion(f"{chapter.number}. {chapter.title}", caption=caption,
                                  value=chapter.number in opened,  # stays open while the list is drawn again
                                  on_value_change=lambda e, n=chapter.number: opened.add(n) if e.value
                                  else opened.discard(n)) \
                        .classes("dw-section-block w-full").mark("planner-chapter"):
                    with ui.column().classes("gap-2 px-4 pb-3 w-full"):
                        if chapter.purpose or chapter.summary:
                            ui.label(chapter.summary or chapter.purpose).classes("dw-small")
                        for scene in chapter.scenes:
                            _scene_row(root, state, chapter.number, scene, busy, later)
                        if not busy and chapter.scenes:
                            quiet("Change this chapter…", lambda c=chapter, later=later: redo_dialog(
                                f"Change chapter {c.number}?",
                                "Say what should change (the events, an ending, who is in it, the pace). Its scene "
                                "list is planned again with your note" + (
                                    "; its briefs are still written just before the chapter is."
                                    if later else ", then every scene's brief.")
                                + " The current outline stays in History.",
                                lambda note, n=c.number: (redo_chapter(state, n, note),
                                                          launch(root, only=("chapter", n))),
                                label="What should change in this chapter", go_label="Change the chapter")) \
                                .classes("self-start").mark("planner-change-chapter")

    def update() -> None:
        if leaving():
            return  # asked to reload already; the timer can tick again before the page is replaced
        current = current_run(root)
        if shown["status"] == RUNNING and not (current and current.running):
            reload_page()  # the run ended: show the result
            return
        running = bool(current and current.running)
        latest = PlannerState.load(root) if running else state
        latest_ready, latest_total = latest.progress() if running else (ready, total)
        time_left.text = eta.left_text(eta.planning_left(latest, usual)) if running else ""
        status.text = ("Finishing the request under way" if running and current.stop_after_current else
                       STATUS_TEXT.get(current.status, current.status) if current else
                       ("Ready to write" if state.stage == DONE else "Paused"))
        spinner.set_visibility(running)
        spent.text = f"{money(state.spent + (current.spent if running else 0))} spent on planning"
        bar.value = latest_ready / latest_total if latest_total else (1.0 if state.stage == DONE else 0.0)
        bar.props(f"color={'warning' if current and current.status in (STOPPED, FAILED_RUN) else 'primary'}")
        reason.text = current.reason if current and not running else (
            "" if state.stage == DONE or running else "Continue planning carries on from where it stopped.")
        copy_reason.set_visibility(bool(current and current.status == FAILED_RUN))
        step = current.steps[-1] if running and current.steps else None
        live_card.set_visibility(bool(step))
        if step:
            live_label.text = step.label
            activity.text = step.activity
            shown_text = tail(step.live)
            if shown_text != live_text.text:  # the newest text stays in view, and nothing moves while nothing comes
                live_text.text = shown_text
                live_scroll.scroll_to(percent=1.0)
        steps = [(s.label, s.status) for s in current.steps] if current else []
        if shown["steps"] != steps:
            shown["steps"] = steps
            draw_chapters()
        if shown["status"] != (current.status if current else None):
            shown["status"] = current.status if current else None
            draw_buttons()

    update()
    if shown["status"] is None:
        draw_buttons()
    ui.timer(1.0, update)


def _guide_row(root: Path, state: PlannerState, latest: PlannerState, busy: bool) -> None:
    """The writer's guide: the lasting facts the writer gets with every scene."""
    with ui.card().classes("dw-card gap-1").props("flat").mark("planner-guide"):
        with ui.element("div").classes("dw-row"):
            ready = bool(latest.guide.strip())
            ui.icon("check_circle" if ready else "radio_button_unchecked").classes("text-xl").style(
                f"color: {'var(--dw-final)' if ready else 'var(--dw-faint)'}")
            with ui.column().classes("gap-0 grow min-w-0"):
                ui.label("The writer's guide").classes("dw-body")
                ui.label(f"{len(latest.guide.split()):,} words · sent with every scene, in Writer guide.md"
                         if ready else "Written first: the voice, characters, world and rules, with no plot").classes(
                    "dw-small")
            if ready:
                quiet("Read", lambda: text_dialog("The writer's guide", latest.guide,
                                                  "To change it by hand, edit Writer guide.md under Story files."),
                      "article").props("dense").mark("planner-guide-read")
            if ready and not busy:
                quiet("Redo…", lambda: redo_dialog(
                    "Write the writer's guide again?", "Only the guide is written again; the current one stays in "
                    "History. The scene briefs already written are kept.",
                    lambda note: (redo_guide(state, note), launch(root, only=("guide",)))), "refresh").props("dense")


def text_dialog(title: str, text: str, hint: str = "", extra: str = "", extra_label: str = "") -> None:
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[46rem] gap-3"):
        ui.label(title).classes("dw-h2")
        with ui.scroll_area().classes("w-full").style("height: 60vh"):
            ui.label(text).classes("dw-pre dw-body")
            if extra.strip():
                with ui.expansion(extra_label, icon="timeline").classes("w-full dw-small"):
                    ui.label(extra).classes("dw-pre dw-small")
        if hint:
            ui.label(hint).classes("dw-small")
        with ui.row().classes("w-full justify-end gap-2"):
            copy_button(text, label="Copy", tip="Copy this").mark("copy-brief")
            primary("Close", dialog.close)
    dialog.open()


def _scene_row(root: Path, state: PlannerState, chapter: int, scene, busy: bool, later: bool = False) -> None:
    """One scene: ✓ its brief is written, ! it failed, a clock: written just before its chapter is (briefs just in
    time), an empty circle: still to plan."""
    icon, colour = {READY: ("check_circle", "var(--dw-final)"), FAILED: ("error", "var(--dw-problem)")}.get(
        scene.status, ("schedule", "var(--dw-muted)") if later else ("radio_button_unchecked", "var(--dw-faint)"))
    tip = {READY: "Brief written", FAILED: "The brief failed: Continue planning tries it again"}.get(
        scene.status, f"Its brief is written just before you write chapter {chapter}, from what was written before "
                      "it" if later else "Brief still to plan")
    with ui.element("div").classes("dw-row").mark("planner-scene"):
        with ui.icon(icon).classes("text-xl").style(f"color: {colour}").mark(f"planner-scene-{icon}"):
            ui.tooltip(tip)
        with ui.column().classes("gap-0 grow min-w-0"):
            ui.label(f"Scene {scene.number}: {scene.title}").classes("dw-body")
            ui.label((f"{scene.words:,} words · " if scene.words else "") + scene.purpose).classes("dw-small")
        if scene.brief:
            quiet("Brief", lambda: brief_dialog(root, state, chapter, scene, busy), "article").props("dense") \
                .mark("planner-brief", f"planner-brief-{chapter}-{scene.number}")


def brief_dialog(root: Path, state: PlannerState, chapter: int, scene, busy: bool) -> None:
    """A scene's brief, with a box to say what should change and have it written again."""
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[46rem] gap-3"):
        ui.label(f"Chapter {chapter}, scene {scene.number}: {scene.title}").classes("dw-h2")
        with ui.scroll_area().classes("w-full").style("height: 46vh"):
            ui.label(scene.brief).classes("dw-pre dw-body")
            if scene.continuity.strip():
                with ui.expansion("How things stand after this scene (the planner's notes; not sent to the writer)",
                                  icon="timeline").classes("w-full dw-small"):
                    ui.label(scene.continuity).classes("dw-pre dw-small")
        ui.label("The writer gets this brief and the writer's guide, nothing else. To change it by hand, edit "
                 "Chapter outline.md under Story files.").classes("dw-small")
        note = ui.textarea("What should change in this brief").props("autogrow outlined").classes("w-full") \
            .mark("planner-brief-note")
        if busy:
            note.disable()
            ui.label("Planning is under way: change it when it's done.").classes("dw-small")

        def again() -> None:
            text = (note.value or "").strip()
            if not text:
                toast("Say what should change first", "warning")
                return
            if not STATE.has_a_key():
                toast("Add a key in Settings first (OpenRouter, Venice, Experiential Labs or another service)",
                      "warning")
                return
            redo_scene(state, chapter, scene.number, text)
            launch(root, only=("scene", chapter, scene.number))
            dialog.close()
            reload_page()

        with ui.row().classes("w-full justify-end gap-2"):
            copy_button(scene.brief, label="Copy", tip="Copy this").mark("copy-brief")
            quiet("Close", dialog.close)
            if not busy:
                primary("Write it again with my changes", again, "refresh").mark("planner-brief-again")
    dialog.open()


def brief_length_dialog(root: Path) -> None:
    """How long a scene's brief is: the room the planner has to name what the writer can't infer."""
    state = PlannerState.load(root)
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[32rem] gap-3"):
        ui.label("How long should a scene's brief be?").classes("dw-h2")
        ui.label(f"For a {state.scene_words:,}-word scene; longer and shorter scenes get briefs in proportion. The "
                 "writer sees only the brief and the writer's guide, so the brief names everything the writer "
                 "can't infer: what happens, who does what, what changes, what is new, and the continuity. How it "
                 "is written is the writer's. A longer brief scripts more of the moves, which tends to make the "
                 "prose follow a script. About 250 words suits an 800-word scene.").classes("dw-small")
        words = ui.number("Words per brief", value=state.brief_words, min=50, max=1500, step=10, precision=0) \
            .classes("w-44").mark("planner-brief-length-words")
        ui.label("Briefs already written keep their length: Re-plan the briefs writes them again.").classes("dw-small")

        def save() -> None:
            latest = PlannerState.load(root)
            latest.brief_words = int(words.value or latest.brief_words)
            latest.save()
            toast(f"New briefs will be about {latest.brief_words:,} words")
            dialog.close()
            reload_page()

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary("Save", save, "check").mark("planner-brief-length-save")
    dialog.open()


def replan_briefs_dialog(root: Path) -> None:
    """Write the briefs again, for the chapters not written yet: in the brief's current form and length."""
    state = PlannerState.load(root)
    chapters = unwritten_with_briefs(state)
    briefs = sum(len(c.scenes) for c in chapters)
    names = ", ".join(str(c.number) for c in chapters)
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[34rem] gap-3"):
        ui.label("Re-plan the briefs?").classes("dw-h2")
        ui.label(f"The planner writes the {briefs} briefs of the chapters not written yet (chapter"
                 f"{'s' if len(chapters) != 1 else ''} {names}) again, from scratch: about {state.brief_words:,} "
                 "words each, the scene choreographed beat by beat. Their scene lists, the chapters and the writer's "
                 "guide stay as they are, and a copy of Chapter outline.md with the current briefs stays in History. "
                 "Chapters already written keep theirs.").classes("dw-small")
        note = ui.textarea("Anything to do differently in every brief (optional)").props("autogrow outlined") \
            .classes("w-full").mark("planner-replan-briefs-note")

        def go() -> None:
            if not STATE.has_a_key():
                toast("Add a key in Settings first (OpenRouter, Venice, Experiential Labs or another service)",
                      "warning")
                return
            redo_briefs(PlannerState.load(root), (note.value or "").strip())
            launch(root)
            dialog.close()
            reload_page()

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary(f"Re-plan {briefs} briefs", go, "refresh").mark("planner-replan-briefs-go")
    dialog.open()


def all_briefs_dialog(root: Path, waiting: int) -> None:
    """Write every chapter's briefs up front, instead of just before each chapter is written."""
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[34rem] gap-3"):
        ui.label("Write all the briefs now?").classes("dw-h2")
        ui.label(f"The planner writes the {waiting} scene briefs still to come, one at a time, from the plan, so you "
                 "can read every chapter's briefs before anything is written. Each chapter is then written from "
                 "briefs planned ahead, not from how the chapters before it actually turned out.").classes("dw-small")
        ui.label("If you later approve a change in the Reader that reaches later chapters, their briefs are written "
                 "again, just before each of them is. You can stop at any time; what is done is kept.") \
            .classes("dw-small")

        def go() -> None:
            if not STATE.has_a_key():
                toast("Add a key in Settings first (OpenRouter, Venice, Experiential Labs or another service)",
                      "warning")
                return
            all_briefs_now(PlannerState.load(root))
            launch(root)
            dialog.close()
            reload_page()

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary(f"Write {waiting} briefs", go, "playlist_add_check").mark("planner-all-briefs-go")
    dialog.open()


def redo_dialog(title: str, text: str, action, label: str = "What to do differently (optional)",
                go_label: str = "Plan again") -> None:
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[32rem] gap-3"):
        ui.label(title).classes("dw-h2")
        ui.label(text).classes("dw-small")
        note = ui.textarea(label).props("autogrow outlined").classes("w-full").mark("planner-redo-note")

        def go() -> None:
            if not STATE.has_a_key():
                toast("Add a key in Settings first (OpenRouter, Venice, Experiential Labs or another service)",
                      "warning")
                return
            action((note.value or "").strip())
            dialog.close()
            reload_page()

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary(go_label, go, "refresh").mark("planner-redo-go")
    dialog.open()
