"""Story settings.md: one story's settings, as a Markdown file you can read and edit.

    # Story settings
    ## Story
    - Title: The Saltmarsh Clock
    - Genre: Literary mystery
    - Key characters: Mara Venn, Tobias Reed
    - Status: in progress
    ## Writing
    - Point of view: Mara Venn
    - Person: third
    - Tense: past
    - Words per chapter: 3000
    - Written as: whole chapters
    - What a scene sees: earlier scenes' text
    ## Models
    - Main model: z-ai/glm-5.3
    ## Files
    - World and characters.md: every chapter

Each setting is a "- Name: value" line under its heading; anything else is a note and is ignored, and a
value of "(not set)" or nothing leaves the setting out. The writer uses it on every request (which files
to send, point of view and tense, lengths, whole chapters or scenes, what a scene sees, the story's own
writer models); the library shows the story details from it.

Stories made before v0.17 have story.yaml instead. It is read the same way, and turned into Story
settings.md the first time the story is opened (the old file is kept in History).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from devils_workshop.project.files import read_text, write_text

SETTINGS_FILE = "Story settings.md"
LEGACY_FILE = "story.yaml"
NOT_SET = "(not set)"

# (section, label, key in the settings data, "a.b" for nested keys)
FIELDS = [
    ("Story", "Title", "title"),
    ("Story", "Language", "language"),
    ("Story", "Genre", "genre"),
    ("Story", "Key characters", "characters"),
    ("Story", "Status", "status"),
    ("Story", "Completed on", "completed_on"),
    ("Story", "Default instructions", "default_instructions"),
    ("Story", "Version", "version"),
    ("Story", "First version", "first_version"),
    ("Writing", "Point of view", "defaults.pov"),
    ("Writing", "Person", "defaults.person"),
    ("Writing", "Tense", "defaults.tense"),
    ("Writing", "Words per chapter", "defaults.chapter_words"),
    ("Writing", "Written as", "defaults.mode"),
    ("Writing", "What a scene sees", "defaults.in_chapter_context"),
    ("Models", "Main model", "models.primary"),
    ("Models", "Fallback model", "models.fallback"),
]
NOTES = {
    "Story": "What the story is, for you and the library. Status: in progress or completed.",
    "Writing": "How the writer writes this story. Written as: whole chapters, or scene by scene. What a scene "
               "sees (scene by scene): earlier scenes' text, earlier scenes' beats, or only its own brief.",
    "Models": "This story's own writer models; leave them out to use the writer's models in Settings.",
    "Files": "Your own files, and what each one is for: every chapter, chapter outline, chapter 3 (any "
             "number), system prompt, or not sent. They are sent in this order.",
}
WORDS = {  # the words the file uses -> the values the app keeps
    "defaults.mode": {"whole chapters": "whole", "whole chapter": "whole", "whole": "whole",
                      "scene by scene": "scenes", "scenes": "scenes"},
    "defaults.in_chapter_context": {"earlier scenes' text": "full", "full": "full",
                                    "earlier scenes' beats": "beats_only", "beats only": "beats_only",
                                    "beats_only": "beats_only", "only its own brief": "independent",
                                    "independent": "independent"},
    "default_instructions": {"on": True, "yes": True, "true": True, "off": False, "no": False, "false": False},
}
SHOWN = {key: {value: words for words, value in reversed(list(options.items()))} for key, options in WORDS.items()}
SHOWN["default_instructions"] = {True: "on", False: "off"}
_LABELS = {label.lower(): key for _, label, key in FIELDS}


def settings_path(root: Path) -> Path:
    """The story's settings file: Story settings.md, or story.yaml for a story not opened since v0.17."""
    root = Path(root)
    return root / SETTINGS_FILE if (root / SETTINGS_FILE).is_file() or not (root / LEGACY_FILE).is_file() \
        else root / LEGACY_FILE


def is_story_folder(folder: Path) -> bool:
    return (Path(folder) / SETTINGS_FILE).is_file() or (Path(folder) / LEGACY_FILE).is_file()


def is_markdown(text: str) -> bool:
    """Story settings.md has "## Section" headings; story.yaml has only "# notes" and "key: value" lines."""
    return bool(re.search(r"^##\s+\S", text, re.M) or re.search(r"^#\s+Story settings\b", text, re.M))


def parse_settings(text: str) -> dict:
    """The settings as data ({"title": ..., "defaults": {...}, "models": {...}, "files": {...}}), from Story
    settings.md or (older stories) story.yaml. Raises ValueError for a story.yaml that isn't valid YAML."""
    if not text.strip():
        return {}
    if not is_markdown(text):
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            raise ValueError(f"isn't valid YAML{f' (line {mark.line + 1})' if mark else ''}") from exc
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError("must be a list of settings")
        return data
    data: dict = {}
    section = ""
    for line in re.sub(r"<!--.*?-->", "", text, flags=re.S).split("\n"):
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            section = heading.group(1).strip().lower()
            if section == "files":
                data.setdefault("files", {})
            continue
        item = re.match(r"^\s*[-*]\s+(.+)$", line)
        if not item:
            continue
        if section == "files":
            name, sep, role = item.group(1).rpartition(":")
            if sep and name.strip():
                data["files"][name.strip().strip('"')] = role.strip()
            continue
        label, sep, value = item.group(1).partition(":")
        key = _LABELS.get(label.strip().lower())
        value = value.strip()
        if not sep or key is None or not value or value == NOT_SET:
            if sep and key is None and label.strip():
                data.setdefault("_unknown", []).append(label.strip())
            continue
        _set(data, key, _value(key, value))
    return data


def _value(key: str, text: str):
    if key in WORDS:
        return WORDS[key].get(text.lower(), text)
    if key == "characters":
        return [name.strip() for name in text.split(",") if name.strip()]
    if key in ("defaults.chapter_words", "version"):
        digits = text.replace(",", "").strip()
        return int(digits) if digits.isdigit() else text
    return text


def _set(data: dict, key: str, value) -> None:
    if "." in key:
        outer, inner = key.split(".", 1)
        data.setdefault(outer, {})[inner] = value
    else:
        data[key] = value


def _get(data: dict, key: str):
    if "." in key:
        outer, inner = key.split(".", 1)
        return (data.get(outer) or {}).get(inner) if isinstance(data.get(outer), dict) else None
    return data.get(key)


def settings_markdown(data: dict) -> str:
    """Story settings.md for these settings."""
    lines = ["# Story settings", "",
             "The writer uses these on every request for this story. Edit them here, or on Story files → "
             "Settings. Each setting is a '- Name: value' line; '(not set)' leaves it out."]
    current = None
    for section, label, key in FIELDS:
        if section != current:
            lines += ["", f"## {section}", f"<!-- {NOTES[section]} -->"]
            current = section
        value = _get(data, key)
        if value is None:
            value = {"status": "in progress", "default_instructions": True}.get(key)
        if value is None or value == "" or value == []:
            if key in ("completed_on", "models.primary", "models.fallback", "language", "version", "first_version"):
                continue  # only when set
            shown = NOT_SET
        elif key in SHOWN:
            shown = SHOWN[key].get(value, value)
        elif isinstance(value, list):
            shown = ", ".join(str(v) for v in value)
        else:
            shown = str(value)
        lines.append(f"- {label}: {shown}")
    files = data.get("files")
    if isinstance(files, dict):
        lines += ["", "## Files", f"<!-- {NOTES['Files']} -->"]
        lines += [f"- {name}: {role}" for name, role in files.items()] or ["- (none yet)"]
    return "\n".join(lines) + "\n"


def write_settings(root: Path, data: dict, action: str = "story-settings") -> None:
    """Save the settings as Story settings.md (one Undo in History)."""
    from devils_workshop.history import write_project_file
    write_project_file(Path(root), SETTINGS_FILE, settings_markdown(data), action)


def update_settings(root: Path, action: str = "story-settings", **changes) -> dict:
    """Change some settings ("defaults.pov"-style keys use __, e.g. defaults__pov="Mara"), keep the rest."""
    data = read_settings(root)
    for name, value in changes.items():
        _set(data, name.replace("__", "."), value)
    write_settings(root, data, action)
    return data


def read_settings(root: Path) -> dict:
    path = settings_path(root)
    try:
        return parse_settings(read_text(path)[0]) if path.is_file() else {}
    except ValueError:
        return {}


def migrate(root: Path) -> bool:
    """story.yaml (a story made before v0.17) becomes Story settings.md, once; the old file is kept in History.
    A story.yaml newer than Story settings.md (copied in, or edited by hand) wins. True if it was turned."""
    from devils_workshop.history import snapshot
    root = Path(root)
    legacy, current = root / LEGACY_FILE, root / SETTINGS_FILE
    if not legacy.is_file() or (current.is_file() and current.stat().st_mtime >= legacy.stat().st_mtime):
        return False
    try:
        data = parse_settings(read_text(legacy)[0])
    except ValueError:
        return False  # left as it is: Problems says what is wrong with it
    snapshot(root, [LEGACY_FILE] + ([SETTINGS_FILE] if current.is_file() else []), "settings-to-markdown")
    write_text(current, settings_markdown(data))
    legacy.unlink()
    return True
