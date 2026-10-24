"""The outline: chapter instructions and beats, optionally split into scenes.

    # Chapter 2: Ink and Salt
    mode: scenes
    Chapter-level instructions...
    ## Scene 1: The archive
    characters: [Ada Quill]
    - Ada shows Mara the ledger room.

"key: value" lines straight after a heading are settings. Only known keys
count as settings, so a beat such as "Note: keep it quiet" stays a beat.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

import yaml

from devils_workshop.project.issues import IssueList
from devils_workshop.project.markdown import blank_out_comments, heading, iter_lines

CHAPTER_KEYS = ("words", "pov", "person", "tense", "mode", "characters")
SCENE_KEYS = ("words", "pov", "person", "tense", "characters", "lookahead")
PERSONS = ("first", "second", "third")
TENSES = ("past", "present")
MODES = ("whole", "scenes")
# A planned scene whose brief the planner writes just before its chapter is written (planner/output.py).
PENDING_BRIEF = "(Brief not written yet: the planner writes it just before this chapter is written.)"

_CHAPTER_RE = re.compile(r"^chapter\s+(\d+)\s*(?:[:.\-–—]\s*(.*))?$", re.IGNORECASE)
_SCENE_RE = re.compile(r"^scene\s+(\d+)\s*(?:[:.\-–—]\s*(.*))?$", re.IGNORECASE)
_SETTING_RE = re.compile(r"^([A-Za-z_]+)\s*:\s*(.*)$")


@dataclass
class Scene:
    number: int
    title: str
    settings: dict
    text: str      # this scene's instructions and beats
    line: int


@dataclass
class OutlineChapter:
    number: int
    title: str
    settings: dict
    text: str      # chapter-level instructions and beats (before any scene)
    line: int
    scenes: list[Scene] = field(default_factory=list)
    source: str = ""   # the file it came from, when the story uses your own files

    def scene(self, number: int) -> Scene | None:
        return next((s for s in self.scenes if s.number == number), None)


@dataclass
class Outline:
    chapters: list[OutlineChapter]
    preamble: str  # text before Chapter 1 (not used for writing)

    def chapter(self, number: int) -> OutlineChapter | None:
        return next((c for c in self.chapters if c.number == number), None)

    @property
    def last_chapter(self) -> int:
        return max((c.number for c in self.chapters), default=0)


def parse_outline(text: str, path: str, issues: IssueList) -> Outline:
    chapters: list[OutlineChapter] = []
    preamble: list[str] = []
    current_chapter: OutlineChapter | None = None
    current_scene: Scene | None = None
    buffer: list[str] = []
    reading_settings = False  # True right after a heading, until the first non-setting line
    orphan_reported = False   # one warning per block of text outside any chapter

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if current_scene is not None:
            current_scene.text = text
        elif current_chapter is not None:
            current_chapter.text = text

    for number, line, in_fence in iter_lines(blank_out_comments(text), 1):
        h1 = None if in_fence else heading(line, 1)
        h2 = None if in_fence else heading(line, 2)

        if h1 is not None:
            flush()
            buffer = []
            current_scene = None
            orphan_reported = False
            match = _CHAPTER_RE.match(h1)
            if not match:
                issues.error(path, number, f"'# {h1}' isn't a chapter heading",
                             "Chapter headings look like: # Chapter 3: Title")
                current_chapter = None
                reading_settings = False
                continue
            current_chapter = OutlineChapter(int(match.group(1)), (match.group(2) or "").strip(), {}, "", number)
            _check_new_chapter(current_chapter, chapters, path, issues)
            chapters.append(current_chapter)
            reading_settings = True
            continue

        if h2 is not None and current_chapter is not None:
            match = _SCENE_RE.match(h2)
            if match:
                flush()
                buffer = []
                current_scene = Scene(int(match.group(1)), (match.group(2) or "").strip(), {}, "", number)
                _check_new_scene(current_scene, current_chapter, path, issues)
                current_chapter.scenes.append(current_scene)
                reading_settings = True
                continue
            if h2.lower().startswith("scene"):
                issues.error(path, number, f"'## {h2}' isn't a scene heading",
                             "Scene headings look like: ## Scene 2: Title")
            else:
                issues.info(path, number, f"'## {h2}' isn't a scene heading, so it is kept as part of the text",
                            "Scene headings look like: ## Scene 2: Title")

        if current_chapter is None:
            if h2 is not None and _SCENE_RE.match(h2):
                issues.error(path, number, f"'## {h2}' comes before any chapter heading",
                             "Put scenes under a chapter: # Chapter 1: Title")
            elif line.strip() and not chapters:
                preamble.append(line)
            elif line.strip() and not orphan_reported:
                issues.warning(path, number, "This text isn't under any chapter and is ignored")
                orphan_reported = True
            continue

        if reading_settings and not in_fence:
            if not line.strip() and not buffer:
                continue
            if _read_setting(line, number, current_chapter, current_scene, path, issues):
                continue
            reading_settings = False
        buffer.append(line)

    flush()
    return Outline(chapters, "\n".join(preamble).strip())


def _check_new_chapter(chapter: OutlineChapter, chapters: list[OutlineChapter], path: str,
                       issues: IssueList) -> None:
    if any(c.number == chapter.number for c in chapters):
        issues.error(path, chapter.line, f"Chapter {chapter.number} appears twice")
    elif chapters and chapter.number < chapters[-1].number:
        issues.warning(path, chapter.line,
                       f"Chapter {chapter.number} comes after chapter {chapters[-1].number}")


def _check_new_scene(scene: Scene, chapter: OutlineChapter, path: str, issues: IssueList) -> None:
    if any(s.number == scene.number for s in chapter.scenes):
        issues.error(path, scene.line, f"Chapter {chapter.number} has two scenes numbered {scene.number}")
        return
    expected = len(chapter.scenes) + 1
    if scene.number != expected:
        issues.warning(path, scene.line,
                       f"Chapter {chapter.number}: expected scene {expected} here, found scene {scene.number}",
                       "Scenes are written in order 1, 2, 3...")


def _read_setting(line: str, number: int, chapter: OutlineChapter, scene: Scene | None,
                  path: str, issues: IssueList) -> bool:
    """Handle a line in the settings block. Return True if it was used up as a setting.

    A misspelt or misplaced setting is reported and skipped (True), so the
    settings after it are still read. Any other line (False) ends the block.
    """
    match = _SETTING_RE.match(line.strip())
    if not match:
        return False
    key, raw = match.group(1).lower(), match.group(2).strip()
    allowed = SCENE_KEYS if scene is not None else CHAPTER_KEYS
    if key not in allowed:
        if key in CHAPTER_KEYS + SCENE_KEYS:
            where = "scenes" if scene is None else "chapters"
            issues.warning(path, number, f"'{key}:' only works for {where}, so this line is ignored")
            return True
        close = difflib.get_close_matches(key, allowed, n=1, cutoff=0.75)
        if close:
            issues.warning(path, number, f"'{key}:' isn't a setting, so this line is ignored",
                           f"Did you mean '{close[0]}:'?")
            return True
        return False

    value = _setting_value(key, raw, number, path, issues)
    if value is None:
        return True  # invalid value: reported, line consumed so it doesn't become a beat
    target = scene.settings if scene is not None else chapter.settings
    target[key] = value
    return True


def _setting_value(key: str, raw: str, number: int, path: str, issues: IssueList):
    if key == "words":
        cleaned = raw.replace(",", "").replace("_", "")
        if not cleaned.isdigit() or int(cleaned) < 1:
            issues.error(path, number, f"'words: {raw}' must be a whole number, e.g. words: 3000")
            return None
        return int(cleaned)
    if key == "pov":
        if not raw:
            issues.error(path, number, "'pov:' needs a character name")
            return None
        return raw
    if key in ("person", "tense", "mode"):
        options = {"person": PERSONS, "tense": TENSES, "mode": MODES}[key]
        if raw.lower() not in options:
            issues.error(path, number, f"'{key}: {raw}' isn't one of: {', '.join(options)}")
            return None
        return raw.lower()
    if key == "characters":
        names = _name_list(raw)
        if names is None:
            issues.error(path, number, "'characters:' must be a list, e.g. characters: [Ada Quill, Tobias Reed]")
        return names
    if key == "lookahead":
        if raw.lower() in ("true", "yes", "on"):
            return True
        if raw.lower() in ("false", "no", "off"):
            return False
        issues.error(path, number, f"'lookahead: {raw}' must be true or false")
        return None
    return raw


def _name_list(raw: str) -> list[str] | None:
    if raw.startswith("["):
        try:
            value = yaml.safe_load(raw)
        except yaml.YAMLError:
            return None
        if not isinstance(value, list):
            return None
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in raw.split(",") if part.strip()]
