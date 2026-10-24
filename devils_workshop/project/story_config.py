"""Story settings.md (story.yaml before v0.17): the settings for one story (see story_settings.py)."""

from __future__ import annotations

from dataclasses import dataclass

from devils_workshop.project.issues import IssueList
from devils_workshop.project.outline import MODES, PERSONS, TENSES

IN_CHAPTER_CONTEXT = ("full", "beats_only", "independent")  # independent: each scene sees only its own brief
TOP_KEYS = ("title", "language", "defaults", "models", "files", "default_instructions", "genre", "characters",
            "status", "completed_on", "version", "first_version")
STATUSES = ("in progress", "completed")
DEFAULT_KEYS = ("pov", "person", "tense", "chapter_words", "mode", "in_chapter_context")
MODEL_KEYS = ("primary", "fallback")


@dataclass
class StoryConfig:
    title: str = "Untitled story"
    language: str = "English"
    pov: str | None = None
    person: str = "third"
    tense: str = "past"
    chapter_words: int = 3000
    mode: str = "whole"
    in_chapter_context: str = "full"
    primary_model: str | None = None   # None = use the app-wide default
    fallback_model: str | None = None
    explicit: frozenset = frozenset()  # which of pov/person/tense the settings set
    own_files: dict[str, str] | None = None  # file -> what it is for (see own_files.py); None = app layout
    default_instructions: bool = True  # use your default instructions (default_instructions.py)
    genre: str = ""                    # for you and the library
    characters: tuple = ()             # the key characters' names, for the library
    status: str = "in progress"        # in progress | completed (you mark it)
    completed_on: str = ""             # the date you marked it completed (YYYY-MM-DD)


def parse_story_config(text: str, path: str, issues: IssueList) -> StoryConfig:
    from devils_workshop.project.story_settings import parse_settings

    config = StoryConfig()
    try:
        data = parse_settings(text)
    except ValueError as exc:
        issues.error(path, None, f"{path} {exc}", "Check indentation and that each line looks like 'key: value'.")
        return config
    for label in data.pop("_unknown", []):
        issues.warning(path, None, f"Unknown setting '{label}' is ignored")

    _warn_unknown(data, TOP_KEYS, "", path, issues)

    title = data.get("title")
    if isinstance(title, str) and title.strip():
        config.title = title.strip()
    else:
        issues.warning(path, None, "No 'title:' set")
    if isinstance(data.get("language"), str):
        config.language = data["language"].strip()
    if isinstance(data.get("genre"), str):
        config.genre = data["genre"].strip()
    names = data.get("characters")
    if isinstance(names, str):
        names = [n for n in names.split(",")]
    if isinstance(names, list):
        config.characters = tuple(str(n).strip() for n in names if str(n).strip())
    status = str(data.get("status") or "in progress").strip().lower()
    config.status = status if status in STATUSES else "in progress"
    config.completed_on = str(data.get("completed_on") or "").strip()

    defaults = _section(data, "defaults", path, issues)
    _warn_unknown(defaults, DEFAULT_KEYS, "defaults.", path, issues)
    if "pov" in defaults:
        if isinstance(defaults["pov"], str) and defaults["pov"].strip():
            config.pov = defaults["pov"].strip()
        else:
            issues.error(path, None, "'defaults.pov' must be a character name")
    config.person = _choice(defaults, "person", PERSONS, config.person, path, issues)
    config.tense = _choice(defaults, "tense", TENSES, config.tense, path, issues)
    config.mode = _choice(defaults, "mode", MODES, config.mode, path, issues)
    config.in_chapter_context = _choice(defaults, "in_chapter_context", IN_CHAPTER_CONTEXT,
                                        config.in_chapter_context, path, issues)
    config.explicit = frozenset(k for k in ("pov", "person", "tense") if k in defaults)
    if "chapter_words" in defaults:
        words = defaults["chapter_words"]
        if isinstance(words, int) and not isinstance(words, bool) and words > 0:
            config.chapter_words = words
        else:
            issues.error(path, None, f"'defaults.chapter_words: {words}' must be a whole number, e.g. 3000")

    models = _section(data, "models", path, issues)
    _warn_unknown(models, MODEL_KEYS, "models.", path, issues)
    for key in MODEL_KEYS:
        value = models.get(key)
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            setattr(config, f"{key}_model", value.strip())
        else:
            issues.error(path, None, f"'models.{key}' must be a model name, e.g. z-ai/glm-5.3")

    if "files" in data:
        config.own_files = _own_files(data["files"], path, issues)
    if "default_instructions" in data:
        value = data["default_instructions"]
        if isinstance(value, bool):
            config.default_instructions = value
        elif str(value).strip().lower() in ("on", "off", "yes", "no"):
            config.default_instructions = str(value).strip().lower() in ("on", "yes")
        else:
            issues.error(path, None, f"'default_instructions: {value}' must be on or off")
    return config


def _own_files(value, path: str, issues: IssueList) -> dict[str, str]:
    """files: {"name.md": "every chapter", ...}: a story kept in your own files."""
    from devils_workshop.project.own_files import ROLE_HINT, parse_role

    if value is None:
        return {}
    if not isinstance(value, dict):
        issues.error(path, None, "'files:' must list each file as '\"name.md\": what it is for'", ROLE_HINT)
        return {}
    roles = {}
    for name, raw in value.items():
        role = parse_role(raw)
        rel = str(name).replace("\\", "/").strip()
        while rel.startswith("./"):
            rel = rel[2:]
        if role is None:
            issues.error(path, None, f"'{name}: {raw}' isn't a known use for a file, so it isn't sent", ROLE_HINT)
            continue
        roles[rel] = role
    return roles


def _section(data: dict, key: str, path: str, issues: IssueList) -> dict:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        issues.error(path, None, f"'{key}:' must contain indented 'key: value' settings")
        return {}
    return value


def _choice(data: dict, key: str, options: tuple, default: str, path: str, issues: IssueList) -> str:
    if key not in data:
        return default
    value = str(data[key]).strip().lower()
    if value not in options:
        issues.error(path, None, f"'{key}: {data[key]}' isn't one of: {', '.join(options)}")
        return default
    return value


def _warn_unknown(data: dict, known: tuple, prefix: str, path: str, issues: IssueList) -> None:
    for key in data:
        if key not in known:
            issues.warning(path, None, f"Unknown setting '{prefix}{key}' is ignored",
                           f"Known settings here: {', '.join(known)}")
