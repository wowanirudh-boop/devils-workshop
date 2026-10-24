"""The planner's output: a story folder the writer opens as it is.

Two kinds of file go to the writer, and nothing else:
    Writer guide.md          the lasting facts (voice, characters, world, rules): sent with every scene
    Chapter outline.md       "# Chapter N: Title", "words: N", then for each scene "## Scene N: Title",
                             "words: N" and its self-contained brief: each scene gets only its own brief
For you (not sent):
    Story plan.md            the plan you agreed with the planner
    Decisions.md             what is settled, still open, ruled out, and your preferences
    Source notes.md          the planner's notes on the files and pictures you gave it
    Chapter architecture.md  every chapter's purpose, summary, point of view, beats, ending and scenes
    Continuity.md            how things stand after each scene, as the planner tracked it
and Story settings.md: the title, scene by scene with every scene on its own ("independent"), and what
each file is for.

The outline is rewritten as scene briefs arrive, so a chapter can be written once its scenes are
planned, while later chapters are still being planned. With briefs written just in time (the default),
a chapter's briefs are written just before it is, so until then its scenes say so and can't be written.
A story planned before the writer's guide existed (no Writer guide.md) keeps its plan sent with every
chapter, as before.

Your edits to Chapter outline.md (a scene's brief) and Continuity.md are read back into the planner's
state before its next request (read_back_edits), so it plans from what you changed.
"""

from __future__ import annotations

import re
from pathlib import Path

from devils_workshop.history import snapshot
from devils_workshop.planner.registry import REGISTRY_FILE, read_approvals, registry_text
from devils_workshop.planner.state import DECISIONS_FILE, GUIDE_FILE, PLAN_FILE, SOURCES_FILE, PlannerState
from devils_workshop.project.files import read_text, write_text
from devils_workshop.project.newproject import ProjectExistsError
from devils_workshop.project.issues import IssueList
from devils_workshop.project.scopes import PLANNER as PLANNER_JOB
from devils_workshop.project.scopes import check
from devils_workshop.project.outline import PENDING_BRIEF, parse_outline
from devils_workshop.project.own_files import EVERY, NOT_SENT, OUTLINE, STORY_FILE, match_heading, settings_text

OUTLINE_FILE = "Chapter outline.md"
ARCHITECTURE_FILE = "Chapter architecture.md"
CONTINUITY_FILE = "Continuity.md"
OUTPUT_FILES = [PLAN_FILE, DECISIONS_FILE, SOURCES_FILE, GUIDE_FILE, OUTLINE_FILE, ARCHITECTURE_FILE,
                CONTINUITY_FILE, REGISTRY_FILE, STORY_FILE]
ROLES = {GUIDE_FILE: EVERY, OUTLINE_FILE: OUTLINE, PLAN_FILE: NOT_SENT, DECISIONS_FILE: NOT_SENT,
         SOURCES_FILE: NOT_SENT, ARCHITECTURE_FILE: NOT_SENT, CONTINUITY_FILE: NOT_SENT, REGISTRY_FILE: NOT_SENT}
INDEPENDENT = "defaults:\n  mode: scenes\n  in_chapter_context: independent\n"
DEFAULT_CHAPTER_WORDS = 3000


def create_planner_story(folder: Path, title: str) -> Path:
    """A new, empty story folder set up for the planner (written scene by scene once planned)."""
    folder = Path(folder)
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ProjectExistsError(f"{folder} already exists and isn't empty; choose a new folder")
    folder.mkdir(parents=True, exist_ok=True)
    write_text(folder / STORY_FILE, settings_text({}, title, DEFAULT_CHAPTER_WORDS, INDEPENDENT))
    PlannerState(folder, title=title).save()
    return folder


def _safe(text: str) -> str:
    """A line that would read as a "Chapter N" or "Scene N" heading is quoted, so it stays inside its scene."""
    lines = []
    for line in text.strip().split("\n"):
        heading = match_heading(line, "chapter") or match_heading(line, "scene")
        lines.append(f"> {line.strip()}" if heading else line)
    return "\n".join(lines)


def _chapter_details(chapter) -> list[str]:
    lines = []
    for label, value in (("Purpose", chapter.purpose), ("Summary", chapter.summary),
                         ("Point of view", chapter.pov), ("When and where", chapter.when_where)):
        if value:
            lines.append(_safe(f"**{label}:** {value}"))
    if chapter.beats:
        lines.append("**Key beats:**")
        lines += [_safe(f"- {beat}") for beat in chapter.beats]
    if chapter.ending:
        lines.append(_safe(f"**Ending:** {chapter.ending}"))
    for label, value in (("Arcs", chapter.arcs), ("Sets up", chapter.sets_up), ("Pays off", chapter.pays_off)):
        if value:
            lines.append(_safe(f"**{label}:** {value}"))
    return lines


def outline_text(state: PlannerState) -> str:
    """The chapters and their scenes' briefs. With a writer's guide, each scene is written from its brief alone,
    so the chapter's own details stay in Chapter architecture.md; without one (planned before the guide), they
    are the chapter's instructions, as before."""
    independent = bool(state.guide.strip())
    parts = []
    for chapter in state.chapters:
        lines = [f"# Chapter {chapter.number}: {chapter.title}"]
        if chapter.words:
            lines.append(f"words: {chapter.words}")
        lines.append("")
        if not independent:
            lines += _chapter_details(chapter)
        for scene in chapter.scenes:
            lines += ["", f"## Scene {scene.number}: {scene.title}"]
            if scene.words:
                lines.append(f"words: {scene.words}")
            lines.append("")
            if scene.brief.strip():
                lines.append(_safe(scene.brief))
            elif independent:  # never a writer's brief: the writer can't write it yet
                lines.append(PENDING_BRIEF)
            else:
                lines.append(_safe(f"**Purpose:** {scene.purpose}" if scene.purpose else "(Brief not written yet.)"))
        parts.append("\n".join(lines).rstrip())
    return "\n\n".join(parts) + "\n"


def architecture_text(state: PlannerState) -> str:
    lines = [f"# {state.title or 'Chapter architecture'}: chapter architecture", "",
             f"{len(state.chapters)} chapters, about {state.total_words():,} words. Written by the story planner "
             "for you to read; the writer gets only Writer guide.md and each scene's brief.", "",
             "| # | Chapter | Words | Purpose | Scenes |", "|---|---|---|---|---|"]
    for c in state.chapters:
        purpose = c.purpose.replace("|", "/").replace("\n", " ")
        lines.append(f"| {c.number} | {c.title} | {c.words:,} | {purpose} | {len(c.scenes) or c.scene_count or ''} |")
    for c in state.chapters:
        lines += ["", f"## {c.number}. {c.title}"] + _chapter_details(c)
        if c.scenes:
            lines += [""] + [f"{s.number}. **{s.title}** ({s.words:,} words): {s.purpose}" for s in c.scenes]
    return "\n".join(lines) + "\n"


def continuity_text(state: PlannerState) -> str:
    lines = [f"# {state.title or 'Continuity'}: continuity", "",
             "How things stand after each scene, as the story planner tracked it while writing the briefs. It is "
             "for you and the planner; the writer never sees it (each brief carries what its scene needs)."]
    for c in state.chapters:
        for scene in c.scenes:
            if scene.continuity.strip():
                lines += ["", f"## After chapter {c.number}, scene {scene.number}: {scene.title}",
                          _safe(scene.continuity)]
    return "\n".join(lines) + "\n"


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def _unquote(text: str) -> str:
    """A brief as you edited it, with the quoting _safe() adds taken off again."""
    return "\n".join(line[2:] if line.startswith("> ") and (match_heading(line[2:], "chapter")
                                                         or match_heading(line[2:], "scene")) else line
                     for line in text.strip().split("\n"))


def read_back_edits(state: PlannerState) -> None:
    """Your edits to a scene's brief (in Chapter outline.md) or its continuity (in Continuity.md) win over the
    planner's saved copy, so its next request plans from them. Only scenes it has planned are read back."""
    root = state.root
    outline = root / OUTLINE_FILE
    if state.chapters and outline.is_file():
        parsed = parse_outline(read_text(outline)[0], OUTLINE_FILE, IssueList())
        for chapter in state.chapters:
            found = parsed.chapter(chapter.number)
            for scene in chapter.scenes if found else []:
                edited = found.scene(scene.number)
                if edited is None or not scene.brief.strip() or edited.text.strip().startswith(PENDING_BRIEF[:20]):
                    continue
                if not _same(edited.text, _safe(scene.brief)) and edited.text.strip():
                    scene.brief = _unquote(edited.text)
    continuity = root / CONTINUITY_FILE
    if continuity.is_file():
        sections: dict[tuple[int, int], list[str]] = {}
        current = None
        for line in read_text(continuity)[0].split("\n"):
            heading = re.match(r"^##\s+After chapter\s+(\d+),\s*scene\s+(\d+)", line, re.I)
            if heading:
                current = (int(heading.group(1)), int(heading.group(2)))
                sections[current] = []
            elif line.startswith("## "):
                current = None
            elif current:
                sections[current].append(line)
        for chapter in state.chapters:
            for scene in chapter.scenes:
                text = "\n".join(sections.get((chapter.number, scene.number), [])).strip()
                if text and scene.continuity.strip() and not _same(text, _safe(scene.continuity)):
                    scene.continuity = _unquote(text)


def write_outputs(state: PlannerState, snapshot_action: str | None = None) -> None:
    """Write the story files from the planner's state. With snapshot_action, a snapshot of the files as they
    were comes first, so History can undo the whole step in one go."""
    from devils_workshop.project.project import load_project

    root = state.root
    if snapshot_action:
        snapshot(root, OUTPUT_FILES, snapshot_action)
    state.save()  # Story plan.md and Decisions.md
    for name in OUTPUT_FILES:
        check(PLANNER_JOB, name)  # the planner keeps its own files, never the story's text
    independent = bool(state.guide.strip())
    if independent:
        write_text(root / GUIDE_FILE, state.guide.strip() + "\n")
    if state.chapters:
        write_text(root / OUTLINE_FILE, outline_text(state))
        write_text(root / ARCHITECTURE_FILE, architecture_text(state))
        if any(s.continuity.strip() for c in state.chapters for s in c.scenes):
            write_text(root / CONTINUITY_FILE, continuity_text(state))
    if state.registry.events or state.registry.facts or state.registry.sources:
        if (root / REGISTRY_FILE).is_file():  # approvals you added while this copy of the state was held
            read_approvals(state.registry, read_text(root / REGISTRY_FILE)[0])
        write_text(root / REGISTRY_FILE, registry_text(state.registry, state.title))
    config = load_project(root).config
    roles = dict(config.own_files or {})
    roles.update({name: role for name, role in ROLES.items() if (root / name).is_file()})
    if not independent and (root / PLAN_FILE).is_file():
        roles[PLAN_FILE] = EVERY  # planned before the writer's guide: the plan goes with every chapter, as before
    words = state.total_words() // len(state.chapters) if state.chapters and state.total_words() else \
        config.chapter_words or DEFAULT_CHAPTER_WORDS
    existing = read_text(root / STORY_FILE)[0] if (root / STORY_FILE).is_file() else ""
    write_text(root / STORY_FILE, settings_text(roles, state.title or config.title, words, existing,
                                             {"in_chapter_context": "independent"} if independent else None))
