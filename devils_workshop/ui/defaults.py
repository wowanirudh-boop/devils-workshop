"""Default instructions: files you add once, used by every story (see project/default_instructions.py)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from nicegui import ui

from devils_workshop.history import snapshot, write_project_file
from devils_workshop.project.default_instructions import (
    BOTH,
    PLANNER_NOTES,
    folder,
    guess_role,
    list_files,
    load_defaults,
    load_roles,
    make_text_copy,
    role_of,
    save_roles,
)
from devils_workshop.project.files import read_text
from devils_workshop.project.own_files import (
    DOCUMENTS,
    EVERY,
    NOT_SENT,
    SYSTEM,
    binary_problem,
    data_looks_binary,
    is_editable,
    looks_binary,
)
from devils_workshop.ui.assist import ai_edit_button
from devils_workshop.ui.components import (
    card,
    empty_state,
    notice,
    page,
    page_header,
    primary,
    quiet,
    secondary,
    toast,
    smooth_reload,
)
from devils_workshop.ui.layout import frame, native_window_available, pick_files
from devils_workshop.ui.setup import FILE_ICONS

ROLE_LABELS = {SYSTEM: "System prompt", BOTH: "Writer and planner", EVERY: "Writer only (every chapter)",
               PLANNER_NOTES: "Planner only", NOT_SENT: "Not sent"}
ROLE_HELP = ("System prompt: sent first in every writing request, before the story's own system prompt (the "
             "planner is given it too, as the writer's rules to plan within). Writer and planner: sent to the writer "
             "with every chapter and to the planner with every request, so the plan and the prose both follow it "
             "(a goal, terminology, rules). Writer only: sent with every chapter (the planner is given it too, as "
             "the writer's rules to plan within). Both go to the writer as your instructions, after the built-in "
             "ones, so where they differ, yours win. Planner only: your planning "
             "preferences, sent with every planner request. Not sent: kept here, not used.")


def defaults_page() -> None:
    project = frame("/defaults")
    files = load_defaults()
    with page("medium"):
        with page_header("Default instructions", "Files you add once and every story uses: your usual system "
                                                 "prompt, house style, rules. Change them here any time; every "
                                                 "story uses the new version from its next request."):
            if files:
                add_buttons()
        if not files:
            with empty_state("rule", "No default instructions yet.",
                             "Add the instruction files you'd otherwise give every story: any text file (.md, "
                             ".txt, .json…), PDF or Word. You only need to do this once."):
                add_buttons()
            return
        roles = load_roles()

        def set_role(name: str, role: str) -> None:
            roles[name] = role
            save_roles({n: role_of(n, roles) for n in list_files()})
            toast(f"{name}: {ROLE_LABELS[role].lower()}")

        with ui.element("div").classes("dw-list"):
            for item in files:
                with ui.element("div").classes("dw-row").mark("default-file"):
                    ui.icon(FILE_ICONS.get(Path(item.name).suffix.lower(), "description")).classes("text-xl") \
                        .style("color: var(--dw-faint)")
                    with ui.column().classes("gap-0 grow min-w-0"):
                        ui.label(item.name).classes("dw-body")
                        words = f"{len(item.text.split()):,} words" if item.text.strip() else "empty"
                        ui.label(item.problem or words).classes("dw-small").style(
                            "color: var(--dw-problem)" if item.problem else "")
                    ui.select(ROLE_LABELS, value=item.role,
                              on_change=lambda e, n=item.name: set_role(n, e.value)).classes("w-48")
                    if is_editable(folder() / item.name):
                        quiet("Edit", lambda n=item.name: edit_dialog(n), "edit").props("dense")
                    elif Path(item.name).suffix.lower() in DOCUMENTS and item.text.strip():
                        quiet("Edit as text", lambda n=item.name: as_text(n), "edit_note").props("dense")                             .tooltip("Saves its text as a .md file you can edit, used in its place (the original "
                                     "is kept, not sent)").mark("default-as-text")
                    quiet("", lambda n=item.name: remove_dialog(n), "delete_outline").props(
                        'dense aria-label="Remove"').tooltip("Remove")
        ui.label(ROLE_HELP).classes("dw-small")
        notice("info", "A story can switch these off under Story files → Prompts. A file a story already "
                       "has (the same text) isn't sent twice."
               + (f" The story open now, {project.config.title}, "
                  f"{'uses them' if project.config.default_instructions else 'has them switched off'}."
                  if project else ""))
        ui.label(f"Kept in {folder()}").classes("dw-small")


def as_text(name: str) -> None:
    try:
        copy = make_text_copy(name)
    except ValueError as exc:
        toast(str(exc), "warning")
        return
    toast(f"{copy} is the editable text of {name}, and is used in its place")
    smooth_reload()


def add_buttons() -> None:
    secondary("Write one here", new_file_dialog, "edit_note")
    if native_window_available():
        primary("Add files…", add_from_disk, "upload_file")
    else:
        primary("Add files…", upload_dialog, "upload_file")


# --- adding, editing, removing ----------------------------------------------------------------------

def _safe_name(name: str) -> str:
    name = Path(name.replace("\\", "/")).name
    return re.sub(r'[<>:"/\\|?*]+', "-", name).strip(" .") or "instructions.md"


def _store(name: str, copy) -> str:
    """Save a file into the folder (a file with the same name is kept in its .history first)."""
    name = _safe_name(name)
    base = folder()
    base.mkdir(parents=True, exist_ok=True)
    if (base / name).exists():
        snapshot(base, [name], "replace")
    copy(base / name)
    return name


async def add_from_disk() -> None:
    chosen = await pick_files()
    if chosen is None:  # the window's own file picker isn't available: choose in the page instead
        upload_dialog()
        return
    added = []
    for path in chosen:
        source = Path(path)
        if looks_binary(source):
            toast(f"{source.name}: {binary_problem(source.name)}", "warning")
            continue
        added.append(_store(source.name, lambda target, s=source: shutil.copyfile(s, target)))
    if added:
        toast("Added: " + ", ".join(added))
        smooth_reload()


def upload_dialog() -> None:
    added: list[str] = []
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[34rem] gap-4"):
        ui.label("Add files").classes("dw-h2")
        ui.label("Choose or drop any text file (.md, .txt, .json…), PDF or Word. A file with the same name as one here "
                 "replaces "
                 "it (the old one is kept as a backup).").classes("dw-small")

        async def received(event) -> None:
            data = await event.file.read()
            if data_looks_binary(Path(event.file.name).suffix, data[:4096]):
                toast(f"{event.file.name}: {binary_problem(event.file.name)}", "warning")
                return
            name = _store(event.file.name, lambda target: target.write_bytes(data))
            added.append(name)

        ui.upload(multiple=True, auto_upload=True, on_upload=received).props(
            'flat bordered').classes("w-full").mark("default-upload")
        with ui.row().classes("w-full justify-end gap-2"):
            primary("Done", lambda: (dialog.close(), smooth_reload()))
    dialog.open()


def new_file_dialog() -> None:
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[30rem] gap-4"):
        ui.label("Write a default instruction").classes("dw-h2")
        name = ui.input("File name", value="My instructions.md").classes("w-full").mark("default-name")
        role = ui.select(ROLE_LABELS, value=BOTH, label="What it is for").classes("w-full")
        chosen = {"by_you": False}
        role.on("update:model-value", lambda: chosen.update(by_you=True))  # only a choice made by hand sticks
        name.on_value_change(lambda e: None if chosen["by_you"] else role.set_value(guess_role(e.value or "")))

        def create() -> None:
            file_name = _safe_name(name.value or "")
            if not Path(file_name).suffix or Path(file_name).suffix.lower() in DOCUMENTS:
                file_name += ".md"
            if (folder() / file_name).exists():
                toast(f"There is already a file called {file_name}", "warning")
                return
            write_project_file(folder(), file_name, "", "new")
            roles = load_roles()
            roles[file_name] = role.value
            save_roles({n: role_of(n, roles) for n in list_files()})
            dialog.close()
            edit_dialog(file_name)

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary("Create and edit", create).mark("default-create")
    dialog.open()


def edit_dialog(name: str) -> None:
    path = folder() / name
    with ui.dialog().props("maximized") as dialog, ui.card().classes("dw-card w-full h-full gap-3"):
        with ui.row().classes("w-full items-center gap-2 dw-dialog-head"):
            ui.label(name).classes("dw-h2 grow")
            quiet("Cancel", dialog.close)

            def save() -> None:
                write_project_file(folder(), name, editor.value, "edit")
                toast(f"{name} saved; every story uses it from its next request")
                dialog.close()
                smooth_reload()

            ai_edit_button(name, lambda: editor.value, lambda text: (editor.set_value(text), save()))
            primary("Save", save, "save").mark("default-save")
        editor = ui.codemirror(read_text(path)[0] if path.is_file() else "", language="Markdown",
                               line_wrapping=True).classes("w-full dw-editor grow").mark("default-editor")
    dialog.open()


def remove_dialog(name: str) -> None:
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[30rem] gap-3"):
        ui.label(f"Remove {name}?").classes("dw-h2")
        ui.label("No story will use it from now on. A copy is kept in the .history folder inside "
                 f"{folder()}.").classes("dw-small")

        def go() -> None:
            snapshot(folder(), [name], "remove")
            (folder() / name).unlink(missing_ok=True)
            roles = {n: r for n, r in load_roles().items() if n != name}
            save_roles(roles)
            dialog.close()
            toast(f"Removed {name}")
            smooth_reload()

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary("Remove", go)
    dialog.open()


def story_switch(project) -> None:
    """For Story files → Prompts: use your default instructions in this story, or not."""
    files = [f for f in load_defaults() if f.role != NOT_SENT and f.text.strip()]
    with card("Your default instructions", "Files you added once for every story (on the Default instructions "
                                           "screen)."):
        if not files:
            ui.label("You haven't added any yet.").classes("dw-small")
        else:
            ui.label(", ".join(f"{f.name} ({ROLE_LABELS[f.role].lower()})" for f in files)).classes("dw-small")

        def toggle(value: bool) -> None:
            set_story_default_instructions(project.root, value)
            toast("This story uses your default instructions" if value else
                  "This story no longer uses your default instructions")

        with ui.row().classes("items-center gap-3"):
            ui.switch("Use them in this story", value=project.config.default_instructions,
                      on_change=lambda e: toggle(bool(e.value))).mark("story-defaults-switch")
            quiet("Manage default instructions", lambda: ui.navigate.to("/defaults"), "rule")


def set_story_default_instructions(root: Path, on: bool) -> None:
    """Default instructions on or off for this story, in its Story settings."""
    from devils_workshop.project.story_settings import update_settings
    update_settings(root, "default-instructions-" + ("on" if on else "off"), default_instructions=on)

