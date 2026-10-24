"""What the story planner keeps between visits: planner/state.json in the story's folder.

Stage 1 (brainstorm) is a chat that keeps a story plan and an agenda of the decisions still open up to
date; you approve the plan. Stage 2 (planning) builds on it: the writer's guide, the chapter architecture,
each chapter's scenes, then one self-contained brief per scene with the continuity after it.
Everything is saved after each step, so planning can stop and carry on later.

The story folder is the planner's memory, and the single source of truth: every request is built from
its files, with only the latest messages of the conversation. You can read and edit all of them, and your
edits are read back before the next request:
    Story plan.md     the plan
    Decisions.md      settled, still open (the agenda), ruled out, and your preferences
    Source notes.md   the planner's notes on each file and picture you attached (made once, in that turn;
                      the files themselves are never sent again)
    Writer guide.md, Chapter outline.md (the briefs) and Continuity.md, once planning has written them
planner/state.json keeps the conversation, the suggestions and how far planning has got (and a copy of the
rest, but the files win).
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path

from devils_workshop.planner.registry import REGISTRY_FILE, Registry, read_approvals
from devils_workshop.project.files import read_text, write_text
from devils_workshop.project.scopes import PLANNER as PLANNER_JOB
from devils_workshop.project.scopes import check

PLAN_FILE = "Story plan.md"
DECISIONS_FILE = "Decisions.md"
SOURCES_FILE = "Source notes.md"
GUIDE_FILE = "Writer guide.md"
PLANNER_DIR = "planner"
STATE_FILE = f"{PLANNER_DIR}/state.json"
INPUTS_DIR = f"{PLANNER_DIR}/inputs"

BRAINSTORM, PLANNING, DONE = "brainstorm", "planning", "done"
WAITING, READY, FAILED = "waiting", "ready", "failed"
FULL = "full"       # a scene's staging: moment by moment, the point of the scene (else normal)
FULL_BRIEF = 2 / 3  # a full scene's brief: about two thirds of the scene's words (the planner does the thinking)
USER, PLANNER = "user", "planner"
IN_ORDER, TOGETHER = "in order", "together"


OPEN, ACCEPTED, DECLINED = "open", "accepted", "declined"


def new_id() -> str:
    """A chapter's or scene's own id: it stays with it whatever its number or place in a list."""
    return uuid.uuid4().hex[:10]


@dataclass
class Turn:
    role: str                          # USER | PLANNER
    text: str                          # for the planner: its message (its changes are applied to `plan`)
    attachments: list[str] = field(default_factory=list)  # file names in planner/inputs
    time: str = ""
    edited: list[str] = field(default_factory=list)       # planner: the plan sections it changed
    suggestions: list[int] = field(default_factory=list)  # planner: the suggestions it made (ids)


@dataclass
class Suggestion:
    """One idea the planner offered. It is never offered again; you answer it with Yes or No."""
    id: int
    text: str
    status: str = OPEN                 # OPEN | ACCEPTED | DECLINED
    told: bool = False                 # your answer has been sent to the planner


@dataclass
class ScenePlan:
    number: int
    title: str
    purpose: str = ""
    words: int = 0
    brief: str = ""
    status: str = WAITING              # WAITING | READY | FAILED
    note: str = ""                     # your note for the next try ("make the reveal later")
    continuity: str = ""               # how things stand after this scene (for the planner; the writer never sees it)
    earlier: str = ""                  # the brief before your change: the planner changes it as your note says
    id: str = ""                       # its own id (new_id), kept when scenes are renumbered
    follows: str = ""                  # how things stood coming in when its brief was written (a hash)
    kind: str = ""                     # what kind of scene it is ("argument", "reunion"...), for variety
    staging: str = ""                  # FULL: staged moment by moment, its brief a stage-by-stage choreography


@dataclass
class ChapterPlan:
    number: int
    title: str
    purpose: str = ""
    summary: str = ""
    words: int = 0
    pov: str = ""
    when_where: str = ""
    beats: list[str] = field(default_factory=list)
    ending: str = ""
    arcs: str = ""                     # where each main character's arc stands in this chapter (v0.23 on)
    sets_up: str = ""                  # what this chapter plants for later, as what to plant (not why)
    pays_off: str = ""                 # what set up earlier pays off here
    strands: str = ""                  # what each of the book's strands does here, its share of the words, and
                                       # the chapter's step on the plan's escalation ladder
    scene_count: int = 0               # how many scenes the architecture gave it
    scenes: list[ScenePlan] = field(default_factory=list)
    note: str = ""                     # your note for re-planning its scenes
    id: str = ""                       # its own id (new_id), kept whatever its number

    @property
    def planned(self) -> bool:
        return bool(self.scenes) and all(s.status == READY for s in self.scenes)


@dataclass
class PlannerState:
    root: Path
    title: str = ""
    stage: str = BRAINSTORM
    turns: list[Turn] = field(default_factory=list)
    plan: str = ""                     # the story plan (Markdown), as the planner last wrote it or you edited it
    model: str = ""                    # "" = the planner's main model in Settings
    send_inputs: bool = True           # (no longer used: the source notes go with every request)
    architecture_note: str = ""        # your note for re-planning the chapters
    chapters: list[ChapterPlan] = field(default_factory=list)
    spent: float = 0.0
    suggestions: list[Suggestion] = field(default_factory=list)  # everything the planner has suggested
    decisions: list[str] = field(default_factory=list)           # what is settled, one line each
    agenda: list[str] = field(default_factory=list)              # the important decisions still open
    ruled_out: list[str] = field(default_factory=list)           # what you rejected: never offered again
    preferences: list[str] = field(default_factory=list)         # your lasting preferences about the book
    notes: dict[str, str] = field(default_factory=dict)          # attached file -> the planner's notes on it
    ready: bool = False                # the planner says the plan is complete enough to plan chapters
    scene_words: int = 800             # a scene's usual length, unless the plan says otherwise
    brief_words: int = 250             # a scene brief for a scene of scene_words (longer scenes, longer briefs):
                                       # what the writer can't infer, not every move (v0.22 to v0.24: 500)
    guide: str = ""                    # the writer's guide: the lasting facts sent with every scene
    guide_plan: str = ""               # the plan the guide was written from (a new plan: a new guide)
    guide_note: str = ""               # your note for writing the guide again
    jit: bool = True                   # write each chapter's briefs just before it is written (from what was
                                       # actually written before it), not all of them up front
    briefs_through: int = 1            # with jit: the chapters up to this one have their briefs planned
    reconciled: list[int] = field(default_factory=list)  # chapters whose actual text the continuity follows
    reviews: dict[str, list[dict]] = field(default_factory=dict)  # chapter -> the Reader conversation
    review_plans: dict[str, dict] = field(default_factory=dict)  # chapter -> a change waiting for your approval
    reconciled_revisions: dict[str, str] = field(default_factory=dict)  # chapter -> the text the planner followed
    departures: dict[str, list[str]] = field(default_factory=dict)  # chapter -> where its text departs from the plan
    registry: Registry = field(default_factory=Registry)  # events, facts and source events (planner/registry.py)
    audits: dict[str, dict] = field(default_factory=dict)  # chapter -> the checks of what was written (audit.py)
    replanned: dict[str, str] = field(default_factory=dict)  # chapter -> the registry note it was planned again for
    scene_order: str = IN_ORDER        # IN_ORDER: each scene after the one before, the planner checking it first;
                                       # TOGETHER: a chapter's scenes at the same time, checked once they're all in

    def suggestion(self, number: int) -> Suggestion | None:
        return next((s for s in self.suggestions if s.id == number), None)

    def answer(self, number: int, status: str) -> None:
        """Your Yes / No to one suggestion (sent with your next message)."""
        found = self.suggestion(number)
        if found:
            found.status, found.told = status, False
            self.save()

    def untold_answers(self) -> list[Suggestion]:
        return [s for s in self.suggestions if s.status != OPEN and not s.told]

    # --- files ---------------------------------------------------------------------------------

    @staticmethod
    def path_for(root: Path) -> Path:
        return Path(root) / STATE_FILE

    @classmethod
    def load(cls, root: Path) -> PlannerState:
        path = cls.path_for(root)
        if not path.is_file():
            return cls(Path(root))
        try:
            data = json.loads(read_text(path)[0])
        except ValueError:
            return cls(Path(root))
        state = cls(Path(root))
        for name in _NESTED:
            data.setdefault(name, None)
        for f in fields(cls):
            if f.name in data and f.name != "root":
                state._take(f.name, data[f.name])
        state._seen = _plain(state)
        state._read_files()
        from devils_workshop.planner.output import read_back_edits
        read_back_edits(state)
        return state

    def _take(self, name: str, value) -> None:
        """One field from saved data, as the objects it holds."""
        if name == "registry":
            self.registry = Registry.from_dict(value)
        elif name == "turns":
            self.turns = [Turn(**_known(Turn, t)) for t in value or []]
        elif name == "suggestions":
            self.suggestions = [Suggestion(**_known(Suggestion, s)) for s in value or []]
        elif name == "chapters":
            self.chapters = [_chapter(raw) for raw in value or []]
        elif value is not None:
            setattr(self, name, value)

    def save(self) -> None:
        """Save, keeping what anyone else saved meanwhile. A run holds its state for a long time while you use
        other screens (the Reader, the planner screen), and each saves its own: a field (or a chapter, or a
        scene) only this copy changed is saved from it, one only the other changed is kept from the file, and
        this copy takes it too, so neither change is lost. When both changed the same scene, this copy wins."""
        with _lock(self.root):
            mine = _plain(self)
            path = self.path_for(self.root)
            seen = getattr(self, "_seen", None)
            if seen is not None and path.is_file():
                try:
                    disk = json.loads(read_text(path)[0])
                except ValueError:
                    disk = None
                if disk is not None:
                    mine = self._merge(mine, seen, disk)
            data = dict(mine)
            data["saved"] = datetime.now().isoformat(timespec="seconds")
            write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            self._seen = mine
        self._write_files()

    def _merge(self, mine: dict, seen: dict, disk: dict) -> dict:
        merged = dict(mine)
        for name, value in mine.items():
            if name == "chapters":
                chapters = _merge_chapters(value, seen.get(name) or [], disk.get(name) or [])
                if chapters != value:
                    merged[name] = chapters
                    self._update_chapters(chapters)
            elif name in disk and value == seen.get(name) and disk[name] != seen.get(name):
                merged[name] = disk[name]  # changed elsewhere, not here
                self._take(name, disk[name])
        return merged

    def _update_chapters(self, chapters: list[dict]) -> None:
        """Take the merged chapters, changing the chapter and scene objects in place (a run holds them)."""
        by_id = {c.id: c for c in self.chapters}
        result = []
        for raw in chapters:
            chapter = by_id.get(raw.get("id"))
            if chapter is None:
                result.append(_chapter(raw))
                continue
            for key, value in raw.items():
                if key != "scenes":
                    setattr(chapter, key, value)
            scenes = {s.id: s for s in chapter.scenes}
            chapter.scenes = []
            for item in raw.get("scenes") or []:
                scene = scenes.get(item.get("id"))
                if scene is None:
                    chapter.scenes.append(ScenePlan(**_known(ScenePlan, item)))
                else:
                    for key, value in _known(ScenePlan, item).items():
                        setattr(scene, key, value)
                    chapter.scenes.append(scene)
            result.append(chapter)
        self.chapters = result

    # --- the files in the story folder ------------------------------------------------------------

    def _read_files(self) -> None:
        """Your edits to the planner's files win over what was saved."""
        plan = self.root / PLAN_FILE
        if plan.is_file():
            self.plan = read_text(plan)[0].strip()
        decisions = self.root / DECISIONS_FILE
        if decisions.is_file():
            found = parse_decisions(read_text(decisions)[0])
            self.decisions, self.agenda = found["settled"], found["open"]
            self.ruled_out, self.preferences = found["ruled_out"], found["preferences"]
        registry = self.root / REGISTRY_FILE
        if registry.is_file():
            read_approvals(self.registry, read_text(registry)[0])
        sources = self.root / SOURCES_FILE
        if sources.is_file():
            attached = {name for turn in self.turns for name in turn.attachments}
            self.notes = rejoin_notes(parse_notes(read_text(sources)[0]), attached)
        guide = self.root / GUIDE_FILE
        if self.guide.strip() and guide.is_file() and read_text(guide)[0].strip() != self.guide.strip():
            self.guide = read_text(guide)[0].strip()  # edited by hand (a guide being written again isn't read)

    def _write_files(self) -> None:
        def keep(name: str, text: str, wanted: bool) -> None:
            check(PLANNER_JOB, name)  # the planner keeps its own files, never the story's text
            path = self.root / name
            if (wanted or path.is_file()) and (not path.is_file() or read_text(path)[0] != text):
                write_text(path, text)

        if self.plan.strip():
            keep(PLAN_FILE, self.plan.strip() + "\n", True)
        keep(DECISIONS_FILE, decisions_text(self.decisions, self.agenda, self.ruled_out, self.preferences),
             bool(self.decisions or self.agenda or self.ruled_out or self.preferences))
        keep(SOURCES_FILE, notes_text(self.notes), bool(self.notes))

    def brief_target(self, scene: ScenePlan) -> int:
        """About how long a scene's brief should be: brief_words for a scene of scene_words, in proportion."""
        if scene.staging == FULL and scene.words:  # a choreography the writer writes from alone
            return max(80, int(round(scene.words * FULL_BRIEF / 10.0)) * 10)
        if not scene.words or not self.scene_words:
            return self.brief_words
        return max(80, int(round(scene.words * self.brief_words / self.scene_words / 10.0)) * 10)

    # --- reading --------------------------------------------------------------------------------

    def chapter(self, number: int) -> ChapterPlan | None:
        return next((c for c in self.chapters if c.number == number), None)

    def total_words(self) -> int:
        return sum(c.words for c in self.chapters)

    def progress(self) -> tuple[int, int]:
        """(scene briefs ready, scene briefs due so far). With briefs just in time, a later chapter's briefs are
        only due when it is about to be written, so they don't count yet."""
        due = [c for c in self.chapters if not self.jit or c.number <= self.briefs_through
               or any(s.status == READY for s in c.scenes)]
        scenes = [s for c in due for s in c.scenes]
        return sum(1 for s in scenes if s.status == READY), len(scenes)

    def summary(self) -> str:
        """One line for the story card: where planning stands."""
        if self.stage == BRAINSTORM:
            return "Planning: brainstorming the story plan"
        if self.stage == DONE:
            return "Plan finished"
        if not self.chapters:
            return "Planning: the chapters"
        planned = sum(1 for c in self.chapters if c.planned)
        return f"Planning: {planned} of {len(self.chapters)} chapters planned"


DECISION_SECTIONS = {  # key -> (heading, shown when empty)
    "settled": ("## Settled", "(nothing yet)"),
    "open": ("## Still open (most important first)", "(nothing open)"),
    "ruled_out": ("## Ruled out", "(nothing yet)"),
    "preferences": ("## Your preferences", "(none yet)"),
}
_EMPTY = {empty for _, empty in DECISION_SECTIONS.values()}


def decisions_text(decisions: list[str], agenda: list[str], ruled_out: list[str] = (),
                   preferences: list[str] = ()) -> str:
    lines = ["# Decisions", "",
             "The story planner keeps this file up to date as you plan, and reads it before every reply: edit it "
             "if you like. Settled decisions are kept to; the open ones are what the planner asks you about next; "
             "what is ruled out is never offered again; your preferences hold for the whole book."]
    for key, items in (("settled", decisions), ("open", agenda), ("ruled_out", ruled_out),
                       ("preferences", preferences)):
        heading, empty = DECISION_SECTIONS[key]
        lines += ["", heading] + ([f"- {item}" for item in items] or [f"- {empty}"])
    return "\n".join(lines) + "\n"


def parse_decisions(text: str) -> dict[str, list[str]]:
    """Each section of Decisions.md (settled, open, ruled_out, preferences): the "- " lines under its heading."""
    found: dict[str, list[str]] = {key: [] for key in DECISION_SECTIONS}
    current = None
    for line in text.split("\n"):
        if line.startswith("## "):
            name = line[3:].strip().lower()
            current = ("settled" if name.startswith("settled") else
                       "open" if name.startswith(("still open", "open")) else
                       "ruled_out" if name.startswith(("ruled out", "rejected")) else
                       "preferences" if "preference" in name else None)
        elif current and line.strip().startswith(("- ", "* ")):
            item = line.strip()[2:].strip()
            if item and item not in _EMPTY:
                found[current].append(item)
    return found


def notes_text(notes: dict[str, str]) -> str:
    lines = ["# Source notes", "",
             "The story planner's notes on each file and picture you gave it, made once, when you attached it. "
             "From then on it works from these notes, not from the files: edit them if something is missing "
             "or wrong."]
    for name, text in notes.items():  # a heading inside the notes goes one level down: "## " names a file
        lines += ["", f"## {name}", re.sub(r"(?m)^(#{1,5} )", r"#\1", text.strip())]
    return "\n".join(lines) + "\n"


def parse_notes(text: str) -> dict[str, str]:
    notes: dict[str, list[str]] = {}
    current = None
    for line in text.split("\n"):
        if line.startswith("## "):
            current = line[3:].strip()
            notes[current] = []
        elif current is not None:
            notes[current].append(re.sub(r"^#(#{2,6} )", r"\1", line))  # its own headings, as notes_text kept them
    return {name: "\n".join(lines).strip() for name, lines in notes.items()}


def rejoin_notes(notes: dict[str, str], attached: set[str]) -> dict[str, str]:
    """Notes saved before their own "## " headings were kept inside them read back as several "files": a section
    that names no file you attached belongs to the file before it."""
    joined: dict[str, str] = {}
    for name, text in notes.items():
        if joined and attached and name not in attached:
            last = next(reversed(joined))
            joined[last] = f"{joined[last]}\n\n## {name}\n{text}".strip()
        else:
            joined[name] = text
    return joined


def is_planner_story(root: Path | None) -> bool:
    return bool(root) and PlannerState.path_for(root).is_file()


def _known(cls, raw: dict, skip: tuple = ()) -> dict:
    names = {f.name for f in fields(cls)} - set(skip)
    return {k: v for k, v in (raw or {}).items() if k in names}


_NESTED = ("registry",)
_LOCKS: dict[str, threading.Lock] = {}


def _lock(root: Path) -> threading.Lock:
    return _LOCKS.setdefault(str(Path(root).resolve()), threading.Lock())


def _plain(state: PlannerState) -> dict:
    data = asdict(state)
    data.pop("root")
    return json.loads(json.dumps(data, ensure_ascii=False))


def _chapter(raw: dict) -> ChapterPlan:
    chapter = ChapterPlan(**_known(ChapterPlan, raw, skip=("scenes",)))
    chapter.scenes = [ScenePlan(**_known(ScenePlan, s)) for s in raw.get("scenes") or []]
    for item in [chapter, *chapter.scenes]:
        item.id = item.id or new_id()
    return chapter


def _merge_items(mine: list[dict], seen: list[dict], disk: list[dict], merge_one) -> list[dict]:
    """Lists of chapters (or scenes) by id: each one from whichever copy changed it."""
    ids = [m.get("id") for m in mine]
    if [d.get("id") for d in disk] != [s.get("id") for s in seen]:
        # the list itself was replaced elsewhere (chapters planned again): if this copy didn't touch it, take it
        return disk if mine == seen else mine
    if ids != [s.get("id") for s in seen]:
        return mine  # this copy replaced the list
    seen_by, disk_by = {s.get("id"): s for s in seen}, {d.get("id"): d for d in disk}
    return [merge_one(m, seen_by.get(m.get("id"), {}), disk_by.get(m.get("id"), m)) for m in mine]


def _merge_scene(mine: dict, seen: dict, disk: dict) -> dict:
    if mine == seen and disk != seen:
        return disk
    return mine


def _merge_chapter(mine: dict, seen: dict, disk: dict) -> dict:
    merged = {key: (disk.get(key, value) if key != "scenes" and value == seen.get(key) and
                    disk.get(key) != seen.get(key) else value) for key, value in mine.items()}
    merged["scenes"] = _merge_items(mine.get("scenes") or [], seen.get("scenes") or [], disk.get("scenes") or [],
                                    _merge_scene)
    return merged


def _merge_chapters(mine: list[dict], seen: list[dict], disk: list[dict]) -> list[dict]:
    return _merge_items(mine, seen, disk, _merge_chapter)
