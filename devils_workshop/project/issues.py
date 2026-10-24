"""Problems found while reading project files.

Parsers never stop at the first problem. They record every problem they find
as an Issue, so plan-check can show you the full list at once.
"""

from __future__ import annotations

from dataclasses import dataclass

ERROR = "error"      # the file can't be used as written
WARNING = "warning"  # it can be used, but probably not the way you meant
INFO = "info"        # worth knowing; nothing is wrong

SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass(frozen=True)
class Issue:
    severity: str
    file: str            # path relative to the project folder, e.g. "characters/mara-venn.md"
    line: int | None     # 1-based line number, when known
    message: str
    hint: str = ""

    def location(self) -> str:
        if not self.file:
            return "(project)"
        return f"{self.file}:{self.line}" if self.line else self.file


class IssueList(list):
    """A list of Issues with shortcuts for adding them."""

    def error(self, file: str, line: int | None, message: str, hint: str = "") -> None:
        self.append(Issue(ERROR, file, line, message, hint))

    def warning(self, file: str, line: int | None, message: str, hint: str = "") -> None:
        self.append(Issue(WARNING, file, line, message, hint))

    def info(self, file: str, line: int | None, message: str, hint: str = "") -> None:
        self.append(Issue(INFO, file, line, message, hint))

    def of(self, severity: str) -> list[Issue]:
        return [issue for issue in self if issue.severity == severity]


def sort_issues(issues: list[Issue]) -> list[Issue]:
    """Most serious first, then by file and line."""
    return sorted(
        issues,
        key=lambda i: (SEVERITY_ORDER[i.severity], i.file, i.line or 0, i.message),
    )
