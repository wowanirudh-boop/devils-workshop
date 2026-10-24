"""Chapter files in chapters/: front matter plus prose.

In scene mode, scenes are separated by invisible markers:
    <!-- scene 2 -->
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from devils_workshop.project.issues import IssueList
from devils_workshop.project.markdown import split_front_matter
from devils_workshop.wordcount import count_words

CHAPTER_FILE_RE = re.compile(r"^ch(\d+)\.md$", re.IGNORECASE)
STATUSES = ("draft", "final")
_SCENE_MARKER_RE = re.compile(r"^<!--\s*scene\s*:?\s*(\d+)\s*-->\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass
class ChapterText:
    path: str
    number: int
    title: str
    version: int
    status: str
    text: str                                         # the prose, markers included
    scenes: dict[int, str] = field(default_factory=dict)  # empty if there are no markers

    @property
    def words(self) -> int:
        return count_words(self.text)


def parse_chapter_file(text: str, path: str, number: int, issues: IssueList) -> ChapterText:
    front = split_front_matter(text, path, issues)
    data = front.data

    declared = data.get("chapter")
    if declared is not None and declared != number:
        issues.warning(path, 2, f"The file name says chapter {number} but 'chapter: {declared}' is set",
                       "The file name wins.")

    status = str(data.get("status", "draft")).lower()
    if status not in STATUSES:
        issues.warning(path, 2, f"'status: {data.get('status')}' isn't one of: {', '.join(STATUSES)}")
        status = "draft"

    version = data.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        issues.warning(path, 2, f"'version: {version}' should be a whole number")
        version = 1

    body = front.body.strip("\n")
    return ChapterText(path, number, str(data.get("title", "")).strip(), version, status, body,
                       _split_scenes(body))


def render_chapter_file(number: int, title: str, version: int, status: str, text: str = "",
                        scenes: dict[int, str] | None = None) -> str:
    """The text of a chapter file. With `scenes`, they are written with scene markers."""
    words = count_words(text) if not scenes else sum(count_words(t) for t in scenes.values())
    head = ["---", f"chapter: {number}"]
    if title:
        head.append(f"title: {json.dumps(title, ensure_ascii=False)}")
    head += [f"version: {version}", f"words: {words}", f"status: {status}", "---"]
    if scenes:
        body = "\n".join(f"<!-- scene {n} -->\n{scenes[n].strip()}" for n in sorted(scenes))
    else:
        body = text.strip()
    return "\n".join(head) + "\n" + body + "\n"


def chapter_file_name(number: int) -> str:
    return f"chapters/ch{number:02d}.md"


def version_file_name(number: int, version: int) -> str:
    return f"chapters/versions/ch{number:02d}.v{version}.md"


def _split_scenes(body: str) -> dict[int, str]:
    markers = list(_SCENE_MARKER_RE.finditer(body))
    scenes: dict[int, str] = {}
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(body)
        scenes[int(marker.group(1))] = body[marker.end():end].strip()
    return scenes
