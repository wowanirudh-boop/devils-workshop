"""Files and pictures you give the planner, kept in planner/inputs in the story's folder.

Any text file, PDF or Word file is read as text (own_files.extract_text). Pictures (.png, .jpg,
.webp, .gif) are sent as pictures to models that can see them. Other binary files are refused.
"""

from __future__ import annotations

from pathlib import Path

from devils_workshop.context.assemble import ImageRef
from devils_workshop.project.file_ops import safe_name
from devils_workshop.project.own_files import binary_problem, data_looks_binary, extract_text
from devils_workshop.planner.state import INPUTS_DIR

PICTURES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
            ".gif": "image/gif"}
MAX_PICTURE_BYTES = 5 * 1024 * 1024


class InputError(Exception):
    """Why a file can't be given to the planner, in plain words."""


def is_picture(name: str) -> bool:
    return Path(name).suffix.lower() in PICTURES


def inputs_dir(root: Path) -> Path:
    return Path(root) / INPUTS_DIR


def add_input(root: Path, name: str, data: bytes) -> str:
    """Keep a copy of a file for the planner. Returns the name it is kept under (a file with the same name
    gets " (2)" and so on, so nothing you gave earlier is replaced)."""
    clean = safe_name(name)
    if not clean:
        raise InputError(f"{name}: give the file a name")
    if is_picture(clean):
        if len(data) > MAX_PICTURE_BYTES:
            raise InputError(f"{clean} is {len(data) / 1024 / 1024:.1f} MB; pictures can be at most 5 MB "
                             "(save a smaller copy)")
    elif data_looks_binary(Path(clean).suffix, data[:4096]):
        raise InputError(f"{clean}: {binary_problem(clean)}")
    folder = inputs_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    target, number = folder / clean, 2
    while target.exists():
        target = folder / f"{Path(clean).stem} ({number}){Path(clean).suffix}"
        number += 1
    target.write_bytes(data)
    return target.name


def input_text(root: Path, name: str) -> tuple[str, str]:
    """(text, problem) of a text, PDF or Word input."""
    path = inputs_dir(root) / name
    if not path.is_file():
        return "", "This file isn't in planner/inputs any more"
    return extract_text(path)


def picture(root: Path, name: str) -> ImageRef | None:
    path = inputs_dir(root) / name
    if not path.is_file() or not is_picture(name):
        return None
    return ImageRef(name, PICTURES[path.suffix.lower()], path.read_bytes())


def list_inputs(root: Path) -> list[str]:
    folder = inputs_dir(root)
    return sorted((p.name for p in folder.iterdir() if p.is_file()), key=str.lower) if folder.is_dir() else []
