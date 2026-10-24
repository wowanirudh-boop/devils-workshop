"""Checking what was written against the plan, the registry and the canon.

The planner's model reads the scenes as written (plan_audit) and reports, for each scene, what it does with each
registered event (happens, recalled, callback, repeated), anything consequential it adds, any fact it
contradicts, anyone who knows something before they could, whether its opening follows from how things stood,
where it departs from its brief, and how things stand at its end. That report is evidence, not a verdict: the
rules here decide, deterministically, against the registry (planner/registry.py):

hard (the story's structure breaks: the scene is written once more with the problems named; if a problem is
    still there, the scene is kept and listed): an event of a later chapter told now (unless you approved it
    early), an event that already happened told again as new, a fact contradicted, a secret known before it is
    disclosed, an event planned for the scene left out of it (or one of the chapter's, or the story's ending),
    leftover markup or a scene that stops almost at once;
advisory (shown, never rewritten for): an unplanned event, a consequential detail the canon doesn't have, an
    event before the events it follows from, an opening that may not follow from the scene before, a departure
    from the brief, attention in the wrong place (a minor moment stretched while an important one is rushed), an
    ending that may be cut off, repeated passages, a scene much longer or shorter than planned;
inconclusive (never counted as passed): what the check couldn't judge, or a reply that couldn't be read.

Deterministic checks of the prose itself (markup, length, repetition) need no request.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

from devils_workshop.context.assemble import ChatMessage
from devils_workshop.context.render import clean
from devils_workshop.planner.registry import ENDING, PLANNED, WRITTEN, Registry
from devils_workshop.wordcount import count_words

AUDIT_JOB = "plan_audit"
HARD, ADVISORY, INCONCLUSIVE = "hard", "advisory", "inconclusive"
HOW = ("happens", "continues", "recalled", "callback", "repeated")
TOO_SHORT = 0.1      # a scene under a tenth of its planned length stopped almost at once
LENGTH_OFF = 0.5     # more than 50% off its planned length: a note
REPEAT_WORDS = 8     # a sentence of at least this many words, word for word twice: repeated
MARKUP = re.compile(r"</?(?:think|thinking|brief|continuity|scene|task|reply)\b|^\s*\*\*(?:coming in|what happens|"
                    r"ends?|requirements here)\b|^\s*words\s*:\s*\d|\((?:full|some|brief)(?:, about [\d,]+ words)?\)",
                    re.I | re.M)


@dataclass
class Finding:
    severity: str          # HARD | ADVISORY | INCONCLUSIVE
    kind: str              # early | repeat | order | contradiction | knowledge | invented | entry | missing |
                           # ending | markup | short | cut_off | unplanned | departure | repetition | length | unsure
    text: str
    chapter: int = 0
    scene: int = 0
    ref: str = ""          # the event or fact it is about

    def line(self) -> str:
        where = f"chapter {self.chapter}" + (f", scene {self.scene}" if self.scene else "")
        return f"{where}: {self.text}"


@dataclass
class SceneAudit:
    number: int
    events: list[tuple[str, str]] = field(default_factory=list)        # (event id, how)
    unplanned: list[str] = field(default_factory=list)
    new_facts: list[tuple[str, str]] = field(default_factory=list)     # (kind, statement)
    contradictions: list[tuple[str, str]] = field(default_factory=list)  # (fact id, what the text says)
    early_knowledge: list[tuple[str, str]] = field(default_factory=list)  # (fact id, how it shows)
    entry_ok: bool | None = None
    entry_note: str = ""
    departures: list[str] = field(default_factory=list)
    exit: str = ""
    unsure: list[str] = field(default_factory=list)
    padding: list[str] = field(default_factory=list)  # minor moments stretched while important ones are rushed
    characters: list[tuple[str, str]] = field(default_factory=list)   # (name, how they stand at the end)
    consequences: list[tuple[str, str]] = field(default_factory=list)  # (who, what changed for them, still open)
    absorbed: list[str] = field(default_factory=list)                 # open consequences this scene absorbs
    ignored: list[tuple[str, str]] = field(default_factory=list)      # (consequence, how it is acted as if not)
    behaviour: list[tuple[str, str]] = field(default_factory=list)    # (who, what doesn't follow from who they are)
    summary: tuple[str, str] = ("", "")                               # (its kind, the scene in a line)
    read: bool = False     # the check's reply had this scene in a form that could be read


def text_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()[:16]


# --- the request -----------------------------------------------------------------------------------------------

def registry_block(registry: Registry, chapter: int) -> str:
    lines = []
    for e in registry.events:
        where = f"chapter {e.chapter}" + (f", scene {e.scene}" if e.scene else "")
        state = (f"already happened in chapter {e.written_in[0]}, scene {e.written_in[1]}" if e.written_in else
                 "not happened yet")
        early = f"; may be told early from chapter {e.early_from}" if e.early_from else ""
        lines.append(f"- [{e.id}] (planned for {where}; {state}{early}) {e.text}")
    now, later = registry.facts_at(chapter)
    parts = [f"<events>\n{chr(10).join(lines) or '(none)'}\n</events>"]
    parts.append("<facts>\n" + ("\n".join(f"- [{f.id}] ({f.kind}{'; ' + f.subject if f.subject else ''}) "
                                          f"{f.statement}" for f in now) or "(none)") + "\n</facts>")
    if later:
        parts.append("<not_true_yet>\n" + "\n".join(
            f"- [{f.id}] (only from chapter {f.from_chapter}; {f.kind}{'; ' + f.subject if f.subject else ''}) "
            f"{f.statement}" for f in later) + "\n</not_true_yet>")
    if registry.changes:
        parts.append("<approved_changes>\n" + "\n".join(f"- {c}" for c in registry.changes) + "\n</approved_changes>")
    return "\n\n".join(parts)


def audit_messages(root, registry: Registry, chapter: int, scenes: list[tuple[int, str, str, int]],
                   coming_in: str) -> list[ChatMessage]:
    """The check of some of a chapter's scenes, as written. scenes: (number, its brief, its text, planned words)."""
    from devils_workshop.planner.prompts import job_text
    parts = [registry_block(registry, chapter)]
    at = (chapter, min(n for n, *_ in scenes))  # only what stood before: a scene written again later in the
    # story is never checked against how the story went on after it
    present = [c for c in registry.present(" ".join(f"{brief} {text}" for _, brief, text, _ in scenes) + " "
                                           + coming_in) if (c.chapter, c.scene) < at]
    if present:
        parts.append("<characters_before>\n" + "\n".join(f"- {c.name} (after chapter {c.chapter}, scene "
                                                         f"{c.scene}): {c.state}" for c in present)
                     + "\n</characters_before>")
    still_open = [c for c in registry.consequences
                  if (c.chapter, c.scene) < at and (not c.absorbed_in or tuple(c.absorbed_in) >= at)]
    if still_open:
        parts.append("<open_consequences>\n" + "\n".join(f"- [{c.id}] ({c.who}) {c.text}" for c in still_open)
                     + "\n</open_consequences>")
    parts.append(f"<coming_in>\n{coming_in.strip() or '(the story begins here)'}\n</coming_in>")
    for number, brief, text, words in scenes:
        parts.append(f'<scene number="{number}" planned_words="{words}">\n<brief>\n{clean(brief)}\n</brief>\n'
                     f"<text>\n{clean(text)}\n</text>\n</scene>")
    listed = ", ".join(str(n) for n, *_ in scenes)
    parts.append(f"<task>\nCheck scene{'s' if len(scenes) > 1 else ''} {listed} of chapter {chapter} as written."
                 "\n</task>")
    return [ChatMessage("system", job_text(root, AUDIT_JOB)), ChatMessage("user", "\n\n".join(parts))]


# --- reading the reply -------------------------------------------------------------------------------------------

def _all(tag: str, body: str) -> list[str]:
    return [m.strip() for m in re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}>", body, re.S | re.I) if m.strip()]


def _tags(tag: str, body: str) -> list[tuple[dict, str]]:
    """(attributes, text) of every <tag .../> or <tag ...>text</tag>."""
    found = []
    for attrs, text in re.findall(rf"<{tag}\b([^>]*?)\s*(?:/>|>(.*?)</{tag}>)", body, re.S | re.I):
        values = {k.lower(): v.strip() for k, v in re.findall(r'(\w+)\s*=\s*"([^"]*)"', attrs)}
        found.append((values, (text or "").strip()))
    return found


def read_audit(reply: str, numbers: list[int]) -> dict[int, SceneAudit]:
    """Each scene's report. A scene the reply has no block for (or a reply that can't be read) has read=False."""
    result = {n: SceneAudit(n) for n in numbers}
    blocks = re.findall(r'<scene\b[^>]*number\s*=\s*"?(\d+)"?[^>]*>(.*?)(?=<scene\b[^>]*number|\Z)', reply,
                        re.S | re.I)
    if not blocks and len(numbers) == 1 and re.search(r"<(event|exit|entry)\b", reply, re.I):
        blocks = [(str(numbers[0]), reply)]  # one scene, written without its <scene> block
    for raw_number, body in blocks:
        number = int(raw_number)
        if number not in result:
            continue
        body = re.sub(r"</scene>\s*$", "", body.strip(), flags=re.I)
        audit = result[number]
        audit.read = True
        for values, _ in _tags("event", body):
            how = values.get("how", "happens").lower()
            if values.get("id"):
                audit.events.append((values["id"], how if how in HOW else "happens"))
        audit.unplanned = _all("unplanned", body)
        audit.new_facts = [(v.get("kind", "fact").lower(), t) for v, t in _tags("new_fact", body) if t]
        audit.contradictions = [(v.get("fact", ""), t) for v, t in _tags("contradiction", body) if t]
        audit.early_knowledge = [(v.get("fact", ""), t) for v, t in _tags("early_knowledge", body)
                                 if v.get("fact") or t]
        entries = _tags("entry", body)
        if entries:
            audit.entry_ok = entries[0][0].get("ok", "yes").lower() in ("yes", "true", "ok")
            audit.entry_note = entries[0][1]
        audit.departures = _all("departure", body)
        exits = _all("exit", body)
        audit.exit = exits[0] if exits else ""
        audit.unsure = _all("unsure", body)
        audit.padding = _all("padding", body)
        audit.characters = [(v.get("name", ""), t) for v, t in _tags("character", body) if v.get("name") and t]
        audit.consequences = [(v.get("who", ""), t) for v, t in _tags("consequence", body) if t]
        audit.absorbed = [v.get("id", "") for v, _ in _tags("absorbed", body) if v.get("id")]
        audit.ignored = [(v.get("id", ""), t) for v, t in _tags("ignored", body) if v.get("id") or t]
        audit.behaviour = [(v.get("who", ""), t) for v, t in _tags("behaviour", body) if t]
        summaries = _tags("summary", body)
        if summaries and summaries[0][1]:
            audit.summary = (summaries[0][0].get("kind", "").lower(), summaries[0][1])
    return result


# --- deciding --------------------------------------------------------------------------------------------------

def evaluate(registry: Registry, chapter: int, audit: SceneAudit, happened_here: set[str] = frozenset(),
             previous: tuple[int, int] | None = None) -> list[Finding]:
    """The findings for one scene's report, against the registry as it stood before the scene. happened_here: the
    events that happened earlier in the same check (scenes before this one). previous: the scene just before this
    one (chapter, scene): an event it told, carried on at the join, is worth a look, never a rewrite."""
    scene = audit.number
    found: list[Finding] = []

    def add(severity: str, kind: str, text: str, ref: str = "") -> None:
        found.append(Finding(severity, kind, text, chapter, scene, ref))

    if not audit.read:
        add(INCONCLUSIVE, "unsure", "the check's reply had nothing that could be read for this scene")
        return found
    seen_here = set(happened_here)
    for event_id, how in audit.events:
        event = registry.event(event_id)
        if event is None:
            add(ADVISORY, "unplanned", f"the check named an event the registry doesn't have ({event_id})")
            continue
        if how == "continues" and not (event.written_in and event.written_in[0] == chapter) and \
                event.id not in seen_here:
            how = "happens"  # nothing in this chapter began it: it happens here
        if how in ("happens", "repeated") and (event.chapter, event.scene) == (chapter, scene) and \
                event.written_in and event.written_in[0] == chapter and event.written_in[1] < scene:
            continue  # told in the scene planned for it: an earlier scene only touched it (a glimpse, a start)
        if how in ("happens", "repeated") and previous and event.written_in == list(previous):
            add(ADVISORY, "overlap", f"{event.id} (\"{event.text}\") was told in the scene just before, and this "
                                     "scene goes over it again where they join", event.id)
            continue
        if how == "repeated":
            add(HARD, "repeat", f"{event.id} (\"{event.text}\") is told again as if it were happening for the first "
                                "time", event.id)
            continue
        if how != "happens":
            continue  # remembered, discussed, reinterpreted: allowed
        if event.written_in and event.written_in != [chapter, scene]:
            add(HARD, "repeat", f"{event.id} (\"{event.text}\") already happened in chapter {event.written_in[0]}, "
                                f"scene {event.written_in[1]}, and happens again here", event.id)
            continue
        if event.id in seen_here:
            add(HARD, "repeat", f"{event.id} (\"{event.text}\") already happened earlier in this chapter", event.id)
            continue
        if event.chapter > chapter and not (event.early_from and event.early_from <= chapter):
            add(HARD, "early", f"{event.id} (\"{event.text}\") is planned for chapter {event.chapter} and happens "
                               "here already", event.id)
        for need in event.prerequisites:
            before = registry.event(need)
            if before and not before.written_in and need not in seen_here:
                add(ADVISORY, "order", f"{event.id} happens before {need} (\"{before.text}\"), which it follows from",
                    event.id)
        seen_here.add(event.id)
    disclosed = {e for e, how in audit.events if how in ("happens", "continues")}
    for fact_id, how in audit.early_knowledge:
        fact = registry.fact(fact_id) if fact_id else None
        if fact is not None and fact.disclosed_by and fact.disclosed_by in disclosed:
            continue  # it is disclosed in this very scene
        if fact is not None and fact.true_at(chapter):
            continue
        add(HARD, "knowledge", f"someone knows what they can't know yet: {fact.statement if fact else how}"
                               + (f" ({how})" if fact and how else ""), fact_id)
    for fact_id, says in audit.contradictions:
        fact = registry.fact(fact_id)
        if fact is None:
            add(ADVISORY, "contradiction", f"the check reports a contradiction with an unknown fact {fact_id}: {says}")
            continue
        if not fact.true_at(chapter):
            continue
        # a state (how bodies, clothes, places or people stand) or where a thing is changes as the story goes:
        # the briefs carry it, so a text that has it otherwise is worth a look, never a rewrite
        add(ADVISORY if fact.kind.lower() in ("state", "object") else HARD, "contradiction",
            f"contradicts {fact.id} (\"{fact.statement}\"): {says}", fact.id)
    for kind, statement in audit.new_facts:
        if _approved(registry, statement):
            continue
        add(ADVISORY, "invented", f"adds a consequential {kind} the canon doesn't have: {statement}")
    for event in registry.of_chapter(chapter):
        if event.scene == scene and not event.written_in and event.id not in seen_here:
            add(HARD, "missing", f"{event.id} is planned for this scene and doesn't happen in it: \"{event.text}\"",
                event.id)
    if audit.entry_ok is False:
        add(ADVISORY, "entry", "its opening doesn't follow from how things stood: " + (audit.entry_note or "(no reason "
                                                                                    "given)"))
    for item in audit.unplanned:
        add(ADVISORY, "unplanned", f"an event the plan doesn't have: {item}")
    for consequence_id, how in audit.ignored:
        open_one = registry.consequence(consequence_id) if consequence_id else None
        about = f"\"{open_one.text}\"" if open_one else consequence_id
        add(ADVISORY, "behaviour", f"behaves as if {about} hadn't happened" + (f": {how}" if how else ""))
    for who, what in audit.behaviour:
        add(ADVISORY, "behaviour", f"{who or 'a character'} doesn't act like themselves here: {what}")
    for item in audit.padding:
        add(ADVISORY, "padding", f"attention in the wrong place: {item}")
    for item in audit.departures:
        add(ADVISORY, "departure", f"departs from its brief: {item}")
    for item in audit.unsure:
        add(INCONCLUSIVE, "unsure", f"couldn't be judged: {item}")
    if not audit.exit:
        add(INCONCLUSIVE, "unsure", "the check didn't say how things stand at the end of the scene")
    return found


def _approved(registry: Registry, statement: str) -> bool:
    plain = " ".join(statement.lower().split())
    return any(" ".join(f.statement.lower().split()) == plain for f in registry.facts)


def commit(registry: Registry, chapter: int, audit: SceneAudit, kind: str = "") -> None:
    """Record what a scene that stands did: where its events happened, how each character now stands, the
    consequences it leaves open and the ones it absorbs, and the scene in a line."""
    from devils_workshop.planner.registry import CharacterState, Consequence, SceneSummary
    for event_id, how in audit.events:
        event = registry.event(event_id)
        if event is not None and how in ("happens", "continues") and not event.written_in:
            event.status, event.written_in = WRITTEN, [chapter, audit.number]
    for name, state in audit.characters:
        known = registry.character(name)
        if known is None:
            registry.characters.append(CharacterState(name.strip(), state, chapter, audit.number))
        elif (known.chapter, known.scene) <= (chapter, audit.number):
            known.state, known.chapter, known.scene = state, chapter, audit.number
    for consequence_id in audit.absorbed:
        found = registry.consequence(consequence_id)
        if found is not None and found.open:
            found.absorbed_in = [chapter, audit.number]
    number = max([int(c.id[1:]) for c in registry.consequences if c.id[1:].isdigit()] + [0])
    for who, text in audit.consequences:
        number += 1
        registry.consequences.append(Consequence(f"C{number}", who, text, chapter, audit.number))
    if audit.summary[1]:
        registry.summaries = [x for x in registry.summaries if (x.chapter, x.scene) != (chapter, audit.number)]
        registry.summaries.append(SceneSummary(chapter, audit.number, kind or audit.summary[0], audit.summary[1]))
        registry.summaries.sort(key=lambda x: (x.chapter, x.scene))


def chapter_findings(registry: Registry, chapter: int, last_chapter: int, checked_all: bool,
                     all_read: bool = True) -> list[Finding]:
    """Once every scene of the chapter is checked: its events that never happened (and the story's ending). When a
    scene's check couldn't be read, whether they happened isn't known: inconclusive, never passed."""
    if not checked_all:
        return []
    if not all_read:
        return [Finding(INCONCLUSIVE, "unsure", "whether every event planned for this chapter happens couldn't be "
                                                "checked (a scene's check couldn't be read)", chapter)]
    found = []
    for event in registry.of_chapter(chapter):  # an event planned for a scene is found missing in that scene's check
        if event.status == PLANNED and not event.written_in and not event.scene:
            ending = event.kind == ENDING and chapter == last_chapter
            found.append(Finding(HARD, "ending" if ending else "missing",
                                 ("the story's ending never happens: " if ending else
                                  f"{event.id} is planned for this chapter and never happens: ") + f"\"{event.text}\"",
                                 chapter, event.scene, event.id))
    return found


# --- the prose itself, no request ---------------------------------------------------------------------------------

def prose_findings(chapter: int, scene: int, text: str, planned_words: int) -> list[Finding]:
    found = []
    words = count_words(text)

    def add(severity: str, kind: str, message: str) -> None:
        found.append(Finding(severity, kind, message, chapter, scene))

    if MARKUP.search(text):
        add(HARD, "markup", "the text still has the planner's or the model's markup in it")
    if planned_words and words < planned_words * TOO_SHORT:
        add(HARD, "short", f"it stops almost at once ({words:,} of about {planned_words:,} words)")
    elif planned_words and abs(words - planned_words) > planned_words * LENGTH_OFF:
        add(ADVISORY, "length", f"{words:,} words, planned at about {planned_words:,}")
    stripped = text.rstrip().rstrip("*_ ")
    if stripped and stripped[-1] not in '.!?"”’)…—-' and not stripped.endswith("..."):
        add(ADVISORY, "cut_off", "it may stop in the middle of a sentence")
    for sentence in repeated_sentences(text):
        add(ADVISORY, "repetition", f"a sentence appears more than once: \"{sentence[:120]}\"")
    return found


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?…])[\"”’)]*\s+", text) if s.strip()]


def repeated_sentences(text: str, others: str = "") -> list[str]:
    """Sentences of REPEAT_WORDS or more words that appear twice (in the text, or in it and `others`)."""
    seen: dict[str, int] = {}
    for sentence in sentences(others):
        key = " ".join(re.sub(r"[^\w\s]", "", sentence.lower()).split())
        if len(key.split()) >= REPEAT_WORDS:
            seen[key] = seen.get(key, 0) + 1
    repeated = []
    for sentence in sentences(text):
        key = " ".join(re.sub(r"[^\w\s]", "", sentence.lower()).split())
        if len(key.split()) < REPEAT_WORDS:
            continue
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            repeated.append(sentence)
    return repeated


# --- what is kept ------------------------------------------------------------------------------------------------

def record(state, chapter: int, scene: int, text: str, audit: SceneAudit | None, findings: list[Finding],
           revision: str = "") -> None:
    """Keep the check of one scene (state.audits)."""
    entry = state.audits.setdefault(str(chapter), {"scenes": {}})
    entry["scenes"][str(scene)] = {
        "text": text_hash(text), "findings": [asdict(f) for f in findings],
        "exit": audit.exit if audit else "", "departures": audit.departures if audit else [],
        "events": [list(e) for e in audit.events] if audit else [], "read": bool(audit and audit.read),
    }
    if revision:
        entry["revision"] = revision


def findings_of(state, chapter: int) -> list[Finding]:
    entry = state.audits.get(str(chapter)) or {}
    found = [Finding(**f) for s in (entry.get("scenes") or {}).values() for f in s.get("findings") or []]
    return found + [Finding(**f) for f in entry.get("chapter_findings") or []]


def hard(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == HARD]

