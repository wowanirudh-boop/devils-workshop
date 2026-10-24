"""Subplot files: a status per chapter plus notes per chapter.

    ---
    name: The forged ledger
    status:
      2: introduced
      4: active
    ---
    ## Summary
    ...
    ## ch4
    Note sent only while writing chapter 4.

A status carries forward until the next one. A subplot is included in a
chapter's request while its status there is introduced, active or climax.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from devils_workshop.project.entries import name_from_filename
from devils_workshop.project.issues import IssueList
from devils_workshop.project.markdown import (
    Section,
    blank_out_comments,
    split_front_matter,
    split_sections,
)

STATUSES = ("dormant", "introduced", "active", "climax", "resolved")
INCLUDED_STATUSES = ("introduced", "active", "climax")
KNOWN_KEYS = ("name", "aliases", "status")

_CHAPTER_NOTE_RE = re.compile(r"^@?\s*ch(?:apter)?\s*(\d+)\s*$", re.IGNORECASE)


@dataclass
class Subplot:
    path: str
    name: str
    aliases: list[str]
    status: dict[int, str]          # chapter -> status, as written
    sections: list[Section]         # Summary and any other non-chapter sections
    chapter_notes: dict[int, str]   # chapter -> note text
    note_lines: dict[int, int]      # chapter -> line number (for messages)

    def status_at(self, chapter: int) -> str | None:
        """The status in effect at a chapter: the latest one at or before it."""
        current = None
        for number in sorted(self.status):
            if number <= chapter:
                current = self.status[number]
        return current

    def included_at(self, chapter: int) -> bool:
        return self.status_at(chapter) in INCLUDED_STATUSES


def parse_subplot(text: str, path: str, issues: IssueList) -> Subplot:
    front = split_front_matter(text, path, issues)
    data = front.data

    for key in data:
        if key not in KNOWN_KEYS:
            issues.warning(path, 2, f"Unknown setting '{key}' is ignored")

    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        name = name_from_filename(stem)
        issues.warning(path, 1, f"No 'name:' set; using '{name}' from the file name")

    aliases = data.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    if not isinstance(aliases, list):
        issues.error(path, 2, "'aliases:' must be a list, e.g. aliases: [SP1]")
        aliases = []
    aliases = [str(a).strip() for a in aliases if str(a).strip()]

    status = _parse_status(data.get("status"), path, issues)

    sections: list[Section] = []
    notes: dict[int, str] = {}
    note_lines: dict[int, int] = {}
    for section in split_sections(blank_out_comments(front.body), front.body_line):
        match = _CHAPTER_NOTE_RE.match(section.title or "")
        if match:
            chapter = int(match.group(1))
            if chapter in notes:
                issues.warning(path, section.line, f"Two notes for chapter {chapter}; they are joined")
                notes[chapter] += "\n\n" + section.text
            else:
                notes[chapter] = section.text
                note_lines[chapter] = section.line
        elif section.title is not None or section.text:
            sections.append(section)

    return Subplot(path, name.strip(), aliases, status, sections, notes, note_lines)


def _parse_status(value, path: str, issues: IssueList) -> dict[int, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        issues.error(path, 2, "'status:' must be chapter numbers with a status each",
                     "e.g.\nstatus:\n  2: introduced\n  4: active")
        return {}
    result: dict[int, str] = {}
    for key, raw in value.items():
        chapter = _chapter_number(key)
        if chapter is None:
            issues.error(path, 2, f"Status key '{key}' isn't a chapter number")
            continue
        word = str(raw).strip().lower()
        if word not in STATUSES:
            issues.error(path, 2, f"Chapter {chapter}: '{raw}' isn't one of: {', '.join(STATUSES)}")
            continue
        result[chapter] = word
    return result


def _chapter_number(key) -> int | None:
    if isinstance(key, bool):
        return None
    if isinstance(key, int):
        return key if key >= 1 else None
    match = _CHAPTER_NOTE_RE.match(str(key))
    return int(match.group(1)) if match else None
