"""Creating a new story project folder from a built-in template."""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from devils_workshop.project.files import read_text, write_text
from devils_workshop.project.story_settings import SETTINGS_FILE, parse_settings, settings_markdown


class ProjectExistsError(Exception):
    """The destination folder already has files in it."""


def create_project(dest: Path, sample: bool = False, title: str | None = None) -> Path:
    """Create a project at `dest`: the blank template, or the sample story.

    Never overwrites anything: the folder must be new or empty.
    """
    dest = Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise ProjectExistsError(f"{dest} already exists and isn't empty; choose a new folder")

    defaults = resources.files("devils_workshop") / "defaults"
    _copy_tree(defaults / ("sample_project" if sample else "blank_project"), dest)
    if not sample:
        _copy_file(defaults / "system_prompt.md", dest / "system_prompt.md")
    if title:
        story = dest / SETTINGS_FILE
        data = parse_settings(read_text(story)[0])
        data["title"] = title
        write_text(story, settings_markdown(data))
    return dest


def _copy_tree(source: Traversable, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name in ("__pycache__",) or item.name.endswith(".pyc"):
            continue
        if item.is_dir():
            _copy_tree(item, dest / item.name)
        else:
            _copy_file(item, dest / item.name)


def _copy_file(source: Traversable, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(source.read_bytes())
