"""Entries: one file per character, place or piece of lore.

An entry file has a settings block, profile sections, and optionally
progressions and secrets written as specially named headings:

    ## @ch3 add                 append to "Developments so far" from chapter 3
    ## @ch5.2 replace Appearance replace the Appearance section from ch5 scene 2
    ## @ch7 set                 body lines are "field: value" updates
    ## secret @ch8              never sent before chapter 8

See PLAN.md §2.1 for the full rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from devils_workshop.project.issues import IssueList
from devils_workshop.project.markdown import (
    Section,
    blank_out_comments,
    split_front_matter,
    split_sections,
)
from devils_workshop.project.positions import Position, parse_position

# Folder name -> entry type
ENTRY_FOLDERS = {"characters": "character", "places": "place", "lore": "lore"}
ENTRY_TYPES = tuple(ENTRY_FOLDERS.values())
CONTEXT_MODES = ("always", "when_mentioned", "never")
PROGRESSION_VERBS = ("add", "replace", "set")
KNOWN_KEYS = ("name", "type", "aliases", "case_sensitive_aliases", "context", "fields")

PROGRESSION_SYNTAX = "## @ch5, ## @ch5 add Appearance, ## @ch5.2 replace Appearance, or ## @ch5 set"

_PROGRESSION_RE = re.compile(
    r"^@\s*(?P<pos>ch(?:apter)?\s*\d+(?:\s*\.\s*\d+)?)(?:\s+(?P<verb>\S+))?(?:\s+(?P<section>.+?))?\s*$",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(r"^secret\s+(?P<pos>@.*)$", re.IGNORECASE)
# Headings that look like a progression or secret but miss the '@'
_MISSING_AT_RE = re.compile(r"^(?:secrets?\s+)?ch(?:apter)?\s*\d+(?:\.\d+)?(?:\s+(?:add|replace|set)\b.*)?$",
                            re.IGNORECASE)


@dataclass
class Progression:
    position: Position
    verb: str                 # "add" | "replace" | "set"
    section: str | None       # target section for add/replace (None = "Developments so far")
    text: str                 # the text to add or the replacement text
    fields: dict = field(default_factory=dict)  # for "set"
    line: int = 0


@dataclass
class Secret:
    position: Position
    text: str
    line: int = 0


@dataclass
class Entry:
    path: str                 # project-relative, e.g. "characters/mara-venn.md"
    name: str
    type: str                 # "character" | "place" | "lore"
    aliases: list[str]
    case_sensitive_aliases: list[str]
    context: str              # "always" | "when_mentioned" | "never"
    fields: dict
    sections: list[Section]   # profile sections in file order (title None = untitled text)
    progressions: list[Progression]
    secrets: list[Secret]

    def all_names(self) -> list[str]:
        """The name plus every alias, in the order written."""
        return [self.name, *self.aliases, *self.case_sensitive_aliases]

    def section(self, title: str) -> Section | None:
        wanted = title.strip().lower()
        for section in self.sections:
            if section.title is not None and section.title.lower() == wanted:
                return section
        return None


def name_from_filename(stem: str) -> str:
    """'mara-venn' -> 'Mara Venn'."""
    return " ".join(part.capitalize() for part in re.split(r"[-_\s]+", stem) if part)


def parse_entry(text: str, path: str, folder: str, issues: IssueList) -> Entry:
    """Parse one entry file. Problems are added to `issues`; a usable Entry is always returned."""
    front = split_front_matter(text, path, issues)
    data = front.data

    for key in data:
        if key not in KNOWN_KEYS:
            issues.warning(path, 2, f"Unknown setting '{key}' is ignored",
                           "Put your own values under 'fields:', e.g.\nfields:\n  age: 34")

    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        name = name_from_filename(stem)
        issues.warning(path, 1, f"No 'name:' set; using '{name}' from the file name")
    name = name.strip()

    folder_type = ENTRY_FOLDERS[folder]
    entry_type = data.get("type", folder_type)
    if entry_type not in ENTRY_TYPES:
        issues.error(path, 2, f"'type: {entry_type}' isn't one of: {', '.join(ENTRY_TYPES)}")
        entry_type = folder_type
    elif entry_type != folder_type:
        issues.warning(path, 2, f"This file is in '{folder}/' but says 'type: {entry_type}'",
                       f"Move it to the matching folder or change the type to '{folder_type}'.")

    context = data.get("context", "when_mentioned")
    if context not in CONTEXT_MODES:
        issues.error(path, 2, f"'context: {context}' isn't one of: {', '.join(CONTEXT_MODES)}")
        context = "when_mentioned"

    aliases = _string_list(data.get("aliases"), "aliases", path, issues)
    case_sensitive = _string_list(data.get("case_sensitive_aliases"), "case_sensitive_aliases", path, issues)

    fields = data.get("fields") or {}
    if not isinstance(fields, dict):
        issues.error(path, 2, "'fields:' must be a list of 'name: value' lines")
        fields = {}
    fields = {str(key): value for key, value in fields.items()}

    sections, progressions, secrets = _parse_body(front.body, front.body_line, path, issues)
    entry = Entry(path, name, entry_type, aliases, case_sensitive, context, fields,
                  sections, progressions, secrets)
    _check_progression_targets(entry, issues)
    return entry


def _string_list(value, key: str, path: str, issues: IssueList) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, (str, int, float)) for item in value):
        issues.error(path, 2, f"'{key}:' must be a list, e.g. {key}: [Mara, the clockmaker]")
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _parse_body(body: str, start_line: int, path: str, issues: IssueList):
    sections: list[Section] = []
    progressions: list[Progression] = []
    secrets: list[Secret] = []

    for section in split_sections(blank_out_comments(body), start_line):
        if section.title is None:
            text = _drop_leading_title(section.text)
            if text:
                sections.append(Section(None, text, section.line))
            continue

        title = section.title
        if title.startswith("@"):
            progression = _parse_progression(section, path, issues)
            if progression:
                progressions.append(progression)
            continue

        secret_match = _SECRET_RE.match(title)
        if secret_match:
            try:
                position = parse_position(secret_match.group("pos"))
            except ValueError as exc:
                issues.error(path, section.line, f"Secret heading: {exc}", "Write it like: ## secret @ch8")
                continue
            if not section.text:
                issues.warning(path, section.line, f"The secret at {position} has no text")
            secrets.append(Secret(position, section.text, section.line))
            continue

        if _MISSING_AT_RE.match(title):
            issues.warning(path, section.line,
                           f"'## {title}' looks like a progression or secret but is missing '@'; "
                           "it is treated as a normal section",
                           f"Progressions: {PROGRESSION_SYNTAX}. Secrets: ## secret @ch8")
        if not title:
            issues.warning(path, section.line, "Empty '##' heading; its text is kept as untitled")
            title = None
        elif any(s.title and s.title.lower() == title.lower() for s in sections):
            issues.warning(path, section.line, f"Section '{title}' appears twice; both are kept")
        sections.append(Section(title, section.text, section.line))

    return sections, progressions, secrets


def _drop_leading_title(text: str) -> str:
    """Remove a '# Name' line at the very top, which many people write out of habit."""
    lines = text.split("\n")
    if lines and re.match(r"^#\s+\S", lines[0]):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _parse_progression(section: Section, path: str, issues: IssueList) -> Progression | None:
    match = _PROGRESSION_RE.match(section.title or "")
    if not match:
        issues.error(path, section.line, f"Can't read the progression heading '## {section.title}'",
                     f"Write it like: {PROGRESSION_SYNTAX}")
        return None
    try:
        position = parse_position(match.group("pos"))
    except ValueError as exc:
        issues.error(path, section.line, f"Progression heading: {exc}")
        return None

    verb = (match.group("verb") or "add").lower()
    target = (match.group("section") or "").strip() or None
    if verb not in PROGRESSION_VERBS:
        issues.error(path, section.line, f"Unknown progression action '{match.group('verb')}'",
                     "Use add, replace or set.")
        return None

    if verb == "set":
        if target:
            issues.error(path, section.line, "'set' doesn't take a section name",
                         "Put 'field: value' lines under '## @chN set'.")
            return None
        fields = _parse_set_fields(section, path, issues)
        if fields is None:
            return None
        return Progression(position, "set", None, "", fields, section.line)

    if verb == "replace" and not target:
        issues.error(path, section.line, "'replace' needs the name of the section it replaces",
                     "e.g. ## @ch5 replace Appearance")
        return None
    if not section.text:
        issues.warning(path, section.line, f"The progression at {position} has no text")
    return Progression(position, verb, target, section.text, {}, section.line)


def _parse_set_fields(section: Section, path: str, issues: IssueList) -> dict | None:
    try:
        data = yaml.safe_load(section.text) if section.text.strip() else None
    except yaml.YAMLError:
        data = "invalid"
    if not isinstance(data, dict) or not data:
        issues.error(path, section.line, "Lines under '## @chN set' must look like 'field: value'",
                     "e.g.\n## @ch7 set\nstatus: missing")
        return None
    return {str(key): value for key, value in data.items()}


def _check_progression_targets(entry: Entry, issues: IssueList) -> None:
    for progression in entry.progressions:
        if progression.verb == "replace" and entry.section(progression.section or "") is None:
            issues.warning(entry.path, progression.line,
                           f"'replace {progression.section}': there is no section with that name",
                           "From that point it will be added as a new section. "
                           "Check the spelling if you meant an existing one.")
