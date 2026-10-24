"""The "no future leaks" rule (PLAN.md §4.1).

Every progression, secret and subplot status is keyed to a Position. Whether
it may be sent depends on where we are writing:

- Scene mode, writing scene M of chapter N: the writing point is (N, M).
  Anything keyed at or before it is ACTIVE; anything after it is FUTURE.
- Whole-chapter mode, writing chapter N: the writing point is (N, 0).
  Items keyed inside chapter N at a scene, (N, k) with k >= 1, are
  IN_THIS_CHAPTER: the model needs them to write the whole chapter, so
  they are sent with a label "from scene k". Anything in a later chapter
  is FUTURE.
"""

from __future__ import annotations

from dataclasses import dataclass

from devils_workshop.project.positions import Position

ACTIVE = "active"
IN_THIS_CHAPTER = "in_this_chapter"
FUTURE = "future"


@dataclass(frozen=True)
class WritingPoint:
    chapter: int
    scene: int | None = None  # None = whole-chapter mode

    @property
    def whole_chapter(self) -> bool:
        return self.scene is None

    @property
    def position(self) -> Position:
        return Position(self.chapter, self.scene or 0)

    def label(self) -> str:
        return f"chapter {self.chapter}" if self.whole_chapter else f"chapter {self.chapter}, scene {self.scene}"


def visibility(position: Position, point: WritingPoint) -> str:
    if position <= point.position:
        return ACTIVE
    if point.whole_chapter and position.chapter == point.chapter:
        return IN_THIS_CHAPTER
    return FUTURE
