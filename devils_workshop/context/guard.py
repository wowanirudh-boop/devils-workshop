"""The last check on a writer's request, at the point it is put together, for scenes written from the planner's
briefs (in_chapter_context "independent").

Deterministic, and nothing is sent when it fails (AssemblyError, shown where the request was asked for):
- the scene's brief is the one the planner wrote for that chapter and scene, so a chapter or scene is never
  given another one's brief by where it sits in Chapter outline.md;
- no other scene's brief is in the request;
- none of the planner's own files that never go to the writer (the plan, the architecture, the continuity, the
  decisions, the source notes, the registry) is in it;
- the writer's guide is the guide as it stands at this chapter (planner/scope.py: what becomes true only later is
  left out).
"""

from __future__ import annotations

import re

from devils_workshop.context.render import clean

PLANNER_ONLY = ("Story plan.md", "Chapter architecture.md", "Continuity.md", "Decisions.md", "Source notes.md",
                "Story registry.md")
FRAGMENT = 160  # characters of another scene's brief that, found in the request, mean the brief is in it


class GuardError(Exception):
    """The request breaks a rule it must keep (raised as an AssemblyError by assemble)."""


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[>*_#`]", " ", clean(text or "")).split()).lower()


def check_scene_request(root, chapter: int, scene: int, title: str, brief_text: str, sections: list,
                        message_text: str) -> None:
    from devils_workshop.planner.output import _safe
    from devils_workshop.planner.state import PlannerState, is_planner_story

    if not is_planner_story(root):
        return
    state = PlannerState.load(root)
    planned = state.chapter(chapter)
    own = next((s for s in planned.scenes if s.number == scene), None) if planned else None
    if own is not None and own.brief.strip():
        if _plain(_safe(own.brief)) != _plain(brief_text) and _plain(own.brief) != _plain(brief_text):
            raise GuardError(f"Chapter {chapter}, scene {scene}: the brief in Chapter outline.md isn't the one the "
                             "planner wrote for this scene, so it isn't sent. Open Chapter outline.md and check the "
                             "chapter and scene headings, or plan the scene again.")
        if title and own.title and _plain(title) != _plain(own.title):
            raise GuardError(f"Chapter {chapter}, scene {scene} is called \"{title}\" in Chapter outline.md but "
                             f"\"{own.title}\" in the plan, so it isn't sent: check the scene headings.")
    sent = _plain(message_text)
    for other in state.chapters:
        for item in other.scenes:
            if (other.number, item.number) == (chapter, scene) or len(item.brief) < FRAGMENT:
                continue
            if own is not None and _plain(item.brief) == _plain(own.brief):
                continue
            if _plain(item.brief)[:FRAGMENT] in sent:
                raise GuardError(f"Chapter {chapter}, scene {scene}: the request has the brief of chapter "
                                 f"{other.number}, scene {item.number} in it, so it isn't sent. Each scene is "
                                 "written from its own brief alone.")
    from devils_workshop.planner.canon import later_blocks
    for block in later_blocks(state.guide, state.registry.guide_scope, chapter):
        if _plain(block) in sent:
            raise GuardError(f"Chapter {chapter}, scene {scene}: the request has a part of the writer's guide that "
                             "is only true from a later chapter, so it isn't sent: \"" + block[:100] + "\"")
    for section in sections:
        name = section.key.split(":", 1)[-1].split("#")[0]
        if section.included and name in PLANNER_ONLY:
            raise GuardError(f"{name} is the planner's own file and never goes to the writer, but Story settings "
                             "sends it: set it to Not sent under Story files.")
