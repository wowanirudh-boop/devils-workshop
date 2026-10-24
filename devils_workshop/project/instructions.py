"""The built-in instructions (the prompts that tell each job what to do), and your own copies of them.

Each job's instruction is the first of:
1. this story's copy:          <story folder>/prompts/jobs/<job>.md   (Story files → Prompts)
2. your copy for every story:  <app folder>/Prompts/<job>.md          (the Prompts screen)
3. the built-in one:           devils_workshop/defaults/jobs/<job>.md

Text inside <!-- --> is a note for you and is never sent.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from devils_workshop.project.files import read_text, write_text
from devils_workshop.settings import PLANNER, WRITER, settings_dir

APP_FOLDER = "Prompts"
STORY_FOLDER = "prompts/jobs"
BUILT_IN, MINE, STORY = "built-in", "yours", "this story's"

# job -> (who uses it, its name, what it is for)
JOBS = {
    "write": (WRITER, "Writing", "Sent after the system prompt with every chapter or scene the writer writes."),
    "scenes": (WRITER, "Planning a chapter's scenes", "When a chapter in scene mode has no scenes yet, the writer's "
               "models propose them with this."),
    "join": (WRITER, "Smoothing the joins", "After a chapter's scenes are written at the same time (stories you "
             "plan yourself), each opening is smoothed into the scene before it."),
    "planner_system": (PLANNER, "The planner's system prompt", "Starts every planner request: who the planner is "
                       "and how it works."),
    "plan_brainstorm": (PLANNER, "Brainstorming the plan", "The conversation: the agenda of decisions, your "
                        "answers, and the story plan."),
    "plan_import": (PLANNER, "Bringing in a plan made elsewhere", "Your plan from another tool, carried into the "
                    "story plan as it is, shown as your instructions want it."),
    "plan_guide": (PLANNER, "The writer's guide", "The lasting facts sent with every scene: voice, characters, "
                   "world, rules."),
    "plan_architecture": (PLANNER, "Designing the chapters", "Every chapter's purpose, scenes, length and ending."),
    "plan_scenes": (PLANNER, "Planning a chapter's scenes", "One chapter's scene list."),
    "plan_scene": (PLANNER, "Writing a scene brief", "The self-contained brief the writer writes one scene from, "
                   "and the continuity after it."),
    "plan_notes": (PLANNER, "Notes on what you attach", "The planner's notes on a file or picture, made once, in the "
                   "turn you attach it."),
    "plan_actual": (PLANNER, "Following what was written", "Before a chapter's briefs are written: how things "
                    "actually stand at the end of the chapter before it, from its text."),
    "plan_revise": (PLANNER, "Changes you ask for in the Reader", "The planner plans which scenes change and how; "
                    "once you approve, the writer makes the changes."),
    "edit_file": (PLANNER, "Editing a file for you", "Ask AI to change: the planner's model edits one of your files "
                  "as you ask."),
    "plan_audit": (PLANNER, "Checking scenes as written", "After a scene is written: what it does with each event "
                   "of the story registry, what it adds or contradicts, and how things stand at its end. The app "
                   "decides from that what must be put right."),
    "plan_canon": (PLANNER, "The story's canon", "The consequential facts the story is held to, from when each is "
                   "true, and which parts of the writer's guide are only true from a later chapter."),
    "plan_source_events": (PLANNER, "The reference's events (adaptations)", "The events of the reference a story "
                           "adapts, in order, with who does what and what follows from what."),
    "plan_source_map": (PLANNER, "Mapping the reference (adaptations)", "Which event of the chapter architecture "
                        "carries each event of the reference."),
}


def builtin_text(job: str) -> str:
    return (resources.files("devils_workshop") / "defaults" / "jobs" / f"{job}.md").read_text(encoding="utf-8")


def app_copy(job: str) -> Path:
    return settings_dir() / APP_FOLDER / f"{job}.md"


def story_copy(root: Path, job: str) -> Path:
    return Path(root) / STORY_FOLDER / f"{job}.md"


def source(root: Path | None, job: str) -> str:
    """Which copy is used: this story's, yours, or the built-in one."""
    if root is not None and story_copy(root, job).is_file():
        return STORY
    return MINE if app_copy(job).is_file() else BUILT_IN


def instruction(root: Path | None, job: str) -> str:
    """The text used for a job (notes included; clean() takes them out before sending)."""
    found = source(root, job)
    if found == STORY:
        return read_text(story_copy(root, job))[0]
    if found == MINE:
        return read_text(app_copy(job))[0]
    return builtin_text(job)


def save_app_copy(job: str, text: str) -> None:
    write_text(app_copy(job), text)


def remove_app_copy(job: str) -> None:
    app_copy(job).unlink(missing_ok=True)
