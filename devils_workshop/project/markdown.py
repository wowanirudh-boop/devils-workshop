"""Small Markdown helpers shared by the parsers.

Only what the story files need: YAML front matter, splitting a body into
"## " sections, and HTML comments (<!-- ... -->), which are author notes and
are never sent to a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from devils_workshop.project.issues import IssueList

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass
class FrontMatter:
    data: dict
    body: str
    body_line: int  # 1-based line number where the body starts in the file


def split_front_matter(text: str, file: str, issues: IssueList) -> FrontMatter:
    """Separate a leading '---' YAML block from the Markdown body."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return FrontMatter({}, text, 1)

    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            yaml_text = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1:])
            body_line = index + 2
            break
    else:
        issues.error(file, 1, "The '---' block at the top is never closed",
                     "Add a line with just --- after the settings.")
        return FrontMatter({}, text, 1)

    try:
        data = yaml.safe_load(yaml_text) if yaml_text.strip() else {}
    except yaml.YAMLError as exc:
        line = None
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            line = mark.line + 2  # +1 for the opening '---', +1 for 1-based numbering
        issues.error(file, line, f"The settings block isn't valid YAML: {_yaml_problem(exc)}",
                     "Check indentation and that each line looks like 'key: value'.")
        return FrontMatter({}, body, body_line)

    if data is None:
        data = {}
    if not isinstance(data, dict):
        issues.error(file, 2, "The settings block must be a list of 'key: value' lines")
        data = {}
    return FrontMatter(data, body, body_line)


def _yaml_problem(exc: yaml.YAMLError) -> str:
    problem = getattr(exc, "problem", None)
    return problem if problem else str(exc).split("\n")[0]


@dataclass
class Section:
    title: str | None  # None for text before the first heading
    text: str
    line: int          # line number of the heading (or of the first text line)


def iter_lines(body: str, start_line: int):
    """Yield (line_number, line, inside_code_fence) for each line of the body."""
    in_fence = False
    for offset, line in enumerate(body.split("\n")):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            yield start_line + offset, line, True
            continue
        yield start_line + offset, line, in_fence


def heading(line: str, level: int) -> str | None:
    """Return the heading text if the line is a heading of exactly this level."""
    match = re.match(r"^(#{1,6})\s+(.*?)(?:\s+#+)?\s*$", line)
    if not match or len(match.group(1)) != level:
        return None
    return match.group(2).strip()


def split_sections(body: str, start_line: int) -> list[Section]:
    """Split a body at '## ' headings. Deeper headings stay inside their section."""
    sections: list[Section] = []
    title: str | None = None
    title_line = start_line
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip("\n")
        if title is not None or text.strip():
            sections.append(Section(title, text.strip(), title_line))

    for number, line, in_fence in iter_lines(body, start_line):
        found = None if in_fence else heading(line, 2)
        if found is not None:
            flush()
            title, title_line, buffer = found, number, []
        else:
            if title is None and not buffer and line.strip():
                title_line = number
            buffer.append(line)
    flush()
    return sections


def strip_comments(text: str) -> str:
    """Remove <!-- ... --> author notes."""
    return _COMMENT_RE.sub("", text)


def blank_out_comments(text: str) -> str:
    """Replace <!-- ... --> notes with empty lines, so headings inside a note are
    ignored but line numbers in messages still match the file."""
    return _COMMENT_RE.sub(lambda match: "\n" * match.group().count("\n"), text)


def is_blank(text: str) -> bool:
    """True if the text has nothing left once author notes are removed."""
    return not strip_comments(text).strip()
