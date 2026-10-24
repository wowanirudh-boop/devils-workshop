"""Counting words in prose."""

from __future__ import annotations

import re

from devils_workshop.project.markdown import strip_comments

# A word is a run of letters/digits, allowing inner apostrophes and hyphens:
# "don't", "harbour-master" and "3:12" (as two words) are counted sensibly.
_WORD_RE = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*")


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(strip_comments(text)))
