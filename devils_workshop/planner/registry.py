"""The story's registry: every consequential event and fact, with an id, where it belongs, and what has happened.

Events come from the chapter architecture, one per key beat and one for each chapter's ending, each owned by its
chapter (and, once the chapter's scenes are planned, by the scene that carries it). The checks of what was written
(planner/audit.py) record where each one actually happens, so an event is marked written once, in its own place:
the same event told again as new, an event of a later chapter told early, or a chapter's event left out is a
problem, while remembering, discussing or reinterpreting an earlier event is not.

Facts are the story's canon: identities, relationships, roles, dates, quantities, earlier meetings, objects and
who knows what, each with where it comes from, from when it is true (a fact that only becomes true in chapter 5,
or a secret a character only learns there, isn't true before), and its authority (the plan, or you).

In an adaptation, source events are the events of the reference, in order, with their roles and causes, each
mapped to the event of this story that carries it (or approved as left out).

Kept in planner/state.json; Story registry.md shows it all for you to read, and is read back: under "## Your
approvals" you can allow an event early (a flashback or foreshadowing the architecture wants: "E-02-1: early
from chapter 1"), leave a source event out ("S7: left out"), approve a change of fact ("F3: <the new fact>") or
a change from the reference ("Name: Ana -> Mara").
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

REGISTRY_FILE = "Story registry.md"
PLANNED, WRITTEN, MISSING = "planned", "written", "missing"
BEAT, ENDING = "beat", "ending"
ORIGINAL, ADAPTATION, IMPORT = "original", "adaptation", "import"  # IMPORT: a plan the author made elsewhere


@dataclass
class Event:
    id: str                    # "E-03-2": chapter 3's second key beat; "E-03-end": its ending
    chapter: int               # the chapter it belongs to
    text: str
    kind: str = BEAT           # BEAT | ENDING
    scene: int = 0             # the scene planned to carry it (0: not known yet)
    status: str = PLANNED      # PLANNED | WRITTEN
    written_in: list[int] = field(default_factory=list)  # [chapter, scene] where it happened
    prerequisites: list[str] = field(default_factory=list)  # events that must have happened first
    sources: list[str] = field(default_factory=list)        # an adaptation: the source events it carries
    early_from: int = 0        # you approved it from this earlier chapter (a flashback, foreshadowing)
    moved: bool = False        # you moved it to another chapter (its chapter is the new one)

    @property
    def unique(self) -> bool:
        return True            # every registered event happens once; later mentions are memories or callbacks


@dataclass
class Fact:
    id: str                    # "F1"
    statement: str
    kind: str = "fact"         # identity | relationship | role | date | quantity | meeting | object | knowledge | ...
    subject: str = ""          # who or what it is about (for knowledge: who knows)
    from_chapter: int = 1      # true from this chapter on (1: from the start)
    until_chapter: int = 0     # true until this chapter (0: to the end)
    source: str = ""           # where it comes from: "Writer guide", "Story plan", "chapter 3 as written", "you"
    authority: str = "plan"    # plan | author (yours: never replaced by anything generated)
    disclosed_by: str = ""     # the event that makes it known (knowledge)

    def true_at(self, chapter: int) -> bool:
        return self.from_chapter <= chapter and (not self.until_chapter or chapter <= self.until_chapter)


@dataclass
class SourceEvent:
    id: str                    # "S12"
    order: int
    text: str
    roles: str = ""            # who does what, by their function in the reference
    causes: list[str] = field(default_factory=list)  # source events it follows from
    required: bool = True
    weight: str = ""           # how much the reference dwells on it: "major" | "minor" | "passing"
    events: list[str] = field(default_factory=list)  # this story's events that carry it
    left_out: str = ""         # approved as left out (why)


@dataclass
class Consequence:
    """What an event changed for someone, still to be lived with: open until a scene absorbs it."""
    id: str                    # "C7"
    who: str                   # the characters it bears on, as the check named them
    text: str
    chapter: int = 0           # where it arose
    scene: int = 0
    absorbed_in: list[int] = field(default_factory=list)  # [chapter, scene] where the story absorbed it

    @property
    def open(self) -> bool:
        return not self.absorbed_in


@dataclass
class CharacterState:
    """How a character stands at the end of the last scene they were in."""
    name: str
    state: str                 # where, body, feelings, wants, knows, how they stand with the others, still reacting to
    chapter: int = 0
    scene: int = 0


@dataclass
class SceneSummary:
    """One written scene in a line, for variety: its kind, and what it did and how."""
    chapter: int
    scene: int
    kind: str
    text: str


@dataclass
class Registry:
    kind: str = ORIGINAL
    events: list[Event] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    sources: list[SourceEvent] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)   # approved changes from the reference ("Name: Ana -> Mara")
    canon_mark: str = ""       # the guide and architecture the facts and the guide's scope were worked out from
    guide_scope: dict[str, int] = field(default_factory=dict)  # guide block (its hash) -> the chapter it is true from
    map_mark: str = ""         # the architecture the source events were mapped to
    reference: str = ""        # an adaptation: the attached file (planner/inputs) it adapts
    plan_files: list[str] = field(default_factory=list)  # IMPORT: the attached files that are the plan, in order
    consequences: list[Consequence] = field(default_factory=list)
    characters: list[CharacterState] = field(default_factory=list)  # each character's latest state
    summaries: list[SceneSummary] = field(default_factory=list)     # every written scene, in a line

    # --- reading ---

    def follows_reference(self) -> bool:
        """An adaptation, or a plan made elsewhere: the chapters carry the reference's events, in its order."""
        return self.kind in (ADAPTATION, IMPORT)

    def event(self, event_id: str) -> Event | None:
        return next((e for e in self.events if e.id.lower() == event_id.strip().lower()), None)

    def fact(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id.lower() == fact_id.strip().lower()), None)

    def source(self, source_id: str) -> SourceEvent | None:
        return next((s for s in self.sources if s.id.lower() == source_id.strip().lower()), None)

    def of_chapter(self, chapter: int) -> list[Event]:
        return [e for e in self.events if e.chapter == chapter]

    def facts_at(self, chapter: int) -> tuple[list[Fact], list[Fact]]:
        """(the facts true at this chapter, the facts that only become true later)."""
        now = [f for f in self.facts if f.true_at(chapter)]
        later = [f for f in self.facts if f.from_chapter > chapter]
        return now, later

    # --- saving ---

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> Registry:
        data = dict(data or {})
        registry = cls(**{k: v for k, v in data.items() if k in ("kind", "changes", "canon_mark", "guide_scope",
                                                               "map_mark", "reference", "plan_files")})
        registry.events = [Event(**_known(Event, e)) for e in data.get("events") or []]
        registry.facts = [Fact(**_known(Fact, f)) for f in data.get("facts") or []]
        registry.sources = [SourceEvent(**_known(SourceEvent, s)) for s in data.get("sources") or []]
        registry.consequences = [Consequence(**_known(Consequence, c)) for c in data.get("consequences") or []]
        registry.characters = [CharacterState(**_known(CharacterState, c)) for c in data.get("characters") or []]
        registry.summaries = [SceneSummary(**_known(SceneSummary, c)) for c in data.get("summaries") or []]
        return registry

    # --- what the story carries forward ---

    def consequence(self, consequence_id: str) -> Consequence | None:
        return next((c for c in self.consequences if c.id.lower() == consequence_id.strip().lower()), None)

    def open_consequences(self) -> list[Consequence]:
        return [c for c in self.consequences if c.open]

    def character(self, name: str) -> CharacterState | None:
        key = name.strip().lower()
        return next((c for c in self.characters if c.name.lower() == key), None)

    def present(self, text: str) -> list[CharacterState]:
        """The characters named in a text (by their full name or first name), with their latest states."""
        plain = " " + " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split()) + " "
        found = []
        for character in self.characters:
            names = {character.name.lower(), character.name.split()[0].lower()} if character.name.strip() else set()
            if any(f" {n} " in plain for n in names if len(n) > 1):
                found.append(character)
        return found

    def like(self, kind: str, before: tuple[int, int]) -> list[SceneSummary]:
        """The written scenes of this kind before a point of the story, in order."""
        key = kind.strip().lower()
        return [s for s in self.summaries if key and s.kind.lower() == key and (s.chapter, s.scene) < before]


def _known(cls, raw: dict) -> dict:
    names = set(cls.__dataclass_fields__)
    return {k: v for k, v in (raw or {}).items() if k in names}


# --- building it ---------------------------------------------------------------------------------------------

def event_id(chapter: int, index: int | str) -> str:
    return f"E-{chapter:02d}-{index}"


def events_from(chapters, keep: list[Event] = ()) -> list[Event]:
    """One event per key beat and one for each ending, from the chapter architecture. An event already in the
    registry with the same chapter and text keeps what is known about it (where it was written, your approvals)."""
    known = {(e.chapter, " ".join(e.text.lower().split())): e for e in keep}
    events = []
    for chapter in chapters:
        items = [(event_id(chapter.number, n), beat, BEAT) for n, beat in enumerate(chapter.beats, 1)]
        if chapter.ending.strip():
            items.append((event_id(chapter.number, "end"), chapter.ending.strip(), ENDING))
        for new_id, text, kind in items:
            old = known.get((chapter.number, " ".join(text.lower().split())))
            if old is not None:
                old.id, old.kind = new_id, kind
                events.append(old)
            else:
                events.append(Event(new_id, chapter.number, text, kind))
    return events


def assign_scenes(registry: Registry, chapter) -> None:
    """Which scene carries each of the chapter's events: the scene whose line is most like it (the checks of
    what was written record where it actually happens). The ending is the last scene's."""
    from devils_workshop.planner.contracts import _alike
    for event in registry.of_chapter(chapter.number):
        if not chapter.scenes:
            event.scene = 0
        elif event.kind == ENDING:
            event.scene = chapter.scenes[-1].number
        else:
            best = max(chapter.scenes, key=lambda s: _alike(event.text, f"{s.title} {s.purpose}"))
            event.scene = best.number if _alike(event.text, f"{best.title} {best.purpose}") > 0 else 0


def forget_written(registry: Registry, chapter: int, scenes: list[int] | None = None) -> None:
    """The chapter (or some of its scenes) is being written again: what happened in it is no longer known."""
    def here(c: int, s: int) -> bool:
        return c == chapter and (scenes is None or s in scenes)

    for event in registry.events:
        if event.written_in and here(*event.written_in):
            event.status, event.written_in = PLANNED, []
    registry.consequences = [c for c in registry.consequences if not here(c.chapter, c.scene)]
    for consequence in registry.consequences:
        if consequence.absorbed_in and here(*consequence.absorbed_in):
            consequence.absorbed_in = []
    registry.summaries = [s for s in registry.summaries if not here(s.chapter, s.scene)]
    # a character's state from a scene written again is kept until that scene's check replaces it


# --- Story registry.md --------------------------------------------------------------------------------------

APPROVALS = "## Your approvals"


def registry_text(registry: Registry, title: str = "") -> str:
    lines = [f"# {title + ': ' if title else ''}story registry", "",
             "Kept by the story planner: every event of the chapter architecture with where it belongs and where it "
             "was written, and the story's facts with when each one is true. The writer never sees this file. Add "
             f"your approvals under \"{APPROVALS}\" (one per line, as the examples show); the rest is rewritten by "
             "the planner.", "",
             f"Kind: {registry.kind}" + (f" (of {registry.reference})" if registry.reference else ""),
             "", "## Events"]
    for e in registry.events:
        where = f"written in chapter {e.written_in[0]}, scene {e.written_in[1]}" if e.written_in else e.status
        planned = f"chapter {e.chapter}" + (f", scene {e.scene}" if e.scene else "")
        extra = f"; early from chapter {e.early_from} (approved)" if e.early_from else ""
        extra += f"; carries {', '.join(e.sources)}" if e.sources else ""
        lines.append(f"- [{e.id}] ({planned}; {where}{extra}) {e.text}")
    lines += ["", "## Facts"]
    for f in registry.facts:
        when = f"from chapter {f.from_chapter}" + (f" to {f.until_chapter}" if f.until_chapter else "")
        who = f"{f.subject}: " if f.subject else ""
        lines.append(f"- [{f.id}] ({f.kind}; {when}; {f.authority}; from {f.source or 'the plan'}) {who}{f.statement}")
    if registry.characters:
        lines += ["", "## Characters, as they stand"]
        lines += [f"- {c.name} (after chapter {c.chapter}, scene {c.scene}): {c.state}" for c in registry.characters]
    if registry.consequences:
        lines += ["", "## Consequences still to be lived with"]
        lines += [f"- [{c.id}] ({c.who}; from chapter {c.chapter}, scene {c.scene}) {c.text}"
                  for c in registry.open_consequences()]
        absorbed = [c for c in registry.consequences if not c.open]
        if absorbed:
            lines += ["", "Absorbed:"] + [f"- [{c.id}] (in chapter {c.absorbed_in[0]}, scene {c.absorbed_in[1]}) "
                                          f"{c.text}" for c in absorbed]
    if registry.summaries:
        lines += ["", "## Scenes so far"]
        lines += [f"- Chapter {s.chapter}, scene {s.scene} ({s.kind or 'scene'}): {s.text}" for s in registry.summaries]
    if registry.follows_reference() or registry.sources:
        lines += ["", "## Source events (the reference, in order)"]
        for s in registry.sources:
            carried = (f"carried by {', '.join(s.events)}" if s.events else
                       f"left out (approved): {s.left_out}" if s.left_out else "not carried yet")
            causes = f"; follows from {', '.join(s.causes)}" if s.causes else ""
            weight = f"; {s.weight}" if s.weight else ""
            lines.append(f"- [{s.id}] ({'required' if s.required else 'optional'}{weight}{causes}; {carried}) {s.text}"
                         + (f" (roles: {s.roles})" if s.roles else ""))
    lines += ["", APPROVALS, "<!-- One per line, e.g.:", "- E-04-1: early from chapter 2",
              "- E-03-2: moved to chapter 4", "- S7: left out",
              "- F3: Mara and Tobias met once before, at the fair", "- Name: Ana -> Mara", "-->"]
    lines += [f"- {line}" for line in _approval_lines(registry)]
    return "\n".join(lines) + "\n"


def _approval_lines(registry: Registry) -> list[str]:
    lines = [f"{e.id}: early from chapter {e.early_from}" for e in registry.events if e.early_from]
    lines += [f"{e.id}: moved to chapter {e.chapter}" for e in registry.events if e.moved]
    lines += [f"{s.id}: left out" + (f" ({s.left_out})" if s.left_out not in ("", "yes") else "")
              for s in registry.sources if s.left_out]
    lines += [f"{f.id}: {f.statement}" for f in registry.facts if f.authority == "author"]
    lines += list(registry.changes)
    return lines


def read_approvals(registry: Registry, text: str) -> None:
    """Your approvals in Story registry.md, into the registry."""
    heading = re.search(r"(?m)^" + re.escape(APPROVALS) + r"[ \t]*$", text)
    if heading is None:
        return
    section = re.sub(r"<!--.*?-->", "", text[heading.end():], flags=re.S)
    section = re.split(r"(?m)^##\s", section)[0]
    changes = []
    for line in section.split("\n"):
        item = line.strip()[2:].strip() if line.strip().startswith(("- ", "* ")) else ""
        if not item:
            continue
        key, _, value = item.partition(":")
        key, value = key.strip(), value.strip()
        early = re.match(r"early\s+from\s+chapter\s+(\d+)", value, re.I)
        moved = re.match(r"moved\s+to\s+chapter\s+(\d+)", value, re.I)
        if registry.event(key) and early:
            registry.event(key).early_from = int(early.group(1))
        elif registry.event(key) and moved:
            event = registry.event(key)
            event.chapter, event.scene, event.moved = int(moved.group(1)), 0, True
        elif registry.source(key) and value.lower().startswith("left out"):
            registry.source(key).left_out = value[len("left out"):].strip(" ()") or "yes"
        elif registry.fact(key) and value:
            fact = registry.fact(key)
            fact.statement, fact.authority, fact.source = value, "author", "you"
        elif re.match(r"F\d+$", key, re.I) and value:
            registry.facts.append(Fact(key.upper(), value, source="you", authority="author"))
        elif "->" in item or "→" in item:
            changes.append(item)
    registry.changes = changes
