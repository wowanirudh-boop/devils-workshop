"""Who may change which files of a story.

The planner keeps the story's foundation and never touches the story's text; the writer writes the story's
text and never touches the planner's files:
- the planner: Story plan.md, Decisions.md, Source notes.md, Writer guide.md, Chapter outline.md,
  Chapter architecture.md, Continuity.md, Story registry.md, Manuscript check.md, Story settings.md (what
  each file is for) and planner/;
- the writer: chapters/ (the chapters and scenes, and their earlier versions).
An edit you ask the planner's model to make for you (Ask AI to change) may change any of your text files,
but never the story's text: changes to the prose go to the writer.
"""

from __future__ import annotations

PLANNER, WRITER, ASSIST = "planner", "writer", "assist"
PLANNER_FILES = ("Story plan.md", "Decisions.md", "Source notes.md", "Writer guide.md", "Chapter outline.md",
                 "Chapter architecture.md", "Continuity.md", "Story registry.md", "Manuscript check.md",
                 "Story settings.md")
PLANNER_FOLDERS = ("planner/",)
STORY_TEXT = ("chapters/",)


class ScopeError(Exception):
    """A job tried to change a file that isn't its to change."""


def _norm(rel: str) -> str:
    rel = str(rel).replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def may_write(job: str, rel: str) -> bool:
    rel = _norm(rel)
    story_text = rel.startswith(STORY_TEXT)
    if job == WRITER:
        return story_text
    if job == PLANNER:
        return rel in PLANNER_FILES or rel.startswith(PLANNER_FOLDERS)
    if job == ASSIST:
        return not story_text
    return False


def check(job: str, rel: str) -> None:
    if not may_write(job, rel):
        who = {WRITER: "The writer", PLANNER: "The planner", ASSIST: "An AI edit"}.get(job, job)
        raise ScopeError(f"{who} can't change {_norm(rel)}: "
                         + ("the story's text is the writer's." if _norm(rel).startswith(STORY_TEXT) else
                            "that file isn't one it keeps."))
