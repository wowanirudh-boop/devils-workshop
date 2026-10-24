"""Turning entries and subplots into the text the model sees (PLAN.md §4.2).

An entry is rendered "as of" the writing point: active progressions are
applied in story order, active secrets become ordinary facts, and nothing
from later in the story is included. In whole-chapter mode, changes that
happen during the chapter are listed separately with the scene they start in.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from devils_workshop.context.timeline import ACTIVE, IN_THIS_CHAPTER, WritingPoint, visibility
from devils_workshop.project.entries import Entry, Progression, Secret
from devils_workshop.project.markdown import strip_comments
from devils_workshop.project.positions import Position
from devils_workshop.project.subplots import Subplot


def clean(text: str) -> str:
    """Remove author notes and extra blank lines."""
    text = strip_comments(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def attr(value: str) -> str:
    return html.escape(value, quote=True)


@dataclass
class _Section:
    title: str | None
    text: str
    changed_at: Position | None = None


@dataclass
class EntryState:
    """An entry as it stands at the writing point."""
    fields: dict
    field_changes: dict           # field name -> Position it last changed
    sections: list[_Section]
    developments: list[tuple[Position, str]]
    in_this_chapter: list[str] = field(default_factory=list)  # whole-chapter mode only


def entry_state(entry: Entry, point: WritingPoint) -> EntryState:
    state = EntryState(dict(entry.fields), {}, [_Section(s.title, clean(s.text)) for s in entry.sections], [])

    items: list[tuple[Position, int, object]] = [(p.position, p.line, p) for p in entry.progressions]
    items += [(s.position, s.line, s) for s in entry.secrets]
    items.sort(key=lambda item: (item[0], item[1]))  # story order; file order breaks ties

    for position, _, item in items:
        seen = visibility(position, point)
        if seen == ACTIVE:
            _apply(state, item, position)
        elif seen == IN_THIS_CHAPTER:
            state.in_this_chapter.append(describe_change(item, f"From scene {position.scene}"))
    return state


def _apply(state: EntryState, item: Progression | Secret, position: Position) -> None:
    if isinstance(item, Secret):
        state.developments.append((position, clean(item.text)))
        return
    if item.verb == "set":
        for key, value in item.fields.items():
            state.fields[key] = value
            state.field_changes[key] = position
        return
    text = clean(item.text)
    if item.section is None:
        state.developments.append((position, text))
        return
    section = _find(state.sections, item.section)
    if section is None:
        state.sections.append(_Section(item.section, text, position))
    elif item.verb == "replace":
        section.text, section.changed_at = text, position
    else:
        section.text = f"{section.text}\n\n{text}".strip()
        section.changed_at = position


def _find(sections: list[_Section], title: str) -> _Section | None:
    wanted = title.strip().lower()
    return next((s for s in sections if s.title is not None and s.title.lower() == wanted), None)


def describe_change(item: Progression | Secret, when: str) -> str:
    """One line describing a change, e.g. 'From scene 2, Appearance becomes: ...'."""
    if isinstance(item, Secret):
        if when.startswith("From"):
            when = "Revealed in scene " + when.rsplit(" ", 1)[-1]
        return f"{when} (don't reveal or hint at it before then): {clean(item.text)}"
    if item.verb == "set":
        values = ", ".join(f"{k}: {_value(v)}" for k, v in item.fields.items())
        return f"{when}: {values}"
    if item.verb == "replace":
        return f"{when}, {item.section} becomes: {clean(item.text)}"
    if item.section:
        return f"{when}, add to {item.section}: {clean(item.text)}"
    return f"{when}: {clean(item.text)}"


def _value(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def render_entry(entry: Entry, point: WritingPoint, role: str = "") -> str:
    state = entry_state(entry, point)
    names = [*entry.aliases, *entry.case_sensitive_aliases]
    attrs = f'name="{attr(entry.name)}" type="{entry.type}"'
    if names:
        attrs += f' aliases="{attr(", ".join(names))}"'
    if role:
        attrs += f' role="{attr(role)}"'

    lines = [f"<entry {attrs}>"]
    if state.fields:
        parts = []
        for key, value in state.fields.items():
            since = state.field_changes.get(key)
            parts.append(f"{key}: {_value(value)}" + (f" (since {since})" if since else ""))
        lines.append(" | ".join(parts))
    for section in state.sections:
        if not section.text and section.title is None:
            continue
        if section.title is not None:
            suffix = f" (as of {section.changed_at})" if section.changed_at else ""
            lines.append(f"## {section.title}{suffix}")
        if section.text:
            lines.append(section.text)
    if state.developments:
        lines.append("## Developments so far")
        lines += [bullet(f"({position}) {text}") for position, text in state.developments]
    if state.in_this_chapter:
        lines.append("## Changes during this chapter")
        lines += [bullet(text) for text in state.in_this_chapter]
    lines.append("</entry>")
    return "\n".join(lines)


def scene_updates(entry: Entry, chapter: int, after_scene: int, up_to_scene: int) -> list[str]:
    """Scene mode: changes that start in scenes after_scene+1 .. up_to_scene of `chapter`."""
    items = [(p.position, p.line, p) for p in entry.progressions]
    items += [(s.position, s.line, s) for s in entry.secrets]
    items.sort(key=lambda item: (item[0], item[1]))
    updates = []
    for position, _, item in items:
        if position.chapter == chapter and after_scene < position.scene <= up_to_scene:
            when = "From this scene on" if position.scene == up_to_scene else f"From scene {position.scene}"
            if isinstance(item, Secret):
                updates.append(f"Revealed in this scene: {clean(item.text)}")
            else:
                updates.append(describe_change(item, when))
    return updates


def render_subplot(subplot: Subplot, chapter: int) -> str:
    status = subplot.status_at(chapter) or "not started"
    lines = [f'<subplot name="{attr(subplot.name)}" status="{status}">']
    for section in subplot.sections:
        text = clean(section.text)
        if section.title is not None:
            lines.append(f"## {section.title}")
        if text:
            lines.append(text)
    note = subplot.chapter_notes.get(chapter)
    if note and clean(note):
        lines.append("## In this chapter")
        lines.append(clean(note))
    lines.append("</subplot>")
    return "\n".join(lines)


def bullet(text: str) -> str:
    first, *rest = text.split("\n")
    return "\n".join([f"- {first}", *[f"  {line}" if line else "" for line in rest]])
