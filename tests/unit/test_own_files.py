"""Stories kept in your own files: headings, guessing, loading, what is sent, scenes, plan-check."""

from pathlib import Path

import pytest

from devils_workshop.chat.scenes import ProposedScene, apply_scene_proposal
from devils_workshop.context.assemble import assemble
from devils_workshop.project.issues import IssueList
from devils_workshop.project.own_files import (
    SCENES_FILE,
    extract_text,
    guess_role,
    match_heading,
    number_value,
    parse_role,
    split_at_headings,
    split_scenes,
    settings_text,
)
from devils_workshop.project.project import load_project
from devils_workshop.project.story_config import parse_story_config
from devils_workshop.tools.plancheck import run_plan_check

from tests.conftest import messages, settings, settings_file, write


def make_pdf(path: Path, pages: list[list[str]]) -> Path:
    """A tiny PDF with one line of text per entry (an empty page has no text, like a scan)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        content = ("BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
                   if lines else "")
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objs)} 0 R "
                    "/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = b"%PDF-1.4\n"
    offsets = []
    for number, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{obj}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)
    return path


OUTLINE = """The Saltmarsh Clock: a quiet mystery on a tidal island.

Chapter One: The Stopped Clock
CH1-MARKER Mara arrives and the harbour clock has stopped.

CHAPTER 2
CH2-MARKER Ada shows Mara the ledger room.

## Chapter 3 - Low Tide
CH3-MARKER Chapter-level note for the low tide.
Scene 1: The causeway
SC1-MARKER They cross before the water returns.
Scene 2: The tower
SC2-MARKER Tobias climbs the forty-one steps.
"""


@pytest.fixture
def own(tmp_path: Path) -> Path:
    """A folder of someone's own story files, not made by the app."""
    root = tmp_path / "My Story"
    write(root, "World and characters.md", "WORLD-MARKER Mara Venn keeps the clock. Ada Quill keeps the ledger.")
    write(root, "Plot outline.md", OUTLINE)
    write(root, "chapters/Chapter 4 - The Missing Page.txt", "CH4-MARKER The page is gone from the ledger.")
    write(root, "Voice.md", "SYSTEM-MARKER Write in a calm, close third person.")
    write(root, "Old ideas.md", "UNSENT-MARKER a dragon, maybe")
    (root / "Cover.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00 a picture")
    return root


def set_up(root: Path, roles: dict[str, str], **extra) -> None:
    write(root, "Story settings.md", settings_text(roles, "The Saltmarsh Clock", 3000))
    if extra:
        settings(root, **extra)


ROLES = {
    "World and characters.md": "every chapter",
    "Plot outline.md": "chapter outline",
    "chapters/Chapter 4 - The Missing Page.txt": "chapter 4",
    "Voice.md": "system prompt",
    "Old ideas.md": "not sent",
}


# --- headings -----------------------------------------------------------------------------------

@pytest.mark.parametrize("line, expected", [
    ("Chapter 3", (3, "")),
    ("# Chapter 3: Low Tide", (3, "Low Tide")),
    ("## Chapter 3 - Low Tide", (3, "Low Tide")),
    ("CHAPTER TWENTY-ONE", (21, "")),
    ("Chapter Three: The Tower", (3, "The Tower")),
    ("Chapter XII", (12, "")),
    ("**Chapter 7.** The Bell", (7, "The Bell")),
    ("Ch. 4", (4, "")),
    ("ch04", (4, "")),
    ("- Chapter 5: Return", (5, "Return")),
    ("1. Chapter 1 – Arrival", (1, "Arrival")),
    ("Chapter 3 The Flood", (3, "The Flood")),
])
def test_chapter_headings(line, expected):
    assert match_heading(line) == expected


@pytest.mark.parametrize("line", [
    "Chapter 3 ends with the flood.",            # a sentence
    "In chapter 3 she leaves.",                  # not at the start
    "CH4 emissions are rising on the island",    # "Ch" only as "Ch. 4", "Ch 4" or a line that is just "ch04"
    "CH2-rich air fills the tunnels",
    "Chapters 1 to 3 are quiet",
    "Characters",
    "Chapter Civil",
    "Chapter 0",
    "Chapter 3 and the flood that follows it are the heart of the book",  # long, no ':' or '-'
    "- **Chapter 01–16:** Oren is fixed aboard the original lifeboat.",  # a note about a span of chapters
    "Chapter 1-3: the quiet days",
    "Chapter 4 to 6 — the storm",
    "**Chapter 2 & 3:** both at sea",
])
def test_not_chapter_headings(line):
    assert match_heading(line) is None


def test_a_note_about_several_chapters_stays_with_the_text_before_them():
    text = ("# Story bible\n## 05 · Space and time\n- **Chapter 01–16:** Oren stays aboard.\n- **Day 17:** Rescue.\n"
            "## 06 · Continuity\nCONTINUITY-MARKER\n## Chapter 01 — The Bench\nBrief one.\n## Chapter 02 — Water\n"
            "Brief two.\n")
    before, parts = split_at_headings(text)
    assert "Oren stays aboard" in before and "CONTINUITY-MARKER" in before  # sent with every chapter
    assert [(p.number, p.title) for p in parts] == [(1, "The Bench"), (2, "Water")]


def test_number_words_and_roman():
    assert [number_value(v) for v in ("one", "nineteen", "forty", "forty two", "ninety-nine", "iv", "xl", "xc")] \
        == [1, 19, 40, 42, 99, 4, 40, 90]
    assert number_value("ivy") is None


def test_split_outline_and_scenes():
    before, parts = split_at_headings(OUTLINE)
    assert before.startswith("The Saltmarsh Clock")
    assert [(p.number, p.title) for p in parts] == [(1, "The Stopped Clock"), (2, ""), (3, "Low Tide")]
    intro, scenes = split_scenes(parts[2].text)
    assert intro == "CH3-MARKER Chapter-level note for the low tide."
    assert [(s.number, s.title) for s in scenes] == [(1, "The causeway"), (2, "The tower")]
    assert "SC2-MARKER" in scenes[1].text and "SC2-MARKER" not in scenes[0].text


def test_long_heading_title_becomes_instructions():
    before, parts = split_at_headings("- Chapter 1: " + "Mara arrives on the island and finds " * 4 + "\n- Chapter 2: X")
    assert parts[0].title == "" and parts[0].text.startswith("Mara arrives")


# --- guessing and roles --------------------------------------------------------------------------------

def test_guess_role():
    assert guess_role("System prompt.md", "") == "system prompt"
    assert guess_role("chapters/Chapter 4 - The Missing Page.txt", "") == "chapter 4"
    assert guess_role("notes_ch07.md", "") == "chapter 7"
    assert guess_role("Plot outline.md", OUTLINE) == "chapter outline"
    assert guess_role("World.md", "Chapter 1 is mentioned once.\nChapter 1") == "every chapter"  # one heading
    assert guess_role("README.md", "") == "not sent"
    assert guess_role("Old ideas.md", "a dragon, maybe") == "not sent"
    assert guess_role("Ideas for later.txt", "") == "not sent"
    assert guess_role("Bold heroine.md", "") == "every chapter"  # 'old' only as a whole word


def test_parse_role():
    assert parse_role("Every chapter") == "every chapter"
    assert parse_role("Chapter 12 only") == "chapter 12"
    assert parse_role("ch 3") == "chapter 3"
    assert parse_role("never") == "not sent"
    assert parse_role("sometimes") is None
    assert parse_role("chapter 0") is None


def test_story_yaml_round_trip_keeps_other_settings():
    text = settings_text({"a: b.md": "every chapter", "yes": "not sent"}, "Devil's Bargain: Part 1", 2500,
                      existing="title: old\nmodels:\n  primary: some/model\nfiles:\n  gone.md: every chapter\n")
    issues = IssueList()
    config = parse_story_config(text, "story.yaml", issues)
    assert not list(issues)
    assert config.title == "Devil's Bargain: Part 1" and config.chapter_words == 2500
    assert config.primary_model == "some/model"
    assert config.own_files == {"a: b.md": "every chapter", "yes": "not sent"}


def test_unknown_role_is_an_error(own):
    set_up(own, {"World and characters.md": "every chapter"})
    settings(own, files={"World and characters.md": "every chapter", "Voice.md": "sometimes"})
    project = load_project(own)
    assert any("isn't a known use" in m for m in messages(project.issues, "error"))


# --- loading and what is sent ----------------------------------------------------------------------------

def test_load_own_folder(own):
    set_up(own, ROLES)
    project = load_project(own)
    assert project.own_files
    assert [(c.number, c.title) for c in project.outline.chapters] == [
        (1, "The Stopped Clock"), (2, ""), (3, "Low Tide"), (4, "The Missing Page")]
    assert project.outline.chapter(4).source == "chapters/Chapter 4 - The Missing Page.txt"
    assert [len(c.scenes) for c in project.outline.chapters] == [0, 0, 2, 0]
    assert project.system_prompt.startswith("SYSTEM-MARKER")
    assert [d.label for d in project.documents] == ["World and characters.md", "Plot outline.md (before the chapters)"]
    assert project.entries == [] and project.subplots == []
    # Nothing in your own chapters/ folder is mistaken for (or complained about as) a written chapter.
    assert project.chapters == {}
    assert not [i for i in project.issues if i.file.startswith("chapters/")]
    assert "isn't a text file" in " ".join(messages(project.issues, "info"))  # Cover.png


def test_chapter_request_sends_only_that_chapter(own):
    set_up(own, ROLES)
    request = assemble(load_project(own), 2)
    text = request.full_text()
    for present in ("WORLD-MARKER", "The Saltmarsh Clock: a quiet mystery", "CH2-MARKER", "SYSTEM-MARKER"):
        assert present in text
    for absent in ("CH1-MARKER", "CH3-MARKER", "SC1-MARKER", "CH4-MARKER", "UNSENT-MARKER"):
        assert absent not in text
    assert "Point of view" not in text          # not set anywhere, so not made up
    assert "Target length: about 3,000 words." in text
    assert request.messages[0].role == "system" and request.messages[0].content.startswith("SYSTEM-MARKER")
    assert not [w for w in request.warnings if "POV" in w or "point-of-view" in w]
    labels = {s.label: s.reason for s in request.sections}
    assert labels["World and characters.md"] == "every chapter"


def test_point_of_view_line_when_story_yaml_sets_it(own):
    set_up(own, ROLES)
    settings(own, defaults__person="first")
    request = assemble(load_project(own), 1)
    assert "Point of view: first person." in request.full_text()


def test_scene_mode_from_scene_headings(own):
    set_up(own, ROLES)
    request = assemble(load_project(own), 3, scene=1, mode="scenes")
    text = request.full_text()
    assert "Write scene 1 of 2" in text and "SC1-MARKER" in text
    assert "SC2-MARKER" in text  # later scenes are shown as "do not write yet", as in the app's own outline


def test_review_can_untick_a_document(own):
    from devils_workshop.context.assemble import Overrides
    set_up(own, ROLES)
    request = assemble(load_project(own), 1, overrides=Overrides(exclude={"doc:World and characters.md"}))
    assert "WORLD-MARKER" not in request.full_text()


def test_pdf_files(own):
    make_pdf(own / "Outline.pdf", [["Chapter 1: Arrival", "PDF1-MARKER Mara reaches the harbour."], [],
                                   ["Chapter 2", "PDF2-MARKER The clock stops."]])
    make_pdf(own / "Scan.pdf", [[]])
    set_up(own, {"Outline.pdf": "chapter outline", "Scan.pdf": "every chapter"})
    project = load_project(own)
    assert [c.number for c in project.outline.chapters] == [1, 2]
    assert "PDF2-MARKER" in assemble(project, 2).full_text()
    assert "PDF1-MARKER" not in assemble(project, 2).full_text()
    warnings = " ".join(messages(project.issues, "warning"))
    assert "1 of 3 pages have no text" in warnings and "No text found in this PDF" in warnings
    assert extract_text(own / "Outline.pdf")[0].startswith("Chapter 1: Arrival")


def test_new_missing_and_unsplittable_files(own):
    set_up(own, {"World and characters.md": "every chapter", "Gone.md": "every chapter",
                 "Old ideas.md": "chapter outline"})
    project = load_project(own)
    warnings = messages(project.issues, "warning")
    assert any("'Gone.md' is listed" in w for w in warnings)
    assert any("New file, not in your list" in w for w in warnings)
    assert any("No chapter headings found" in w for w in warnings)
    # The outline without headings is still sent, so nothing in it is silently lost.
    assert "Old ideas.md" in [d.label for d in project.documents]


def test_plan_check(own):
    set_up(own, ROLES)
    report = run_plan_check(load_project(own))
    assert report.ok
    assert [r.number for r in report.chapters] == [1, 2, 3, 4]
    assert not [i for i in report.issues if "POV" in i.message or "outline" in i.message.lower()]

    set_up(own, {"World and characters.md": "every chapter"})
    report = run_plan_check(load_project(own))
    assert "No chapters found in your files" in messages(report.issues, "warning")


def test_approved_scenes_go_to_app_scenes_file(own):
    set_up(own, ROLES)
    before = (own / "Plot outline.md").read_text()
    project = load_project(own)
    apply_scene_proposal(project, 1, [ProposedScene("Harbour", ["Mara arrives."]),
                                      ProposedScene("Clock", ["It has stopped."])])
    apply_scene_proposal(load_project(own), 2, [ProposedScene("Ledger", ["Ada shows the room."])])
    assert (own / "Plot outline.md").read_text() == before  # your files are never changed
    project = load_project(own)
    assert [s.title for s in project.outline.chapter(1).scenes] == ["Harbour", "Clock"]
    assert [s.title for s in project.outline.chapter(2).scenes] == ["Ledger"]
    assert (own / SCENES_FILE).is_file()
    assert "Write scene 1 of 2" in assemble(project, 1, scene=1, mode="scenes").full_text()


def make_docx(path: Path, paragraphs: list[str]) -> Path:
    """A minimal Word file: just word/document.xml with one paragraph per entry."""
    import zipfile
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w='
           f'"http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)
    return path


def test_word_files(own):
    make_docx(own / "Outline.docx", ["Chapter 1: Arrival", "DOCX1-MARKER Mara reaches the harbour.",
                                     "Chapter 2", "DOCX2-MARKER The clock stops."])
    (own / "Broken.docx").write_bytes(b"not a zip")
    set_up(own, {"Outline.docx": "chapter outline", "Broken.docx": "every chapter"})
    project = load_project(own)
    assert [c.number for c in project.outline.chapters] == [1, 2]
    text = assemble(project, 2).full_text()
    assert "DOCX2-MARKER" in text and "DOCX1-MARKER" not in text
    assert "This Word file couldn't be read (it may be damaged" in " ".join(messages(project.issues, "warning"))
    assert "Outline.docx" in __import__("devils_workshop.project.own_files", fromlist=["x"]).list_story_files(own)[0]
