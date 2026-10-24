"""The planner beside the writer, chapter by chapter.

Before a chapter is written (briefs just in time), the planner reads the chapter before it as it was actually
written and notes how things stand at its end (reconcile, plan_actual), then writes this chapter's briefs
from that. While you read a chapter (the Reader), you ask for changes: the planner plans them (ask,
plan_revise): which scenes change, how, their updated briefs, and what it means for later chapters. Once
you approve, it updates its own files (apply_to_plan), and the writer makes the changes (chat/review.py).
The planner never touches the story's text; the writer never touches the planner's files.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from devils_workshop.context.assemble import ChatMessage
from devils_workshop.context.render import attr, clean
from devils_workshop.llm.generate import GenerationResult
from devils_workshop.planner import prompts
from devils_workshop.planner.state import READY, ChapterPlan, PlannerState

ACTUAL_JOB, REVISE_JOB = "plan_actual", "plan_revise"
REVISE, REWRITE = "revise", "rewrite"
REVIEW_TURNS = 6  # the latest messages of a chapter's conversation in the Reader


def chapter_texts(root, number: int) -> dict[int, str]:
    """The chapter's text as written, scene by scene ({1: text} for a chapter written whole)."""
    from devils_workshop.project.project import load_project
    written = load_project(root).chapters.get(number)
    if written is None:
        return {}
    return dict(written.scenes) if written.scenes else ({1: written.text} if written.text.strip() else {})


def _chapter_text_block(texts: dict[int, str]) -> str:
    return "<chapter_text>\n" + "\n\n".join(f'<scene number="{n}">\n{clean(t)}\n</scene>'
                                            for n, t in sorted(texts.items())) + "\n</chapter_text>"


def _briefs_block(chapter: ChapterPlan) -> str:
    return "<briefs>\n" + "\n\n".join(f'<brief scene="{s.number}" title="{attr(s.title)}">\n{s.brief.strip()}\n</brief>'
                                      for s in chapter.scenes) + "\n</briefs>"


def _coming_in(state: PlannerState, chapter: ChapterPlan) -> str:
    before = state.chapter(chapter.number - 1)
    last = before.scenes[-1] if before and before.scenes else None
    return (last.continuity.strip() if last and last.continuity.strip() else
            "(the story begins here)" if before is None else "(not noted)")


# --- following what was actually written -----------------------------------------------------------------

def actual_messages(state: PlannerState, chapter: ChapterPlan, texts: dict[int, str]) -> list[ChatMessage]:
    parts = [prompts.downstream_context(state), f"<chapter>\n{prompts.chapter_block(chapter)}\n</chapter>",
             _briefs_block(chapter), f"<continuity_before>\n{_coming_in(state, chapter)}\n</continuity_before>",
             _chapter_text_block(texts),
             f"<task>\nNote how things actually stand at the end of chapter {chapter.number}.\n</task>"]
    return [prompts.system_message(state.root, ACTUAL_JOB, state.scene_words),
            ChatMessage("user", "\n\n".join(parts))]


def read_actual(reply: str) -> tuple[str, list[str]]:
    """(how things stand at the end of the chapter, where the text departs from the briefs)."""
    found = re.search(r"<continuity>(.*?)(?:</continuity>|$)", reply, re.DOTALL | re.I)
    differences = re.search(r"<differences>(.*?)(?:</differences>|$)", reply, re.DOTALL | re.I)
    lines = [re.sub(r"^\s*[-*]\s*", "", line).strip() for line in (differences.group(1) if differences else "")
             .split("\n")]
    return (found.group(1).strip() if found else reply.strip()), [line for line in lines if line]


def chapter_revision(root, number: int) -> str:
    """The chapter's text as it is now, as a short hash (project/revisions.py)."""
    from devils_workshop.project.project import load_project
    from devils_workshop.project.revisions import revision
    return revision(load_project(root).chapters.get(number))


def needs_reconcile(state: PlannerState, number: int, root) -> bool:
    """The chapter has text the continuity doesn't follow yet: never followed, or changed since (by any screen,
    or by hand) from the text the planner followed."""
    chapter = state.chapter(number)
    if not (chapter and chapter.scenes and chapter_texts(root, number)):
        return False
    if number not in state.reconciled:
        return True
    followed = state.reconciled_revisions.get(str(number))
    return bool(followed) and followed != chapter_revision(root, number)


# --- changes you ask for in the Reader ---------------------------------------------------------------------

@dataclass
class SceneChange:
    number: int
    how: str            # REVISE (keep the text, change what's needed) | REWRITE (write it again from its brief)
    change: str = ""    # what the writer must change
    brief: str = ""     # the scene's brief, updated


@dataclass
class ChangePlan:
    message: str = ""
    scenes: list[SceneChange] = field(default_factory=list)
    later: str = ""
    suggestions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """There is something to approve (and nothing it needs your answer for first)."""
        return bool(self.scenes) and not self.questions

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ChangePlan:
        plan = cls(**{k: v for k, v in data.items() if k != "scenes"})
        plan.scenes = [SceneChange(**s) for s in data.get("scenes") or []]
        return plan


def revise_messages(state: PlannerState, chapter: ChapterPlan, texts: dict[int, str],
                    request: str) -> list[ChatMessage]:
    parts = [prompts.downstream_context(state)]
    if state.guide.strip():
        parts.append(f"<writers_guide>\n{state.guide.strip()}\n</writers_guide>")
    parts += [f"<chapter_architecture>\n{prompts.architecture_lines(state)}\n</chapter_architecture>",
              f"<chapter>\n{prompts.chapter_block(chapter)}\n</chapter>", _briefs_block(chapter),
              f"<continuity_before>\n{_coming_in(state, chapter)}\n</continuity_before>", _chapter_text_block(texts)]
    earlier = state.reviews.get(str(chapter.number), [])[-REVIEW_TURNS:]
    if earlier:
        parts.append("<review>\n" + "\n".join(f"{'Author' if t['role'] == 'user' else 'Planner'}: {t['text']}"
                                              for t in earlier) + "\n</review>")
    parts.append(f"<request>\n{request.strip()}\n</request>")
    return [prompts.system_message(state.root, REVISE_JOB, state.scene_words), ChatMessage("user", "\n\n".join(parts))]


def read_revise(reply: str, chapter: ChapterPlan) -> ChangePlan:
    plan = ChangePlan()
    known = {s.number for s in chapter.scenes}
    for attrs, body in re.findall(r"<scene\b([^>]*)>(.*?)</scene>", reply, re.DOTALL | re.I):
        number = re.search(r'number\s*=\s*"?(\d+)', attrs)
        if not number or int(number.group(1)) not in known:
            continue
        how = re.search(r'how\s*=\s*"?(\w+)', attrs)
        change = re.search(r"<change>(.*?)</change>", body, re.DOTALL | re.I)
        brief = re.search(r"<brief>(.*?)</brief>", body, re.DOTALL | re.I)
        plan.scenes.append(SceneChange(int(number.group(1)), REWRITE if how and how.group(1).lower() == REWRITE
                                       else REVISE, change.group(1).strip() if change else "",
                                       brief.group(1).strip() if brief else ""))
    later = re.search(r"<later>(.*?)</later>", reply, re.DOTALL | re.I)
    plan.later = later.group(1).strip() if later else ""
    plan.suggestions = [m.strip() for m in re.findall(r"<suggestion>(.*?)</suggestion>", reply, re.DOTALL | re.I)
                        if m.strip()]
    plan.questions = [m.strip() for m in re.findall(r"<question>(.*?)</question>", reply, re.DOTALL | re.I)
                      if m.strip()]
    message = re.search(r"<reply>(.*?)(?:</reply>|$)", reply, re.DOTALL | re.I)
    plan.message = message.group(1).strip() if message else re.sub(r"<[^>]+>.*?</[^>]+>", "", reply,
                                                                    flags=re.DOTALL).strip()
    if plan.questions:
        plan.scenes = []  # it needs your answer first
    return plan


def remember(state: PlannerState, number: int, role: str, text: str) -> None:
    state.reviews.setdefault(str(number), []).append({"role": role, "text": text})


def apply_to_plan(state: PlannerState, number: int, plan: ChangePlan) -> None:
    """Your approved change, in the planner's files: the changed scenes' briefs. The chapter's text will change,
    so the continuity is followed from it again before the next chapter's briefs; if later chapters' briefs were
    already written and the change reaches them, they are written again just before their chapters are."""
    chapter = state.chapter(number)
    for change in plan.scenes:
        scene = next((s for s in chapter.scenes if s.number == change.number), None)
        if scene is not None and change.brief.strip():
            scene.brief, scene.status = change.brief.strip(), READY
    if number in state.reconciled:
        state.reconciled.remove(number)
    if plan.later.strip():
        unwritten = [c for c in state.chapters if c.number > number and not chapter_texts(state.root, c.number)]
        if any(s.brief for c in unwritten for s in c.scenes):  # briefs written before this change: again, later
            for later in unwritten:
                for scene in later.scenes:
                    scene.brief, scene.status, scene.continuity = "", "waiting", ""
            state.jit = True
            state.briefs_through = min(state.briefs_through, min(c.number for c in unwritten) - 1)
    state.review_plans.pop(str(number), None)
    summary = ", ".join(f"scene {c.number} ({'revised' if c.how == REVISE else 'rewritten'})" for c in plan.scenes)
    remember(state, number, "planner", f"Approved: {summary}.")
    state.save()


def cost_of(*results: GenerationResult | None) -> float:
    return sum(r.total_cost for r in results if r is not None)
