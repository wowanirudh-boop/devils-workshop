"""The Reader's loop: you read a chapter, ask for changes, the planner plans them, you approve, the writer
makes them, and you read again. Changes replace the chapter's text in place (no earlier version is kept,
but History has one Undo for each change).

The planner plans and updates only its own files (the scenes' briefs); the writer changes only the story's
text: a scene to revise gets its current text and the change, a scene to rewrite is written again from its
new brief. The scenes are written at the same time.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from devils_workshop.chat.runner import send
from devils_workshop.chat.session import SCENE, ChapterChat, Prepared
from devils_workshop.context.assemble import assemble
from devils_workshop.llm.generate import GenerationResult, generate
from devils_workshop.planner import review
from devils_workshop.planner.output import write_outputs
from devils_workshop.planner.state import PlannerState
from devils_workshop.project.project import load_project
from devils_workshop.settings import PLANNER, AppSettings


@dataclass
class ReviewTask:
    """A request from the Reader, carrying on while you look at other screens."""
    chapter: int
    kind: str                     # "ask" (the planner plans) | "make" (the change is made)
    running: bool = True
    activity: str = "Sending…"
    failure: str = ""
    cost: float = 0.0
    done: list[int] = field(default_factory=list)  # scenes changed


async def ask(root: Path, number: int, request: str, *, settings: AppSettings, catalog, client,
              task: ReviewTask | None = None, **options) -> review.ChangePlan | None:
    """Your request for chapter `number`, then the planner's plan for it (kept until you approve or ask again)."""
    state = PlannerState.load(root)
    chapter = state.chapter(number)
    texts = review.chapter_texts(root, number)
    if chapter is None or not texts:
        raise ValueError(f"Chapter {number} hasn't been planned and written yet")
    messages = review.revise_messages(state, chapter, texts, request)
    review.remember(state, number, "user", request.strip())
    state.save()
    if task:
        task.activity = "The planner is working out the change…"
    result = await generate(messages, client=client, settings=settings, catalog=catalog, log_root=root / "logs",
                            label=f"review-ch{number:02d}", target_words=600, job="plan-revise", role=PLANNER,
                            patterns=[], min_words=3, **options)
    state = PlannerState.load(root)
    state.spent += result.total_cost
    if task:
        task.cost += result.total_cost
    if not result.ok:
        state.save()
        if task:
            task.failure = result.failure or "The planner's request failed"
        return None
    plan = review.read_revise(result.text, chapter)
    review.remember(state, number, "planner", plan.message or "Here is the change.")
    if plan.ready:
        state.review_plans[str(number)] = plan.to_dict()
    else:
        state.review_plans.pop(str(number), None)
    state.save()
    return plan


async def make_changes(root: Path, number: int, *, settings: AppSettings, catalog, client, sizes: dict | None = None,
                       task: ReviewTask | None = None, calibration_dir: Path | None = None, **options) -> bool:
    """The change you approved: the planner's files first, then the writer, every changed scene at the same time,
    saved in place in one go. False (and task.failure) if nothing could be changed."""
    state = PlannerState.load(root)
    raw = state.review_plans.get(str(number))
    if not raw:
        if task:
            task.failure = "There is no change waiting for approval"
        return False
    plan = review.ChangePlan.from_dict(raw)
    review.apply_to_plan(state, number, plan)
    write_outputs(state, snapshot_action=f"review-ch{number:02d}-plan")

    project = load_project(root)
    chat = ChapterChat.load(root, number)
    sizes = sizes or {}
    prepared: dict[int, Prepared] = {}
    for change in plan.scenes:
        if change.how == review.REWRITE:
            prepared[change.number] = Prepared(SCENE, number, change.number,
                                               assemble(project, number, change.number, mode="scenes", **sizes))
        else:
            prepared[change.number] = chat.prepare_change(project, change.change or "Make the change in the brief.",
                                                          change.number, **sizes)
    if task:
        task.activity = f"The writer is changing scene{'s' if len(prepared) > 1 else ''} " + \
            ", ".join(map(str, sorted(prepared))) + "…"
    results: list[GenerationResult] = await asyncio.gather(*(
        send(p, project=project, settings=settings, catalog=catalog, client=client,
             calibration_dir=calibration_dir, **options) for p in prepared.values()))
    texts = {n: r.text for n, r in zip(prepared, results) if r.ok}
    cost = sum(r.total_cost for r in results)
    if task:
        task.cost += cost
    if texts:
        chat.replace_scenes(load_project(root), texts, action=f"ch{number:02d}-review")
    failed = [n for n, r in zip(prepared, results) if not r.ok]
    if task:
        task.done = sorted(texts)
        if failed:
            first = next(r for r in results if not r.ok)
            task.failure = (f"Scene{'s' if len(failed) > 1 else ''} {', '.join(map(str, failed))} couldn't be "
                            f"changed: {first.failure or 'the request failed'}")
    return bool(texts)
