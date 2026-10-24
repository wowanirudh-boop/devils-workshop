"""The story as a PDF book: a title page, contents, each chapter on a new page, scenes joined by a break.

The text is the same as the Read screen's (tools/reading.py): only the current version of each
written chapter, paragraphs justified with the first line indented, *italics* and **bold** kept.
Fonts: Georgia, which every Windows PC has (so curly quotes, dashes and accented letters come out
right); DejaVu Serif on Linux; otherwise the PDF's built-in Times, with the few characters it
lacks replaced by plain ones.
"""

from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass
from pathlib import Path

from devils_workshop.project.project import Project
from devils_workshop.tools.reading import _BREAK, ReadingChapter, reading_chapters

PAGE_SIZES = {"A5": "A5 (good on phones)", "A4": "A4", "Letter": "US Letter"}
TEXT_SIZES = {10.0: "Small", 11.5: "Medium", 13.0: "Large", 15.0: "Extra large"}
_INLINE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])"
                     r"|(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])")
_PLAIN = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2014": "--", "\u2013": "-",
                        "\u2026": "...", "\u2042": "* * *", "\u00a0": " "})


@dataclass
class PdfOptions:
    page: str = "A5"
    size: float = 11.5          # body text, in points
    chapter: int | None = None  # just this chapter (None: the whole story)


def _font_files() -> dict[str, Path] | None:
    """The regular, italic, bold and bold-italic files of a serif font with the characters prose needs."""
    windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    candidates = [
        {"": windows / "georgia.ttf", "I": windows / "georgiai.ttf", "B": windows / "georgiab.ttf",
         "BI": windows / "georgiaz.ttf"},
        {"": Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
         "I": Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"),
         "B": Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
         "BI": Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf")},
    ]
    for files in candidates:
        if all(path.is_file() for path in files.values()):
            return files
    return None


def _pieces(text: str) -> list[tuple[str, str]]:
    """(style, text) runs of one paragraph: "", "I" or "B"."""
    pieces, at = [], 0
    for match in _INLINE.finditer(text):
        if match.start() > at:
            pieces.append(("", text[at:match.start()]))
        if match.group(1):
            pieces.append(("B", match.group(1)))
        else:
            pieces.append(("I", match.group(2) or match.group(3)))
        at = match.end()
    if at < len(text):
        pieces.append(("", text[at:]))
    return pieces


def _paragraphs(text: str) -> list[str]:
    """The same paragraph rules as the Read screen: blank lines separate paragraphs (or one per line if
    there are none), a single line break inside a paragraph is a space, and "* * *" lines are breaks."""
    text = text.strip().replace("\r\n", "\n")
    if not text:
        return []
    blocks = re.split(r"\n\s*\n", text) if re.search(r"\n\s*\n", text) else text.split("\n")
    out = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if _BREAK.match(block):
            out.append("\x00break")
        else:
            out.append(" ".join(line.strip() for line in block.lstrip("#").split("\n")))
    return out


def story_pdf(project: Project, options: PdfOptions | None = None) -> bytes:
    from fpdf import FPDF

    options = options or PdfOptions()
    chapters = [c for c in reading_chapters(project) if options.chapter is None or c.number == options.chapter]
    title = project.config.title or "Story"
    files = _font_files()
    clean = (lambda s: s) if files else (lambda s: s.translate(_PLAIN).encode("latin-1", "replace").decode("latin-1"))
    body = options.size
    break_mark = "\u2042" if files and files[""].name.lower().startswith("dejavu") else "*   *   *"

    class Book(FPDF):
        def footer(self) -> None:
            if self.page_no() > 1:
                self.set_y(-14)
                self.set_font(family, "", body * 0.75)
                self.set_text_color(130, 125, 118)
                self.cell(0, 6, str(self.page_no() - 1), align="C")
                self.set_text_color(0, 0, 0)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # fpdf2's note that A5's size was corrected in an old release
        pdf = Book(format=options.page if options.page in PAGE_SIZES else "A5")
    pdf.set_title(title)
    pdf.set_creator("Devil's Workshop")
    if files:
        family = "Body"
        for style, path in files.items():
            pdf.add_font(family, style, str(path))
    else:
        family = "Times"
    margin = 18 if options.page == "A5" else 25
    pdf.set_margins(margin, margin, margin)
    pdf.set_auto_page_break(True, margin + 2)
    line = body * 0.3528 * 1.45  # points to mm, times the line spacing

    # Title page
    pdf.add_page()
    pdf.set_y(pdf.h * 0.33)
    pdf.set_font(family, "", body * 2.3)
    pdf.multi_cell(0, body * 0.3528 * 2.3 * 1.25, clean(title), align="C")
    words = sum(c.words for c in chapters)
    pdf.ln(6)
    pdf.set_font(family, "I", body)
    what = f"Chapter {chapters[0].number}" if options.chapter and chapters else \
        f"{len(chapters)} chapter{'s' if len(chapters) != 1 else ''}"
    pdf.multi_cell(0, line, clean(f"{what} \u00b7 {words:,} words"), align="C")

    # Contents, filled in once the chapters' pages are known
    if len(chapters) > 1:
        pdf.add_page()

        def contents(book, outline) -> None:
            book.set_xy(book.l_margin, book.t_margin + 10)
            book.set_font(family, "", body * 1.4)
            book.cell(0, line * 2, "Contents", align="C", new_x="LMARGIN", new_y="NEXT")
            book.ln(4)
            book.set_font(family, "", body)
            for entry in outline:
                book.cell(0, line * 1.3, clean(entry.name), new_x="LMARGIN", new_y="LAST")
                book.cell(0, line * 1.3, str(entry.page_number - 1), align="R", new_x="LMARGIN", new_y="NEXT")

        pdf.insert_toc_placeholder(contents, pages=max(1, len(chapters) // 28 + 1))

    for index, chapter in enumerate(chapters):  # after the contents, the next page is already there
        _chapter(pdf, chapter, family, body, line, clean, break_mark, new_page=not (index == 0 and len(chapters) > 1))
    if not chapters:
        pdf.add_page()
        pdf.set_font(family, "I", body)
        pdf.multi_cell(0, line, "Nothing is written yet.", align="C")
    return bytes(pdf.output())


def _chapter(pdf, chapter: ReadingChapter, family: str, body: float, line: float, clean, break_mark: str,
             new_page: bool = True) -> None:
    if new_page:
        pdf.add_page()
    name = f"Chapter {chapter.number}" + (f": {chapter.title}" if chapter.title else "")
    pdf.start_section(clean(name))
    pdf.set_y(pdf.t_margin + 18)
    pdf.set_font(family, "", body * 0.8)
    pdf.set_text_color(130, 125, 118)
    pdf.cell(0, line, f"CHAPTER {chapter.number}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    if chapter.title:
        pdf.set_font(family, "", body * 1.6)
        pdf.multi_cell(0, body * 0.3528 * 1.6 * 1.3, clean(chapter.title), align="C")
    pdf.ln(line * 1.5)
    pdf.set_font(family, "", body)
    indent = body * 0.3528 * 1.6
    with pdf.text_columns(text_align="J", line_height=1.45) as columns:
        first = True
        for index, scene in enumerate(chapter.scenes):
            paragraphs = _paragraphs(scene.text)
            if index and paragraphs:
                paragraphs.insert(0, "\x00break")
            for paragraph in paragraphs:
                if paragraph == "\x00break":
                    with columns.paragraph(text_align="C", top_margin=line * 0.6, bottom_margin=line * 0.6) as par:
                        par.write(break_mark)
                    first = True
                    continue
                with columns.paragraph(first_line_indent=0 if first else indent) as par:
                    for style, text in _pieces(paragraph):
                        pdf.set_font(family, style, body)
                        par.write(clean(text))
                    pdf.set_font(family, "", body)
                first = False
