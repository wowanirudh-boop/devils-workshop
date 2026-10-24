"""Reading and writing text files.

Story files may come from Windows editors, so reading accepts UTF-8 (with or
without a byte-order mark) and falls back to Windows-1252. Line endings are
normalised to "\\n" so the parsers only deal with one kind.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def read_text(path: Path) -> tuple[str, str]:
    """Return (text, encoding_used)."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
        encoding = "cp1252"
    return text.replace("\r\n", "\n").replace("\r", "\n"), encoding


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 text atomically: a crash never leaves a half-written file.

    The text goes to a temporary file in the same folder first, which then
    replaces the target in one step.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def relative_name(path: Path, root: Path) -> str:
    """Project-relative path with forward slashes, for messages."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
