"""Sending the planner's requests: one brainstorm turn, and the planning run.

The planning run goes one request at a time: the chapter architecture (if there is none), then for each
chapter its scene list, then one brief per scene, in order. The state and the story files are saved after
every step, so a run can stop (or fail, or the app close) and carry on later from where it was.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from devils_workshop.context.assemble import ChatMessage
from devils_workshop import resume
from devils_workshop.history import snapshot
from devils_workshop.keepawake import awake
from devils_workshop.llm.catalog import Catalog
from devils_workshop.llm.generate import (
    ATTEMPT,
    DISCARD,
    SWITCH,
    TEXT,
    WAIT,
    WARNING,
    Event,
    GenerationResult,
    generate,
    message_tokens,
)
from devils_workshop.llm.openrouter import OpenRouterClient
from devils_workshop.llm.outcomes import CANCELLED
from devils_workshop.planner import audit, canon, prompts, review
from devils_workshop.planner.output import OUTPUT_FILES, write_outputs
from devils_workshop.planner.registry import IMPORT, Registry, assign_scenes, events_from, forget_written
from devils_workshop.planner.state import (
    ACCEPTED,
    BRAINSTORM,
    DECLINED,
    DONE,
    FAILED,
    PLANNER,
    PLANNING,
    READY,
    USER,
    WAITING,
    ChapterPlan,
    PlannerState,
    ScenePlan,
    Suggestion,
    Turn,
)
from devils_workshop.settings import PLANNER as PLANNER_ROLE
from devils_workshop.settings import MAX_AT_ONCE, AppSettings, JobOptions

MIN_WORDS = 3   # a planning reply can be short; under this it counts as empty
OUTPUT_GUESS = {"guide": 1800, "architecture": 4000, "scenes": 500}  # words, for the spending estimate

RUNNING, FINISHED, STOPPED, FAILED_RUN = "running", "finished", "stopped", "failed"
log = logging.getLogger(__name__)


def models(state: PlannerState, settings: AppSettings) -> tuple[str, str | None]:
    """The model chosen on this story's Planner screen, else the planner's main model in Settings; the
    planner's fallback. (The story's own models in story.yaml are the writer's.)"""
    primary, fallback = settings.models_of(PLANNER_ROLE)
    return state.model or primary, fallback


def sees_pictures(catalog: Catalog, model: str) -> bool | None:
    info = catalog.get(model)
    return info.sees_pictures if info else None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


async def _send(state: PlannerState, messages, *, settings: AppSettings, catalog: Catalog,
                client: OpenRouterClient, label: str, job: str, target_words: int = 0,
                chapter: int | None = None, scene: int | None = None,
                on_event: Callable[[Event], None] | None = None, cancel: asyncio.Event | None = None,
                secret: str | None = None, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                min_words: int = MIN_WORDS, options: JobOptions | None = None) -> GenerationResult:
    primary, fallback = models(state, settings)
    return await generate(messages, client=client, settings=settings, catalog=catalog,
                          log_root=state.root / "logs", label=label, target_words=target_words,
                          primary=primary, fallback=fallback, job=job, role=PLANNER_ROLE, chapter=chapter, scene=scene,
                          on_event=on_event, cancel=cancel, secret=secret, sleep=sleep,
                          patterns=[], min_words=min_words, options=options)


# Every planner job thinks as the planner is set to (Settings: its thinking level and room), the checks included:
# the author wants the planner to think more, not less. A job here the story can do without: when it still fails
# after the retries (llm/generate.py tries again by the failure's reason), the story goes on without it (the canon
# is tried again on the next run; a scene's check is left "not judged", which the manuscript check shows).
OPTIONAL_JOBS = ("canon", "audit")


# --- stage 1: one brainstorm turn ---------------------------------------------------------------------

_APPROVE = re.compile(r"\b(?:i\s+)?approv(?:e|ed|ing)\b|\blooks?\s+(?:good|great|right|fine)\b|\blgtm\b|"
                      r"\bgo\s+ahead\b|\bfinali[sz]e\b|\bthat'?s\s+(?:it|perfect)\b|\bsign(?:ed)?\s+off\b", re.I)
_NOT = re.compile(r"\b(?:don'?t|do\s+not|not|never|can'?t|won'?t)\s+(?:\w+\s+){0,2}"
                  r"(?:approv|look|go|finali|sign)", re.I)
FILL_GAPS = ("Settle every decision still open on the agenda yourself, and fill every remaining gap in the plan, "
             "with your best choices, marked (my choice), and tell me briefly what you chose.")
SIMILAR = 0.6  # a new suggestion this close to an earlier one is a repeat, and is dropped


def is_approval(text: str) -> bool:
    """You approve the plan in your own words ("I approve", "looks good", "go ahead")."""
    return bool(_APPROVE.search(text)) and not _NOT.search(text)


def _answers(state: PlannerState) -> str:
    """Your Yes / No to the planner's suggestions since your last message, as words for it to act on. What you
    said No to is ruled out (Decisions.md), so it is never offered again."""
    yes = [s.text for s in state.untold_answers() if s.status == ACCEPTED]
    no = [s.text for s in state.untold_answers() if s.status == DECLINED]
    for text in no:
        if not _repeat(text, state.ruled_out):
            state.ruled_out.append(text)
    parts = []
    if yes:
        parts.append("Yes to these suggestions: put them in the plan now.\n" + "\n".join(f"- {t}" for t in yes))
    if no:
        parts.append("No to these suggestions: leave them out.\n" + "\n".join(f"- {t}" for t in no))
    return "\n\n".join(parts)


def _repeat(text: str, earlier: list[str]) -> bool:
    plain = re.sub(r"\W+", " ", text.lower())
    return any(difflib.SequenceMatcher(None, plain, re.sub(r"\W+", " ", e.lower())).ratio() >= SIMILAR
               for e in earlier)


def _apply(state: PlannerState, reply: prompts.AgentReply) -> tuple[list[str], list[int]]:
    """Put the planner's changes into the state. Returns (sections changed, new suggestion ids)."""
    if reply.whole_plan and not reply.edits:  # the old form: a complete plan
        changed = ["the whole plan"] if reply.whole_plan.strip() != state.plan.strip() else []
        state.plan = reply.whole_plan.strip()
    else:
        state.plan, changed = prompts.apply_edits(state.plan, reply.edits)
    for old in reply.replaced:  # a decision that changes an earlier one: the earlier one no longer stands
        state.decisions = [d for d in state.decisions if not _repeat(old, [d])]
    for decision in reply.decisions:
        if not _repeat(decision, state.decisions):
            state.decisions.append(decision)
    new_ids = []
    for text in reply.suggestions:
        if _repeat(text, [s.text for s in state.suggestions]):
            continue  # offered before: never again
        number = max([s.id for s in state.suggestions] + [0]) + 1
        state.suggestions.append(Suggestion(number, text))
        new_ids.append(number)
    for key in ("ruled_out", "preferences"):
        kept = getattr(state, key)
        for item in getattr(reply, key):
            if not _repeat(item, kept):
                kept.append(item)
    if reply.agenda is not None:
        state.agenda = [item for item in reply.agenda if not _repeat(item, state.decisions)]
    if reply.ready and not state.agenda:  # ready only once nothing important is left open
        state.ready = True
    return changed, new_ids


async def brainstorm(state: PlannerState, text: str, attachments: list[str], *, settings: AppSettings,
                     catalog: Catalog, client: OpenRouterClient, **send_options) -> GenerationResult:
    """Your message (with your Yes / No to its suggestions; empty: the last one is sent again), then the
    planner's turn: it edits the plan's sections itself, records what is decided, and offers only new
    suggestions. If you accepted something or approved the plan and it changed nothing, it is asked once more
    to make the changes. Nothing changes if the request fails."""
    answers = _answers(state)
    if text.strip() or attachments or answers:
        state.turns.append(Turn(USER, "\n\n".join(p for p in (answers, text.strip()) if p), list(attachments),
                                _now()))
        for suggestion in state.untold_answers():
            suggestion.told = True
        state.save()
    last = next((t.text for t in reversed(state.turns) if t.role == USER), "")
    approved = is_approval(last)
    fill_gaps = last.strip().endswith(FILL_GAPS)
    needs_changes = approved or fill_gaps or "Yes to these suggestions" in last
    pictures = bool(sees_pictures(catalog, models(state, settings)[0]))
    noted = await take_notes(state, pictures, settings=settings, catalog=catalog, client=client, **send_options)
    if noted is not None and not noted.ok:
        return noted  # a file couldn't be read; Try again carries on from it
    messages = prompts.brainstorm_messages(state, pictures, approved=approved or fill_gaps)
    job = "plan-import" if prompts.converting(state) else "plan-brainstorm"  # a plan made elsewhere, carried in
    result = await _send(state, messages, settings=settings, catalog=catalog, client=client,
                         label=job, job=job, **send_options)
    if noted is not None:
        result.total_cost += noted.total_cost
    state.spent += result.total_cost - (noted.total_cost if noted is not None else 0)
    if not result.ok:
        state.save()
        return result
    reply = prompts.parse_agent_reply(result.text)
    state.ready = False  # only the latest reply says whether the plan is ready
    changed, new_ids = _apply(state, reply)
    if needs_changes and not changed:  # it said it would, but the plan is the same: ask once more, for edits
        follow_up = messages + [
            ChatMessage("assistant", result.text),
            ChatMessage("user", "The plan hasn't changed. Make the changes now: send an <edit section=\"...\"> "
                                "block with the complete new text of every section that changes"
                                + (", settling every open decision and replacing every remaining 'to decide' "
                                   "with your best choice, an empty <agenda>, then <ready/>"
                                   if approved or fill_gaps else "")
                                + ", and a one-line <reply>.")]
        second = await _send(state, follow_up, settings=settings, catalog=catalog, client=client,
                             label="plan-brainstorm-edits", job="plan-brainstorm", **send_options)
        state.spent += second.total_cost
        result.total_cost += second.total_cost
        if second.ok:
            again = prompts.parse_agent_reply(second.text)
            more_changed, more_ids = _apply(state, again)
            changed, new_ids = changed + [c for c in more_changed if c not in changed], new_ids + more_ids
    if (approved or fill_gaps) and changed:
        state.agenda = []  # it settled what was open
        state.ready = True
    message = reply.message or ("I've updated the plan." if changed else "I didn't change the plan this time.")
    state.turns.append(Turn(PLANNER, message, time=_now(), edited=changed, suggestions=new_ids))
    state.save()
    return result


def plan_mark(plan: str) -> str:
    """Tells whether the plan changed since the writer's guide was written from it."""
    import hashlib
    return hashlib.sha1(plan.strip().encode("utf-8")).hexdigest()[:16]


PLAN_NOTE = "(Your plan, made elsewhere: carried into the story plan in full, never summarised.)"


async def take_notes(state: PlannerState, pictures: bool, **send_options) -> GenerationResult | None:
    """The planner's notes on each file and picture of your latest message that has none yet, one request each
    (a short text file goes in as it is): from now on these notes stand for the file (Source notes.md). Returns
    the last request's result (its total_cost covers them all), or None if nothing needed one."""
    turn = next((t for t in reversed(state.turns) if t.role == USER), None)
    last, spent = None, 0.0
    for name in turn.attachments if turn else []:
        if name in state.notes:
            continue
        if state.registry.kind == IMPORT and name in state.registry.plan_files:  # sent in full, never summarised
            state.notes[name] = PLAN_NOTE
            state.save()
            continue
        quick = prompts.note_without_request(state, name, pictures)
        if quick is not None:
            state.notes[name] = quick
            state.save()
            continue
        messages = prompts.notes_messages(state, name, turn.text, pictures)
        last = await _send(state, messages, label="plan-notes", job="plan-notes", **send_options)
        spent += last.total_cost
        state.spent += last.total_cost
        if not last.ok:
            state.save()
            last.total_cost = spent
            return last
        state.notes[name] = _with_length(state, name, last.text.strip())
        state.save()
    if last is not None:
        last.total_cost = spent
    return last


def _with_length(state: PlannerState, name: str, notes: str) -> str:
    """The notes on a file, saying how long the file is: the planner never sees the file again, and a story
    adapted from it is planned to a length."""
    from devils_workshop.planner.inputs import input_text
    words = len(input_text(state.root, name)[0].split())
    return f"(The file is {words:,} words long.)\n\n{notes}" if words else notes


def approve(state: PlannerState) -> None:
    """Lock the plan: it is saved as Story plan.md, and planning can start, with the writer's guide first
    (written again if the plan changed since it was written)."""
    state.stage = PLANNING
    if state.guide and state.guide_plan != plan_mark(state.plan):
        state.guide = ""
    state.save()
    write_outputs(state, snapshot_action="planner-plan-approved")


def back_to_brainstorm(state: PlannerState) -> None:
    """Change the plan again. The chapters and scenes planned so far are kept until you plan again."""
    state.stage = BRAINSTORM
    state.save()


# --- choosing what to re-plan -------------------------------------------------------------------------

def redo_guide(state: PlannerState, note: str = "") -> None:
    state.guide, state.guide_note, state.stage = "", note.strip(), PLANNING
    state.save()


def redo_architecture(state: PlannerState, note: str = "") -> None:
    state.chapters, state.architecture_note, state.stage = [], note.strip(), PLANNING
    state.save()


def redo_chapter(state: PlannerState, number: int, note: str = "") -> None:
    chapter = state.chapter(number)
    if chapter:
        chapter.scenes, chapter.note = [], note.strip()
        state.stage = PLANNING
        state.save()


def unwritten_with_briefs(state: PlannerState) -> list[ChapterPlan]:
    """The chapters that have briefs but no text yet: the ones whose briefs can be planned again."""
    from devils_workshop.project.project import NotAProjectError, load_project
    try:
        written = load_project(state.root).chapters
    except NotAProjectError:
        written = {}

    def has_text(number: int) -> bool:
        chapter = written.get(number)
        return bool(chapter and (chapter.text.strip() or chapter.scenes))

    return [c for c in state.chapters if any(s.brief for s in c.scenes) and not has_text(c.number)]


def redo_briefs(state: PlannerState, note: str = "") -> list[int]:
    """Plan the briefs again, from scratch (in the brief's current form and length), for every chapter that has
    briefs but isn't written yet; their scene lists stay. Returns those chapters' numbers."""
    chapters = unwritten_with_briefs(state)
    for chapter in chapters:
        for scene in chapter.scenes:
            scene.brief, scene.continuity, scene.earlier = "", "", ""
            scene.status, scene.note = WAITING, note.strip()
    if chapters:
        state.stage = PLANNING
        state.save()
    return [c.number for c in chapters]


def briefs_to_come(state: PlannerState) -> int:
    """How many scene briefs are left for just before their chapters are written (briefs just in time)."""
    if not state.jit:
        return 0
    return sum(1 for c in state.chapters if c.number > state.briefs_through for s in c.scenes if s.status != READY)


def all_briefs_now(state: PlannerState) -> None:
    """Write every chapter's briefs up front, from the plan, instead of just before each chapter is written. (A
    change you approve in the Reader that reaches later chapters still sends their briefs back to just in time.)"""
    state.jit, state.stage = False, PLANNING
    state.save()


def redo_scene(state: PlannerState, number: int, scene_number: int, note: str = "") -> None:
    chapter = state.chapter(number)
    scene = next((s for s in chapter.scenes if s.number == scene_number), None) if chapter else None
    if scene:
        scene.earlier = scene.brief if note.strip() else ""  # changed as the note says, not written from nothing
        scene.brief, scene.status, scene.note = "", WAITING, note.strip()
        state.stage = PLANNING
        state.save()


# --- stage 2: the planning run -----------------------------------------------------------------------

@dataclass
class PlanStep:
    kind: str                 # "guide" | "architecture" | "scenes" | "scene" | "actual"
    label: str
    chapter: int | None = None
    scene: int | None = None
    status: str = RUNNING     # RUNNING | FINISHED | FAILED_RUN | STOPPED
    cost: float = 0.0
    note: str = ""
    live: str = ""
    activity: str = ""


def next_job(state: PlannerState, only: tuple | None = None, skip: frozenset = frozenset()) -> tuple | None:
    """The next thing to plan: ("guide",), ("architecture",), ("scenes", chapter) or ("scene", chapter, scene).
    Every chapter's scene list comes before any brief; with jit, briefs only up to state.briefs_through.
    only=("briefs", n): just chapter n's briefs, after ("actual", chapter n-1): how things stand at the end of
    the chapter before it, as it was actually written (if it has text the continuity doesn't follow yet)."""
    if only and only[0] in ("briefs", "reconcile"):
        from devils_workshop.planner.review import needs_reconcile
        before, chapter = state.chapter(only[1] - 1), state.chapter(only[1])
        if before and needs_reconcile(state, before.number, state.root):
            return ("actual", before)
        for scene in chapter.scenes if chapter and only[0] == "briefs" else []:
            if scene.status != READY:
                return ("scene", chapter, scene)
        return None
    if only and only[0] == "audit":
        if "audit" in skip:
            return None
        chapter = state.chapter(only[1])
        todo = [n for n in only[2] if not audited(state, only[1], n)] if chapter else []
        return ("audit", chapter, todo) if todo else None
    if only and only[0] == "manuscript":  # every chapter as written, checked (in order) where it isn't yet
        for chapter in state.chapters if "audit" not in skip else []:
            todo = [n for n in sorted(review.chapter_texts(state.root, chapter.number))
                    if not audited(state, chapter.number, n)]
            if todo:
                return ("audit", chapter, todo)
        return None
    if only and only[0] == "canon":
        return ("canon",) if canon_due(state) and "canon" not in skip else None
    if not state.guide.strip() and (only is None or only[0] == "guide"):
        return ("guide",)
    if only and only[0] == "guide":
        return None
    registry = state.registry
    if only is None and registry.follows_reference() and registry.reference and not registry.sources:
        return ("sources",)
    if not state.chapters:
        return ("architecture",) if only is None else None
    mapped = registry.map_mark == canon.map_mark(state)
    if only is None and registry.follows_reference() and registry.sources and not mapped:
        return ("map",)
    if only is None and canon_due(state) and "canon" not in skip:
        return ("canon",)
    for chapter in state.chapters:
        if only and chapter.number != only[1]:
            continue
        if not chapter.scenes:
            return ("scenes", chapter)
    for chapter in state.chapters:
        if only and chapter.number != only[1]:
            continue
        if state.jit and (not only or only[0] == "chapter") and chapter.number > state.briefs_through:
            break  # written just before the chapter is (from what was written before it), even after a change
        for scene in chapter.scenes:
            if only and len(only) > 2 and scene.number != only[2]:
                continue
            if scene.status != READY:
                return ("scene", chapter, scene)
    return None


def canon_due(state: PlannerState) -> bool:
    """The canon and the guide's timing aren't worked out yet for this guide and these chapters."""
    return bool(state.guide.strip() and state.registry.events and
                state.registry.canon_mark != canon.canon_mark(state))


def audited(state: PlannerState, chapter: int, number: int) -> bool:
    """The scene's text as it is now has been checked."""
    texts = review.chapter_texts(state.root, chapter)
    entry = ((state.audits.get(str(chapter)) or {}).get("scenes") or {}).get(str(number))
    return bool(entry and number in texts and entry.get("text") == audit.text_hash(texts[number]))


def exit_before(state: PlannerState, chapter: int, scene: int) -> str:
    """How things stood before a scene: the end of the scene before as its check found it, else as the planner
    noted it."""
    if scene > 1:
        before_chapter, before_scene = state.chapter(chapter), scene - 1
    else:
        before_chapter = state.chapter(chapter - 1)
        before_scene = before_chapter.scenes[-1].number if before_chapter and before_chapter.scenes else 0
    if before_chapter is None or not before_scene:
        return "(the story begins here)" if chapter == 1 and scene == 1 else "(not noted)"
    entry = ((state.audits.get(str(before_chapter.number)) or {}).get("scenes") or {}).get(str(before_scene))
    if entry and entry.get("exit"):
        return entry["exit"]
    found = next((s for s in before_chapter.scenes if s.number == before_scene), None)
    return found.continuity.strip() if found and found.continuity.strip() else "(not noted)"


def scene_before(state: PlannerState, chapter: int, scene: int) -> tuple[int, int] | None:
    """(chapter, scene) of the scene just before this one: in this chapter, or the last of the chapter before."""
    if scene > 1:
        return chapter, scene - 1
    before = state.chapter(chapter - 1)
    return (before.number, before.scenes[-1].number) if before and before.scenes else None


def audit_input(state: PlannerState, chapter: ChapterPlan, numbers: list[int]) -> tuple[list, str]:
    texts = review.chapter_texts(state.root, chapter.number)
    scenes = []
    for number in sorted(numbers):
        planned = next((s for s in chapter.scenes if s.number == number), None)
        scenes.append((number, planned.brief if planned else "", texts.get(number, ""),
                       planned.words if planned else 0))
    return scenes, exit_before(state, chapter.number, min(numbers))


def record_audit(state: PlannerState, chapter: ChapterPlan, numbers: list[int], reply: str) -> str:
    """The checks of the scenes, decided against the registry and kept (state.audits); where each event happened;
    how things stand after each scene, as written. Once every scene of the chapter is checked: its events that
    never happened, and the chapter counts as followed (the next chapter's briefs start from its end)."""
    registry = state.registry
    reports = audit.read_audit(reply, numbers)
    texts = review.chapter_texts(state.root, chapter.number)
    happened: set[str] = set()
    for number in sorted(numbers):
        forget_written(registry, chapter.number, [number])
        report = reports[number]
        planned = next((s for s in chapter.scenes if s.number == number), None)
        findings = audit.evaluate(registry, chapter.number, report, happened,
                                  scene_before(state, chapter.number, number)) + audit.prose_findings(
            chapter.number, number, texts.get(number, ""), planned.words if planned else 0)
        # What the scene did is recorded even when it has a problem: the text stands as written until it is changed,
        # and the scenes after it follow from it (a problem never makes later scenes look out of order).
        audit.commit(registry, chapter.number, report, planned.kind if planned else "")
        happened |= {e for e, how in report.events if how in ("happens", "continues")}
        if planned is not None and report.exit:
            planned.continuity = report.exit  # how things stand after it, as written
        audit.record(state, chapter.number, number, texts.get(number, ""), report, findings)
    entry = state.audits[str(chapter.number)]
    checked_all = bool(chapter.scenes) and all(audited(state, chapter.number, s.number) for s in chapter.scenes)
    last = max((c.number for c in state.chapters), default=chapter.number)
    all_read = all(s.get("read") for s in entry["scenes"].values())
    entry["chapter_findings"] = [asdict(f) for f in audit.chapter_findings(registry, chapter.number, last,
                                                                           checked_all, all_read)]
    if checked_all:
        entry["revision"] = review.chapter_revision(state.root, chapter.number)
        if chapter.number not in state.reconciled:
            state.reconciled.append(chapter.number)
        state.reconciled_revisions[str(chapter.number)] = entry["revision"]
        state.departures[str(chapter.number)] = [d for s in entry["scenes"].values() for d in s.get("departures", [])]
    found = audit.findings_of(state, chapter.number)
    mine = [f for f in found if f.scene in numbers or (f.kind in ("missing", "ending") and not f.scene)]
    problems = audit.hard(mine)
    notes = [f for f in mine if f.severity == audit.ADVISORY]
    unsure = [f for f in mine if f.severity == audit.INCONCLUSIVE]
    summary = [f"{len(problems)} to put right" if problems else "nothing to put right"]
    if notes:
        summary.append(f"{len(notes)} note{'s' if len(notes) != 1 else ''}")
    if unsure:
        summary.append(f"{len(unsure)} not judged")
    return ", ".join(summary) + ("".join(f"; {f.line()}" for f in problems[:3]))


@dataclass
class PlanRun:
    root: Path
    settings: AppSettings
    catalog: Catalog
    client: Callable[[], OpenRouterClient]
    only: tuple | None = None     # ("chapter", n) or ("scene", n, k): just that; None: everything still to plan
    spend_cap: float | None = None
    secret: str | None = None
    estimate: Callable[[str, int, int], tuple[float, float] | None] = lambda model, tokens, words: None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    name: Callable[[str], str] = lambda model: model
    remember: bool = True         # keep a note, so a run cut off carries on (off inside Auto-write, which keeps its own)
    _reserved: float = 0.0        # worst-case cost of the requests under way
    _skip: set = field(default_factory=set)  # optional jobs that didn't answer this time: not asked again this run
    _replanned: bool = False      # the chapters were planned again once this run, to carry the reference's events

    steps: list[PlanStep] = field(default_factory=list)
    status: str = RUNNING
    reason: str = ""
    spent: float = 0.0
    stop_after_current: bool = False
    cancel: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def running(self) -> bool:
        return self.status == RUNNING

    def stop(self, now: bool = False) -> None:
        """Stop after the request under way, or (now) stop it too; nothing from it is saved."""
        self.stop_after_current = True
        if now:
            self.cancel.set()

    async def run(self) -> None:
        self._note()
        with awake():  # the whole run, pauses between requests included
            await self._run()

    def _note(self) -> None:
        """Keep a note of the run, so it carries on if the app closes before it ends (devils_workshop/resume.py)."""
        if self.remember:
            resume.note(self.root, resume.PLAN, only=list(self.only) if self.only else None,
                        spend_cap=self.spend_cap, spent=self.spent)

    async def _run(self) -> None:
        try:
            state = PlannerState.load(self.root)
            what = {None: "plan", "guide": "guide", "chapter": "chapter", "scene": "scene", "briefs": "briefs",
                    "audit": "check", "canon": "canon", "reconcile": "follow", "manuscript": "check"}[
                self.only[0] if self.only else None]
            if self.only and self.only[0] == "briefs":
                state.briefs_through = max(state.briefs_through, self.only[1])
                state.save()
            snapshot(self.root, OUTPUT_FILES, f"planner-{what}")  # one Undo in History puts the files back
            canon_run: asyncio.Future | None = None  # the canon, worked out while the scene lists are planned
            try:
                while True:
                    if self.stop_after_current:
                        return self._end(STOPPED, "Stopped by you. Everything planned so far is saved; Continue "
                                                  "planning carries on from here.")
                    job = next_job(state, self.only, frozenset(self._skip | ({"canon"} if canon_run else set())))
                    if job is None:
                        break
                    if job[0] == "canon" and self.only is None:
                        canon_run = asyncio.ensure_future(self._do(state, job))  # needs only the guide and chapters
                        continue
                    if job[0] == "scene" and canon_run is not None:  # a brief is written from the guide as scoped
                        finished, canon_run = await canon_run, None
                        if not finished:
                            return
                        continue
                    if not await self._job(state, job):
                        return
                if canon_run is not None:
                    finished, canon_run = await canon_run, None
                    if not finished:
                        return
            finally:
                if canon_run is not None:
                    await canon_run  # stopped meanwhile: the canon under way is finished and saved
            if not next_job(state, skip=frozenset({"canon"})):  # the canon, if it didn't answer, is tried next time
                state.stage = DONE
                state.save()
            write_outputs(state)
            self._end(FINISHED, "Finished. The story is ready to write." if state.stage == DONE else "Finished.")
        except Exception as exc:  # noqa: BLE001 - shown on the screen, and in the app log
            log.exception("Planning run failed")
            self._end(FAILED_RUN, f"Something went wrong: {exc}. Everything planned before it is saved.")

    async def _job(self, state: PlannerState, job: tuple) -> bool:
        """One job of the run (several chapters' briefs side by side, when they can go so). False: the run ended."""
        batch = self._side_by_side(state) if job[0] == "scene" else []
        if len(batch) > 1:
            await self._briefs_side_by_side(state, batch)
            return self.running
        return await self._do(state, job)

    def _side_by_side(self, state: PlannerState) -> list[ChapterPlan]:
        """The chapters whose briefs this run writes next, if they can go side by side: a whole run (not one
        chapter or scene) with Speed (Settings → Auto-write) above 1."""
        if self.only is not None or int(self.settings.at_once or 1) < 2:
            return []
        return [c for c in state.chapters if (not state.jit or c.number <= state.briefs_through)
                and any(s.status != READY for s in c.scenes)]

    async def _briefs_side_by_side(self, state: PlannerState, batch: list[ChapterPlan]) -> None:
        """Several chapters' briefs at the same time (up to Speed), each chapter's scenes in order. A chapter
        whose chapter before is in this batch too comes in from that chapter's planned ending."""
        slots = asyncio.Semaphore(max(1, min(int(self.settings.at_once or 1), MAX_AT_ONCE)))
        numbers = {c.number for c in batch}

        async def chapter_briefs(chapter: ChapterPlan) -> None:
            async with slots:
                from_plan = chapter.number - 1 in numbers
                for scene in chapter.scenes:
                    if not self.running or self.stop_after_current:
                        return
                    if scene.status == READY:
                        continue
                    if not await self._do(state, ("scene", chapter, scene), chapter_from_plan=from_plan):
                        self.stop_after_current = True  # the others finish the brief under way, then stop
                        return

        await asyncio.gather(*(chapter_briefs(c) for c in batch))
        if self.running and self.stop_after_current:
            self._end(STOPPED, "Stopped by you. Everything planned so far is saved; Continue planning carries on "
                               "from here.")

    async def _do(self, state: PlannerState, job: tuple, chapter_from_plan: bool = False) -> bool:
        primary = models(state, self.settings)[0]
        pictures = bool(sees_pictures(self.catalog, primary))
        kind = job[0]
        guess = OUTPUT_GUESS.get(kind, 0)
        if kind == "guide":
            step = self._add(PlanStep(kind, "The writer's guide"))
            messages = prompts.guide_messages(state, pictures)
        elif kind == "actual":
            chapter = job[1]
            step = self._add(PlanStep(kind, f"Chapter {chapter.number}: how things stand after it, as written",
                                      chapter.number))
            texts = review.chapter_texts(self.root, chapter.number)
            messages = review.actual_messages(state, chapter, texts)
            guess = 500
        elif kind == "architecture":
            step = self._add(PlanStep(kind, "Chapter architecture"))
            messages = prompts.architecture_messages(state, pictures)
        elif kind == "scenes":
            chapter: ChapterPlan = job[1]
            step = self._add(PlanStep(kind, f"Chapter {chapter.number}: scenes", chapter.number))
            messages = prompts.scenes_messages(state, chapter, pictures)
        elif kind == "canon":
            step = self._add(PlanStep(kind, "The story's canon, and the guide as it stands at each chapter"))
            messages = canon.canon_messages(state)
            guess = 1200
        elif kind == "sources":
            step = self._add(PlanStep(kind, f"The events of {state.registry.reference}, the story this adapts"))
            messages = canon.source_events_messages(state, state.registry.reference)
            guess = 2500
        elif kind == "map":
            step = self._add(PlanStep(kind, "How the chapters carry the reference's events"))
            messages = canon.map_messages(state)
            guess = 800
        elif kind == "audit":
            chapter, numbers = job[1], job[2]
            listed = ", ".join(map(str, numbers))
            step = self._add(PlanStep(kind, f"Chapter {chapter.number}: checking scene{'s' if len(numbers) > 1 else ''}"
                                            f" {listed} as written", chapter.number))
            scenes, coming_in = audit_input(state, chapter, numbers)
            before = Registry.from_dict(state.registry.to_dict())  # as it stands without the scenes checked now
            forget_written(before, chapter.number, numbers)
            messages = audit.audit_messages(self.root, before, chapter.number, scenes, coming_in)
            guess = 350 * len(numbers)
        else:
            chapter, scene = job[1], job[2]
            scene: ScenePlan
            step = self._add(PlanStep(kind, f"Chapter {chapter.number}, scene {scene.number}: {scene.title}",
                                      chapter.number, scene.number))
            messages = prompts.scene_messages(state, chapter, scene, pictures, chapter_from_plan=chapter_from_plan)
            guess = state.brief_target(scene) + 250  # the brief, then the continuity

        tokens = sum(message_tokens(m) for m in messages)
        estimate = self.estimate(primary, tokens, guess)
        worst = estimate[1] if estimate else 0.0
        if self.spend_cap is not None and self.spent + self._reserved + worst > self.spend_cap:
            step.status = STOPPED
            return self._end(STOPPED, f"Spending cap: ${self.spent:.4f} spent so far, and the next request could "
                                      f"cost up to ${worst:.4f}, which would pass your cap of ${self.spend_cap:.2f}.")
        label = (f"plan-{kind}" + (f"-ch{job[1].number:02d}" if len(job) > 1 else "")
                 + (f"-s{job[2].number}" if kind == "scene" else ""))
        self._reserved += worst  # requests under way side by side count against the cap too
        optional = kind in OPTIONAL_JOBS
        try:
            result = await self._request(state, step, messages, label, f"plan-{kind}", guess, optional=optional)
        finally:
            self._reserved -= worst
        if result is None:
            return False
        if optional and not result.ok:  # it didn't answer: the story goes on without it
            self._skip.add(kind)
            reason = result.failure or result.outcome
            if kind == "audit":
                for number in numbers:
                    audit.record(state, chapter.number, number, "", None, [audit.Finding(
                        audit.INCONCLUSIVE, "unsure", f"the check didn't answer ({reason}), so this scene isn't "
                        "checked yet: the manuscript check can check it later", chapter.number, number)])
                step.note = "the check didn't answer, so these scenes aren't checked yet; writing goes on"
            else:
                step.note = (f"couldn't be worked out this time ({reason}); the writer's guide is sent as it is, "
                             "and the canon is tried again next time")
            step.status = FINISHED
            state.save()
            write_outputs(state)
            return True
        try:
            if kind == "guide":
                state.guide, state.guide_plan, state.guide_note = result.text.strip(), plan_mark(state.plan), ""
                step.note = f"{len(state.guide.split()):,} words"
            elif kind == "canon":
                facts, scope = canon.read_canon(result.text, state.guide, state.registry.facts)
                state.registry.facts, state.registry.guide_scope = facts, scope
                state.registry.canon_mark = canon.canon_mark(state)
                step.note = f"{len(facts)} facts; {len(scope)} part{'s' if len(scope) != 1 else ''} of the guide " \
                            "only true from a later chapter"
            elif kind == "sources":
                found = canon.read_source_events(result.text)
                if not found:
                    raise ValueError("the reply listed no events of the reference")
                state.registry.sources = found
                step.note = f"{len(found)} events"
            elif kind == "map":
                canon.read_map(result.text, state.registry)
                state.registry.map_mark = canon.map_mark(state)
                found = canon.mapping_findings(state.registry)
                problems = audit.hard(found)
                step.note = "; ".join(f.text for f in found) or "every required event of the reference is carried"
                if problems and not self._replanned and self.only is None:
                    # The chapters left out what they must carry: planned once more, told what, before giving up
                    self._replanned = True
                    redo_architecture(state, "Your chapters don't carry the reference as they must. Plan them "
                                             "again as they were, changed only to carry these: "
                                      + "; ".join(f.text for f in problems))
                    state.registry.map_mark = ""  # and checked again, even if they come back the same
                    step.note += "; the chapters are planned once more to carry it"
                elif problems:
                    step.status = FAILED_RUN
                    state.save()
                    write_outputs(state)
                    return self._end(FAILED_RUN, "The chapters don't carry the reference as they should: "
                                     + "; ".join(f.text for f in problems))
            elif kind == "audit":
                step.note = record_audit(state, chapter, numbers, result.text)
            elif kind == "actual":
                continuity, differences = review.read_actual(result.text)
                chapter.scenes[-1].continuity = continuity  # the next chapter's briefs follow on from this
                if chapter.number not in state.reconciled:
                    state.reconciled.append(chapter.number)
                state.reconciled_revisions[str(chapter.number)] = review.chapter_revision(self.root, chapter.number)
                state.departures[str(chapter.number)] = differences
                step.note = ("where the text departs from the plan: " + "; ".join(differences)) if differences \
                    else "it follows the plan"
            elif kind in ("architecture", "scenes"):
                items = await self._read_list(state, step, messages, result, label, kind,
                                             chapter if kind == "scenes" else None)
                if items is None:
                    return False
                if kind == "architecture":
                    state.chapters = prompts.chapters_from(items)
                    state.architecture_note = ""
                    state.registry.events = events_from(state.chapters, state.registry.events)
                    step.note = f"{len(state.chapters)} chapters, about {state.total_words():,} words" + (
                        f"; {step.note}" if step.note else "")
                else:
                    chapter.scenes = prompts.scenes_from(items, chapter.words)
                    chapter.note = ""
                    assign_scenes(state.registry, chapter)
                    step.note = f"{len(chapter.scenes)} scenes" + (f"; {step.note}" if step.note else "")
            else:
                brief, continuity = prompts.split_scene_reply(result.text)
                if not brief:
                    raise ValueError("the reply had no brief")
                scene.brief, scene.continuity = brief, continuity
                scene.status, scene.note, scene.earlier = READY, "", ""
                scene.follows = audit.text_hash(exit_before(state, chapter.number, scene.number))
                step.note = f"{len(scene.brief.split()):,} words" + (f"; {step.note}" if step.note else "")
        except ValueError as exc:
            if kind == "scene":
                scene.status = FAILED
            step.status = FAILED_RUN
            state.save()
            return self._end(FAILED_RUN, f"{step.label}: {exc}. Nothing from this request was saved; Continue "
                                         "planning tries it again.")
        step.status = FINISHED
        state.save()
        write_outputs(state)
        return True

    async def _request(self, state: PlannerState, step: PlanStep, messages, label: str, job: str,
                       guess: int, min_words: int = MIN_WORDS, options: JobOptions | None = None,
                       optional: bool = False) -> GenerationResult | None:
        """Send one request for the step; None (and the run ended) if it failed or was stopped. optional: a
        request the run can do without (the check): its failure is handed back instead, and the run goes on."""
        step.activity = "Sending…"
        result = await _send(state, messages, settings=self.settings, catalog=self.catalog, client=self.client(),
                             label=label, job=job, target_words=guess,  # no chapter: planning costs
                             on_event=lambda e: self._event(step, e),     # aren't a chapter's costs
                             cancel=self.cancel, secret=self.secret, sleep=self.sleep, min_words=min_words,
                             options=options)
        step.live, step.activity = "", ""
        step.cost += result.total_cost
        self.spent += result.total_cost
        state.spent += result.total_cost
        self._note()
        if result.ok or (optional and result.outcome != CANCELLED):
            return result
        step.status = FAILED_RUN if result.outcome != CANCELLED else STOPPED
        state.save()
        if result.outcome == CANCELLED:
            self._end(STOPPED, "Stopped by you. Nothing from the unfinished request was saved.")
        else:
            self._end(FAILED_RUN, f"{step.label}: {result.failure or 'the request failed'}. Nothing from this "
                                  "request was saved; every attempt is in Activity.")
        return None

    async def _read_list(self, state: PlannerState, step: PlanStep, messages, result: GenerationResult,
                         label: str, kind: str, chapter: ChapterPlan | None = None) -> list[dict] | None:
        """The chapters or scenes in a reply, once they pass the checks (planner/contracts.py). A reply that can't
        be read, or fails a check, is asked for once more, saying what was wrong; if that fails too, the step
        fails (ValueError) and nothing from it is saved."""
        key = "chapters" if kind == "architecture" else "scenes"
        reply = result
        for attempt in (1, 2):
            try:
                items = prompts.parse_list(reply.text, key)
            except ValueError as exc:
                if attempt == 2:
                    raise
                fix = (f"Your reply couldn't be read ({exc}). Send the same {key} again in exactly the format the "
                       "instruction gives, and nothing else.")
            else:
                verdict = self._check_list(state, kind, items, chapter)
                if verdict.ok:
                    if verdict.notes:
                        step.note = "; ".join(verdict.notes)
                    return items
                problem = "; ".join(verdict.problems)
                if attempt == 2:
                    raise ValueError(f"the {key} still can't be used ({problem})")
                fix = (f"Your reply can't be used as it is: {problem}. Send the complete {key} again, with that "
                       "put right, in exactly the format the instruction gives, and nothing else.")
            again = messages + [ChatMessage("assistant", reply.text), ChatMessage("user", fix)]
            reply = await self._request(state, step, again, label + "-format", f"plan-{kind}", OUTPUT_GUESS[kind])
            if reply is None:
                return None
        return None

    @staticmethod
    def _check_list(state: PlannerState, kind: str, items: list[dict], chapter: ChapterPlan | None):
        from devils_workshop.planner import contracts
        if kind == "architecture":
            return contracts.check_chapters(items, contracts.expected_chapters(state.plan))
        later = [(c.number, beat) for c in state.chapters if c.number > chapter.number
                 for beat in [*c.beats, c.ending] if beat]
        return contracts.check_scenes(items, prompts.scene_count(state, chapter), chapter.words, later,
                                      [*chapter.beats, chapter.ending])

    def _event(self, step: PlanStep, event: Event) -> None:
        if event.kind == TEXT:
            step.live += event.text
            step.activity = f"Planning with {self.name(event.model)}…"
        elif event.kind == DISCARD:
            step.live = ""
            step.activity = f"Discarded: {event.message}"
        elif event.kind in (WAIT, SWITCH, WARNING):
            step.activity = event.message
        elif event.kind == ATTEMPT:
            step.activity = f"Waiting for {self.name(event.model)}…"

    def _add(self, step: PlanStep) -> PlanStep:
        self.steps.append(step)
        return step

    def _end(self, status: str, reason: str) -> bool:
        self.status, self.reason = status, reason
        if self.remember:
            resume.forget(self.root, resume.PLAN)  # it ended where the app saw it: nothing to carry on
        return False


# --- runs in progress (they outlive the screen that started them) -----------------------------------

RUNS: dict[str, PlanRun] = {}


def current_run(root: Path) -> PlanRun | None:
    return RUNS.get(str(Path(root).resolve()))


def start_run(run: PlanRun) -> asyncio.Task:
    RUNS[str(Path(run.root).resolve())] = run
    return asyncio.get_running_loop().create_task(run.run())
