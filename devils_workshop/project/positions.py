"""Positions in the story: a chapter, optionally a scene within it.

Written in files as "ch5" (start of chapter 5) or "ch5.2" (from scene 2 of
chapter 5). Positions compare in reading order:
    ch4.3 < ch5 < ch5.2 < ch6
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_POSITION_RE = re.compile(r"^@?\s*ch(?:apter)?\s*(\d+)(?:\s*\.\s*(\d+))?\s*$", re.IGNORECASE)


@dataclass(frozen=True, order=True)
class Position:
    chapter: int
    scene: int = 0  # 0 means "from the start of the chapter"

    def __str__(self) -> str:
        return f"ch{self.chapter}.{self.scene}" if self.scene else f"ch{self.chapter}"

    def label(self) -> str:
        if self.scene:
            return f"chapter {self.chapter}, scene {self.scene}"
        return f"chapter {self.chapter}"


def parse_position(text: str) -> Position:
    """Parse "ch5", "@ch5.2" or "chapter 5". Raises ValueError with a readable message."""
    match = _POSITION_RE.match(text.strip())
    if not match:
        raise ValueError(f"'{text.strip()}' is not a position; write it like ch5 or ch5.2")
    chapter = int(match.group(1))
    scene = int(match.group(2)) if match.group(2) is not None else 0
    if chapter < 1:
        raise ValueError(f"'{text.strip()}': chapters are numbered from 1")
    if match.group(2) is not None and scene < 1:
        raise ValueError(f"'{text.strip()}': scenes are numbered from 1")
    return Position(chapter, scene)
