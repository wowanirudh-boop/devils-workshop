"""Two of your files that are the same document (for example a handoff saved both as .md and as .docx).

If both are sent, the model gets everything twice: a chapter outline sent twice gives every chapter
its instructions twice, and in scene mode every scene twice (scenes 1–5 again as 6–10). Files are
compared by their words, so the same text in different formats (Markdown, Word, PDF) counts as a copy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SAME = 0.8          # share of distinct words in common (Jaccard) for "the same document"
LENGTH_RATIO = 0.7  # and the shorter one has at least this share of the longer one's words
MIN_WORDS = 50      # a near-empty file says nothing
_WORD = re.compile(r"[^\W\d_]{3,}")
# Which copy to keep: a text file (headings and *emphasis* exactly as written) before Word, Word before PDF.
_FORMAT_ORDER = {".md": 0, ".markdown": 0, ".txt": 1, ".docx": 3, ".pdf": 4}


@dataclass
class Duplicate:
    keep: str          # the file that stays
    copy: str          # the file that repeats it
    similarity: float  # 0..1


def similarity(a: str, b: str) -> float:
    """How alike two texts are, by the distinct words they use (0: nothing in common, 1: the same)."""
    words_a, words_b = _WORD.findall(a.lower()), _WORD.findall(b.lower())
    if min(len(words_a), len(words_b)) < MIN_WORDS:
        return 0.0
    if min(len(words_a), len(words_b)) / max(len(words_a), len(words_b)) < LENGTH_RATIO:
        return 0.0
    set_a, set_b = set(words_a), set(words_b)
    return len(set_a & set_b) / len(set_a | set_b)


def _rank(rel: str, order: int) -> tuple:
    return (_FORMAT_ORDER.get(Path(rel).suffix.lower(), 2), order)


def find_duplicates(texts: dict[str, str]) -> list[Duplicate]:
    """Pairs of files that are the same document. Each copy is reported once, against the file to keep
    (a .md over a .docx over a .pdf; otherwise the one listed first)."""
    names = list(texts)
    found: dict[str, Duplicate] = {}
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            if first in found or second in found:
                continue
            score = similarity(texts[first], texts[second])
            if score >= SAME:
                keep, copy = sorted((first, second), key=lambda rel: _rank(rel, names.index(rel)))
                found[copy] = Duplicate(keep, copy, score)
    return list(found.values())
