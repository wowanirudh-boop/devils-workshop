"""Read: the whole story, or one chapter, set for reading (scenes joined, no labels)."""

from __future__ import annotations

import os
import re
import sys

from nicegui import run, ui

from devils_workshop.project.revisions import CURRENT, ExportError, exports, save_export
from devils_workshop.tools.pdf import PAGE_SIZES, TEXT_SIZES, PdfOptions, story_pdf
from devils_workshop.tools.reading import READER_JS, reader_html, reading_chapters, standalone_html
from devils_workshop.ui.copying import copy_button
from devils_workshop.ui.components import empty_state, notice, page, page_header, primary, quiet, secondary, toast
from devils_workshop.ui.layout import frame, need_story
from devils_workshop.ui.manuscript import manuscript_card
from devils_workshop.ui.phone_access import from_phone
from devils_workshop.ui.review_panel import review_panel

EXPORTS = "exports"


def reader_page(chapter: int | None = None) -> None:
    project = frame("/story/read")
    if project is None:
        need_story()
        return
    ui.add_head_html(f"<script>{READER_JS}</script>")
    written = reading_chapters(project)
    missing = chapter if chapter and chapter not in {c.number for c in written} else None
    chapter = None if missing else chapter
    with page("wide") as column:
        with page_header("Read", "The story as a reader will see it: chapters in order, scenes joined.") as actions:
            if written:
                options = {0: "Whole story", **{c.number: f"Chapter {c.number}" + (f": {c.title}" if c.title else "")
                                                for c in written}}
                ui.select(options, value=chapter or 0, on_change=lambda e: ui.navigate.to(
                    "/story/read" + (f"?chapter={e.value}" if e.value else ""))).classes("w-72 dw-full-phone")
                label = "Download…" if from_phone() else "Save as PDF…"
                primary(label, lambda: save_dialog(project, chapter, column), "picture_as_pdf").mark("reader-save") \
                    .tooltip("The story as a PDF book (or one web page), to read anywhere")
        if missing:
            notice("info", f"Chapter {missing} isn't written yet, so here is the whole story.")
        out_of_date = [(p, s) for p, s in exports(project) if s.state != CURRENT]
        if out_of_date:
            notice("warning", "Saved copies that aren't the story as it is now: " + "; ".join(
                f"{p.name} ({s.detail})" for p, s in out_of_date) + ". Save again for the current text.").mark(
                "reader-stale-exports")
        if not written:
            with empty_state("auto_stories", "Nothing to read yet",
                             "Chapters appear here as soon as they are written."):
                primary("Go to chapters", lambda: ui.navigate.to("/story/chapter"))
            return
        if chapter:  # one chapter: read it, and ask for changes beside it
            with ui.element("div").classes("dw-cols main-side"):
                text = ui.html(reader_html(project, chapter), sanitize=False).classes("w-full dw-reader-box")
                with ui.column().classes("gap-4 min-w-0"):
                    review_panel(project, chapter)
        else:
            manuscript_card(project)
            text = ui.html(reader_html(project, chapter), sanitize=False).classes("w-full dw-reader-box")
            ui.label("Choose a chapter above to read it on its own and ask for changes.").classes("dw-small")
        with actions:
            copy_button(source=text, label="Copy text", tip="Copy what is shown here").mark("copy-reader") \
                .move(actions, 0)
    ui.timer(0.1, lambda: ui.run_javascript("dwReader.apply()"), once=True)


def _file_name(project, suffix: str = ".html", chapter: int | None = None) -> str:
    name = re.sub(r'[<>:"/\\|?*]+', "", project.config.title).strip() or "Story"
    return name + (f" - chapter {chapter}" if chapter else "") + suffix


def save_file(project, quiet: bool = False):
    """The whole story as one web page, saved with the record of which text of each chapter it has."""
    path = project.root / EXPORTS / _file_name(project)
    save_export(project, path, "html", standalone_html)
    if not quiet:
        toast(f"Saved {path}")
    return path


def save_dialog(project, chapter: int | None = None, page_column=None) -> None:
    """PDF (page size and text size) or one web page; saved in the story's exports folder, and on a phone
    downloaded to the phone as well."""
    phone = from_phone()
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[30rem] max-w-full gap-3"):
        ui.label("Download the story" if phone else "Save the story").classes("dw-h2")
        kind = ui.toggle({"pdf": "PDF book", "html": "Web page (.html)"}, value="pdf").mark("reader-kind")
        with ui.column().classes("gap-3 w-full") as pdf_options:
            with ui.row().classes("gap-3 w-full no-wrap"):
                page_size = ui.select(PAGE_SIZES, value="A5", label="Page size").classes("grow").mark("reader-page")
                text_size = ui.select(TEXT_SIZES, value=11.5, label="Text size").classes("grow").mark("reader-text")
            what = ui.select({0: "Whole story", **({chapter: f"Chapter {chapter} only"} if chapter else {})},
                             value=chapter or 0, label="What").classes("w-full")
        help_text = ui.label().classes("dw-small")

        def describe() -> None:
            pdf_options.set_visibility(kind.value == "pdf")
            help_text.text = ("A book: title page, contents, each chapter on a new page. A5 reads well on a phone."
                              if kind.value == "pdf" else
                              "One file with the whole story, with the reader's text size and themes, for any "
                              "browser.")

        kind.on_value_change(describe)
        describe()

        async def go() -> None:
            try:
                if kind.value == "html":
                    path = project.root / EXPORTS / _file_name(project)
                    data = save_export(project, path, "html", standalone_html)
                    mime = "text/html"
                else:
                    options = PdfOptions(page=page_size.value, size=float(text_size.value),
                                         chapter=what.value or None)
                    path = project.root / EXPORTS / _file_name(project, ".pdf", options.chapter)
                    data = await run.io_bound(save_export, project, path, "pdf",  # a long story takes a moment
                                              lambda p: story_pdf(p, options), options.chapter)
                    mime = "application/pdf"
            except ExportError as exc:
                toast(str(exc), "negative")
                return
            dialog.close()
            with page_column or ui.context.client.content:  # the page, not the dialog just closed
                if phone:
                    ui.download.content(data, path.name, mime)
                    return
                saved(path)

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary("Download" if phone else "Save", go, "download").mark("reader-save-go")
    dialog.on_value_change(lambda e: None if e.value else dialog.delete())  # gone once closed
    dialog.open()


def saved(path) -> None:
    """Where it went, with a way to open it (on this PC)."""
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[30rem] max-w-full gap-3"):
        ui.label("Saved").classes("dw-h2")
        ui.label(str(path)).classes("dw-small dw-pre").mark("reader-saved")
        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Close", dialog.close)
            if sys.platform == "win32":
                secondary("Show in folder", lambda: (os.startfile(path.parent), dialog.close()), "folder_open")
                primary("Open it", lambda: (os.startfile(path), dialog.close()), "open_in_new")
    dialog.on_value_change(lambda e: None if e.value else dialog.delete())
    dialog.open()
