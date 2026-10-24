"""Proposing scenes for a chapter that has only chapter-level instructions (D16).

The model suggests a scene list; you approve or edit it; it is then written
into outline.md under the chapter (with a snapshot first, so it can be undone).
For a story kept in your own files, it goes into app-scenes.md instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from devils_workshop.context.assemble import (
    AssembledRequest,
    ChatMessage,
    assemble,
    author_instructions,
    load_instruction,
)
from devils_workshop.context.render import clean
from devils_workshop.history import write_project_file
from devils_workshop.project.files import read_text
from devils_workshop.project.issues import IssueList
from devils_workshop.project.markdown import is_blank
from devils_workshop.project.outline import parse_outline
from devils_workshop.project.own_files import SCENES_FILE
from devils_workshop.project.project import OUTLINE_FILE, Project

SCENES_HEADER = ("<!-- Scenes you approved for chapters in your own files. Your own files are never changed. "
                 "Edit or delete scenes here; '# Chapter N' then '## Scene N: Title' and one beat per line. -->\n")


@dataclass
class ProposedScene:
    title: str
    beats: list[str]
    pov: str = ""
    characters: list[str] = field(default_factory=list)


def prepare_scene_proposal(project: Project, chapter: int, notes: str = "") -> AssembledRequest:
    """Same reference material as writing the chapter; a planning task instead of a writing one."""
    request = assemble(project, chapter, mode="whole", notes=notes)
    outline = project.outline.chapter(chapter)
    system_parts = [clean(text) for text in project.system_texts()]
    system_parts.append(clean(load_instruction(project, "scenes")))
    system_parts.append(author_instructions(request.sections))

    user = request.messages[1].content
    context = user.rsplit("<task>", 1)[0].rstrip()
    title = f', "{outline.title}"' if outline.title else ""
    task = [f"<task>\nPlan the scenes for chapter {chapter}{title}.",
            f"Target length of the whole chapter: about {request.target_words:,} words."]
    if not is_blank(outline.text):
        task += ["", "Instructions for this chapter:", clean(outline.text)]
    if notes.strip():
        task += ["", "Notes for this request:", clean(notes)]
    task.append("</task>")
    request.messages = [ChatMessage("system", "\n\n".join(p for p in system_parts if p)),
                        ChatMessage("user", f"{context}\n\n" + "\n".join(task))]
    return request


def parse_scene_proposal(reply: str) -> list[ProposedScene]:
    """Read the model's JSON, tolerating extra text or code fences around it."""
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("The reply didn't contain a scene list")
    try:
        data = json.loads(reply[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"The scene list couldn't be read: {exc.msg}") from exc
    scenes = []
    for raw in data.get("scenes") or []:
        if not isinstance(raw, dict):
            continue
        beats = raw.get("beats") or []
        if isinstance(beats, str):
            beats = [beats]
        characters = raw.get("characters") or []
        if isinstance(characters, str):
            characters = [c.strip() for c in characters.split(",")]
        scenes.append(ProposedScene(str(raw.get("title") or "").strip(),
                                    [str(b).strip() for b in beats if str(b).strip()],
                                    str(raw.get("pov") or "").strip(),
                                    [str(c).strip() for c in characters if str(c).strip()]))
    if not scenes:
        raise ValueError("The reply had no scenes")
    return scenes


def apply_scene_proposal(project: Project, chapter: int, scenes: list[ProposedScene]) -> None:
    """Write the approved scenes at the end of the chapter's section: in outline.md, or, for a
    story kept in your own files, in app-scenes.md (your files are never changed)."""
    outline = project.outline.chapter(chapter)
    if outline is None:
        raise ValueError(f"Chapter {chapter} isn't in the outline")
    if outline.scenes:
        raise ValueError(f"Chapter {chapter} already has scenes in the outline")
    if project.own_files:
        target = SCENES_FILE
        path = project.root / target
        text = read_text(path)[0] if path.is_file() else SCENES_HEADER
        parsed = parse_outline(text, target, IssueList())
        lines = text.rstrip("\n").split("\n")
        if parsed.chapter(chapter) is None:
            lines += ["", f"# Chapter {chapter}"]
            parsed = parse_outline("\n".join(lines), target, IssueList())
        heading_line = parsed.chapter(chapter).line
        later = [c.line for c in parsed.chapters if c.line > heading_line]
    else:
        target = OUTLINE_FILE
        lines = read_text(project.root / OUTLINE_FILE)[0].split("\n")
        heading_line = outline.line
        later = [c.line for c in project.outline.chapters if c.line > outline.line]
    end = (min(later) - 1) if later else len(lines)
    while end > heading_line and not lines[end - 1].strip():
        end -= 1

    chapter_pov = outline.settings.get("pov", project.config.pov) or ""
    block = []
    for number, scene in enumerate(scenes, 1):
        title = re.sub(r"\s+", " ", scene.title)
        block += ["", f"## Scene {number}: {title}" if title else f"## Scene {number}"]
        if scene.pov and scene.pov.lower() != chapter_pov.lower() and not project.own_files:
            block.append(f"pov: {scene.pov}")
        if scene.characters and not project.own_files:
            block.append("characters: [" + ", ".join(scene.characters) + "]")
        block += [f"- {beat}" for beat in scene.beats]
    new = lines[:end] + block + lines[end:]
    text = "\n".join(new)
    write_project_file(project.root, target, text if text.endswith("\n") else text + "\n", f"ch{chapter:02d}-scenes")
