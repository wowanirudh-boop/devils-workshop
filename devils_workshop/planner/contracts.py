"""Checking the planner's lists before they become the plan: the chapter architecture and each chapter's scenes.

A list is the plan only once it passes. Every chapter keeps the number it was given, so a chapter's title is never
paired with another chapter's text by its position in the reply. A list that fails is asked for once more, with
what was wrong; if it still fails, the step fails and says why, and nothing from it is saved (planner/run.py).

Hard problems (the list can't be used): a chapter without a number, two chapters with the same number, numbers
that aren't 1 to N, a chapter count other than the plan's Length, a chapter or scene with nothing in it, a scene
count other than the one asked for, two scenes that are the same, a scene that does what a later chapter is
planned to do. Notes (shown, not blocking): scene lengths that don't add up to the chapter's.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

SAME_SCENE = 0.9     # two scene lines this alike are one scene planned twice
LATER_CHAPTER = 0.75  # a scene line this close to a later chapter's beat (and far closer than to any of its own
                      # chapter's beats) is out of scope
WORDS_OFF = 0.25     # scene lengths more than 25% off the chapter's length: a note


@dataclass
class ListCheck:
    problems: list[str] = field(default_factory=list)  # the list can't be the plan
    notes: list[str] = field(default_factory=list)     # worth knowing; the list is used

    @property
    def ok(self) -> bool:
        return not self.problems


def number_of(value) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def expected_chapters(plan: str) -> int | None:
    """The number of chapters the plan's Length section asks for ("12 chapters"), if it gives one number."""
    section = re.search(r"(?ims)^##\s*length\b[^\n]*\n(.*?)(?=^##\s|\Z)", plan or "")
    if not section:
        return None
    found = {int(n) for n in re.findall(r"\b(\d{1,3})\s+chapters?\b", section.group(1), re.I)}
    return found.pop() if len(found) == 1 else None


def order_chapters(items: list[dict]) -> list[dict]:
    """The chapters in the order of their own numbers (as given in the reply when none has a number)."""
    numbers = [number_of(item.get("number")) for item in items]
    if all(n is not None for n in numbers):
        return [item for _, item in sorted(zip(numbers, items), key=lambda pair: pair[0])]
    return list(items)


def check_chapters(items: list[dict], expected: int | None = None) -> ListCheck:
    result = ListCheck()
    if not items:
        result.problems.append("there are no chapters")
        return result
    numbers = [number_of(item.get("number")) for item in items]
    given = [n for n in numbers if n is not None]
    if given and len(given) < len(numbers):
        missing = [str(i) for i, n in enumerate(numbers, 1) if n is None]
        result.problems.append(f"chapter block{'s' if len(missing) > 1 else ''} {', '.join(missing)} "
                               "(counting from the top) ha" + ("ve" if len(missing) > 1 else "s") + " no number")
    twice = sorted({n for n in given if given.count(n) > 1})
    if twice:
        result.problems.append("more than one chapter is numbered " + ", ".join(map(str, twice)))
    if given and not twice and len(given) == len(numbers) and sorted(given) != list(range(1, len(given) + 1)):
        result.problems.append(f"the chapters are numbered {', '.join(map(str, sorted(given)))}, not 1 to "
                               f"{len(given)}")
    if expected and len(items) != expected:
        result.problems.append(f"the plan's Length asks for {expected} chapters, and the reply has {len(items)}")
    for position, item in enumerate(items, 1):
        number = number_of(item.get("number")) or position
        if not any(str(item.get(key) or "").strip() for key in ("purpose", "summary", "ending")) \
                and not item.get("beats"):
            result.problems.append(f"chapter {number} has no purpose, summary, beats or ending")
    bodies = {}
    for position, item in enumerate(items, 1):
        body = _plain(" ".join(str(item.get(k) or "") for k in ("purpose", "summary", "ending")))
        if body and body in bodies:
            result.problems.append(f"chapters {bodies[body]} and {number_of(item.get('number')) or position} "
                                   "have the same purpose, summary and ending")
        bodies.setdefault(body, number_of(item.get("number")) or position)
    return result


def check_scenes(items: list[dict], expected: int, chapter_words: int = 0,
                 later_beats: list[tuple[int, str]] = (), own_beats: list[str] = ()) -> ListCheck:
    """One chapter's scene list. later_beats: (chapter, beat) of the chapters after it, for the scope check;
    own_beats: this chapter's own beats."""
    result = ListCheck()
    if expected and len(items) != expected:
        result.problems.append(f"the chapter needs exactly {expected} scenes, and the reply has {len(items)}")
    lines = [_plain(str(item.get("purpose") or "")) for item in items]
    for number, line in enumerate(lines, 1):
        if not line:
            result.problems.append(f"scene {number} says nothing about what happens in it")
    for a in range(len(lines)):
        for b in range(a + 1, len(lines)):
            if lines[a] and lines[b] and difflib.SequenceMatcher(None, lines[a], lines[b]).ratio() >= SAME_SCENE:
                result.problems.append(f"scenes {a + 1} and {b + 1} describe the same thing")
    for number, line in enumerate(lines, 1):
        if not line:
            continue
        own = max((_alike(line, beat) for beat in own_beats), default=0.0)
        for chapter, beat in later_beats:
            alike = _alike(line, beat)
            if alike >= LATER_CHAPTER and own < alike - 0.25:
                result.problems.append(f"scene {number} does what chapter {chapter} is planned to do (\"{beat}\")")
                break
    words = [number_of(item.get("words")) or 0 for item in items]
    if chapter_words and all(words) and abs(sum(words) - chapter_words) > chapter_words * WORDS_OFF:
        result.notes.append(f"the scenes add up to {sum(words):,} words; the chapter is planned at "
                            f"{chapter_words:,}")
    return result


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


_SMALL = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with", "her", "his", "their", "is",
          "it", "as", "by", "from", "that", "this", "into", "she", "he", "they", "be", "was", "are", "has", "have"}


def _alike(a: str, b: str) -> float:
    """How alike two lines are in their content words (0 to 1)."""
    wa = {w for w in _plain(a).split() if w not in _SMALL and len(w) > 2}
    wb = {w for w in _plain(b).split() if w not in _SMALL and len(w) > 2}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb)) if min(len(wa), len(wb)) >= 3 else 0.0
