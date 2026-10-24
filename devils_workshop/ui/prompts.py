"""Prompts: the story's system prompt, and every built-in prompt, editable.

Story files → Prompts edits them for one story; the Prompts screen (in the sidebar) edits them for every
story. A story's own copy wins over yours, and yours over the built-in one (project/instructions.py).
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from devils_workshop.history import snapshot, write_project_file
from devils_workshop.project.files import read_text
from devils_workshop.project.instructions import (
    BUILT_IN,
    JOBS,
    MINE,
    STORY,
    app_copy,
    builtin_text,
    instruction,
    remove_app_copy,
    save_app_copy,
    source,
    story_copy,
)
from devils_workshop.project.own_files import DOCUMENTS, STORY_FILE, SYSTEM, looks_binary, make_text_copy, settings_text
from devils_workshop.project.project import SYSTEM_PROMPT_FILE, Project
from devils_workshop.settings import PLANNER, WRITER
from devils_workshop.ui.assist import ai_edit_button
from devils_workshop.ui.components import (
    card,
    notice,
    page,
    page_header,
    primary,
    quiet,
    secondary,
    smooth_reload,
    toast,
)
from devils_workshop.ui.layout import frame

SOURCE_TEXT = {STORY: "this story's copy", MINE: "your copy, for every story", BUILT_IN: "built-in"}
ROLE_TITLES = {WRITER: "The writer's prompts", PLANNER: "The planner's prompts"}


def prompt_panel(project: Project) -> None:
    from devils_workshop.planner.state import is_planner_story
    from devils_workshop.ui.defaults import story_switch

    story_switch(project)
    root = project.root
    name = SYSTEM_PROMPT_FILE
    marked = [rel for rel, role in (project.config.own_files or {}).items() if role == SYSTEM]
    with card("This story's system prompt", "The writer's: sent at the start of every writing request for this "
                                            "story (after your default instructions): who the model should be, "
                                            "the tone, and any rules for the whole book. The planner has its own "
                                            "(below) and plans within this one."):
        if marked:
            name = marked[0]
            ui.label(f"Your system prompt is {', '.join(marked)} (chosen under What's sent).").classes("dw-small")
        if Path(name).suffix.lower() in DOCUMENTS or looks_binary(root / name):
            def as_text() -> None:
                try:
                    copy = make_text_copy(root, name)
                except ValueError as exc:
                    toast(str(exc), "warning")
                    return
                toast(f"{copy} is now the system prompt; edit it here")
                smooth_reload()

            notice("info", f"{name} can't be edited as it is. Edit as text saves its text as a .md file you can "
                           "edit here, which becomes the system prompt (the original is kept, not sent).",
                   "Edit as text", as_text)
        else:
            path = root / name
            system = ui.codemirror(read_text(path)[0] if path.is_file() else "", language="Markdown",
                                   line_wrapping=True).classes("w-full dw-editor").style("min-height: 30vh")

            def save_system() -> None:
                write_project_file(root, name, system.value, "edit-system-prompt")
                if project.own_files and not marked:  # a new file: mark it as the system prompt
                    roles = {**project.config.own_files, name: SYSTEM}
                    existing = read_text(root / STORY_FILE)[0]
                    write_project_file(root, STORY_FILE, settings_text(roles, project.config.title,
                                                                    project.config.chapter_words, existing),
                                       "your-files")
                toast("System prompt saved")

            with ui.row().classes("gap-2 items-center"):
                primary("Save system prompt", save_system, "save")
                ai_edit_button(name, lambda: system.value, lambda text: (system.set_value(text), save_system()),
                               root, mark="ai-edit-system")

    roles = (WRITER, PLANNER) if is_planner_story(root) else (WRITER,)
    for role in roles:
        with card(ROLE_TITLES[role], "What each job is told to do. Saving one here makes a copy for this story "
                                     "only; your own copies for every story are on the Prompts screen."):
            for job, (owner, label, about) in JOBS.items():
                if owner == role:
                    _job_editor(root, job, label, about)
    quiet("Edit them for every story on the Prompts screen", lambda: ui.navigate.to("/prompts"), "tune") \
        .classes("self-start")


def _job_editor(root: Path | None, job: str, label: str, about: str) -> None:
    """One prompt: for a story (root) or, with no root, your copy for every story."""
    used = source(root, job)
    current = instruction(root, job)
    with ui.expansion(label, caption=f"{about} Now: {SOURCE_TEXT[used]}.", icon="notes") \
            .classes("w-full dw-section-block").mark(f"prompt-{job}"):
        with ui.column().classes("gap-2 px-3 pb-3 w-full"):
            editor = ui.codemirror(current, language="Markdown", line_wrapping=True) \
                .classes("w-full dw-editor").style("min-height: 24vh").mark(f"prompt-editor-{job}")
            ui.label("Text between <!-- and --> is a note for you and is never sent.").classes("dw-small")

            def save() -> None:
                if root is None:
                    save_app_copy(job, editor.value)
                    toast(f"{label}: saved for every story")
                else:
                    write_project_file(root, f"prompts/jobs/{job}.md", editor.value, f"edit-{job}-instruction")
                    toast(f"{label}: saved for this story")
                smooth_reload()

            def reset() -> None:
                if root is None:
                    remove_app_copy(job)
                    toast(f"{label}: using the built-in version")
                elif story_copy(root, job).is_file():
                    snapshot(root, [f"prompts/jobs/{job}.md"], f"reset-{job}-instruction")
                    story_copy(root, job).unlink()
                    toast(f"{label}: this story's copy removed")
                smooth_reload()

            with ui.row().classes("gap-2 items-center"):
                secondary("Save for every story" if root is None else "Save for this story", save, "save") \
                    .mark(f"prompt-save-{job}")
                ai_edit_button(f"prompts/jobs/{job}.md" if root is not None else f"{job}.md", lambda: editor.value,
                               lambda text: (editor.set_value(text), save()), root, mark=f"ai-edit-{job}")
                if root is None and app_copy(job).is_file():
                    quiet("Use the built-in version", reset, "restart_alt").mark(f"prompt-reset-{job}")
                if root is not None and used == STORY:
                    quiet("Remove this story's copy", reset, "restart_alt").mark(f"prompt-reset-{job}")
                if editor.value != builtin_text(job):
                    quiet("Show the built-in version", lambda: setattr(editor, "value", builtin_text(job)),
                          "visibility").props("dense")


def prompts_page() -> None:
    """Your prompts for every story: the writer's and the planner's."""
    frame("/prompts")
    with page("medium"):
        with page_header("Prompts", "What the writer and the planner are told to do for each job. Change any of "
                                    "them here for every story; a story can still keep its own copy (Story "
                                    "files → Prompts). Your system prompt files are under Default instructions."):
            secondary("Default instructions", lambda: ui.navigate.to("/defaults"), "rule")
        for role in (WRITER, PLANNER):
            with card(ROLE_TITLES[role]):
                for job, (owner, label, about) in JOBS.items():
                    if owner == role:
                        _job_editor(None, job, label, about)
