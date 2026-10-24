"""The reading view: prose formatting, scenes stitched together, the whole story and one chapter."""

from devils_workshop.chat.session import ChapterChat
from devils_workshop.project.project import load_project
from devils_workshop.tools.reading import prose_html, reader_html, reading_chapters, standalone_html


def test_paragraphs_breaks_and_emphasis():
    html = prose_html("First paragraph.\n\nSecond, with *italics* and **bold**.\n\n* * *\n\nAfter the break.")
    assert html.split("\n") == [
        '<p class="dwr-first">First paragraph.</p>',
        "<p>Second, with <em>italics</em> and <strong>bold</strong>.</p>",
        '<p class="dwr-break">⁂</p>',
        '<p class="dwr-first">After the break.</p>',
    ]


def test_text_is_escaped_and_single_line_paragraphs_are_split():
    assert "&lt;script&gt;" in prose_html("<script>alert(1)</script>")
    html = prose_html("One line.\nTwo line.\nThree line.\nFour line.")
    assert html.count("<p") == 4
    assert prose_html("snake_case_name and 2*3*4") == '<p class="dwr-first">snake_case_name and 2*3*4</p>'
    joined = prose_html("A sentence that was\nwrapped by an editor.\n\nNext paragraph.")
    assert '<p class="dwr-first">A sentence that was wrapped by an editor.</p>' in joined


def write_scenes(root, chapter, texts):
    project = load_project(root)
    chat = ChapterChat.load(root, chapter)
    for text in texts:
        chat.apply(project, chat.prepare_write(project, "scenes"), text)


def test_scenes_are_stitched_with_breaks_and_titles_kept_for_labels(sample):
    write_scenes(sample, 2, ["SCENE-ONE text.", "SCENE-TWO text."])
    chapters = reading_chapters(load_project(sample))
    assert [c.number for c in chapters] == [1, 2]  # the sample comes with chapter 1 written
    two = chapters[1]
    assert [(s.number, s.title) for s in two.scenes] == [(1, "The archive"), (2, "The harbour at night")]
    html = reader_html(load_project(sample), only=2)
    assert html.index("SCENE-ONE") < html.index("⁂") < html.index("SCENE-TWO")
    assert "Scene 2 · The harbour at night" in html  # shown only when 'Scenes' is switched on
    assert 'href="/story/read?chapter=1"' in html and "Whole story" in html


def test_whole_story_has_contents_and_says_what_is_missing(sample):
    write_scenes(sample, 2, ["SCENE-ONE text."])
    html = reader_html(load_project(sample))
    assert "The Saltmarsh Clock" in html and 'href="#ch1"' in html and 'id="ch2"' in html
    assert "Not written yet: chapters 3, 4" in html
    assert html.index('id="ch1"') < html.index('id="ch2"')


def test_one_file_for_any_device(sample):
    page = standalone_html(load_project(sample))
    assert page.startswith("<!doctype html>") and "window.dwReader" in page and "dwReader.apply()" in page
    assert "dwr-state" not in page.replace(".dwr-state", "")  # no draft/final labels in the reading copy
    assert "Not written yet" not in page


def test_nothing_written():
    from devils_workshop.project.project import Project
    from devils_workshop.project.outline import Outline
    from devils_workshop.project.story_config import StoryConfig
    from pathlib import Path
    empty = Project(Path("."), StoryConfig(), None, {}, [], [], Outline([], ""), {})
    assert "Nothing is written yet." in reader_html(empty)
