"""Story files → Files: browse, add, edit, rename and delete the story's files (every change is kept in
History)."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from urllib.parse import quote

from nicegui import ui

from devils_workshop.context.render import render_entry
from devils_workshop.context.timeline import WritingPoint
from devils_workshop.history import write_project_file
from devils_workshop.project.file_ops import (
    ADD_AS,
    FileOpError,
    add_own_file,
    add_story_file,
    delete_file,
    duplicate_of,
    protected,
    rename_file,
)
from devils_workshop.project.files import read_text
from devils_workshop.project.issues import ERROR, WARNING
from devils_workshop.project.own_files import (
    DOCUMENTS,
    SCENES_FILE,
    chapter_of,
    extract_text,
    is_editable,
    list_story_files,
    make_text_copy,
)
from devils_workshop.project.project import Project, load_project
from devils_workshop.project.scopes import ASSIST, may_write
from devils_workshop.tools.plancheck import run_plan_check
from devils_workshop.ui.assist import ai_edit_dialog
from devils_workshop.ui.components import notice, primary, quiet, secondary, toast
from devils_workshop.ui.layout import native_window_available, pick_files
from devils_workshop.ui.state import STATE

LANGUAGES = {".yaml": "YAML", ".yml": "YAML", ".json": "JSON", ".toml": "TOML", ".xml": "XML", ".html": "HTML",
             ".htm": "HTML", ".css": "CSS"}  # the editor's highlighting; anything else as Markdown
FOLDERS = [  # folder -> heading in the tree
    ("bible", "Story bible"), ("characters", "Characters"), ("places", "Places"), ("lore", "Lore"),
    ("subplots", "Subplots"), ("chapters", "Written chapters"), ("notes", "Notes"), ("prompts", "Instructions"),
]
TOP_FILES = {"outline.md": "Outline", "system_prompt.md": "System prompt", "Story settings.md": "Story settings"}
TEMPLATES = {  # new-file kind -> (folder, template in the blank project)
    "Character": ("characters", "characters/_example-character.md"),
    "Place": ("places", "places/_example-place.md"),
    "Lore": ("lore", "lore/_example-lore.md"),
    "Subplot": ("subplots", "subplots/_example-subplot.md"),
    "Note": ("notes", None),
}
BIBLE_NAMES = {"style_guide.md": "Style guide", "story_bible.md": "Story bible", "world_rules.md": "World rules"}


def _friendly(project: Project, rel: str) -> str:
    """'characters/mara-venn.md' -> 'Mara Venn'; 'chapters/ch03.md' -> 'Chapter 3'."""
    entry = next((e for e in project.entries if e.path == rel), None)
    if entry:
        return entry.name
    subplot = next((s for s in project.subplots if s.path == rel), None)
    if subplot:
        return subplot.name
    name = rel.rsplit("/", 1)[-1]
    if name in BIBLE_NAMES:
        return BIBLE_NAMES[name]
    match = re.fullmatch(r"ch(\d+)\.md", name)
    if match:
        return f"Chapter {int(match.group(1))}"
    return Path(name).stem.replace("-", " ").replace("_", " ").strip().capitalize() or name


def file_tree(project: Project) -> list[dict]:
    root = project.root
    nodes = [{"id": name, "label": label} for name, label in TOP_FILES.items() if (root / name).is_file()]
    for folder, heading in FOLDERS:
        path = root / folder
        if not path.is_dir():
            continue
        children = []
        for item in sorted(path.rglob("*")):
            if item.is_file() and item.suffix in (".md", ".yaml", ".txt") and not item.name.endswith(".partial.md") \
                    and "versions" not in item.relative_to(root).parts:
                rel = item.relative_to(root).as_posix()
                children.append({"id": rel, "label": _friendly(project, rel)})
        if children:
            nodes.append({"id": f"{folder}/", "label": heading, "children": children})
    return nodes


def own_file_tree(root: Path) -> list[dict]:
    """Your own files (PDF and Word files too, shown read-only), plus story.yaml and what the app wrote."""
    names = list(list_story_files(root)[0])
    names += [n for n in ("Story settings.md", SCENES_FILE) if (root / n).is_file()]
    names += sorted(p.relative_to(root).as_posix() for p in (root / "chapters").glob("ch*.md")
                    if not p.name.endswith(".partial.md")) if (root / "chapters").is_dir() else []
    nodes, folders = [], {}
    for rel in sorted(set(names), key=lambda r: (r.count("/") > 0, r.lower())):
        folder, _, rest = rel.partition("/")
        if not rest:
            nodes.append({"id": rel, "label": rel})
            continue
        if folder not in folders:
            folders[folder] = {"id": f"{folder}/", "label": f"{folder}/", "children": []}
            nodes.append(folders[folder])
        folders[folder]["children"].append({"id": rel, "label": rest})
    return nodes


def _leaves(nodes: list[dict]) -> list[dict]:
    found = []
    for node in nodes:
        found += _leaves(node["children"]) if "children" in node else [node]
    return found


def files_panel(project: Project, path: str = "") -> None:
    root = project.root
    own = project.own_files
    tree_nodes = own_file_tree(root) if own else file_tree(project)
    first = next((n["id"] for n in tree_nodes if "children" not in n), "Story settings.md") if own else "outline.md"
    state = {"path": path or first, "saved": ""}

    leaves = {n["id"]: n["id"] for n in _leaves(tree_nodes)}

    def edit_as_text(rel: str) -> None:
        try:
            copy = make_text_copy(root, rel)
        except ValueError as exc:
            toast(str(exc), "warning")
            return
        toast(f"{copy} is the editable text of {Path(rel).name}, and is sent in its place from now on")
        ui.navigate.to(f"/story/files?path={quote(copy)}")
    with ui.row().classes("dw-phone-only w-full items-center no-wrap gap-1"):  # the tree, on a phone
        picker = ui.select(leaves, value=state["path"], label="File", with_input=True,
                           on_change=lambda e: open_file(e.value)).classes("grow min-w-0").mark("file-picker")
        ui.button(icon="add", on_click=lambda: new_file_dialog(root, own), color=None) \
            .props('flat round aria-label="New file"').classes("dw-quiet")
        ui.button(icon="upload_file", on_click=lambda: add_files_dialog(root, own), color=None) \
            .props('flat round aria-label="Add files"').classes("dw-quiet")
    with ui.element("div").classes("dw-cols tree"):
        with ui.card().classes("dw-card tight gap-2 dw-desktop-only").props("flat") \
                .style("position: sticky; top: 16px"):
            with ui.row().classes("items-center w-full gap-1"):
                ui.label("Files").classes("dw-h3")
                ui.space()
                quiet("New", lambda: new_file_dialog(root, own), "add").props("dense").tooltip(
                    "Write a new file here")
                quiet("Add", lambda: add_files_dialog(root, own), "upload_file").props("dense").tooltip(
                    "Add files from your computer: any text file, PDF or Word").mark("files-add")
            tree = ui.tree(tree_nodes, on_select=lambda e: open_file(e.value)) \
                .props("dense no-connectors selected-color=primary").classes("w-full")
            tree.expand()
            ui.label("Every change keeps a copy in History, so you can always go back.").classes("dw-small mt-2")
        with ui.card().classes("dw-card gap-3").props("flat"):
            with ui.row().classes("w-full items-center no-wrap gap-3 dw-wrap-phone"):
                with ui.column().classes("gap-0 min-w-0"):
                    name_label = ui.label().classes("dw-h2")
                    path_label = ui.label().classes("dw-small")
                ui.space()
                unsaved = ui.label("Unsaved changes").classes("dw-chip draft")
                preview_select = ui.select({}, label="Preview for chapter…").classes("w-56").tooltip(
                    "What the model is told about this character, place or lore when writing that chapter")
                ai_button = quiet("Ask AI to change", lambda: ai_edit_dialog(
                    state["path"], editor.value, lambda text: (editor.set_value(text), save()), root),
                    "auto_fix_high").props("dense").mark("ai-edit") \
                    .tooltip("Describe a change; the planner's model proposes it, and you see it before it is saved")
                save_button = primary("Save", lambda: save(), "save")
                with ui.button(icon="more_horiz", color=None).props('flat round aria-label="More actions"') \
                        .classes("dw-quiet").mark("file-actions") as more:
                    with ui.menu():
                        ui.menu_item("Rename…", lambda: rename_dialog(root, state["path"])).mark("file-rename")
                        if own:
                            ui.menu_item("Choose what it's for…", lambda: ui.navigate.to("/story/files?tab=sent"))
                        ui.menu_item("Delete…", lambda: delete_dialog(root, state["path"], name_label.text,
                                                                     own)).mark("file-delete")
            editor = ui.codemirror("", language="Markdown", line_wrapping=True).classes("w-full dw-editor") \
                .style("min-height: 62vh")
            read_only = ui.column().classes("w-full gap-3")
            preview = ui.label().classes("dw-raw")
            preview.set_visibility(False)

    def mark_dirty() -> None:
        dirty = editor.value != state["saved"]
        unsaved.set_visibility(dirty)

    def open_file(rel: str | None) -> None:
        if not rel or rel.endswith("/"):
            return
        target = root / rel
        if not target.is_file():
            return
        state["path"] = rel
        tree.props(f'selected="{rel}"')
        if picker.value != rel and rel in picker.options:
            picker.value = rel
        name_label.text = _friendly(project, rel) if not own else Path(rel).name
        path_label.text = rel
        editable = is_editable(target)
        state["saved"] = read_text(target)[0] if editable else ""
        editor.value = state["saved"]
        editor.language = LANGUAGES.get(target.suffix.lower(), "Markdown")
        editor.set_visibility(editable)
        save_button.set_visibility(editable)
        ai_button.set_visibility(editable and may_write(ASSIST, rel))  # never the story's text
        read_only.clear()
        if not editable:
            with read_only:
                _read_only_view(target, lambda r=rel: edit_as_text(r))
        more.set_visibility(not protected(rel))
        is_entry = not own and rel.split("/", 1)[0] in ("characters", "places", "lore")
        chapters = {c.number: f"Chapter {c.number}" for c in STATE.project().outline.chapters}
        preview_select.options = chapters if is_entry else {}
        preview_select.value = None
        preview_select.update()
        preview_select.set_visibility(is_entry)
        preview.set_visibility(False)
        mark_dirty()

    def show_preview(chapter: int | None) -> None:
        if chapter is None:
            preview.set_visibility(False)
            return
        current = STATE.project()
        entry = next((e for e in current.entries if e.path == state["path"]), None)
        if entry is None:
            preview.text = "Save the file first (or it couldn't be read; see Problems)."
        else:
            preview.text = (f"What the model is told about {entry.name} when writing chapter {chapter} "
                            f"(later changes and secrets left out):\n\n" + render_entry(entry, WritingPoint(chapter)))
        preview.set_visibility(True)

    def save() -> None:
        rel = state["path"]
        write_project_file(root, rel, editor.value, f"edit-{rel}")
        state["saved"] = editor.value
        mark_dirty()
        report = run_plan_check(STATE.project())
        problems = [i for i in report.issues if i.file == rel and i.severity in (ERROR, WARNING)]
        if problems:
            toast(f"Saved, but there's a problem in this file: {problems[0].message}", "warning")
        else:
            toast("Saved")

    editor.on_value_change(lambda: mark_dirty())
    preview_select.on_value_change(lambda e: show_preview(e.value))
    save_button.tooltip("Save this file")
    open_file(state["path"])


def _read_only_view(path: Path, as_text=None) -> None:
    """A PDF or Word file: what the app reads from it. Edit as text makes an editable copy that takes its place."""
    kind = "Word" if path.suffix.lower() == ".docx" else "PDF"
    text, problem = extract_text(path)
    if path.suffix.lower() in DOCUMENTS and as_text is not None and text.strip():
        notice("info", f"A {kind} file can't be edited here. Edit as text saves the text below as a .md file "
                       "you can edit, which is sent in its place from now on (the original is kept, not sent).",
               "Edit as text", as_text)
    else:
        notice("info", f"{kind} files can't be edited here. Edit it in its own program; the app reads the latest "
                       "version each time it sends a request.")
    if problem:
        notice("problem", problem)
    ui.label(f"The text the app reads from it ({len(text.split()):,} words):" if text.strip() else
             "The app found no text in it.").classes("dw-small")
    if text.strip():
        ui.label(text).classes("dw-raw").style("max-height: 56vh; overflow: auto")


def new_file_dialog(root: Path, own: bool = False) -> None:
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[30rem] gap-3"):
        ui.label("New file").classes("dw-h2")
        kind = ui.select(list(TEMPLATES), value="Character", label="Kind").classes("w-full")
        kind.set_visibility(not own)
        name = ui.input("File name (e.g. Chapter 4 notes)" if own else "Name (e.g. Mara Venn)").classes("w-full")
        if own:
            ui.label("Then choose what it is for under What's sent.").classes("dw-small")

        def create() -> None:
            title = (name.value or "").strip()
            if not title:
                toast("Give it a name", "warning")
                return
            if own:  # your own folder: a plain file, named as you typed it
                folder, template = "", None
                rel = "".join(c for c in title if c not in '<>:"/\\|?*').strip() + ".md"
            else:
                folder, template = TEMPLATES[kind.value]
                slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "untitled"
                rel = f"{folder}/{slug}.md"
            if (root / rel).exists():
                toast(f"{rel} already exists", "warning")
                return
            if template:
                text = (resources.files("devils_workshop") / "defaults" / "blank_project" / template).read_text(
                    encoding="utf-8")
                text = re.sub(r"^# Files whose names.*\n", "", text, flags=re.MULTILINE)
                text = re.sub(r"^name: .*$", f"name: {title}", text, count=1, flags=re.MULTILINE)
            else:
                text = f"# {title}\n\n"
            write_project_file(root, rel, text, f"new-{rel}")
            dialog.close()
            ui.navigate.to(f"/story/files?path={quote(rel)}")

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary("Create", create)
    dialog.open()


def _role_label(role: str) -> str:
    from devils_workshop.ui.setup import ROLE_LABELS
    number = chapter_of(role)
    return f"chapter {number} only" if number else ROLE_LABELS.get(role, role).lower()


def add_files_dialog(root: Path, own: bool) -> None:
    """Add files from your computer: into your own folder as they are, or (a story made by the app) as a
    character, place, the outline…"""
    added: list[str] = []
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[34rem] gap-4"):
        ui.label("Add files").classes("dw-h2")
        kind = None
        if own:
            ui.label("Copies files into this story's folder: any text file (.md, .txt, .json, .yaml, .csv…), PDF or Word. Each new file is sent the "
                     "way its name and text suggest; you can change that under What's sent. A file with the "
                     "same name is replaced (the old one stays in History).").classes("dw-small")
        else:
            kind = ui.select({k: label for k, (label, _, _) in ADD_AS.items()}, value="character",
                             label="Add it as").classes("w-full").mark("add-as")
            ui.label("Any text file (.md, .txt, .json, .yaml, .csv…), PDF or Word; each is saved as text. Each file added can be "
                     "undone in History.").classes("dw-small")

        def store(name: str, data: bytes) -> None:
            try:
                if own:
                    rel, role = add_own_file(root, name, data)
                    copy = duplicate_of(root, rel, load_project(root).config.own_files or {})
                    toast(f"Added {rel}: the same document as {copy}, so it isn't sent (sending both would give the "
                          "model everything twice)" if copy else f"Added {rel} (sent as: {_role_label(role)})",
                          "warning" if copy else "positive")
                else:
                    label, _, replaces = ADD_AS[kind.value]
                    if replaces and any(r == replaces for r in added):
                        toast(f"Only one file can be {label.split(' (')[0].lower()}; {name} wasn't added", "warning")
                        return
                    rel = add_story_file(root, name, data, kind.value)
                    toast(f"Added {name} as {label.split(' (')[0].lower()}")
                added.append(rel)
            except FileOpError as exc:
                toast(str(exc), "warning")

        def finish() -> None:
            dialog.close()
            if added:
                ui.navigate.to("/story/files?tab=sent" if own else f"/story/files?path={quote(added[-1])}")

        async def received(event) -> None:
            store(event.file.name, await event.file.read())

        native = native_window_available()
        upload = ui.upload(multiple=True, auto_upload=True, on_upload=received).props(
            'flat bordered').classes("w-full").mark("files-upload")
        upload.set_visibility(not native)

        async def choose() -> None:
            chosen = await pick_files()
            if chosen is None:  # the window's own file picker isn't available: choose in the page instead
                upload.set_visibility(True)
                return
            for item in chosen:
                source = Path(item)
                try:
                    data = source.read_bytes()
                except OSError as exc:
                    toast(f"{source.name} couldn't be read: {exc}", "warning")
                    continue
                store(source.name, data)
            if added:
                finish()

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            if native:
                secondary("Done", finish)
                primary("Choose files…", choose, "upload_file")
            else:
                primary("Done", finish).mark("files-add-done")
    dialog.open()


def rename_dialog(root: Path, rel: str) -> None:
    path = Path(rel)
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[30rem] gap-3"):
        ui.label(f"Rename {path.name}").classes("dw-h2")
        name = ui.input("New name", value=path.stem).classes("w-full").mark("rename-name")
        notes = [f"It stays a {path.suffix} file in the same folder."]
        if rel.split("/", 1)[0] in ("characters", "places", "lore", "subplots"):
            notes.append("The name the story uses comes from the 'name:' line inside the file, so that doesn't "
                         "change.")
        ui.label(" ".join(notes + ["Undo it in History if you change your mind."])).classes("dw-small")

        def go() -> None:
            try:
                new_rel = rename_file(root, rel, name.value or "")
            except FileOpError as exc:
                toast(str(exc), "warning")
                return
            dialog.close()
            toast(f"Renamed to {Path(new_rel).name}")
            ui.navigate.to(f"/story/files?path={quote(new_rel)}")

        name.on("keydown.enter", go)
        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary("Rename", go).mark("rename-go")
    dialog.open()


def delete_dialog(root: Path, rel: str, label: str, own: bool) -> None:
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[30rem] gap-3"):
        ui.label(f"Delete {label}?").classes("dw-h2")
        listed = own and rel in (STATE.project().config.own_files or {})
        ui.label(f"{rel} is deleted from the story folder" + (" and is no longer sent" if listed else "") +
                 ". You can bring it back with Undo in History.").classes("dw-small")

        def go() -> None:
            try:
                delete_file(root, rel)
            except FileOpError as exc:
                toast(str(exc), "warning")
                return
            dialog.close()
            toast(f"Deleted {Path(rel).name}. Undo it in History if you need it back.")
            ui.navigate.to("/story/files")

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary("Delete", go, "delete_outline").props("color=negative").mark("delete-go")
    dialog.open()
