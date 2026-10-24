"""The story's canon, the writer's guide as it stands at each chapter, and an adaptation's reference events.

Canon (plan_canon, once the guide and the chapter architecture exist, and again whenever either changes): the
consequential facts, each true from a chapter on, and the blocks of the writer's guide (its paragraphs and list
items) that only become true in a later chapter. The guide sent with a scene of chapter N leaves those blocks out
(scoped_guide), so a scene never gets, as lasting background, what happens after it; the last check on the
request (context/guard.py) makes sure none of them is in it.

An adaptation (Story registry.md "Kind: adaptation", with its reference chosen on the planner screen): the
reference's events, in order, with roles and causes, made once from the reference itself (plan_source_events),
then mapped to the architecture's events (plan_source_map). Every required source event must be carried, in the
reference's order, unless you approved the change; a source event's causes become the prerequisites of the events
that carry it, so the checks of what was written hold the story to the reference's causal order.
"""

from __future__ import annotations

import hashlib
import re

from devils_workshop.context.assemble import ChatMessage
from devils_workshop.context.render import clean
from devils_workshop.planner.audit import ADVISORY, HARD, Finding
from devils_workshop.planner.registry import IMPORT, Fact, Registry, SourceEvent

CANON_JOB, SOURCE_EVENTS_JOB, SOURCE_MAP_JOB = "plan_canon", "plan_source_events", "plan_source_map"
MIN_BLOCK = 40  # characters: a shorter guide block (a heading, a label) is never left out


def _hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).lower().encode("utf-8")).hexdigest()[:16]


# --- the guide, block by block ---------------------------------------------------------------------------------

def guide_blocks(guide: str) -> list[str]:
    """The guide's blocks, in order: each heading, each list item (with the lines under it), each paragraph."""
    blocks: list[list[str]] = []
    for line in guide.strip().split("\n"):
        stripped = line.strip()
        if not stripped:
            blocks.append([])
            continue
        starts = stripped.startswith("#") or re.match(r"^(?:[-*•]|\d+[.)])\s", stripped)
        if starts or not blocks or not blocks[-1] or blocks[-1][0].lstrip().startswith("#"):
            blocks.append([line])
        else:
            blocks[-1].append(line)
    return ["\n".join(b).strip() for b in blocks if "\n".join(b).strip()]


def _sentences(block: str) -> list[str]:
    """A block's sentences, and the clauses of a sentence between semicolons (a later fact is often one of them)."""
    return [s for s in re.split(r"(?<=[.!?…;])\s+", block) if s.strip()]


def scoped_guide(guide: str, scope: dict[str, int], chapter: int) -> str:
    """The guide as it stands at this chapter: without what is only true from a later one (a whole block, or the
    sentences of a block the canon quoted: the rest of a character's entry is still sent)."""
    kept = []
    for block in guide_blocks(guide):
        if len(block) >= MIN_BLOCK and scope.get(_hash(block), 1) > chapter:
            continue
        parts = _sentences(block)
        later = [s for s in parts if scope.get(_hash(s), 1) > chapter]
        kept.append(" ".join(s for s in parts if s not in later) if later else block)
    return "\n\n".join(k for k in kept if k.strip())


def guide_for(root, chapter: int, text: str) -> str:
    """Writer guide.md as sent with a scene of this chapter (a story without the planner: as it is)."""
    from devils_workshop.planner.state import PlannerState, is_planner_story
    if not is_planner_story(root):
        return text
    scope = PlannerState.load(root).registry.guide_scope
    return scoped_guide(text, scope, chapter) if scope else text


def later_blocks(guide: str, scope: dict[str, int], chapter: int) -> list[str]:
    """What of the guide is only true from a later chapter: whole blocks, and quoted sentences."""
    found = []
    for block in guide_blocks(guide):
        if len(block) >= MIN_BLOCK and scope.get(_hash(block), 1) > chapter:
            found.append(block)
        else:
            found += [s for s in _sentences(block) if len(s) >= MIN_BLOCK and scope.get(_hash(s), 1) > chapter]
    return found


def canon_mark(state) -> str:
    events = "\n".join(f"{e.id} {e.chapter} {e.text}" for e in state.registry.events)
    return _hash(state.guide + "\n--\n" + events)


# --- the canon request ------------------------------------------------------------------------------------------

def canon_messages(state) -> list[ChatMessage]:
    from devils_workshop.planner.prompts import architecture_lines, decisions_block, job_text, plan_without_plot
    blocks = guide_blocks(state.guide)
    events = "\n".join(f"- [{e.id}] (chapter {e.chapter}) {e.text}" for e in state.registry.events)
    parts = [f"<story_plan>\n{plan_without_plot(state.plan)}\n</story_plan>", decisions_block(state, notes=False),
             f"<chapter_architecture>\n{architecture_lines(state)}\n</chapter_architecture>",
             f"<events>\n{events or '(none)'}\n</events>",
             "<guide_blocks>\n" + "\n\n".join(f'<block n="{n}">\n{b}\n</block>' for n, b in enumerate(blocks, 1))
             + "\n</guide_blocks>",
             "<task>\nList the canon, and the guide blocks that are only true from a later chapter.\n</task>"]
    return [ChatMessage("system", job_text(state.root, CANON_JOB)),
            ChatMessage("user", "\n\n".join(p for p in parts if p))]


def _attrs(text: str) -> dict:
    return {k.lower(): v.strip() for k, v in re.findall(r'(\w+)\s*=\s*"([^"]*)"', text)}


def _int(value, default: int = 1) -> int:
    try:
        return max(1, int(str(value).strip()))
    except (TypeError, ValueError):
        return default


def read_canon(reply: str, guide: str, keep: list[Fact] = ()) -> tuple[list[Fact], dict[str, int]]:
    """(the facts, guide block or sentence hash -> the chapter it is true from). Your own facts (authority
    "author") stay. A block given with the sentences that are only true later scopes just those sentences."""
    blocks = guide_blocks(guide)
    facts = [f for f in keep if f.authority == "author"]
    taken = {f.id.upper() for f in facts}
    number = 0
    for attrs, text in re.findall(r"<fact\b([^>]*)>(.*?)</fact>", reply, re.S | re.I):
        values = _attrs(attrs)
        number += 1
        fact_id = (values.get("id") or f"F{number}").upper()
        if fact_id in taken or not text.strip():
            continue
        taken.add(fact_id)
        facts.append(Fact(fact_id, " ".join(text.split()), values.get("kind", "fact").lower(),
                          values.get("subject", ""), _int(values.get("from")), source="Writer guide and plan",
                          disclosed_by=values.get("disclosed_by", ""),
                          until_chapter=_int(values.get("until"), 0) if values.get("until") else 0))
    scope, quoted_in = {}, set()
    tags = re.findall(r"<block\b([^>]*?)(?:/>|>(.*?)</block>)", reply, re.I | re.S)
    for whole in (False, True):  # the quoted parts first: a block with any part quoted is never left out whole
        for attrs, quoted in tags:
            values = _attrs(attrs)
            index, start = _int(values.get("n"), 0), _int(values.get("from"))
            if not 1 <= index <= len(blocks) or start <= 1:
                continue
            block = blocks[index - 1]
            wanted = [" ".join(q.split()).lower().rstrip(".;") for q in re.split(r"\n+", quoted or "") if q.strip()]
            named = [s for s in _sentences(block) if any(w and w in " ".join(s.split()).lower() for w in wanted)]
            if named and not whole:
                quoted_in.add(index)
                for unit in named:
                    scope[_hash(unit)] = start
            elif not named and whole and index not in quoted_in:
                scope[_hash(block)] = start
    return facts, scope


# --- an adaptation ---------------------------------------------------------------------------------------------

def source_events_messages(state, name: str) -> list[ChatMessage]:
    from devils_workshop.planner.inputs import input_text
    from devils_workshop.planner.prompts import job_text, your_plan
    if state.registry.kind == IMPORT:  # the author's plan made elsewhere, weighed by the story plan it became
        return [ChatMessage("system", job_text(state.root, SOURCE_EVENTS_JOB)),
                ChatMessage("user", f"{your_plan(state)}\n\n<story_plan>\n{state.plan.strip()}\n</story_plan>\n\n"
                                    "<task>\nList the events of the author's plan, in order.\n</task>")]
    text, problem = input_text(state.root, name)
    body = clean(text) if text.strip() else f"(couldn't be read: {problem or 'no text'})"
    return [ChatMessage("system", job_text(state.root, SOURCE_EVENTS_JOB)),
            ChatMessage("user", f'<file name="{name}">\n{body}\n</file>\n\n<task>\nList the reference\'s events, in '
                                "order.\n</task>")]


def read_source_events(reply: str) -> list[SourceEvent]:
    events = []
    for order, (attrs, text) in enumerate(re.findall(r"<source_event\b([^>]*)>(.*?)</source_event>", reply,
                                                     re.S | re.I), 1):
        values = _attrs(attrs)
        if not text.strip():
            continue
        causes = [c.strip().upper() for c in values.get("causes", "").split(",") if c.strip()]
        weight = values.get("weight", "").lower()
        events.append(SourceEvent((values.get("id") or f"S{order}").upper(), order, " ".join(text.split()),
                                  values.get("roles", ""), causes, values.get("required", "yes").lower() != "no",
                                  weight if weight in ("major", "minor", "passing") else ""))
    return events


def map_mark(state) -> str:
    sources = "\n".join(f"{s.id} {s.text}" for s in state.registry.sources)
    events = "\n".join(f"{e.id} {e.text}" for e in state.registry.events)
    return _hash(sources + "\n--\n" + events + "\n--\n" + "\n".join(state.registry.changes) + "\n--\n"
                 + "\n".join(state.decisions))


def map_messages(state) -> list[ChatMessage]:
    from devils_workshop.planner.prompts import decisions_block, job_text
    registry = state.registry
    sources = "\n".join(f"- [{s.id}] {s.text}" + (f" (roles: {s.roles})" if s.roles else "")
                        for s in registry.sources)
    events = "\n".join(f"- [{e.id}] (chapter {e.chapter}) {e.text}" for e in registry.events)
    parts = [f"<source_events>\n{sources}\n</source_events>", f"<events>\n{events}\n</events>"]
    decided = decisions_block(state, notes=False)  # what the author settled: a change decided is no departure
    if decided:
        parts.insert(0, decided)
    if registry.changes:
        parts.append("<approved_changes>\n" + "\n".join(f"- {c}" for c in registry.changes) + "\n</approved_changes>")
    parts.append("<task>\nSay which event carries each source event.\n</task>")
    return [ChatMessage("system", job_text(state.root, SOURCE_MAP_JOB)), ChatMessage("user", "\n\n".join(parts))]


def read_map(reply: str, registry: Registry) -> None:
    """Which events carry each source event; a source event's causes become those events' prerequisites."""
    for source in registry.sources:
        source.events = []
    for event in registry.events:
        event.sources = []
    for attrs in re.findall(r"<map\b([^>]*?)/?>", reply, re.I):
        values = _attrs(attrs)
        source = registry.source(values.get("source", ""))
        if source is None:
            continue
        ids = [i.strip() for i in values.get("event", "").split(",") if i.strip() and i.strip().lower() != "none"]
        source.events = [i for i in ids if registry.event(i)]
        if not source.events and values.get("replaced", "").lower() in ("yes", "true") and not source.left_out:
            source.left_out = "the plan's decisions replace it"  # shown in Story registry.md, with your approvals
        for event_id in source.events:
            registry.event(event_id).sources.append(source.id)
    for event in registry.events:
        needs = []
        for source_id in event.sources:
            for cause in registry.source(source_id).causes:
                cause_source = registry.source(cause)
                needs += [e for e in (cause_source.events if cause_source else []) if e != event.id
                          and registry.event(e).chapter <= event.chapter]  # a later chapter's event is no cause yet
        event.prerequisites = sorted(set(needs), key=needs.index)


def mapping_findings(registry: Registry) -> list[Finding]:
    """How the architecture adapts the reference. Hard: a required event not carried (and not approved as left
    out). Worth a look: one carried out of the reference's order (unless an event is approved early), which an
    adaptation may do on purpose."""
    if not registry.follows_reference() or not registry.sources:
        return []
    found = []
    for source in registry.sources:
        if source.required and not source.events and not source.left_out:
            found.append(Finding(HARD, "missing", f"the reference's {source.id} (\"{source.text}\") isn't carried by "
                                                  "any chapter: plan it in, or approve leaving it out in Story "
                                                  "registry.md", ref=source.id))
    latest, latest_id = 0, ""
    for source in sorted(registry.sources, key=lambda s: s.order):
        chapters = [registry.event(e).chapter for e in source.events if registry.event(e)]
        approved = any(registry.event(e).early_from for e in source.events if registry.event(e))
        if not chapters:
            continue
        first = min(chapters)
        if first < latest and not approved:
            found.append(Finding(ADVISORY, "order", f"the reference's {source.id} comes after {latest_id} but is "
                                                    f"planned for chapter {first}, before chapter {latest} (approve it "
                                                    "as a deliberate reordering in Story registry.md to stop this "
                                                    "note)", first, ref=source.id))
        if first >= latest:
            latest, latest_id = first, source.id
    return found
