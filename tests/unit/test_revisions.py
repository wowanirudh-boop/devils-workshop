"""Exports know which text of each chapter they hold: saved from one consistent reading, checked against it, and
reported out of date once a chapter changes (a new version, or a change made in place)."""

import json

import pytest

from devils_workshop.chat.session import ChapterChat
from devils_workshop.project.project import load_project
from devils_workshop.project.revisions import (
    CURRENT,
    STALE,
    UNKNOWN,
    ExportError,
    export_status,
    manifest_path,
    revision,
    save_export,
)
from devils_workshop.tools.pdf import story_pdf
from devils_workshop.tools.reading import standalone_html

from tests.conftest import write

V1 = "VERSION-ONE the harbour bell rang twice before dawn and nobody answered it. " * 12
V2 = "VERSION-TWO the harbour bell rang once at noon and the whole town came out to look. " * 12


def chapter_two(sample, text: str, version: int = 1):
    write(sample, "chapters/ch02.md", f"---\nchapter: 2\nstatus: draft\nversion: {version}\n---\n{text}")
    return load_project(sample)


def test_an_export_made_from_version_1_is_out_of_date_once_version_2_is_approved(sample):
    project = chapter_two(sample, V1)
    path = sample / "exports" / "Story.html"
    data = save_export(project, path, "html", standalone_html)
    assert b"VERSION-ONE" in data and path.read_bytes() == data
    manifest = json.loads(manifest_path(path).read_text(encoding="utf-8"))
    assert {c["chapter"]: c["version"] for c in manifest["chapters"]}[2] == 1
    assert export_status(load_project(sample), path).state == CURRENT

    chat = ChapterChat.load(sample, 2)  # version 2 replaces version 1 (version 1 kept in versions/)
    chat._new_version(load_project(sample), load_project(sample).chapters[2], text=V2, action="test")
    project = load_project(sample)
    assert project.chapters[2].version == 2
    status = export_status(project, path)
    assert status.state == STALE and "chapter 2 changed since" in status.detail

    again = save_export(project, path, "html", standalone_html)  # saved again: version 2, and current
    assert b"VERSION-TWO" in again and b"VERSION-ONE" not in again
    assert export_status(project, path).state == CURRENT


def test_a_change_made_in_place_keeps_the_version_number_but_not_the_revision(sample):
    project = chapter_two(sample, V1)
    path = sample / "exports" / "Story.html"
    save_export(project, path, "html", standalone_html)
    before = revision(project.chapters[2])
    project = chapter_two(sample, V1.replace("twice", "three times"))  # the Reader's change: same version
    assert project.chapters[2].version == 1 and revision(project.chapters[2]) != before
    assert export_status(project, path).state == STALE


def test_a_chapter_written_after_the_export_makes_it_out_of_date(sample):
    project = load_project(sample)
    path = sample / "exports" / "Story.html"
    save_export(project, path, "html", standalone_html)
    status = export_status(chapter_two(sample, V1), path)
    assert status.state == STALE and "chapter 2 written since" in status.detail


def test_an_export_that_doesnt_hold_the_chapters_is_never_saved(sample):
    project = chapter_two(sample, V1)
    path = sample / "exports" / "Story.html"
    with pytest.raises(ExportError, match="chapter 2"):
        save_export(project, path, "html", lambda p: standalone_html(p).replace("VERSION-ONE", "SOMETHING-ELSE"))
    assert not path.exists() and not manifest_path(path).exists()


def test_a_chapter_that_changes_while_the_export_is_made_stops_it(sample):
    project = chapter_two(sample, V1)
    path = sample / "exports" / "Story.html"

    def build(p):
        text = standalone_html(p)
        chapter_two(sample, V2, version=2)  # changed on disk meanwhile
        return text

    with pytest.raises(ExportError, match="changed while"):
        save_export(project, path, "html", build)
    assert not path.exists()


def test_a_pdf_is_checked_against_the_chapters_too(sample):
    project = chapter_two(sample, V1)
    path = sample / "exports" / "Story.pdf"
    data = save_export(project, path, "pdf", story_pdf)
    assert data.startswith(b"%PDF") and export_status(project, path).state == CURRENT
    with pytest.raises(ExportError):
        save_export(project, sample / "exports" / "Other.pdf", "pdf", lambda p: story_pdf(chapter_two(sample, V2)))


def test_an_export_from_before_these_records_is_unknown_not_current(sample):
    path = sample / "exports" / "Old.html"
    write(sample, "exports/Old.html", "<html>an old copy</html>")
    assert export_status(load_project(sample), path).state == UNKNOWN
