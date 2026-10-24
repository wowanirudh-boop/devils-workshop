"""Adding, renaming and deleting story files, each undoable in one step; removing a whole story."""

import os
import sys
from pathlib import Path

import pytest

from devils_workshop.history import list_snapshots, restore
from devils_workshop.project.file_ops import (
    FileOpError,
    add_own_file,
    add_story_file,
    delete_file,
    move_to_recycle_bin,
    own_files_inside,
    protected,
    rename_file,
    story_folder_problem,
)
from devils_workshop.project.own_files import EVERY, NOT_SENT, settings_text
from devils_workshop.project.project import load_project

from tests.conftest import settings, settings_file, write
from tests.unit.test_own_files import make_docx, make_pdf


@pytest.fixture
def own(tmp_path) -> Path:
    """A story in your own files: World.md is sent with every chapter, the outline splits into chapters."""
    root = tmp_path / "My Story"
    write(root, "World.md", "Mara Venn keeps the harbour clock.")
    write(root, "Plot outline.md", "Chapter 1: The Stopped Clock\nThe clock has stopped.\n")
    write(root, "drafts/Old scene.md", "An old scene.")
    roles = {"World.md": EVERY, "Plot outline.md": "chapter outline", "drafts/Old scene.md": NOT_SENT}
    write(root, "story.yaml", settings_text(roles, "My Story", 2500))
    return root


def roles(root: Path) -> dict[str, str]:
    return dict(load_project(root).config.own_files)


def undo_last(root: Path) -> None:
    restore(root, list_snapshots(root)[0].id)


# --- your own files -----------------------------------------------------------------------------------

def test_add_own_file_is_listed_and_undone_in_one_step(own):
    rel, role = add_own_file(own, r"C:\Users\me\Desktop\Chapter 2 notes.txt", b"Ada shows Mara the ledger.")
    assert rel == "Chapter 2 notes.txt" and role == "chapter 2"  # the same guess the setup screen makes
    assert (own / rel).read_text() == "Ada shows Mara the ledger." and roles(own)[rel] == "chapter 2"
    assert load_project(own).config.title == "My Story" and load_project(own).config.chapter_words == 2500

    undo_last(own)
    assert not (own / rel).exists() and rel not in roles(own)


def test_add_own_file_with_the_same_name_replaces_it_and_keeps_its_role(own):
    add_own_file(own, "World.md", b"A new world.")
    assert (own / "World.md").read_text() == "A new world." and roles(own)["World.md"] == EVERY
    undo_last(own)
    assert (own / "World.md").read_text() == "Mara Venn keeps the harbour clock."


def test_add_own_pdf_and_reject_other_kinds(own, tmp_path):
    pdf = make_pdf(tmp_path / "x.pdf", [["Chapter 3: Low Tide", "The tide goes out.", "Chapter 4: The Page", "A page is missing."]])
    rel, role = add_own_file(own, "Later chapters.pdf", pdf.read_bytes())
    assert role == "chapter outline" and 3 in {c.number for c in load_project(own).outline.chapters}
    with pytest.raises(FileOpError, match="isn't a text file"):
        add_own_file(own, "photo.jpg", b"...")
    with pytest.raises(FileOpError, match="isn't a text file"):
        add_own_file(own, "data.bin2", b"ab\x00cd")  # any name: what's inside counts
    with pytest.raises(FileOpError, match="save it as .docx"):
        add_own_file(own, "old.doc", b"...")
    before = settings_file(own)
    for name in ("Story settings.md", "story.yaml"):  # e.g. picked with every other file
        with pytest.raises(FileOpError, match="app's own file"):
            add_own_file(own, name, b"title: Someone else's story\n")
    assert settings_file(own) == before and "story.yaml" not in roles(own) and "Story settings.md" not in roles(own)
    with pytest.raises(FileOpError, match="app's own file"):
        add_own_file(own, "app-scenes.md", b"...")


def test_rename_own_file_keeps_what_it_is_for(own):
    new = rename_file(own, "World.md", "Setting")
    assert new == "Setting.md"  # the extension stays
    assert (own / "Setting.md").is_file() and not (own / "World.md").exists()
    assert roles(own) == {"Setting.md": EVERY, "Plot outline.md": "chapter outline", "drafts/Old scene.md": NOT_SENT}
    assert list(roles(own))[0] == "Setting.md"  # same place in the list

    undo_last(own)
    assert (own / "World.md").is_file() and not (own / "Setting.md").exists() and "World.md" in roles(own)


def test_rename_in_a_subfolder_and_refusals(own):
    assert rename_file(own, "drafts/Old scene.md", "First scene.md") == "drafts/First scene.md"
    with pytest.raises(FileOpError, match="already a file called Plot outline.md"):
        rename_file(own, "World.md", "Plot outline")
    with pytest.raises(FileOpError, match="Give it a name"):
        rename_file(own, "World.md", "  ")
    with pytest.raises(FileOpError, match="Story settings.md"):
        rename_file(own, "Story settings.md", "other")


def test_delete_own_file_unlists_it_and_undo_brings_both_back(own):
    delete_file(own, "drafts/Old scene.md")
    assert not (own / "drafts").exists()  # the folder it emptied goes too
    assert "drafts/Old scene.md" not in roles(own)
    assert not [i for i in load_project(own).issues if "isn't in the folder" in i.message]

    undo_last(own)
    assert (own / "drafts" / "Old scene.md").read_text() == "An old scene."
    assert roles(own)["drafts/Old scene.md"] == NOT_SENT


def test_files_that_cant_be_renamed_or_deleted(sample):
    for rel in ("Story settings.md", "story.yaml", "app-scenes.md", "chapters/ch01.md", "chapters/versions/ch01.v1.md"):
        assert protected(rel)
    assert not protected("chapters/my own notes.md")  # your own folder may have one called chapters
    with pytest.raises(FileOpError):
        delete_file(sample, "chapters/ch01.md")
    with pytest.raises(FileOpError, match="isn't there"):
        delete_file(sample, "characters/nobody.md")


# --- a story made by the app ------------------------------------------------------------------------

def test_add_story_file_as_a_character_gets_a_name(sample, tmp_path):
    docx = make_docx(tmp_path / "x.docx", ["## Profile", "A lighthouse keeper who counts ships."])
    rel = add_story_file(sample, "Old_Bess.docx", docx.read_bytes(), "character")
    assert rel == "characters/old-bess.md"
    text = (sample / rel).read_text(encoding="utf-8")
    assert text.startswith("---\nname: Old Bess\n---\n") and "counts ships" in text
    entry = next(e for e in load_project(sample).entries if e.path == rel)
    assert entry.name == "Old Bess" and entry.type == "character"

    undo_last(sample)
    assert not (sample / rel).exists()


def test_add_story_file_replacing_the_outline_and_as_a_note(sample):
    before = (sample / "outline.md").read_text(encoding="utf-8")
    assert add_story_file(sample, "plan.md", b"# Chapter 1: Only one\nSomething happens.\n", "outline") == "outline.md"
    assert [c.title for c in load_project(sample).outline.chapters] == ["Only one"]
    undo_last(sample)
    assert (sample / "outline.md").read_text(encoding="utf-8") == before

    assert add_story_file(sample, "Ideas.txt", b"Maybe a storm.", "note") == "notes/ideas.md"
    assert (sample / "notes" / "ideas.md").read_text() == "Maybe a storm."  # notes get no name block
    with pytest.raises(FileOpError, match="has no text"):
        add_story_file(sample, "empty.md", b"   ", "lore")


def test_delete_and_rename_in_a_story_made_by_the_app(sample):
    rel = next(e.path for e in load_project(sample).entries if e.path.startswith("places/"))
    delete_file(sample, rel)
    assert rel not in [e.path for e in load_project(sample).entries]
    undo_last(sample)
    assert rel in [e.path for e in load_project(sample).entries]

    new = rename_file(sample, rel, "harbour")
    assert new == rel.rsplit("/", 1)[0] + "/harbour.md" and (sample / new).is_file()
    assert "## Files" not in settings_file(sample)  # no list to update


# --- a whole story ---------------------------------------------------------------------------------

def test_which_story_folders_can_be_deleted(sample, own, tmp_path, monkeypatch):
    assert story_folder_problem(sample) == "" and story_folder_problem(own) == ""
    assert "isn't a story folder" in story_folder_problem(tmp_path)
    assert "can't be found" in story_folder_problem(tmp_path / "gone")
    assert own_files_inside(own) == ["drafts/Old scene.md", "Plot outline.md", "World.md"]

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    write(tmp_path, "story.yaml", "title: Home\n")
    assert "user folder" in story_folder_problem(tmp_path)  # someone picked their whole user folder
    write(tmp_path / "Documents", "story.yaml", "title: Docs\n")
    assert "main folders" in story_folder_problem(tmp_path / "Documents")
    write(tmp_path / "Documents" / "Inner", "story.yaml", "title: Inner\n")
    assert "main folders" in story_folder_problem(tmp_path / "Documents")
    outer = tmp_path / "Documents" / "Inner"
    write(outer / "Nested", "story.yaml", "title: Nested\n")
    assert "other stories" in story_folder_problem(outer)


@pytest.mark.skipif(sys.platform == "win32", reason="the real Recycle Bin is tested on the CI Windows runner")
def test_without_a_recycle_bin_it_goes_to_the_apps_trash(own):
    moved = move_to_recycle_bin(own)  # a Linux server: the app's Trash folder, where it can be got back
    assert not own.exists() and moved.is_dir() and moved.name.startswith(f"{own.name} (deleted ")


@pytest.mark.skipif(sys.platform != "win32" or not os.environ.get("CI"),
                    reason="puts a folder in the Recycle Bin, so it runs only on the CI Windows runner")
def test_move_to_recycle_bin_on_windows(own):
    move_to_recycle_bin(own)
    assert not own.exists()


# --- any text file ---------------------------------------------------------------------------------

def test_any_text_file_is_a_source(own):
    from devils_workshop.context.assemble import assemble
    rel, role = add_own_file(own, "Rules.json", b'{"rule": "JSON-MARKER no adverbs"}')
    assert role == EVERY and rel in load_project(own).config.own_files
    add_own_file(own, "Cast.csv", b"name,role\nMara,CSV-MARKER clockmaker\n")
    add_own_file(own, "Voice.yaml", b"tone: YAML-MARKER dry\n")
    add_own_file(own, "Notes.txt", "UTF16-MARKER from Notepad".encode("utf-16"))  # has a BOM
    sent = assemble(load_project(own), 1, mode="whole").full_text()
    for marker in ("JSON-MARKER", "CSV-MARKER", "YAML-MARKER", "UTF16-MARKER"):
        assert marker in sent


def test_any_text_file_is_listed_and_editable_binary_ones_are_not(own):
    from devils_workshop.project.own_files import extract_text, is_editable, list_story_files
    write(own, "plan.json", '{"a": 1}')
    write(own, "page.html", "<p>hi</p>")
    write(own, "no extension", "plain text")
    (own / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    (own / "odd.dat").write_bytes(b"ab\x00cd")
    readable, other = list_story_files(own)
    assert {"plan.json", "page.html", "no extension"} <= set(readable)
    assert other == ["cover.png", "odd.dat"]
    assert is_editable(own / "plan.json") and not is_editable(own / "cover.png")
    assert extract_text(own / "cover.png") == ("", extract_text(own / "odd.dat")[1])


def test_add_any_text_file_to_a_story_made_by_the_app(sample):
    rel = add_story_file(sample, "harbour.json", b'{"name": "the harbour", "tides": 2}', "lore")
    assert rel == "lore/harbour.md"
    assert '"tides": 2' in (sample / rel).read_text(encoding="utf-8")
    with pytest.raises(FileOpError, match="isn't a text file"):
        add_story_file(sample, "map.png", b"\x89PNG\r\n\x1a\n\x00", "place")


def test_story_yaml_listed_by_mistake_is_never_sent(own):
    from devils_workshop.context.assemble import assemble
    from devils_workshop.project.story_settings import read_settings
    files = {**read_settings(own)["files"], "story.yaml": "every chapter", "Story settings.md": "every chapter"}
    settings(own, files=files)
    project = load_project(own)
    assert {"story.yaml", "Story settings.md"} <= set(project.config.own_files)  # as an older version could leave
    assert [d.label for d in project.documents] == ["World.md"]
    assert "chapter_words" not in assemble(project, 1, mode="whole").full_text()
    assert any("app's own file" in i.message for i in project.issues)
