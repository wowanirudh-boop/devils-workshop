"""The whole manuscript, checked: after the last chapter is written, and whenever you ask (Read → Check).

It brings together, without any request of its own:
- structure: every planned chapter and scene written, in order, none empty;
- the checks of what was written (planner/audit.py), for every chapter whose text is as it was checked: events
  told early or twice, facts contradicted, knowledge before it is disclosed, consequential inventions, openings
  that don't follow, a chapter's events or the story's ending missing;
- the registry: every event of the architecture happened, once (an event of a chapter not checked yet, or
  changed since, is not known: inconclusive, never passed);
- the prose: leftover markup, scenes that stop almost at once or mid-sentence, sentences repeated across the
  manuscript;
- the saved copies (exports/): whether each still holds the chapters as they are now;
- lengths and coverage: words planned and written per chapter and scene, and events per chapter.

Hard problems are kept apart from advisory ones and from what couldn't be judged. Nothing here judges how good the
prose is: that is yours.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from devils_workshop.planner.audit import (
    ADVISORY,
    HARD,
    INCONCLUSIVE,
    Finding,
    findings_of,
    prose_findings,
    repeated_sentences,
    text_hash,
)

REPORT_FILE = "Manuscript check.md"


@dataclass
class ChapterLine:
    number: int
    title: str
    planned_words: int
    words: int
    scenes_planned: int
    scenes_written: int
    events: int
    events_written: int
    checked: str           # "yes" | "changed since" | "no"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    chapters: list[ChapterLine] = field(default_factory=list)

    def of(self, severity: str) -> list[Finding]:
        return [f for f in self.findings if f.severity == severity]

    @property
    def verdict(self) -> str:
        if self.of(HARD):
            return "problems to put right"
        if self.of(INCONCLUSIVE):
            return "inconclusive: some of it couldn't be checked"
        return "no structural problems found"


def check_manuscript(root: Path) -> Report:
    from devils_workshop.planner.state import PlannerState, is_planner_story
    from devils_workshop.project.project import load_project
    from devils_workshop.project.revisions import CURRENT, exports

    project = load_project(root)
    report = Report()
    add = report.findings.append
    planner = is_planner_story(root)
    state = PlannerState.load(root) if planner else None
    planned = ([(c.number, c.title, c.words, [(s.number, s.words) for s in c.scenes]) for c in state.chapters]
               if state and state.chapters else
               [(c.number, c.title, c.settings.get("words", 0), [(s.number, s.settings.get("words", 0))
                                                                 for s in c.scenes]) for c in project.outline.chapters])
    numbers = [n for n, *_ in planned]
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        add(Finding(HARD, "order", f"the chapters are numbered {', '.join(map(str, numbers))}, not 1 to "
                                   f"{len(numbers)}"))
    for extra in sorted(set(project.chapters) - set(numbers)):
        add(Finding(ADVISORY, "unplanned", "a written chapter that isn't in the plan", extra))
    everything = []
    for number, title, words, scenes in planned:
        written = project.chapters.get(number)  # the story read once, not once per chapter
        texts = (dict(written.scenes) if written and written.scenes else
                 {1: written.text} if written and written.text.strip() else {})
        if not texts:
            add(Finding(HARD, "missing", "isn't written yet", number))
        for scene, scene_words in scenes:
            if texts and scene not in texts and not (len(texts) == 1 and 1 in texts and not scenes[1:]):
                add(Finding(HARD, "missing", "isn't written yet", number, scene))
        for scene, text in sorted(texts.items()):
            target = dict(scenes).get(scene, 0)
            if not text.strip():
                add(Finding(HARD, "short", "is empty", number, scene))
                continue
            report.findings += prose_findings(number, scene, text, target)
            for sentence in repeated_sentences(text, "\n".join(everything)):
                add(Finding(ADVISORY, "repetition", f"a sentence from earlier in the story appears again: "
                                                    f"\"{sentence[:120]}\"", number, scene))
            everything.append(text)
        checked = "no"
        events = state.registry.of_chapter(number) if state else []
        if state is not None and texts:
            entry = (state.audits.get(str(number)) or {}).get("scenes") or {}
            current = [n for n, t in texts.items() if (entry.get(str(n)) or {}).get("text") == text_hash(t)]
            if entry and len(current) == len(texts):
                checked = "yes"
                report.findings += [f for f in findings_of(state, number) if f.kind not in ("markup", "short",
                                                                                        "cut_off", "repetition",
                                                                                        "length")]
            else:
                checked = "changed since" if entry else "no"
                add(Finding(INCONCLUSIVE, "unsure", "hasn't been checked against the plan since it was written"
                            if not entry else "has changed since it was checked against the plan", number))
        elif texts and not planner:
            add(Finding(INCONCLUSIVE, "unsure", "events, facts and continuity aren't checked: the story wasn't "
                                                "planned with the planner", number))
        report.chapters.append(ChapterLine(number, title, words, sum(len(t.split()) for t in texts.values()),
                                           len(scenes), len(texts), len(events),
                                           sum(1 for e in events if e.written_in), checked))
    if state is not None and planned:
        last = planned[-1][0]
        ending = next((e for e in state.registry.of_chapter(last) if e.kind == "ending"), None)
        if ending is not None and not ending.written_in and not any(
                f.kind == "ending" for f in report.findings):
            add(Finding(HARD if _checked(report, last) else INCONCLUSIVE, "ending",
                        f"the story's ending (\"{ending.text}\") " + ("never happens" if _checked(report, last)
                                                                     else "isn't known to happen yet"), last))
    for path, status in exports(project):
        if status.state != CURRENT:
            add(Finding(ADVISORY, "export", f"the saved copy {path.name} isn't the manuscript as it is now: "
                                            f"{status.detail}"))
    return report


def _checked(report: Report, number: int) -> bool:
    return any(c.number == number and c.checked == "yes" for c in report.chapters)


def report_text(report: Report, title: str = "") -> str:
    lines = [f"# {title + ': ' if title else ''}manuscript check", "",
             f"Verdict: {report.verdict}. This checks structure, continuity and the plan, not how good the prose is.",
             ""]
    for severity, heading in ((HARD, "## Must be put right"), (INCONCLUSIVE, "## Couldn't be judged"),
                              (ADVISORY, "## Worth a look")):
        found = report.of(severity)
        lines += [heading] + ([f"- {f.line() if f.chapter else f.text}" for f in found] or ["- (nothing)"]) + [""]
    lines += ["## Lengths and coverage", "",
              "| Chapter | Words planned | Words written | Scenes | Events happened | Checked |",
              "|---|---|---|---|---|---|"]
    for c in report.chapters:
        lines.append(f"| {c.number}. {c.title} | {c.planned_words:,} | {c.words:,} | {c.scenes_written} of "
                     f"{c.scenes_planned} | {c.events_written} of {c.events} | {c.checked} |")
    return "\n".join(lines) + "\n"


def write_report(root: Path, report: Report) -> Path:
    from devils_workshop.planner.state import PlannerState, is_planner_story
    from devils_workshop.project.files import write_text
    from devils_workshop.project.scopes import PLANNER, check
    check(PLANNER, REPORT_FILE)
    title = PlannerState.load(root).title if is_planner_story(root) else ""
    path = Path(root) / REPORT_FILE
    write_text(path, report_text(report, title))
    return path
