"""Story files → Settings: this story's settings (Story settings.md), as a form.

The same settings can be edited in the file itself (Files → Story settings.md), by hand or with Ask AI to change.
"""

from __future__ import annotations

from datetime import date

from nicegui import ui

from devils_workshop.project.files import read_text
from devils_workshop.project.project import Project
from devils_workshop.project.story_settings import SETTINGS_FILE, read_settings, settings_markdown, write_settings
from devils_workshop.ui.assist import ai_edit_button
from devils_workshop.ui.components import card, model_name, primary, quiet, smooth_reload, toast
from devils_workshop.ui.state import STATE

PERSON = {"first": "First", "second": "Second", "third": "Third"}
TENSE = {"past": "Past", "present": "Present"}
MODE = {"whole": "Whole chapters", "scenes": "Scene by scene"}
SEES = {"full": "Earlier scenes' text", "beats_only": "Earlier scenes' beats", "independent": "Only its own brief"}
STATUS = {"in progress": "In progress", "completed": "Completed"}


def settings_panel(project: Project) -> None:
    root = project.root
    config = project.config
    characters = sorted({e.name for e in project.entries if e.type == "character"} | set(config.characters))
    models = {"": "The writer's models (Settings)"}
    models.update({mid: model_name(mid) for mid in sorted(STATE.catalog.models)})
    for mid in (config.primary_model, config.fallback_model):
        if mid:
            models.setdefault(mid, model_name(mid))

    with card("This story", "For you and the library."):
        title = ui.input("Title", value=config.title).classes("w-full").mark("settings-title")
        genre = ui.input("Genre", value=config.genre, placeholder="e.g. Literary mystery").classes("w-full") \
            .mark("settings-genre")
        key_characters = ui.input_chips("Key characters", value=list(config.characters),
                                        new_value_mode="add-unique").classes("w-full").mark("settings-characters")
        if characters:
            ui.label("Characters in your files: " + ", ".join(characters)).classes("dw-small")
        status = ui.toggle(STATUS, value=config.status).mark("settings-status")
        ui.label(f"Completed on {config.completed_on}." if config.status == "completed" and config.completed_on else
                 "Mark it completed when the story is done: the library shows it under Completed.").classes(
            "dw-small")

    with card("How the writer writes it"):
        with ui.row().classes("gap-3 w-full"):
            pov = ui.select({c: c for c in characters} | ({config.pov: config.pov} if config.pov else {}),
                            value=config.pov, label="Point of view", with_input=True, new_value_mode="add-unique",
                            clearable=True).classes("w-64").mark("settings-pov")
            person = ui.select(PERSON, value=config.person, label="Person").classes("w-32")
            tense = ui.select(TENSE, value=config.tense, label="Tense").classes("w-32")
        with ui.row().classes("gap-3 w-full"):
            words = ui.number("Words per chapter", value=config.chapter_words, min=100, step=100, precision=0) \
                .classes("w-44")
            mode = ui.select(MODE, value=config.mode, label="Written as").classes("w-44")
            sees = ui.select(SEES, value=config.in_chapter_context, label="What a scene sees").classes("w-56") \
                .mark("settings-sees")
        ui.label("What a scene sees, when written scene by scene: the text of the scenes before it, only their "
                 "beats, or only its own brief (stories made with the planner: every brief stands on its own, so "
                 "a chapter's scenes are written at the same time).").classes("dw-small")
        defaults = ui.switch("Use my default instructions", value=config.default_instructions)

    with card("This story's writer models", "Leave them as the writer's models to use the ones in Settings."):
        main = ui.select(models, value=config.primary_model or "", label="Main model", with_input=True) \
            .classes("w-full").mark("settings-main-model")
        fallback = ui.select(models, value=config.fallback_model or "", label="Fallback model", with_input=True) \
            .classes("w-full")

    def save() -> None:
        data = read_settings(root)
        data.update({"title": (title.value or "").strip() or config.title, "genre": (genre.value or "").strip(),
                     "characters": [str(c).strip() for c in key_characters.value or [] if str(c).strip()],
                     "default_instructions": bool(defaults.value)})
        if status.value != config.status:
            data["status"] = status.value
            data["completed_on"] = date.today().isoformat() if status.value == "completed" else ""
        written = dict(data.get("defaults") or {})
        written.update({"pov": pov.value or None, "person": person.value, "tense": tense.value,
                        "chapter_words": int(words.value or config.chapter_words), "mode": mode.value,
                        "in_chapter_context": sees.value})
        data["defaults"] = {k: v for k, v in written.items() if v not in (None, "")}
        data["models"] = {k: v for k, v in (("primary", main.value), ("fallback", fallback.value)) if v}
        write_settings(root, data)
        toast("Story settings saved")
        smooth_reload()

    def file_text() -> str:
        path = root / SETTINGS_FILE
        return read_text(path)[0] if path.is_file() else settings_markdown(read_settings(root))

    def accept(text: str) -> None:
        from devils_workshop.history import write_project_file
        write_project_file(root, SETTINGS_FILE, text, "story-settings")
        smooth_reload()

    with ui.row().classes("gap-2 items-center"):
        primary("Save settings", save, "save").mark("settings-save")
        ai_edit_button(SETTINGS_FILE, file_text, accept, root, mark="ai-edit-settings")
        quiet("Edit the file", lambda: ui.navigate.to("/story/files?path=Story%20settings.md"), "description")
