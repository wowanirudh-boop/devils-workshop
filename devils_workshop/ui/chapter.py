"""Chapters: the manuscript workspace (docs/ui-redesign.md, "Chapters").

Each chapter is its own chat (PLAN.md §6). Write the chapter (or its scenes) ->
review -> the text appears -> saved. Then ask for changes, continue a short reply,
compare or restore versions, mark final, or go on to the next chapter.
"""

from __future__ import annotations

import asyncio
import difflib
import html
from dataclasses import dataclass, field

from nicegui import ui

from devils_workshop.chat.auto import SCENES, AutoOptions
from devils_workshop.chat.runner import send
from devils_workshop.chat.scenes import (
    ProposedScene,
    apply_scene_proposal,
    parse_scene_proposal,
    prepare_scene_proposal,
)
from devils_workshop.chat.session import CONTINUE, ChapterChat, Prepared
from devils_workshop.context.assemble import AssemblyError, Overrides
from devils_workshop.context.render import clean
from devils_workshop.llm.costs import read_costs, summarize
from devils_workshop.llm.generate import ATTEMPT, DISCARD, KEEPALIVE, REASONING, SWITCH, TEXT, WAIT, WARNING, Event
from devils_workshop.project.chapters import parse_chapter_file
from devils_workshop.project.files import read_text
from devils_workshop.project.issues import IssueList
from devils_workshop.project.project import Project
from devils_workshop.settings import settings_dir
from devils_workshop.ui import auto
from devils_workshop.ui.copying import copy_button
from devils_workshop.ui.components import (
    chip,
    dot,
    empty_state,
    model_name,
    money,
    notice,
    page,
    plural,
    primary,
    prose_html,
    quiet,
    secondary,
    toast,
    smooth_reload,
)
from devils_workshop.ui.duplicates import duplicates_notice
from devils_workshop.ui.layout import frame, need_story
from devils_workshop.ui.review import review
from devils_workshop.ui.state import STATE
from devils_workshop.wordcount import count_words

SHORT_SHARE = 0.6


@dataclass
class LastRun:
    """What the most recent request did, shown under the text."""
    summary: str = ""
    warnings: list[str] = field(default_factory=list)
    short: bool = False
    failed: str = ""


LAST_RUN: dict[int, LastRun] = {}


@dataclass
class Writing:
    """A chapter request under way. It lives outside the page, so a page opened while it runs
    (a phone back from sleep, another window) shows it too, and can stop it."""
    version: int
    text: str = ""
    status: str = "Sending…"
    cancel: asyncio.Event = field(default_factory=asyncio.Event)


WRITING: dict[tuple[str, int], Writing] = {}
PENDING_SCENES: dict[tuple[str, int], str] = {}  # proposed scenes whose page closed before they arrived


def chapter_index_page() -> None:
    """/story/chapter: open the first chapter that isn't final yet."""
    project = STATE.project()
    if project is None or not project.outline.chapters:
        frame("/story/chapter")
        if project is None:
            need_story()
            return
        with page("narrow"):
            if project.own_files:
                with empty_state("article", "No chapters found in your files yet",
                                 "Mark a file as 'Chapter outline' (it is split at its Chapter headings) or as "
                                 "'Chapter 3 only'."):
                    primary("Choose what your files are for", lambda: ui.navigate.to("/story/files?tab=sent"))
            else:
                with empty_state("article", "The outline has no chapters yet",
                                 "Add a heading like '# Chapter 1: Title' to outline.md, with what should happen."):
                    primary("Open the outline", lambda: ui.navigate.to("/story/files?path=outline.md"))
        return
    for chapter in project.outline.chapters:
        written = project.chapters.get(chapter.number)
        if written is None or written.status != "final":
            ui.navigate.to(f"/story/chapter/{chapter.number}")
            return
    ui.navigate.to(f"/story/chapter/{project.outline.chapters[-1].number}")


def chapter_page(number: int) -> None:
    project = frame("/story/chapter")
    if project is None:
        need_story()
        return
    with ui.element("div").classes("dw-workspace flex"):
        chapter_sidebar(project, number)
        with ui.element("div").classes("dw-chapter-pane"):
            chapter_picker(project, number)
            if project.outline.chapter(number) is None:
                where = "your files" if project.own_files else "the outline"
                with page("narrow"):
                    with empty_state("article", f"Chapter {number} isn't in {where}"):
                        primary("Go to the overview", lambda: ui.navigate.to("/story"))
                return
            ChapterScreen(number).build()


def state_of(project: Project, number: int) -> str | None:
    written = project.chapters.get(number)
    if written is None or not (written.text or written.scenes):
        return None
    return "final" if written.status == "final" else "draft"


def chapter_picker(project: Project, current: int) -> None:
    """The chapter list, on a phone."""
    options = {c.number: f"{c.number} · {c.title or f'Chapter {c.number}'}" for c in project.outline.chapters}
    if current not in options:
        options[current] = f"Chapter {current}"
    with ui.row().classes("dw-phone-only w-full px-4 pt-3"):
        ui.select(options, value=current, label="Chapter",
                  on_change=lambda e: ui.navigate.to(f"/story/chapter/{e.value}")).classes("w-full") \
            .mark("chapter-picker")


def chapter_sidebar(project: Project, current: int) -> None:
    with ui.column().classes("dw-chapter-list"):
        with ui.row().classes("items-center w-full px-2 pb-2"):
            ui.label("Chapters").classes("dw-h3")
            ui.space()
            ui.label(str(len(project.outline.chapters))).classes("dw-small")
        for chapter in project.outline.chapters:
            written = project.chapters.get(chapter.number)
            active = " active" if chapter.number == current else ""
            with ui.element("div").classes(f"dw-chapter-item{active}").on(
                    "click", lambda n=chapter.number: ui.navigate.to(f"/story/chapter/{n}")):
                ui.label(str(chapter.number)).classes("dw-chapter-num")
                dot(state_of(project, chapter.number))
                ui.label(chapter.title or f"Chapter {chapter.number}").classes("dw-chapter-name")
                if written and written.words:
                    ui.label(f"{written.words / 1000:.1f}k").classes("dw-chapter-words")
        ui.space()
        quiet("Auto-write several chapters", lambda: ui.navigate.to("/story/auto"), "bolt").classes("w-full")


class ChapterScreen:
    def __init__(self, number: int):
        self.number = number
        self.stream_label = None
        self.status_label = None
        self.reload()
        self.key = (str(self.project.root.resolve()), number)
        self.watching = self.running  # opened while a request was under way: reload when it ends

    @property
    def running(self) -> bool:
        return self.key in WRITING

    @property
    def live(self) -> Writing | None:
        return WRITING.get(self.key)

    def reload(self) -> None:
        self.project = STATE.project()
        self.chat = ChapterChat.load(self.project.root, self.number)
        self.outline = self.project.outline.chapter(self.number)
        self.written = self.project.chapters.get(self.number)
        self.mode = self.chat.mode or self.chat.default_mode(self.project)

    @property
    def target(self) -> int:
        return self.outline.settings.get("words", self.project.config.chapter_words)

    # --- layout ------------------------------------------------------------------------------

    def build(self) -> None:
        # Everything after a request is drawn inside this element: the button that started
        # the request is gone by then (the screen redraws while the text streams in).
        self.root = ui.element("div").classes("dw-page medium").style("padding-bottom: 0")
        with self.root:
            duplicates_notice(self.project)
            self.header()
            self.brief()
            self.body()
        ui.timer(0.3, self.tick)
        if self.key in PENDING_SCENES:
            reply = PENDING_SCENES.pop(self.key)
            ui.timer(0.1, lambda: self.review_scenes(reply), once=True)

    @ui.refreshable_method
    def header(self) -> None:
        with ui.row().classes("w-full items-start no-wrap gap-4 dw-wrap-phone"):
            with ui.column().classes("gap-1 min-w-0 grow"):
                ui.label(f"Chapter {self.number}").classes("dw-eyebrow")
                ui.label(self.outline.title or f"Chapter {self.number}").classes("dw-title")
                with ui.row().classes("items-center gap-3 mt-1"):
                    chip("writing" if self.running else state_of(self.project, self.number))
                    ui.label(self.facts()).classes("dw-small")
            with ui.row().classes("items-center gap-2 no-wrap"):
                toggle = ui.toggle({"whole": "Whole chapter", "scenes": "Scene by scene"}, value=self.mode,
                                   on_change=self.change_mode).tooltip(
                    "Whole chapter: written in one go. Scene by scene: one scene at a time, each following on "
                    "from the ones before.")
                if self.running or auto.busy(self.project):
                    toggle.disable()
                self.more_menu()

    def facts(self) -> str:
        if self.running:
            return f"Writing version {self.live.version}…"
        parts = []
        if self.written:
            parts.append(f"{self.written.words:,} of about {self.target:,} words")
            parts.append(f"version {self.written.version}")
        else:
            parts.append(f"about {self.target:,} words")
        if self.mode == "scenes":
            parts.append(plural(len(self.outline.scenes), "scene"))
        cost = summarize(read_costs(self.project.root / "logs"), self.number).total
        if cost:
            parts.append(f"{money(cost)} spent on this chapter")
        return " · ".join(parts)

    def more_menu(self) -> None:
        busy = self.running or auto.busy(self.project)
        with ui.button(icon="more_horiz", color=None).props('flat round aria-label="More actions"') \
                .classes("dw-quiet") \
                .tooltip("More actions") as button:
            with ui.menu():
                if self.written:
                    ui.menu_item("Read this chapter", lambda: ui.navigate.to(f"/story/read?chapter={self.number}"))
                    if not busy:
                        if self.mode == "whole":
                            ui.menu_item("Write the whole chapter again", lambda: self.write())
                        elif self.written.scenes:
                            ui.menu_item("Start again from scene 1", lambda: self.write(restart=True))
                    versions = self.chat.versions(self.project)
                    if len(versions) > 1:
                        ui.menu_item("Compare versions…", self.compare_dialog)
                        if not busy:
                            ui.menu_item("Restore an earlier version…", self.restore_dialog)
                    if self.written.status == "final" and not busy:
                        ui.menu_item("Mark as draft again", lambda: self.mark_final(False))
                if self.written:
                    ui.menu_item("Edit the text by hand", lambda: ui.navigate.to(
                        f"/story/files?path=chapters/ch{self.number:02d}.md"))
        if busy and not self.written:
            button.disable()

    def edit_brief(self) -> None:
        source = self.outline.source or "outline.md"
        ui.navigate.to(f"/story/files?path={source}")

    def brief(self) -> None:
        has_text = bool(self.written and (self.written.text or self.written.scenes))
        with ui.expansion("What this chapter should do", icon="assignment", value=not has_text) \
                .classes("dw-section-block").mark("brief"):
            with ui.column().classes("gap-2 px-4 pb-4 dw-brief"):
                text = clean(self.outline.text)
                if text:
                    ui.markdown(text).classes("dw-brief")
                elif not self.outline.scenes:
                    ui.label("No instructions for this chapter yet.").classes("dw-muted")
                for scene in self.outline.scenes:
                    with ui.element("div").classes("dw-brief-scene"):
                        ui.label(f"Scene {scene.number}" + (f" · {scene.title}" if scene.title else "")).classes(
                            "dw-h3")
                        if clean(scene.text):
                            ui.markdown(clean(scene.text)).classes("dw-brief dw-small")
                quiet("Edit", self.edit_brief, "edit").classes("self-start -ml-2")

    @ui.refreshable_method
    def body(self) -> None:
        if self.running:
            self.writing_view()
            return
        self.stream_label = self.status_label = None
        self.last_run_note()
        if auto.busy(self.project):
            notice("info", "Auto-write is running for this story. The buttons here come back when it stops.",
                   "See its progress", lambda: ui.navigate.to("/story/auto"))
            if self.written:
                ui.html(f'<div class="dw-prose">{prose_html(self.display_text())}</div>', sanitize=False)
            return
        if not self.written or not (self.written.text or self.written.scenes):
            self.not_written()
            return
        self.written_view()

    def writing_view(self) -> None:
        with ui.card().classes("dw-card gap-4").props("flat"):
            with ui.row().classes("items-center w-full no-wrap gap-3"):
                ui.spinner(size="20px")
                self.status_label = ui.label(self.live.status).classes("dw-small grow")
                ui.button("Stop", icon="stop", on_click=self.stop, color=None).props("flat").classes("dw-danger")
            self.stream_label = ui.label(self.live.text).classes("dw-prose dw-pre")

    def not_written(self) -> None:
        if self.mode == "scenes" and not self.outline.scenes:
            with empty_state("view_agenda", "This chapter has no scenes yet",
                             "The model can plan the scenes from the brief above. Plan and write them all in one "
                             "go, or look at the plan first."):
                secondary("Propose scenes to review", self.propose_scenes)
                primary("Plan and write all scenes automatically", self.write_all_scenes, "bolt")
            return
        if self.mode == "whole":
            what = f"Chapter {self.number} hasn't been written yet"
            how = ("The model gets the brief above and the story files that matter here. You'll see exactly what "
                   "is sent before anything goes out.")
        else:
            what = f"Chapter {self.number} has {plural(len(self.outline.scenes), 'scene')} to write"
            how = ("Write them all in one go, each scene following on from the ones before, or one at a time with "
                   "a review of what is sent before each.")
        with ui.card().classes("dw-card items-center text-center").props("flat").style("padding: 36px 24px"):
            ui.icon("edit_note").classes("text-5xl").style("color: var(--dw-faint)")
            ui.label(what).classes("dw-h2")
            ui.label(how).classes("dw-subtitle").style("max-width: 540px")
            with ui.expansion("Add instructions for this attempt (optional)", icon="add").classes(
                    "dw-section-block text-left").style("max-width: 560px"):
                self.notes = ui.textarea(placeholder="e.g. Open with the storm; keep Ada offstage.").props(
                    "autogrow").classes("w-full px-3 pb-3").mark("notes")
            with ui.row().classes("gap-2 justify-center mt-2"):
                if self.mode == "whole":
                    primary(f"Write chapter {self.number}", lambda: self.write(), "edit")
                else:
                    secondary("Write scene 1 (with review)", lambda: self.write())
                    primary("Write all scenes automatically", self.write_all_scenes, "bolt").tooltip(
                        "Each scene in turn, in this chapter's chat, without stopping for review")

    def written_view(self) -> None:
        copy_row = ui.row().classes("w-full justify-end").style("margin-bottom: -8px")
        prose = ui.html(f'<div class="dw-prose">{prose_html(self.display_text())}</div>', sanitize=False) \
            .classes("w-full").style("padding: 8px 4px 0")
        with copy_row:  # the text as shown, paragraphs and scene breaks kept
            copy_button(source=prose, label="Copy text", tip="Copy the chapter's text").mark("copy-chapter")
        next_scene = self.chat.next_scene(self.project) if self.mode == "scenes" else None
        if next_scene:
            with ui.card().classes("dw-card tight").props("flat"):
                with ui.row().classes("items-center w-full gap-3"):
                    ui.label(f"Scene {next_scene} of {len(self.outline.scenes)} is next").classes("dw-h3 grow")
                    secondary(f"Write scene {next_scene} (with review)", lambda: self.write())
                    primary("Write all remaining scenes", self.write_all_scenes, "bolt").mark("write-remaining")
        elif self.chat.smooth_pending and self.written.scenes and not auto.busy(self.project):
            notice("info", "These scenes were written at the same time, and the joins between them haven't been "
                           "smoothed yet (the run stopped first).", "Smooth the joins", self.write_all_scenes)
        self.composer()

    def composer(self) -> None:
        later = [c.number for c in self.project.outline.chapters if c.number > self.number]
        final = self.written.status == "final"
        with ui.element("div").classes("dw-composer"):
            with ui.card().classes("dw-card tight gap-3").props("flat").style(
                    "box-shadow: 0 8px 28px rgba(0,0,0,.08)"):
                with ui.row().classes("w-full items-end no-wrap gap-2 dw-wrap-phone"):
                    self.change_text = ui.textarea(placeholder="Ask for changes, e.g. Make the ending colder; "
                                                               "Tobias shouldn't apologise.") \
                        .props("autogrow dense rows=1").classes("grow dw-full-phone").mark("change-request")
                    self.scope = None
                    if self.written.scenes:
                        scopes = {0: "Whole chapter", **{n: f"Scene {n}" for n in sorted(self.written.scenes)}}
                        self.scope = ui.select(scopes, value=0, label="Apply to").classes("w-40")
                    primary("Review & send", self.change, "send")
                with ui.row().classes("w-full items-center gap-2"):
                    quiet("Continue writing", self.continue_writing, "playlist_add").tooltip(
                        "Asks the model to carry on from where the text stops")
                    ui.space()
                    if final:
                        chip("final", "Marked final")
                    else:
                        secondary("Mark as final", lambda: self.mark_final(True), "check")
                    if later:
                        secondary(f"Next: chapter {later[0]}", lambda: ui.navigate.to(
                            f"/story/chapter/{later[0]}")).props("icon-right=arrow_forward")

    def display_text(self) -> str:
        if not self.written:
            return ""
        if self.written.scenes:
            return "\n\n".join(f"— Scene {n} —\n\n{self.written.scenes[n]}" for n in sorted(self.written.scenes))
        return self.written.text

    def last_run_note(self) -> None:
        run = LAST_RUN.get(self.number)
        if not run:
            return
        if run.failed:
            notice("problem", run.failed)
        elif run.summary:
            ui.label(run.summary).classes("dw-small").mark("last-run")
        for warning in run.warnings:
            if run.short and "shorter than planned" in warning:
                notice("warning", warning, "Continue writing", self.continue_writing)
            else:
                notice("warning", warning)

    # --- actions -------------------------------------------------------------------------------

    def change_mode(self, event) -> None:
        self.mode = event.value
        self.header.refresh()
        self.body.refresh()

    def sizes(self) -> dict:
        primary_model, _ = STATE.models_for(self.project)
        return STATE.sizes_for(primary_model)

    async def write(self, restart: bool = False) -> None:
        notes = getattr(self, "notes", None)
        notes = (notes.value or "") if notes is not None and not self.written else ""

        def build(overrides: Overrides) -> Prepared:
            return self.chat.prepare_write(self.project, self.mode, notes, overrides, restart=restart,
                                           **self.sizes())

        what = "chapter" if self.mode == "whole" else "next scene"
        await self.run(build, f"Write {what}")

    def write_all_scenes(self) -> None:
        """The rest of this chapter's scenes without review stops, as set on the Auto-write screen
        (at the same time with their joins smoothed, or one after another)."""
        if not STATE.has_a_key():
            toast("Add a key in Settings first (OpenRouter, Venice, Experiential Labs or another service)", "warning")
            return
        settings = STATE.app_settings()
        auto.start(self.project, AutoOptions([self.number], mode=SCENES, continue_short=True, plan_scenes=True,
                                             at_once=settings.at_once, scenes_together=settings.scenes_together))
        ui.navigate.to("/story/auto")

    async def change(self) -> None:
        text = self.change_text.value or ""
        scene = self.scope.value if self.scope and self.scope.value else None

        def build(overrides: Overrides) -> Prepared:
            return self.chat.prepare_change(self.project, text, scene, overrides, **self.sizes())

        await self.run(build, "Change " + (f"scene {scene}" if scene else "the chapter"))

    async def continue_writing(self) -> None:
        def build(overrides: Overrides) -> Prepared:
            return self.chat.prepare_continue(self.project, **self.sizes())

        await self.run(build, "Continue the chapter")

    async def run(self, build, title: str) -> None:
        with self.root:
            await self._run(build, title)

    async def _run(self, build, title: str) -> None:
        if not STATE.has_a_key():
            toast("Add a key in Settings first (OpenRouter, Venice, Experiential Labs or another service)", "warning")
            return
        try:
            prepared = build(Overrides())
        except AssemblyError as exc:
            toast(str(exc), "negative")
            return
        if not STATE.catalog.models:
            await STATE.refresh_catalog()
        primary_model, _ = STATE.models_for(self.project)
        final = await review(prepared, build, primary_model, title, f"Chapter {self.number}"
                             + (f" · {self.outline.title}" if self.outline.title else ""))
        if final is None:
            return
        await self.send(final)

    async def send(self, prepared: Prepared) -> None:
        live = WRITING[self.key] = Writing(version=(self.written.version + 1) if self.written else 1)
        self.header.refresh()
        self.body.refresh()
        try:
            result = await send(prepared, project=self.project, settings=STATE.app_settings(),
                                catalog=STATE.catalog, client=STATE.client(),
                                on_event=lambda event: self.on_event(live, event),
                                cancel=live.cancel, secret=STATE.api_key(), calibration_dir=settings_dir())
        finally:
            WRITING.pop(self.key, None)
        # The page that sent it may have closed meanwhile (a phone asleep too long): save everything
        # first, and only then touch the page if it is still there.
        gone = self.root.client.is_deleted
        run = LastRun(warnings=list(result.warnings) if result else [])
        if result.ok:
            if prepared.kind == "scenes":
                if gone:
                    PENDING_SCENES[self.key] = result.text  # shown when the chapter is opened again
                    return
                self.reload()
                self.header.refresh()
                self.body.refresh()
                await self.review_scenes(result.text)
                return
            chapter = self.chat.apply(self.project, prepared, result.text)
            target = prepared.target_words
            model = model_name(result.model) + (" (fallback)" if result.used_fallback else "")
            run.summary = (f"Saved as version {chapter.version} · {result.words:,} words (aimed for about "
                           f"{target:,}) · written by {model} · this request cost {money(result.total_cost)}")
            run.short = prepared.kind != CONTINUE and result.words < target * SHORT_SHARE
            if run.short:
                run.warnings.append(f"This came out much shorter than planned ({result.words:,} of about "
                                    f"{target:,} words). Continue writing asks the model to carry on from where "
                                    "it stopped.")
        else:
            run.failed = result.failure or "The request failed."
        LAST_RUN[self.number] = run
        if gone:
            return
        toast("Saved" if result.ok else "Nothing was saved", "positive" if result.ok else "negative")
        smooth_reload()  # the chapter list, sidebar and header all show the new state

    @staticmethod
    def on_event(live: Writing, event: Event) -> None:
        if event.kind == TEXT:
            live.text += event.text
            live.status = f"Writing with {model_name(event.model)}"
        elif event.kind == DISCARD:
            live.text = ""
            live.status = f"Discarded: {event.message}"
        elif event.kind in (WAIT, SWITCH, WARNING):
            live.status = event.message
        elif event.kind == ATTEMPT:
            live.status = f"{event.message}: waiting for the model…"
        elif event.kind in (REASONING, KEEPALIVE) and not live.text:
            live.status = f"{model_name(event.model)} is thinking…"

    def tick(self) -> None:
        live = self.live
        if live is None:
            if self.watching:  # it ended while this page watched: show the result
                self.watching = False
                smooth_reload()
            return
        if self.stream_label is not None:
            self.stream_label.text = live.text
            words = count_words(live.text)
            self.status_label.text = live.status + (f" · {words:,} words so far" if words else "")

    def stop(self) -> None:
        if self.live:
            self.live.cancel.set()
            self.live.status = "Stopping…"

    def mark_final(self, final: bool) -> None:
        self.chat.mark_final(self.project, final)
        toast("Marked final" if final else "Marked as draft")
        smooth_reload()

    def restore(self, version: int) -> None:
        self.chat.restore_version(self.project, version)
        toast(f"Version {version} is now the current text (saved as a new version)")
        smooth_reload()

    # --- versions ------------------------------------------------------------------------------

    def _version_text(self, number: int) -> str:
        path = self.project.root / f"chapters/versions/ch{self.number:02d}.v{number}.md"
        old = parse_chapter_file(read_text(path)[0], "", self.number, IssueList())
        return old.text if not old.scenes else "\n\n".join(old.scenes[n] for n in sorted(old.scenes))

    def compare_dialog(self) -> None:
        versions = self.chat.versions(self.project)
        others = {v.number: f"Version {v.number} · {v.words:,} words" for v in versions if not v.current}
        current = self.written.version
        with ui.dialog().props("maximized") as dialog, ui.card().classes("w-full h-full gap-0 p-0 no-wrap"):
            with ui.row().classes("dw-dialog-head w-full items-center gap-3"):
                ui.label("Compare versions").classes("dw-h2")
                select = ui.select(others, value=max(others), label="Earlier version").classes("w-64")
                ui.label(f"with the current text (version {current})").classes("dw-small")
                ui.space()
                quiet("Close", dialog.close)
            with ui.scroll_area().classes("w-full grow"):
                with ui.element("div").classes("dw-page narrow") as area:
                    pass

            def show() -> None:
                area.clear()
                with area:
                    ui.html(diff_html(self._version_text(select.value), self.display_text()), sanitize=False)

            select.on_value_change(show)
            show()
        dialog.open()

    def restore_dialog(self) -> None:
        versions = [v for v in self.chat.versions(self.project) if not v.current]
        with ui.dialog() as dialog, ui.card().classes("dw-card w-[34rem] gap-3"):
            ui.label("Restore an earlier version").classes("dw-h2")
            ui.label("The version you pick becomes the current text. The text you have now is kept as a "
                     "version too, so nothing is lost.").classes("dw-small")
            with ui.element("div").classes("dw-list"):
                for version in reversed(versions):
                    with ui.element("div").classes("dw-row"):
                        ui.label(f"Version {version.number}").classes("dw-body grow")
                        ui.label(f"{version.words:,} words").classes("dw-small")
                        secondary("Restore", lambda v=version.number: (dialog.close(), self.restore(v)))
            with ui.row().classes("w-full justify-end"):
                quiet("Cancel", dialog.close)
        dialog.open()

    # --- scenes ------------------------------------------------------------------------------

    async def propose_scenes(self) -> None:
        def build(overrides: Overrides) -> Prepared:
            return Prepared("scenes", self.number, None, prepare_scene_proposal(self.project, self.number))

        await self.run(build, "Propose scenes")

    async def review_scenes(self, reply: str) -> None:
        try:
            scenes = parse_scene_proposal(reply)
        except ValueError as exc:
            LAST_RUN[self.number] = LastRun(failed=f"{exc}. The full reply is in Activity.")
            self.body.refresh()
            return
        editors = []
        with ui.dialog() as dialog, ui.card().classes("dw-card w-[48rem] gap-3"):
            ui.label("Proposed scenes").classes("dw-h2")
            ui.label("Edit anything you like, then save them as this chapter's scenes.").classes("dw-small")
            with ui.scroll_area().classes("h-[60vh] w-full"):
                with ui.column().classes("gap-3 w-full pr-3"):
                    for number, scene in enumerate(scenes, 1):
                        with ui.card().classes("dw-card tight gap-2").props("flat"):
                            title = ui.input(f"Scene {number}", value=scene.title).classes("w-full")
                            beats = ui.textarea("What happens (one point per line)", value="\n".join(scene.beats)) \
                                .props("autogrow").classes("w-full")
                            editors.append((title, beats, scene))
            with ui.row().classes("w-full justify-end gap-2"):
                quiet("Discard", lambda: dialog.submit(False))
                primary("Save these scenes", lambda: dialog.submit(True))
        approve = await dialog
        dialog.delete()
        if approve:
            approved = [ProposedScene(t.value or "", [b for b in (bt.value or "").splitlines() if b.strip()],
                                      s.pov, s.characters) for t, bt, s in editors]
            apply_scene_proposal(self.project, self.number, approved)
            toast("Scenes saved")
        smooth_reload()


def _paragraphs(text: str) -> list[str]:
    return [p.strip().replace("\n", " ") for p in text.split("\n\n") if p.strip() and not p.startswith("— Scene ")]


def diff_html(old: str, new: str) -> str:
    """Paragraph by paragraph: removed text struck through in red, added text in green, unchanged text faded
    (long unchanged stretches folded)."""
    a, b = _paragraphs(old), _paragraphs(new)
    if a == b:
        return '<div class="dw-notice info">These two versions are identical.</div>'
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            same = a[i1:i2]
            shown = same if len(same) <= 3 else [same[0], None, same[-1]]
            for para in shown:
                if para is None:
                    out.append(f'<p class="dw-small" style="text-align:center">… {len(same) - 2} paragraphs '
                               "unchanged …</p>")
                else:
                    out.append(f'<p style="opacity:.55">{html.escape(para)}</p>')
            continue
        for para in a[i1:i2]:
            out.append('<p style="background:var(--dw-problem-bg);text-decoration:line-through;'
                       f'text-decoration-color:var(--dw-problem);padding:4px 8px;border-radius:6px">'
                       f"{html.escape(para)}</p>")
        for para in b[j1:j2]:
            out.append(f'<p style="background:var(--dw-final-bg);padding:4px 8px;border-radius:6px">'
                       f"{html.escape(para)}</p>")
    legend = ('<div class="dw-small" style="margin-bottom:16px">Removed text is struck through in red; '
              "new text is green.</div>")
    return f'{legend}<div class="dw-prose">{"".join(out)}</div>'
