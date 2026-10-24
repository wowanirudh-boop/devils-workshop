"""Auto-write: chapters, or their scenes, written without a review stop, several at the same time.

Each chapter is still its own chat, built exactly as the chapter screen builds
it, and every request is logged and costed as usual; it just isn't shown to you
first. Chapters never see each other's text, so writing several at once loses
nothing. Up to `at_once` requests run at the same time; earlier chapters go first.

Scene mode, in one of two ways:
- one after another (scenes_together off): all of a chapter's scenes in that
  chapter's one chat, each written with the scenes before it in front of the model;
- together (scenes_together on, D43): once the scenes are planned, they are all
  written at the same time, each from the scene plan (the scenes before it as
  beats). One small extra request then rewrites the opening of scenes 2 onwards
  so each follows on from the one before (joins.py).

A run stops, and says why, when:
- both models fail or refuse a request (nothing from that request is saved),
- a request is over your size limit (you decide what to leave out, on the chapter screen),
- the next request could take the run's spending past your cap,
- you press Stop.
Requests already under way when that happens are finished and saved (unless you
pressed Stop now); no new ones start.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from devils_workshop import resume
from devils_workshop import eta
from devils_workshop.chat.joins import apply_openings, opening, parse_join, prepare_join, to_smooth
from devils_workshop.chat.runner import models_for, send
from devils_workshop.chat.scenes import apply_scene_proposal, parse_scene_proposal, prepare_scene_proposal
from devils_workshop.chat.session import JOIN, ChapterChat, Prepared, _join
from devils_workshop.project.outline import PENDING_BRIEF
from devils_workshop.context.assemble import (AssemblyError, ChatMessage, assemble_continue, assemble_part,
                                              brief_ending)
from devils_workshop.keepawake import awake
from devils_workshop.llm.catalog import Catalog
from devils_workshop.llm.generate import ATTEMPT, DISCARD, SWITCH, TEXT, WAIT, WARNING, Event, GenerationResult
from devils_workshop.llm.openrouter import OpenRouterClient
from devils_workshop.llm.outcomes import CANCELLED
from devils_workshop.project.project import Project, load_project
from devils_workshop.settings import MAX_AT_ONCE, AppSettings
from devils_workshop.wordcount import count_words

AS_SET, WHOLE, SCENES = "as set", "whole", "scenes"
PLAN = "scenes"        # the job name of a scene plan (as on the chapter screen)
SHORT_SHARE = 0.6      # a reply under 60% of its target counts as short

# Run states
RUNNING, FINISHED, STOPPED, FAILED = "running", "finished", "stopped", "failed"
# Step states
WAITING, WRITING, DONE, SKIPPED = "waiting", "writing", "done", "skipped"

log = logging.getLogger(__name__)


@dataclass
class AutoOptions:
    chapters: list[int]
    mode: str = AS_SET              # AS_SET: each chapter as the outline / story.yaml sets it
    rewrite: bool = False           # write chapters that already have text again (earlier versions are kept)
    continue_short: bool = True     # ask once to carry on a reply under 60% of its target
    plan_scenes: bool = True        # scene mode, chapter without scenes: plan them first, approved automatically
    spend_cap: float | None = None  # dollars for the whole run
    at_once: int = 3                # requests at the same time (1: one after another)
    scenes_together: bool = False   # scene mode: write a chapter's scenes at the same time, then smooth the joins
    pause_after_chapter: bool = False  # stop after each chapter is written, so you can read it first


@dataclass
class Step:
    chapter: int
    label: str
    status: str = WAITING
    kind: str = "write"  # "write" | "plan" | "join"
    words: int = 0
    cost: float = 0.0
    note: str = ""
    live: str = ""       # the text streaming in while it is written
    activity: str = ""   # e.g. "Writing with GLM 5.3…"


class _Slots:
    """At most n requests at the same time; a free slot goes to the earliest chapter (then scene) waiting."""

    def __init__(self, n: int):
        self.free = n
        self.waiting: list = []
        self.order = itertools.count()

    async def acquire(self, priority: tuple) -> None:
        if self.free > 0 and not self.waiting:
            self.free -= 1
            return
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self.waiting, (priority, next(self.order), future))
        await future  # the slot is handed over by release()

    def release(self) -> None:
        while self.waiting:
            future = heapq.heappop(self.waiting)[2]
            if not future.done():
                future.set_result(None)
                return
        self.free += 1


@dataclass
class AutoRun:
    root: Path
    options: AutoOptions
    settings: AppSettings
    catalog: Catalog
    client: Callable[[], OpenRouterClient]
    secret: str | None = None
    calibration_dir: Path | None = None
    sizes: Callable[[str], dict] = lambda model: {}
    estimate: Callable[[str, int, int], tuple[float, float] | None] = lambda model, tokens, words: None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    name: Callable[[str], str] = lambda model: model  # a model's display name, for the live status

    steps: list[Step] = field(default_factory=list)
    status: str = RUNNING
    reason: str = ""
    spent: float = 0.0
    began: float = 0.0                         # when the run began (time.monotonic), for the time left
    stop_after_current: bool = False
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    _problem: tuple[str, str] | None = None   # the first reason the run stopped early
    reason_extra: str = ""                     # e.g. why the run paused
    _reserved: float = 0.0                     # worst-case cost of the requests under way
    _slots: _Slots | None = None
    _finished: set[int] = field(default_factory=set)  # chapters this run has written (or found written)
    _flagged: set[int] = field(default_factory=set)   # chapters with problems the checks couldn't put right

    @property
    def running(self) -> bool:
        return self.status == RUNNING

    @property
    def writing(self) -> list[Step]:
        """The steps being written right now."""
        return [s for s in self.steps if s.status == WRITING]

    def stop(self, now: bool = False) -> None:
        """Stop after the requests under way (or now: they are abandoned and nothing from them saved)."""
        self.stop_after_current = True
        if now:
            self.cancel.set()

    # --- the run ------------------------------------------------------------------------------

    async def run(self) -> None:
        self._note()
        self.began = time.monotonic()
        with awake():  # the whole run, pauses between requests included
            await self._run()
        written = self.written_words()
        if written >= 3000:  # a pace worth keeping: the next run's first estimate starts from it
            eta.save_pace((time.monotonic() - self.began) / 60 / (written / 1000))

    def written_words(self) -> int:
        """The words this run has written so far, each scene once (one written again replaces itself)."""
        latest = {s.label: s.words for s in self.steps
                  if s.kind == "write" and s.status == DONE and s.words and "put right" not in s.label}
        return sum(latest.values())

    def _note(self) -> None:
        """Keep a note of the run and the chapters it still has to write, so it carries on if the app closes before
        it ends (devils_workshop/resume.py)."""
        options = asdict(self.options)
        options["chapters"] = [n for n in sorted(set(self.options.chapters)) if n not in self._finished]
        resume.note(self.root, resume.AUTO, options=options, spent=self.spent)

    async def _chapter_and_note(self, number: int) -> None:
        await self._chapter(number)
        if not self._problem and not self.stop_after_current:
            self._finished.add(number)
            self._note()

    async def _run(self) -> None:
        self._slots = _Slots(max(1, min(int(self.options.at_once or 1), MAX_AT_ONCE)))
        try:
            numbers = sorted(set(self.options.chapters))
            if self._planner_story() and numbers:  # each chapter follows the one before it as written: finish those
                earlier = unfinished_before(load_project(self.root), numbers[0])
                if earlier:
                    numbers = sorted(set(numbers) | set(earlier))
                    self.reason_extra += (f" Chapter{'s' if len(earlier) > 1 else ''} {', '.join(map(str, earlier))} "
                                          "wasn't finished, so it was finished first.")
            if self.options.pause_after_chapter or self._briefs_just_in_time():
                # One chapter at a time: its briefs follow on from what was written before it.
                for number in numbers:
                    if self.stop_after_current or self._problem:
                        break
                    written = sum(1 for s in self.steps if s.status == DONE and s.kind == "write")
                    await self._chapter_and_note(number)
                    wrote = sum(1 for s in self.steps if s.status == DONE and s.kind == "write") > written
                    if wrote and self.options.pause_after_chapter and number != numbers[-1] and \
                            number in self.options.chapters:
                        self.reason_extra = (f" Paused after chapter {number} so you can read it: in the Reader, ask "
                                             "for changes, or write the next chapter.")
                        break
            else:
                # Started in order, so the earliest chapter takes the first slot; later ones queue by chapter.
                await asyncio.gather(*(self._chapter_and_note(number) for number in numbers))
            if self._problem:
                self._end(*self._problem)
            elif self.stop_after_current:
                self._end(STOPPED, "Stopped by you.")
            else:
                written = sum(1 for s in self.steps if s.status == DONE and s.kind == "write" and s.words)
                what = "1 part" if written == 1 else f"{written} parts"
                flagged = sorted(self._flagged)
                if flagged:
                    self.reason_extra += (f" Chapter{'s' if len(flagged) > 1 else ''} {', '.join(map(str, flagged))} "
                                          "has problems the checks couldn't put right: the manuscript check lists "
                                          "them, and the Reader is where to ask for the change.")
                self._end(FINISHED, f"Finished: {what} written, ${self.spent:.2f} spent." + self.reason_extra
                          + self._manuscript_check())
        except Exception as exc:  # anything unexpected ends the run with its reason, never silently
            log.exception("Auto-write stopped by an error")
            self.cancel.set()
            self._end(FAILED, f"Stopped by an unexpected error: {exc}")

    async def _chapter(self, number: int) -> None:
        """Write one chapter (its plan and scenes, or the whole of it)."""
        project = load_project(self.root)
        outline = project.outline.chapter(number)
        if outline is None:
            self._add(Step(number, f"Chapter {number}", SKIPPED, note="not in the outline"))
            return
        chat = ChapterChat.load(self.root, number)
        mode = chat.default_mode(project) if self.options.mode == AS_SET else self.options.mode
        written = project.chapters.get(number)
        has_text = bool(written and (written.text or written.scenes))
        unfinished_scenes = bool(written and written.scenes and outline.scenes and chat.next_scene(project))

        if has_text and not self.options.rewrite and not (mode == SCENES and unfinished_scenes):
            if chat.smooth_pending and written.scenes and not unfinished_scenes:
                # Written together in an earlier run that stopped before the joins were smoothed.
                await self._smooth(chat, number, self.sizes(models_for(project, self.settings)[0]))
                return
            self._add(Step(number, f"Chapter {number}", SKIPPED,
                           note=f"already written (version {written.version}); left as it is"))
            return

        planner = mode == SCENES and self._planner_story()
        if planner and not await self._before_chapter(number, has_text and self.options.rewrite):
            return
        project = load_project(self.root)
        outline = project.outline.chapter(number)
        if mode == SCENES and not outline.scenes:
            if not self.options.plan_scenes:
                mode = WHOLE
            elif not await self._plan(number):
                return
        briefs: asyncio.Future | None = None  # the chapter's briefs, written while its first scenes are
        if mode == SCENES and any(s.text.strip().startswith(PENDING_BRIEF[:20]) for s in outline.scenes):
            if planner and self._in_order():  # each scene is written as soon as its brief is ready
                briefs = asyncio.ensure_future(self._planner_briefs(number))
            elif not await self._planner_briefs(number):  # the planner writes them now, from what came before
                return
        restart = has_text and self.options.rewrite
        project = load_project(self.root)
        outline = project.outline.chapter(number)
        sizes = self.sizes(models_for(project, self.settings)[0])

        if mode == WHOLE:
            prepared = chat.prepare_write(project, WHOLE, **sizes)
            await self._write(project, chat, prepared, f"Chapter {number}")
        elif planner and self._in_order():
            try:
                await self._scenes_in_order(project, chat, number, restart, sizes, briefs)
            finally:
                if briefs is not None and not briefs.done():
                    await briefs  # stopped meanwhile: the brief under way is finished and saved
        elif self.options.scenes_together or project.config.in_chapter_context == "independent":
            await self._scenes_together(project, chat, number, restart, sizes)  # independent: always at once
            if planner and not self._problem and not self.cancel.is_set():
                written = load_project(self.root).chapters.get(number)
                if written and written.scenes:
                    await self._check_scenes(number, sorted(written.scenes), sizes)
        else:
            while not self.stop_after_current:
                if not restart and chat.next_scene(project) is None:
                    break
                prepared = chat.prepare_write(project, SCENES, restart=restart, **sizes)
                restart = False
                label = f"Chapter {number}, scene {prepared.scene} of {len(outline.scenes) or '?'}"
                if not await self._write(project, chat, prepared, label):
                    return

    def _briefs_just_in_time(self) -> bool:
        from devils_workshop.planner.state import PlannerState, is_planner_story
        return is_planner_story(self.root) and PlannerState.load(self.root).jit

    async def _planner_briefs(self, number: int) -> bool:
        """A chapter planned by the planner, briefs just in time: the planner first follows the chapter before it
        as it was written, then writes this chapter's briefs (with the planner's own models)."""
        return await self._planner(number, ("briefs", number), f"Chapter {number}: the planner writes its scene "
                                                                "briefs") is not None

    async def _planner(self, number: int, only: tuple, label: str):
        """One planning run for this chapter (the planner's models): its last step, or None if it couldn't finish
        (the run stops, and says why)."""
        from devils_workshop.planner.run import FINISHED as PLANNED
        from devils_workshop.planner.run import PlanRun
        step = self._add(Step(number, label, WRITING, kind="plan"))
        step.activity = "Planning…"
        plan_run = PlanRun(self.root, self.settings, self.catalog, self.client, only=only,
                           secret=self.secret, sleep=self.sleep, estimate=self.estimate, name=self.name, remember=False,
                           spend_cap=None if self.options.spend_cap is None else self.options.spend_cap - self.spent)
        task = asyncio.ensure_future(plan_run.run())
        while not task.done():
            current = plan_run.steps[-1] if plan_run.steps else None
            step.activity = current.label + (f": {current.activity}" if current and current.activity else "") \
                if current else "Planning…"
            step.live = current.live[-2000:] if current else ""
            if self.cancel.is_set():
                plan_run.stop(now=True)
            await asyncio.sleep(0.2)
        step.cost = plan_run.spent
        self.spent += plan_run.spent
        self._note()
        step.live = step.activity = ""
        if plan_run.status != PLANNED:
            self._fail(step, f"{label}: the planner couldn't finish: {plan_run.reason}")
            return None
        step.status, step.note = DONE, "; ".join(s.note for s in plan_run.steps if s.note) or "done"
        return plan_run

    # --- stories the planner planned: each chapter follows what was written, each scene is checked ------------

    def _manuscript_check(self) -> str:
        """Once every chapter the planner planned is written: the whole manuscript checked (no request), and the
        report saved as Manuscript check.md."""
        from devils_workshop.planner.manuscript import check_manuscript, write_report
        from devils_workshop.planner.state import PlannerState
        if not self._planner_story():
            return ""
        state = PlannerState.load(self.root)
        written = load_project(self.root).chapters
        if not state.chapters or any(c.number not in written for c in state.chapters):
            return ""
        report = check_manuscript(self.root)
        write_report(self.root, report)
        return f" Manuscript check: {report.verdict} (Manuscript check.md)."

    def _planner_story(self) -> bool:
        from devils_workshop.planner.state import is_planner_story
        return is_planner_story(self.root) and load_project(self.root).config.in_chapter_context == "independent"

    def _in_order(self) -> bool:
        from devils_workshop.planner.state import IN_ORDER, PlannerState
        return PlannerState.load(self.root).scene_order == IN_ORDER

    async def _before_chapter(self, number: int, rewrite: bool) -> bool:
        """Before a chapter the planner planned is written: the canon and the guide's timing are up to date, an
        adaptation still carries its reference, the chapter before is followed as written (if it changed since,
        or was never followed), and if the registry shows that what this chapter is planned to do already
        happened, or was moved into it, its scenes are planned again first."""
        from devils_workshop.planner import canon
        from devils_workshop.planner.registry import forget_written
        from devils_workshop.planner.review import needs_reconcile
        from devils_workshop.planner.run import canon_due, redo_chapter
        from devils_workshop.planner.state import PlannerState

        state = PlannerState.load(self.root)
        if canon_due(state) and not await self._planner(number, ("canon",), "The story's canon, and the guide as it "
                                                                            "stands at this chapter"):
            return False
        problems = [f for f in canon.mapping_findings(PlannerState.load(self.root).registry) if f.severity == "hard"]
        if problems:
            self._fail(self._add(Step(number, f"Chapter {number}", kind="plan")),
                       "The chapters don't carry the reference as they should: " + "; ".join(f.text for f in problems))
            return False
        state = PlannerState.load(self.root)
        if rewrite:
            forget_written(state.registry, number)
            state.audits.pop(str(number), None)
            state.save()
        if ensure_registry(state):
            state.save()
        stale = stale_scenes(state, number - 1) if number > 1 else []
        if stale:  # checked before, changed since (the Reader's changes, or by hand): checked again
            sizes = self.sizes(models_for(load_project(self.root), self.settings)[0])
            if not await self._check_scenes(number - 1, stale, sizes):
                return False
            state = PlannerState.load(self.root)
        if number > 1 and needs_reconcile(state, number - 1, self.root):
            if not await self._planner(number, ("reconcile", number), f"Chapter {number - 1}: how things stand "
                                                                       "after it, as written"):
                return False
            state = PlannerState.load(self.root)
        note = replan_note(state, number)
        written = load_project(self.root).chapters.get(number)
        if note and not (written and (written.scenes or written.text.strip())):
            redo_chapter(state, number, note)
            if not await self._planner(number, ("chapter", number), f"Chapter {number}: its scenes planned again "
                                                                     "from what has happened"):
                return False
        return True

    async def _scenes_in_order(self, project: Project, chat: ChapterChat, number: int, restart: bool,
                               sizes: dict, briefs: asyncio.Future | None = None) -> None:
        """Each scene after the one before, from its brief alone, then checked. A scene is written as soon as its
        brief is ready (briefs: the planner still writing the chapter's later ones), while the scene before it is
        still being checked: when that check finds the scene before departed from its brief in a way this one
        must know, its brief is brought in line with how things actually stand and it is written again;
        otherwise nothing waits for the check."""
        outline = project.outline.chapter(number)
        written = project.chapters.get(number)
        done = set() if restart or not written else set(written.scenes)
        first = True
        checking: asyncio.Future | None = None  # the check of the scene before, while this one is written
        try:
            for scene in outline.scenes:
                if scene.number in done:
                    continue
                if self.stop_after_current or self._problem:
                    return
                if not await self._brief_ready(number, scene.number, briefs):
                    return
                if checking is None and not await self._follow_on(number, scene.number, briefs):
                    return
                text, step = await self._scene_from_brief(number, chat, scene.number, len(outline.scenes), sizes)
                if checking is not None:
                    checked, checking = await checking, None
                    if not checked:
                        return  # the run stops (and says why); this scene is written again when it carries on
                    if text is not None and self._needs_follow_on(number, scene.number):
                        if not await self._follow_on(number, scene.number, briefs):
                            return
                        step.note = "written again: the scene before it ended differently from its brief"
                        text, _ = await self._scene_from_brief(number, chat, scene.number, len(outline.scenes), sizes)
                if text is None:
                    return
                chat.apply_together(load_project(self.root), {scene.number: text}, restart=restart and first)
                first = False
                checking = asyncio.ensure_future(self._check_scenes(number, [scene.number], sizes))
        finally:
            if checking is not None:
                await checking

    async def _brief_ready(self, number: int, scene: int, briefs: asyncio.Future | None) -> bool:
        """Wait until the planner has written this scene's brief. False: it couldn't (the run stops, and says why)."""
        def written() -> bool:
            outline = load_project(self.root).outline.chapter(number)
            found = next((s for s in outline.scenes if s.number == scene), None) if outline else None
            return found is not None and not found.text.strip().startswith(PENDING_BRIEF[:20])

        while not written():
            if briefs is None or briefs.done():
                return bool(briefs is None or briefs.result()) and written()
            await asyncio.sleep(0.5)
        return True

    async def _scene_from_brief(self, number: int, chat: ChapterChat, scene: int, scenes: int,
                                sizes: dict) -> tuple[str | None, Step]:
        """One scene written from its brief as it is now: its text, or None (the run stops, and says why); and
        its step."""
        project = load_project(self.root)
        step = self._add(Step(number, f"Chapter {number}, scene {scene} of {scenes}"))
        try:
            prepared = chat.prepare_scene_alone(project, scene, **sizes)
        except AssemblyError as exc:  # the last check on the request refused it: nothing is sent
            self._fail(step, f"{step.label}: {exc}")
            return None, step
        return await self._scene_alone(project, prepared, step), step

    def _needs_follow_on(self, number: int, scene: int) -> bool:
        """The scene before this one departed from its brief, and this brief was written from how it was planned
        to end."""
        from devils_workshop.planner.audit import text_hash
        from devils_workshop.planner.run import exit_before
        from devils_workshop.planner.state import PlannerState

        state = PlannerState.load(self.root)
        chapter = state.chapter(number)
        planned = next((s for s in chapter.scenes if s.number == scene), None) if chapter else None
        if planned is None or not planned.brief.strip():
            return False
        return bool(previous_departures(state, number, scene)) and \
            planned.follows != text_hash(exit_before(state, number, scene))

    async def _follow_on(self, number: int, scene: int, briefs: asyncio.Future | None = None) -> bool:
        """The scene's brief follows how things actually stand: when the scene before it departed from its brief
        and this brief was written from how it was planned to end, the planner writes the brief again (changed only
        as far as that needs)."""
        from devils_workshop.planner.run import redo_scene
        from devils_workshop.planner.state import PlannerState

        if not self._needs_follow_on(number, scene):
            return True
        if briefs is not None and not briefs.done() and not await briefs:  # one planner at a time on the briefs
            return False
        state = PlannerState.load(self.root)
        departures = previous_departures(state, number, scene)
        note = ("The scene before this one has been written, and it departed from its brief, so how things stand "
                "coming in is now as <continuity_so_far> says. Change this brief only as far as that needs: where "
                "everyone is, what they hold and know, and how the scene opens. Where the scene before departed:\n"
                + "\n".join(f"- {d}" for d in departures))
        redo_scene(state, number, scene, note)
        return await self._planner(number, ("scene", number, scene), f"Chapter {number}, scene {scene}: its brief "
                                                                      "follows on from what was written") is not None

    async def _check_scenes(self, number: int, scenes: list[int], sizes: dict) -> bool:
        """The planner checks the scenes as written. A scene with something to put right is changed once by the
        writer, with the problems named, and checked again; if a problem is still there, the run stops and says
        what, and the text is kept for you to read (the Reader, or the manuscript check, shows it)."""
        from devils_workshop.planner.audit import findings_of, hard
        from devils_workshop.planner.state import PlannerState

        if not await self._planner(number, ("audit", number, scenes), f"Chapter {number}: checking scene"
                                   f"{'s' if len(scenes) > 1 else ''} {', '.join(map(str, scenes))} as written"):
            return False
        problems = _by_scene(hard(findings_of(PlannerState.load(self.root), number)), scenes)
        if not problems:
            return True
        project = load_project(self.root)
        chat = ChapterChat.load(self.root, number)
        texts = {}
        for scene, found in sorted(problems.items()):
            step = self._add(Step(number, f"Chapter {number}, scene {scene}: put right what the check found"))
            change = REPAIR + "\n".join(f"- {f.text}" for f in found)
            try:
                prepared = chat.prepare_change(project, change, scene, **sizes)
            except AssemblyError as exc:  # the last check on the request refused it: nothing is sent
                self._fail(step, f"{step.label}: {exc}")
                return False
            result = await self._request(project, prepared, step)
            if result is None:
                return False
            texts[scene] = result.text.strip()
            step.words, step.status = result.words, DONE
        chat.replace_scenes(load_project(self.root), texts, action=f"ch{number:02d}-put-right")
        again = sorted(texts)
        if not await self._planner(number, ("audit", number, again), f"Chapter {number}: checking scene"
                                   f"{'s' if len(again) > 1 else ''} {', '.join(map(str, again))} again"):
            return False
        still = _by_scene(hard(findings_of(PlannerState.load(self.root), number)), again)
        if still:  # kept and shown, and the chapter goes on: a chapter is never left half-written for it
            kinds = sorted({f.kind for found in still.values() for f in found})
            self.steps[-1].note = (f"still to put right after one more try (scene{'s' if len(still) > 1 else ''} "
                                   f"{', '.join(map(str, sorted(still)))}: {', '.join(kinds)}); kept, and listed in "
                                   "the manuscript check")
            self._flagged.add(number)
            self._log_run("kept with problems", number, scenes=sorted(still), finding_kinds=kinds)
        return True

    async def _plan(self, number: int) -> bool:
        step = self._add(Step(number, f"Chapter {number}: plan scenes", kind="plan"))
        project = load_project(self.root)
        prepared = Prepared(PLAN, number, None, prepare_scene_proposal(project, number))
        result = await self._request(project, prepared, step)
        if result is None:
            return False
        try:
            scenes = parse_scene_proposal(result.text)
        except ValueError as exc:
            self._fail(step, f"Chapter {number}: the scene plan couldn't be read ({exc}). The reply is in Activity.")
            return False
        # Other chapters may have saved their plans meanwhile: read the outline again just before saving.
        apply_scene_proposal(load_project(self.root), number, scenes)
        step.status, step.note = DONE, f"{len(scenes)} scenes planned and saved (undo in History)"
        return True

    async def _write(self, project: Project, chat: ChapterChat, prepared: Prepared, label: str) -> bool:
        """A whole chapter, or the next scene in the chapter's chat; saved as soon as it is back."""
        step = self._add(Step(prepared.chapter, label))
        result = await self._request(project, prepared, step, keep_slot=self.options.continue_short)
        if result is None:
            return False
        try:
            chat.apply(project, prepared, result.text)
            step.words = result.words
            if self.options.continue_short and stopped_short(result, prepared.target_words):
                more = chat.prepare_continue(project, **self.sizes(models_for(project, self.settings)[0]))
                extra = await self._request(project, more, step, holding=True)
                if extra is None:
                    return False
                chat.apply(project, more, extra.text)
                step.words += extra.words
                step.note = "continued once (the first reply was short)"
                result = extra
        finally:
            if self.options.continue_short:
                self._slots.release()
        if result.used_fallback:
            step.note = (step.note + "; " if step.note else "") + f"written by the fallback model ({result.model})"
        step.status = DONE
        return True

    async def _scenes_together(self, project: Project, chat: ChapterChat, number: int, restart: bool,
                               sizes: dict) -> None:
        outline = project.outline.chapter(number)
        written = project.chapters.get(number)
        done = {} if restart or not written else dict(written.scenes)
        todo = [s.number for s in outline.scenes if s.number not in done]
        prepared = {n: chat.prepare_scene_alone(project, n, **sizes) for n in todo}
        steps = {n: self._add(Step(number, f"Chapter {number}, scene {n} of {len(outline.scenes)}")) for n in todo}
        results = await asyncio.gather(*(self._scene_alone(project, prepared[n], steps[n]) for n in todo))
        texts = {n: text for n, text in zip(todo, results) if text is not None}
        if texts:
            chat.apply_together(load_project(self.root), texts, restart=restart)
        if len(texts) < len(todo) or self.cancel.is_set():
            return  # a scene is missing: no joins to smooth yet (writing the rest later smooths them)
        if len(outline.scenes) > 1 and chat.smooth_pending:
            await self._smooth(chat, number, sizes)

    async def _scene_alone(self, project: Project, prepared: Prepared, step: Step) -> str | None:
        """One scene written at the same time as the others. Its text, or None (nothing to save). A scene longer
        than one reply holds is written in parts, each carrying on from the one before in the same chat."""
        parts = prepared.request.parts
        keep = self.options.continue_short or bool(parts)
        result = await self._request(project, prepared, step, keep_slot=keep)
        if result is None:
            return None
        text, step.words = result.text.strip(), result.words
        try:
            if parts:
                history = prepared.messages + [ChatMessage("assistant", text)]
                ending = brief_ending(project.outline.chapter(prepared.chapter).scene(prepared.scene).text)
                for part in parts[1:]:
                    last = part is parts[-1]
                    request = assemble_part(project, prepared.chapter, prepared.scene, history, part,
                                            ending if last else "", **self.sizes(models_for(project, self.settings)[0]))
                    extra = await self._request(project, Prepared("continue", prepared.chapter, prepared.scene,
                                                                  request), step, holding=True)
                    if extra is None:
                        return text  # what came back so far is kept; the run stops
                    piece = extra.text.strip()
                    text, step.words = _join(text, piece), step.words + extra.words
                    history = request.messages + [ChatMessage("assistant", piece)]
                    result = extra
                step.note = f"written in {len(parts)} parts (a long scene)"
            elif self.options.continue_short and stopped_short(result, prepared.target_words):
                history = prepared.messages + [ChatMessage("assistant", text)]
                request = assemble_continue(project, prepared.chapter, "scenes", history, prepared.scene,
                                            **self.sizes(models_for(project, self.settings)[0]))
                request.target_words = max(request.target_words - result.words, 300)
                more = Prepared("continue", prepared.chapter, prepared.scene, request)
                extra = await self._request(project, more, step, holding=True)
                if extra is None:
                    return text  # what came back first is kept; the run stops
                text = _join(text, extra.text.strip())
                step.words += extra.words
                step.note = "continued once (the first reply was short)"
                result = extra
        finally:
            if keep:
                self._slots.release()
        if result.used_fallback:
            step.note = (step.note + "; " if step.note else "") + f"written by the fallback model ({result.model})"
        step.status = DONE
        return text

    async def _smooth(self, chat: ChapterChat, number: int, sizes: dict) -> None:
        """One small request: new openings for scenes 2 onwards, so each follows on from the one before."""
        project = load_project(self.root)
        scenes = project.chapters[number].scenes
        if not to_smooth(scenes):
            chat.smooth_pending = False  # every scene is a single paragraph: there is no opening to rewrite
            chat.save()
            return
        step = self._add(Step(number, f"Chapter {number}: smooth the joins between scenes", kind="join"))
        prepared = Prepared(JOIN, number, None, prepare_join(project, number, scenes, **sizes))
        result = await self._request(project, prepared, step, fatal=False, finishing=True)
        if result is None:
            return  # still pending: the next run for this chapter, or the chapter screen, smooths them
        openings, rejected = parse_join(result.text, scenes)
        if openings:
            chat.apply_join(load_project(self.root), apply_openings(scenes, openings))
        else:
            chat.smooth_pending = False  # tried; the step says the reply couldn't be used
            chat.save()
        changed = [n for n, text in openings.items() if text.split() != opening(scenes[n]).split()]
        step.words = sum(count_words(openings[n]) for n in changed)
        if not openings:
            step.note = "the reply couldn't be used, so the scenes are as written (the reply is in Activity)"
        else:
            step.note = (f"new opening for scene{'s' if len(changed) != 1 else ''} "
                         f"{', '.join(map(str, changed))}" if changed else "the openings already followed on")
            if rejected:
                step.note += f"; scene{'s' if len(rejected) != 1 else ''} {', '.join(map(str, rejected))} " \
                             "left as written (the new opening didn't look right)"
        step.status = DONE

    # --- sending, with the checks ------------------------------------------------------------

    async def _request(self, project: Project, prepared: Prepared, step: Step, keep_slot: bool = False,
                       holding: bool = False, fatal: bool = True, finishing: bool = False) -> GenerationResult | None:
        """Wait for a free slot, check size and spending, then send. None: nothing to save (the step says
        why; unless fatal=False, the run stops). keep_slot: the caller releases the slot (it may send a
        continuation in it); holding: the caller already has a slot; finishing: it completes work already
        under way (the joins of scenes just written), so it goes ahead while the run is stopping, unless
        you pressed Stop now."""
        if not holding:
            step.activity = "Waiting for a free slot…"
            await self._slots.acquire((step.chapter, len(self.steps)))
            if self.stop_after_current and not (finishing and not self.cancel.is_set()):
                self._slots.release()
                step.status, step.note, step.activity = SKIPPED, "not started (the run was stopping)", ""
                return None
        step.status = WRITING
        result = None
        try:
            size = prepared.request.size
            if size.over_limit or size.exceeds_context:
                return self._fail(step, f"{step.label}: {size.message()} Open the chapter to choose what to leave "
                                        "out, then start auto-write again.", status=STOPPED)
            worst = self._worst(project, prepared)
            cap = self.options.spend_cap
            if cap is not None and self.spent + self._reserved + worst > cap:
                return self._fail(step, f"Spending cap: ${self.spent:.4f} spent so far, and the next request could "
                                        f"cost up to ${worst:.4f}, which would pass your cap of ${cap:.2f}.",
                                  status=STOPPED)
            self._reserved += worst
            try:
                result = await self._send(project, prepared, step)
            finally:
                self._reserved -= worst
            step.cost += result.total_cost
            self.spent += result.total_cost
            self._note()
            if result.ok:
                return result
            failed, result = result, None  # nothing to keep: the slot is handed back below
            if failed.outcome == CANCELLED:
                return self._fail(step, "Stopped by you. Nothing from the unfinished request was saved.",
                                  status=STOPPED)
            reason = f"{step.label}: {failed.failure or 'the request failed'}. Nothing from this request was " \
                     "saved; every attempt is in Activity."
            if not fatal:
                step.status, step.note = SKIPPED, reason
                return None
            return self._fail(step, reason)
        finally:
            step.live, step.activity = "", ""
            if not holding and not (keep_slot and result is not None):
                self._slots.release()

    def _worst(self, project: Project, prepared: Prepared) -> float:
        estimate = self.estimate(models_for(project, self.settings)[0], prepared.request.size.tokens,
                                 prepared.target_words)
        return estimate[1] if estimate else 0.0

    async def _send(self, project: Project, prepared: Prepared, step: Step) -> GenerationResult:
        step.live, step.activity = "", "Sending…"

        def on_event(event: Event) -> None:
            if event.kind == TEXT:
                step.live += event.text
                step.activity = f"Writing with {self.name(event.model)}…"
            elif event.kind == DISCARD:
                step.live = ""
                step.activity = f"Discarded: {event.message}"
            elif event.kind in (WAIT, SWITCH, WARNING):
                step.activity = event.message
            elif event.kind == ATTEMPT:
                step.activity = f"Waiting for {self.name(event.model)}…"

        return await send(prepared, project=project, settings=self.settings, catalog=self.catalog,
                          client=self.client(), on_event=on_event, cancel=self.cancel, secret=self.secret,
                          calibration_dir=self.calibration_dir, sleep=self.sleep)

    # --- bookkeeping ----------------------------------------------------------------------------

    def _add(self, step: Step) -> Step:
        self.steps.append(step)
        return step

    def _fail(self, step: Step, reason: str, status: str = FAILED) -> None:
        """Mark the step, and stop the run once the requests under way are done. The first problem is the
        run's reason. Returns None, so `return self._fail(...)` reads as "no result"."""
        step.status = status
        step.note = reason
        if self._problem is None:
            self._problem = (status, reason)
            self._log_run("stopped", step.chapter, status=status, step_kind=step.kind,
                          cause="spending cap" if reason.startswith("Spending cap") else
                          "stopped by you" if "Stopped by you" in reason else
                          "request too large" if status == STOPPED else "failed")
        self.stop_after_current = True
        return None

    def _log_run(self, event: str, chapter: int | None, **details) -> None:
        """What happened to the run, for the content-free diagnostics: never the reason's text (it can quote the
        story), only what kind of thing happened, and where."""
        from devils_workshop.llm import diagnostics
        diagnostics.write({"kind": "run", "event": event, "story": diagnostics.story_key(self.root),
                           "chapter": chapter, "chapters": sorted(set(self.options.chapters)), **details})

    def _end(self, status: str, reason: str) -> None:
        self.status, self.reason = status, reason
        resume.forget(self.root, resume.AUTO)  # it ended where the app saw it (a pause too): nothing to carry on


REPAIR = ("The check of this scene as written found what must be put right before the story goes on. Change the "
          "scene only as far as that needs, and keep everything else as it is. The reader knows only what earlier "
          "scenes show: never put something right by saying it was already said, seen or done off the page:\n")


def _by_scene(findings: list, scenes: list[int]) -> dict[int, list]:
    """The problems of the scenes just checked, by scene. A chapter's event that no scene was planned to carry,
    and never happened, goes to the last scene checked."""
    found: dict[int, list] = {}
    for finding in findings:
        scene = finding.scene if finding.scene in scenes else (
            max(scenes) if finding.kind in ("missing", "ending") and not finding.scene else None)
        if scene is not None:
            found.setdefault(scene, []).append(finding)
    return found


def previous_departures(state, number: int, scene: int) -> list[str]:
    """Where the scene before this one (in this chapter, or the last of the chapter before) departed from its
    brief, as its check (or the planner's reading of that chapter) found."""
    if scene > 1:
        chapter, before = number, scene - 1
    else:
        previous = state.chapter(number - 1)
        if previous is None or not previous.scenes:
            return []
        chapter, before = previous.number, previous.scenes[-1].number
    entry = ((state.audits.get(str(chapter)) or {}).get("scenes") or {}).get(str(before))
    if entry is not None:
        return list(entry.get("departures") or [])
    return list(state.departures.get(str(chapter)) or []) if scene == 1 else []


def replan_note(state, number: int) -> str:
    """When the registry shows that what this chapter is planned to do already happened earlier, or events were
    moved into it, the note its scenes are planned again with ("" when there is nothing new since they were)."""
    import hashlib
    events = state.registry.of_chapter(number)
    early = [e for e in events if e.written_in and e.written_in[0] < number]
    moved = [e for e in events if e.moved and not e.written_in]
    if not early and not moved:
        return ""
    lines = []
    if early:
        lines.append("These events planned for this chapter already happened earlier, so plan them only as "
                     "something remembered or referred to, never as happening again:")
        lines += [f"- {e.id}: {e.text} (happened in chapter {e.written_in[0]}, scene {e.written_in[1]})"
                  for e in early]
    if moved:
        lines.append("These events were moved into this chapter, so plan them in:")
        lines += [f"- {e.id}: {e.text}" for e in moved]
    note = "\n".join(lines)
    mark = hashlib.sha256(note.encode("utf-8")).hexdigest()[:16]
    if state.replanned.get(str(number)) == mark:
        return ""
    state.replanned[str(number)] = mark
    return note


def stopped_short(result: GenerationResult, target_words: int) -> bool:
    """A reply under 60% of its target that was cut off (the model ran out of room, or it stops mid-sentence) is
    carried on once. One that ends where it means to is kept as it is: asking for more would only pad it, or
    tell its events again."""
    if result.words >= target_words * SHORT_SHARE:
        return False
    finish = result.attempts[-1].finish_reason if result.attempts else None
    text = result.text.rstrip().rstrip("*_ ")
    return finish == "length" or not text or text[-1] not in '.!?"”’)…'


def ensure_registry(state) -> bool:
    """A story planned before the registry: its events from its chapter architecture (True if made now)."""
    from devils_workshop.planner.registry import assign_scenes, events_from
    if state.registry.events or not state.chapters:
        return False
    state.registry.events = events_from(state.chapters)
    for chapter in state.chapters:
        assign_scenes(state.registry, chapter)
    return True


def stale_scenes(state, number: int) -> list[int]:
    """The scenes of a chapter that were checked as written and have changed since."""
    from devils_workshop.planner.run import audited
    entry = state.audits.get(str(number)) or {}
    checked = sorted(int(n) for n in (entry.get("scenes") or {}))
    return [n for n in checked if not audited(state, number, n)]


def chapter_finished(project: Project, number: int) -> bool:
    """Written, and in scene mode with every scene of its outline written."""
    written = project.chapters.get(number)
    if written is None or not (written.text.strip() or written.scenes):
        return False
    outline = project.outline.chapter(number)
    if not written.scenes or outline is None:
        return True
    return all(s.number in written.scenes for s in outline.scenes)


def unfinished_before(project: Project, number: int) -> list[int]:
    """The planned chapters before this one that aren't finished (not written, or some scenes missing)."""
    return [c.number for c in project.outline.chapters if c.number < number and not chapter_finished(project, c.number)]
