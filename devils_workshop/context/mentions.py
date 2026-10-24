"""Finding which entries are mentioned in a piece of text.

Matching is by whole words and not case-sensitive, except for an entry's
case_sensitive_aliases. Possessives match ("Mara's" counts for "Mara");
hyphenated words don't ("ledger-room" doesn't count for "ledger").

When names overlap, the longest match wins: in "the ledger room", the archive
(alias "the ledger room") is mentioned, not the ledger (alias "the ledger").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from devils_workshop.project.entries import Entry
from devils_workshop.project.markdown import strip_comments


@dataclass(frozen=True)
class _Pattern:
    regex: re.Pattern
    entry_path: str


def _compile(name: str, case_sensitive: bool) -> re.Pattern:
    # Words in a multi-word name may be split by any whitespace, including a line break.
    # A hyphen joins words, so "the ledger" doesn't match inside "the ledger-room".
    body = r"\s+".join(re.escape(part) for part in name.split())
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(rf"(?<![\w-]){body}(?![\w-])", flags)


class MentionFinder:
    def __init__(self, entries: list[Entry]):
        self._patterns: list[_Pattern] = []
        for entry in entries:
            for name in [entry.name, *entry.aliases]:
                if name.strip():
                    self._patterns.append(_Pattern(_compile(name, False), entry.path))
            for name in entry.case_sensitive_aliases:
                if name.strip():
                    self._patterns.append(_Pattern(_compile(name, True), entry.path))

    def count(self, texts: list[str]) -> dict[str, int]:
        """Return {entry path: number of mentions} across all the texts."""
        counts: dict[str, int] = {}
        for text in texts:
            for path in self._matches(strip_comments(text)):
                counts[path] = counts.get(path, 0) + 1
        return counts

    def _matches(self, text: str) -> list[str]:
        found = []  # (start, end, entry path)
        for pattern in self._patterns:
            for match in pattern.regex.finditer(text):
                found.append((match.start(), match.end(), pattern.entry_path))
        # Keep the longest match at each place; drop matches inside it.
        found.sort(key=lambda m: (m[0], -(m[1] - m[0])))
        chosen, taken_until = [], -1
        for start, end, path in found:
            if start >= taken_until:
                chosen.append(path)
                taken_until = end
        return chosen
