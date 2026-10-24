"""The story as a PDF book, and the reader's settings (size, spacing, width, full page)."""

import io

from pypdf import PdfReader

from devils_workshop.project.project import load_project
from devils_workshop.tools import pdf as pdf_module
from devils_workshop.tools.pdf import PdfOptions, _paragraphs, _pieces, story_pdf
from devils_workshop.tools.reading import READER_JS, reader_html, standalone_html

from tests.conftest import write

CHAPTER_TWO = ("The ledger room smelled of brine. *Ada* was already talking, **fast**, about the "
               "harbour’s “missing page” — and nobody listened…\n\nSecond paragraph of chapter two.\n\n* * *\n\n"
               "After the break, the tide came in. ") * 3


def written(sample):
    """The sample story with chapters 1 (as it comes) and 2 written."""
    write(sample, "chapters/ch02.md", "---\nchapter: 2\nstatus: draft\nversion: 1\n---\n" + CHAPTER_TWO)
    return load_project(sample)


def read(data: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(data))


def text(reader: PdfReader) -> str:
    return " ".join(" ".join(page.extract_text() for page in reader.pages).split())  # justified lines: wide gaps


def test_the_whole_story_as_a_pdf_book(sample):
    project = written(sample)
    book = read(story_pdf(project))
    assert book.metadata.title == "The Saltmarsh Clock"
    assert [entry.title for entry in book.outline] == ["Chapter 1: The Stopped Clock", "Chapter 2: Ink and Salt"]
    pages = [page.extract_text() for page in book.pages]
    assert "The Saltmarsh Clock" in pages[0] and "2 chapters" in pages[0]  # title page
    assert "Contents" in pages[1] and "Ink and Salt" in pages[1]          # contents, with page numbers
    everything = text(book)
    assert "Brindle Cove woke" in everything and "The ledger room smelled of brine" in everything
    assert "harbour" in everything and "After the break" in everything
    assert "*Ada*" not in everything and "**fast**" not in everything  # emphasis is styled, not shown as stars


def test_one_chapter_and_page_sizes(sample):
    project = written(sample)
    one = read(story_pdf(project, PdfOptions(chapter=2, page="A4", size=13.0)))
    assert [entry.title for entry in one.outline] == ["Chapter 2: Ink and Salt"]
    assert "Brindle Cove" not in text(one)
    a5 = read(story_pdf(project, PdfOptions(page="A5")))
    assert float(one.pages[0].mediabox.width) > float(a5.pages[0].mediabox.width)  # A4 is wider than A5


def test_without_a_unicode_font_the_text_still_comes_out(sample, monkeypatch):
    monkeypatch.setattr(pdf_module, "_font_files", lambda: None)  # e.g. a computer without Georgia or DejaVu
    everything = text(read(story_pdf(written(sample))))
    assert "harbour's \"missing page\" -- and nobody listened..." in everything


def test_an_empty_story(tmp_path):
    from devils_workshop.project.newproject import create_project
    project = load_project(create_project(tmp_path / "blank", title="Blank"))
    assert "Nothing is written yet." in text(read(story_pdf(project)))


def test_paragraphs_and_emphasis_like_the_reader():
    assert _paragraphs("One\nline.\n\nTwo.\n\n* * *\n\nThree.") == ["One line.", "Two.", "\x00break", "Three."]
    assert _paragraphs("A\nB\nC") == ["A", "B", "C"]  # no blank lines: one paragraph per line
    assert _pieces("She *ran*, **fast**, _away_.") == [("", "She "), ("I", "ran"), ("", ", "), ("B", "fast"),
                                                      ("", ", "), ("I", "away"), ("", ".")]


def test_reader_settings(sample):
    html = reader_html(written(sample))
    for control in ("dwReader.panel()", "dwReader.full()", "dwReader.size(-1)", "dwReader.spacing('tight')",
                    "dwReader.width('full')", "dwReader.theme('sepia')", "dwReader.scenes()", "Exit full page"):
        assert control in html
    for rule in ('data-width="full"] .dwr-book { max-width: none', ".dwr-reader.dwr-fullpage { position: fixed",
                 "overflow-wrap: anywhere"):
        assert rule in html
    assert "phone() ? 17 : 20" in READER_JS and "phone() ? \"full\" : \"narrow\"" in READER_JS
    assert "dwReader.full()" in standalone_html(load_project(sample))  # the saved web page has them too
