"""Plan-check: find problems in a story project before anything is sent.

No API calls. It reports everything the parsers found, plus checks that need
the whole project (unknown names, duplicate aliases, positions beyond the
outline, secrets that lookahead beats could reveal). It also builds the
chapter overview and the subplot x chapter matrix.
"""

from __future__ import annotations

from dataclasses import dataclass

from devils_workshop.project.issues import ERROR, INFO, WARNING, Issue, IssueList, sort_issues
from devils_workshop.project.markdown import is_blank
from devils_workshop.project.positions import Position
from devils_workshop.project.project import (
    BIBLE_FILES,
    OUTLINE_FILE,
    STORY_FILE,
    SYSTEM_PROMPT_FILE,
    Project,
)

WORDS_TOO_FEW = 200
WORDS_TOO_MANY = 15000
STATUS_LETTERS = {None: "-", "dormant": "-", "introduced": "I", "active": "A", "climax": "C", "resolved": "R"}


@dataclass
class ChapterRow:
    number: int
    title: str
    mode: str
    scenes: int
    target_words: int
    pov: str
    drafted_words: int | None  # None if there is no chapter file yet


@dataclass
class PlanCheckReport:
    title: str
    issues: list[Issue]
    chapters: list[ChapterRow]
    subplot_matrix: list[tuple[str, list[str]]]  # (subplot name, one letter per chapter)
    counts: dict[str, int]

    def of(self, severity: str) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == severity]

    @property
    def ok(self) -> bool:
        return not self.of(ERROR)


def run_plan_check(project: Project) -> PlanCheckReport:
    issues = IssueList(project.issues)
    if project.own_files:
        _check_own_files(project, issues)
    else:
        _check_core_files(project, issues)
        _check_names(project, issues)
        _check_positions(project, issues)
        _check_outline(project, issues)
    _check_chapter_files(project, issues)

    counts = {
        "chapters": len(project.outline.chapters),
        "characters": sum(1 for e in project.entries if e.type == "character"),
        "places": sum(1 for e in project.entries if e.type == "place"),
        "lore": sum(1 for e in project.entries if e.type == "lore"),
        "subplots": len(project.subplots),
    }
    return PlanCheckReport(project.config.title, sort_issues(issues), _chapter_rows(project),
                           _subplot_matrix(project), counts)


# --- checks -------------------------------------------------------------------

def _check_core_files(project: Project, issues: IssueList) -> None:
    if project.default_system and (project.system_prompt is None or is_blank(project.system_prompt)):
        pass  # your default instructions give the system prompt
    elif project.system_prompt is None:
        issues.warning(SYSTEM_PROMPT_FILE, None, "There is no system prompt for this story",
                       "Create system_prompt.md, or add one to your default instructions; it is sent with every "
                       "request.")
    elif is_blank(project.system_prompt):
        issues.warning(SYSTEM_PROMPT_FILE, None, "The system prompt is empty")

    for key, name in BIBLE_FILES.items():
        text = project.bible[key]
        if text is None:
            issues.warning(name, None, "File missing; nothing from it will be sent")
        elif is_blank(text):
            issues.info(name, None, "Empty (author notes in <!-- --> are never sent)")

    if not (project.root / OUTLINE_FILE).is_file():
        issues.warning(OUTLINE_FILE, None, "There is no outline yet")
    elif not project.outline.chapters:
        issues.warning(OUTLINE_FILE, None, "The outline has no chapters",
                       "Chapters start with a heading like: # Chapter 1: Title")


def _check_own_files(project: Project, issues: IssueList) -> None:
    if not project.outline.chapters:
        issues.warning(STORY_FILE, None, "No chapters found in your files",
                       "In 'Your files', mark a file as 'chapter outline' (it is split at its Chapter headings) "
                       "or as 'chapter 3' (one file per chapter).")
    for chapter in project.outline.chapters:
        if is_blank(chapter.text) and not chapter.scenes:
            issues.warning(chapter.source or STORY_FILE, chapter.line,
                           f"Chapter {chapter.number} has no instructions")
    if project.system_prompt is None and not project.default_system:
        issues.info(STORY_FILE, None, "No file is marked as the system prompt",
                    "Optional: mark one under Story files → What's sent, or add one to your default instructions.")


def _check_names(project: Project, issues: IssueList) -> None:
    # The same name or alias on two entries makes mention detection ambiguous.
    owners: dict[str, list[str]] = {}
    for entry in project.entries:
        for name in {n.lower() for n in entry.all_names()}:
            owners.setdefault(name, []).append(entry.path)
    for name, paths in sorted(owners.items()):
        if len(paths) > 1:
            issues.warning(paths[0], None, f"'{name}' is a name or alias of {len(paths)} entries: "
                           + ", ".join(paths),
                           "Mentions of it would pull in all of them; make each alias unique.")

    def check_character(name: str, where: str, line: int | None, setting: str) -> None:
        entry = project.find_entry(name)
        if entry is None:
            issues.warning(where, line, f"{setting} '{name}' doesn't match any entry name or alias")
        elif setting == "POV" and entry.type != "character":
            issues.warning(where, line, f"POV '{name}' is a {entry.type}, not a character")

    if project.config.pov:
        check_character(project.config.pov, STORY_FILE, None, "POV")
    elif project.outline.chapters:
        issues.warning(STORY_FILE, None, "No default POV character is set",
                       "Set 'Point of view' under Writing in Story settings (Story files → Settings).")

    for chapter in project.outline.chapters:
        for holder, line in [(chapter, chapter.line)] + [(scene, scene.line) for scene in chapter.scenes]:
            if "pov" in holder.settings:
                check_character(holder.settings["pov"], OUTLINE_FILE, line, "POV")
            for name in holder.settings.get("characters", []):
                check_character(name, OUTLINE_FILE, line, "Listed character")
                entry = project.find_entry(name)
                if entry is not None and entry.context == "never":
                    issues.info(OUTLINE_FILE, line,
                                f"'{name}' has 'context: never' but is listed here, so it will be included")


def _check_positions(project: Project, issues: IssueList) -> None:
    last = project.outline.last_chapter
    for entry in project.entries:
        for item, kind in [(p, "progression") for p in entry.progressions] + \
                          [(s, "secret") for s in entry.secrets]:
            _check_position(project, item.position, kind, entry.path, item.line, issues)
        for secret in entry.secrets:
            _check_secret_lookahead(project, entry.name, secret.position, entry.path, secret.line, issues)

    for subplot in project.subplots:
        if not subplot.status:
            issues.warning(subplot.path, None, "No 'status:' set, so this subplot is never included")
        for chapter in subplot.status:
            if last and chapter > last:
                issues.warning(subplot.path, None,
                               f"Status at chapter {chapter} is beyond the last outlined chapter ({last})")
        for chapter, line in subplot.note_lines.items():
            if last and chapter > last:
                issues.warning(subplot.path, line,
                               f"Note for chapter {chapter} is beyond the last outlined chapter ({last})")


def _check_position(project: Project, position: Position, kind: str, path: str, line: int,
                    issues: IssueList) -> None:
    last = project.outline.last_chapter
    if last and position.chapter > last:
        issues.warning(path, line, f"This {kind} at {position} is beyond the last outlined chapter ({last})",
                       "It won't be used until the outline reaches that chapter. Check the number if that's a typo.")
        return
    if not position.scene:
        return
    chapter = project.outline.chapter(position.chapter)
    if chapter is None:
        return
    if not chapter.scenes:
        issues.warning(path, line,
                       f"This {kind} is keyed to {position}, but chapter {position.chapter} has no scenes",
                       f"Use @ch{position.chapter} instead, or add scenes to the outline.")
    elif chapter.scene(position.scene) is None:
        issues.warning(path, line,
                       f"This {kind} is keyed to {position}, but chapter {position.chapter} "
                       f"has only {len(chapter.scenes)} scene(s)")


def _check_secret_lookahead(project: Project, name: str, position: Position, path: str, line: int,
                            issues: IssueList) -> None:
    """Earlier scenes see later scenes' beats ("do not write yet"). Those beats could give a secret away."""
    if not position.scene:
        return
    chapter = project.outline.chapter(position.chapter)
    if chapter is None:
        return
    exposed = [s.number for s in chapter.scenes
               if s.number >= position.scene and s.settings.get("lookahead", True)]
    if exposed:
        scenes = ", ".join(str(n) for n in exposed)
        issues.warning(path, line,
                       f"{name}'s secret is revealed at {position}, but the beats of scene(s) {scenes} "
                       f"are shown to earlier scenes of chapter {position.chapter}",
                       "If those beats give the secret away, add 'lookahead: false' under those scene headings.")


def _check_outline(project: Project, issues: IssueList) -> None:
    for chapter in project.outline.chapters:
        mode = chapter.settings.get("mode", project.config.mode)
        if mode == "scenes" and not chapter.scenes:
            issues.info(OUTLINE_FILE, chapter.line,
                        f"Chapter {chapter.number} is set to scene mode but has no scenes; "
                        "the app will propose scenes for you to approve")
        if is_blank(chapter.text) and not chapter.scenes:
            issues.warning(OUTLINE_FILE, chapter.line, f"Chapter {chapter.number} has no instructions or beats")
        for holder, label in [(chapter, f"Chapter {chapter.number}")] + \
                             [(s, f"Chapter {chapter.number} scene {s.number}") for s in chapter.scenes]:
            words = holder.settings.get("words")
            if words is not None and not WORDS_TOO_FEW <= words <= WORDS_TOO_MANY:
                issues.warning(OUTLINE_FILE, holder.line, f"{label}: 'words: {words}' looks unusual",
                               f"Expected between {WORDS_TOO_FEW} and {WORDS_TOO_MANY}; check for a typo.")
        for scene in chapter.scenes:
            if is_blank(scene.text):
                issues.warning(OUTLINE_FILE, scene.line,
                               f"Chapter {chapter.number} scene {scene.number} has no beats")


def _check_chapter_files(project: Project, issues: IssueList) -> None:
    where = "your files" if project.own_files else "the outline"
    for number, chapter in project.chapters.items():
        if project.outline.chapters and project.outline.chapter(number) is None:
            issues.warning(chapter.path, None, f"There is no chapter {number} in {where}")


# --- overview -----------------------------------------------------------------

def _chapter_rows(project: Project) -> list[ChapterRow]:
    rows = []
    config = project.config
    for chapter in project.outline.chapters:
        drafted = project.chapters.get(chapter.number)
        rows.append(ChapterRow(
            number=chapter.number,
            title=chapter.title,
            mode=chapter.settings.get("mode", config.mode),
            scenes=len(chapter.scenes),
            target_words=chapter.settings.get("words", config.chapter_words),
            pov=chapter.settings.get("pov", config.pov or ("-" if project.own_files else "(not set)")),
            drafted_words=drafted.words if drafted else None,
        ))
    return rows


def _subplot_matrix(project: Project) -> list[tuple[str, list[str]]]:
    numbers = [c.number for c in project.outline.chapters]
    return [(s.name, [STATUS_LETTERS[s.status_at(n)] for n in numbers]) for s in project.subplots]


# --- text output (CLI) --------------------------------------------------------

def format_report(report: PlanCheckReport) -> str:
    counts = report.counts
    lines = [
        f"Plan-check: {report.title}",
        f"{_plural(counts['chapters'], 'chapter')} outlined, {_plural(counts['characters'], 'character')}, "
        f"{_plural(counts['places'], 'place')}, {counts['lore']} lore, {_plural(counts['subplots'], 'subplot')}",
        "",
        f"Errors: {len(report.of(ERROR))}   Warnings: {len(report.of(WARNING))}   Info: {len(report.of(INFO))}",
    ]
    for issue in report.issues:
        lines.append(f"  {issue.severity.upper():7} {issue.location()}  {issue.message}")
        for hint_line in issue.hint.splitlines():
            lines.append(f"          {hint_line}")

    if report.chapters:
        lines += ["", "Chapters"]
        table = [("Ch", "Title", "Mode", "Scenes", "Target", "POV", "Drafted")]
        for row in report.chapters:
            table.append((str(row.number), row.title or "-", row.mode, str(row.scenes or "-"),
                          str(row.target_words), row.pov,
                          f"{row.drafted_words} words" if row.drafted_words is not None else "-"))
        lines += _format_table(table)

    if report.subplot_matrix and report.chapters:
        lines += ["", "Subplots by chapter"]
        header = ("Subplot", *[str(row.number) for row in report.chapters])
        lines += _format_table([header] + [(name, *letters) for name, letters in report.subplot_matrix])
        lines.append("  - not started/dormant   I introduced   A active   C climax   R resolved")
    return "\n".join(lines)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _format_table(rows: list[tuple[str, ...]]) -> list[str]:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return ["  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows]
