"""Your own files: use a folder of your own story files as it is.

Shown when you open a folder that has no Story settings yet, and later as Story files →
What's sent, to change what each file is for. Nothing in your files changes: the
list is saved in Story settings.md in the same folder.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from devils_workshop.history import write_project_file
from devils_workshop.project.duplicates import find_duplicates
from devils_workshop.project.files import read_text
from devils_workshop.project.own_files import (
    EVERY,
    NOT_SENT,
    OUTLINE,
    STORY_FILE,
    SYSTEM,
    chapter_of,
    extract_text,
    guess_role,
    list_story_files,
    split_at_headings,
    settings_text,
)
from devils_workshop.project.project import NotAProjectError, load_project
from devils_workshop.project.story_config import StoryConfig
from devils_workshop.ui.components import card, notice, page, page_header, primary, secondary, toast
from devils_workshop.ui.layout import frame
from devils_workshop.ui.state import STATE

ROLE_LABELS = {EVERY: "Every chapter", OUTLINE: "Chapter outline", SYSTEM: "System prompt", NOT_SENT: "Not sent"}
ROLE_HELP = ("Every chapter: sent with every chapter. Chapter outline: split at its 'Chapter 1', 'Chapter 2'… "
             "headings, and each chapter gets only its part. Chapter N only: the instructions for that one chapter. "
             "System prompt: who the model should be and the rules for the whole book. Not sent: never sent.")
FILE_ICONS = {".pdf": "picture_as_pdf", ".docx": "description", ".txt": "notes", ".md": "article",
              ".json": "data_object", ".yaml": "data_object", ".yml": "data_object", ".csv": "table_chart"}


def role_options(highest: int) -> dict[str, str]:
    options = dict(list(ROLE_LABELS.items())[:3])
    options.update({f"chapter {n}": f"Chapter {n} only" for n in range(1, max(60, highest + 20) + 1)})
    options[NOT_SENT] = ROLE_LABELS[NOT_SENT]
    return options


def setup_page(folder: str = "") -> None:
    """A folder without story.yaml: choose what each file is for, then open it."""
    if not folder:
        ui.navigate.to("/story/files?tab=sent")
        return
    frame()
    root = Path(folder)
    with page("medium"):
        with page_header("Use this folder for a story", str(root), eyebrow="Your own files"):
            pass
        if not root.is_dir():
            notice("problem", f"Folder not found: {root}")
            return
        your_files_panel(root, None)


def your_files_panel(root: Path, config: StoryConfig | None) -> None:
    readable, other = list_story_files(root)
    texts = {rel: extract_text(root / rel) for rel in readable}
    roles = {}
    for rel in readable:
        known = config.own_files.get(rel) if config and config.own_files else None
        text, problem = texts[rel]
        roles[rel] = known or (NOT_SENT if problem and not text.strip() else guess_role(rel, text))
    new_files = {rel for rel in readable if config and config.own_files is not None and rel not in config.own_files}
    # A file that is the same document as another (e.g. the .docx of a .md) starts as Not sent: sending both
    # would give the model everything twice. Only files whose role is being guessed now.
    guessed = set(readable) if config is None or config.own_files is None else new_files
    copies = {}
    for found in find_duplicates({rel: texts[rel][0] for rel in readable if roles[rel] != NOT_SENT}):
        if found.copy in guessed:
            roles[found.copy] = NOT_SENT
            copies[found.copy] = found.keep
    highest = max([chapter_of(r) or 0 for r in roles.values()] +
                  [len(split_at_headings(t)[1]) for t, _ in texts.values()] + [0])
    options = role_options(highest)

    with ui.row().classes("w-full items-center no-wrap gap-4"):
        ui.label("Your files stay exactly as they are. Choose what each one is for; the app keeps its own work "
                 "(the chapters it writes, its records) in separate folders here."
                 + (" To rename or delete a file, open it under Files." if config else "")).classes("dw-subtitle")
        if config:  # set up already, so a file added now can go straight on the list
            from devils_workshop.ui.files import add_files_dialog
            secondary("Add files…", lambda: add_files_dialog(root, True), "upload_file").classes("flex-none")
    if config and config.own_files is not None:  # a file sent twice already: say so loudly, with the fix
        from devils_workshop.ui.duplicates import duplicates_notice
        duplicates_notice(load_project(root))
    with card(classes="gap-4"):
        with ui.row().classes("gap-4 items-end w-full"):
            title = ui.input("Story title", value=config.title if config else root.name).classes("grow")
            words = ui.number("Words per chapter", value=config.chapter_words if config else 3000, min=100,
                              precision=0).classes("w-48")

    if not readable:
        notice("info", "This folder has no files the app can read yet (text files, PDF or Word). To start a story from "
                       "scratch, "
                       "use 'New story' on the All stories screen instead.")
    else:
        with ui.element("div").classes("dw-list"):
            for rel in readable:
                text, problem = texts[rel]
                with ui.element("div").classes("dw-row"):
                    ui.icon(FILE_ICONS.get(Path(rel).suffix.lower(), "description")).classes("text-xl").style(
                        "color: var(--dw-faint)")
                    with ui.column().classes("gap-0 grow min-w-0"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(rel).classes("dw-body")
                            if rel in new_files:
                                ui.label("New").classes("dw-chip writing")
                            if rel in copies:
                                ui.label(f"Copy of {copies[rel]}, so not sent").classes("dw-chip draft") \
                                    .mark("copy-chip")
                        ui.label(_describe(text, problem)).classes("dw-small").style(
                            "color: var(--dw-problem)" if problem else "")
                    ui.select(options, value=roles[rel], with_input=True,
                              on_change=lambda e, r=rel: (roles.__setitem__(r, e.value), summary.refresh())) \
                        .classes("w-60")
    if readable:
        ui.label(ROLE_HELP).classes("dw-small")
    if other:
        notice("info", "These aren't text files (pictures, archives…), so nothing can be sent from them: "
                       + ", ".join(other))

    @ui.refreshable
    def summary() -> None:
        ui.label(_summary(roles, texts)).classes("dw-body")

    with ui.row().classes("w-full items-center gap-4"):
        summary()
        ui.space()

        def save() -> None:
            name = (title.value or "").strip() or root.name
            existing = read_text(root / STORY_FILE)[0] if (root / STORY_FILE).is_file() else ""
            chosen = {rel: role for rel, role in roles.items() if role}
            write_project_file(root, STORY_FILE, settings_text(chosen, name, int(words.value or 3000), existing),
                               "your-files")
            try:
                STATE.open_story(root)
            except NotAProjectError as exc:
                toast(str(exc), "negative")
                return
            toast("Saved")
            ui.navigate.to("/story")

        primary("Save and open" if config is None else "Save", save, "check")


def _describe(text: str, problem: str) -> str:
    parts = [f"{len(text.split()):,} words"] if text.strip() else []
    headings = len(split_at_headings(text)[1])
    if headings:
        parts.append(f"{headings} chapter heading{'s' if headings != 1 else ''}")
    if problem:
        parts.append(problem)
    return " · ".join(parts) or "empty"


def _summary(roles: dict[str, str], texts: dict[str, tuple[str, str]]) -> str:
    chapters: set[int] = set()
    every, system = 0, []
    for rel, role in roles.items():
        if role == OUTLINE:
            chapters |= {p.number for p in split_at_headings(texts[rel][0])[1]}
        elif role == EVERY:
            every += 1
        elif role == SYSTEM:
            system.append(rel)
        elif chapter_of(role or ""):
            chapters.add(chapter_of(role))
    found = _ranges(sorted(chapters)) if chapters else "none yet"
    return (f"Chapters: {found}  ·  sent with every chapter: {every} file{'s' if every != 1 else ''}  ·  "
            f"system prompt: {', '.join(system) or 'none'}")


def _ranges(numbers: list[int]) -> str:
    """[1, 2, 3, 5] -> "1–3, 5"."""
    spans, start = [], numbers[0]
    for prev, cur in zip(numbers, numbers[1:] + [None]):
        if cur != prev + 1:
            spans.append(f"{start}–{prev}" if prev > start else str(start))
            start = cur
    return ", ".join(spans)
