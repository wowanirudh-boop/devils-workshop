"""Default instructions: your own files, added once in the app and used by every story (D44).

They live in the app's folder, next to settings.yaml, in "Default instructions":
    Windows:  %APPDATA%\\DevilsWorkshop\\Default instructions\\
Each file is one of:
- part of the system prompt: sent first, before a story's own system prompt (the writer's);
- sent with every chapter, as the author's instructions at the end of the writer's system message (the planner
  is given it as the writer's rules to plan within);
- for the planner: your planning preferences, sent with every planner request (after its own system prompt);
- for the writer and the planner: both of those (the default for a file you haven't chosen for);
- off: kept, but not sent.
What each file is for is kept in roles.yaml in the same folder. A story can switch
them all off ("Default instructions: off" in its Story settings). A file a story
already has (the same text) isn't sent twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from devils_workshop.project.files import read_text, write_text
from devils_workshop.project.own_files import EVERY, NOT_SENT, SYSTEM, extract_text, looks_binary
from devils_workshop.settings import settings_dir

FOLDER = "Default instructions"
ROLES_FILE = "roles.yaml"
PLANNER_NOTES = "planner"  # your instructions for the planner, for every story
BOTH = "writer and planner"  # sent with every chapter, and to the planner with every planner request
ROLES = (SYSTEM, BOTH, EVERY, PLANNER_NOTES, NOT_SENT)
WRITER_ROLES = (EVERY, BOTH)          # sent to the writer with every chapter
PLANNER_ROLES = (PLANNER_NOTES, BOTH)  # sent to the planner with every request
KEY_PREFIX = "default:"  # documents from here are keyed "default:<file name>"


@dataclass
class DefaultFile:
    name: str
    role: str
    text: str
    problem: str = ""


def folder() -> Path:
    return settings_dir() / FOLDER


def list_files(base: Path | None = None) -> list[str]:
    """The files in the folder the app can read (any text file, PDF or Word; not roles.yaml), by name."""
    base = base or folder()
    if not base.is_dir():
        return []
    return sorted((p.name for p in base.iterdir() if p.is_file() and p.name != ROLES_FILE
                   and not p.name.startswith((".", "~$")) and not looks_binary(p)), key=str.lower)


def guess_role(name: str) -> str:
    """A file you haven't chosen for: the system prompt if its name says so, otherwise both the writer and the
    planner (a goal, terminology or rules should shape the plan as well as the prose)."""
    return SYSTEM if "system" in name.lower() else BOTH


def load_roles(base: Path | None = None) -> dict[str, str]:
    path = (base or folder()) / ROLES_FILE
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(read_text(path)[0]) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(name): str(role) for name, role in data.items() if str(role) in ROLES}


def save_roles(roles: dict[str, str], base: Path | None = None) -> None:
    header = ("# What each of your default instructions is for: system prompt, writer and planner, every chapter,\n"
              "# planner, or not sent.\n"
              "# Change it on the Default instructions screen.\n")
    write_text((base or folder()) / ROLES_FILE, header + yaml.safe_dump(dict(roles), sort_keys=False,
                                                                         allow_unicode=True, width=1000))


def role_of(name: str, roles: dict[str, str]) -> str:
    return roles.get(name) or guess_role(name)


def make_text_copy(name: str, base: Path | None = None) -> str:
    """Edit a PDF or Word file as text: its text is saved as an editable .md file with the original's role; the
    original is kept and set to not sent. Returns the copy's name."""
    from devils_workshop.project.own_files import text_copy_name

    base = base or folder()
    text, problem = extract_text(base / name)
    if not text.strip():
        raise ValueError(problem or f"No text could be read from {name}")
    copy = text_copy_name(base, name)
    write_text(base / copy, text.strip() + "\n")
    roles = load_roles(base)
    roles[copy], roles[name] = role_of(name, roles), NOT_SENT
    save_roles({n: role_of(n, roles) for n in list_files(base)}, base)
    return copy


def load_defaults(base: Path | None = None) -> list[DefaultFile]:
    """Every file with its role and text (for an unreadable file, the problem instead)."""
    base = base or folder()
    roles = load_roles(base)
    result = []
    for name in list_files(base):
        text, problem = extract_text(base / name)
        result.append(DefaultFile(name, role_of(name, roles), text, problem))
    return result
