from devils_workshop.project.files import read_text, write_text
from devils_workshop.project.markdown import (
    blank_out_comments,
    is_blank,
    split_front_matter,
    split_sections,
    strip_comments,
)
from devils_workshop.wordcount import count_words

from tests.conftest import messages


def test_front_matter_split(issues):
    front = split_front_matter("---\nname: Mara\n---\n## Profile\nText", "f.md", issues)
    assert front.data == {"name": "Mara"}
    assert front.body == "## Profile\nText"
    assert front.body_line == 4
    assert not issues


def test_no_front_matter(issues):
    front = split_front_matter("## Profile\nText", "f.md", issues)
    assert front.data == {} and front.body_line == 1


def test_unclosed_front_matter(issues):
    split_front_matter("---\nname: Mara\n## Profile", "f.md", issues)
    assert "never closed" in messages(issues, "error")[0]


def test_invalid_yaml_reports_line(issues):
    split_front_matter("---\nname: Mara\naliases: [unclosed\n---\n", "f.md", issues)
    assert issues[0].severity == "error"
    assert issues[0].line is not None


def test_front_matter_must_be_mapping(issues):
    split_front_matter("---\n- a\n- b\n---\n", "f.md", issues)
    assert "key: value" in messages(issues, "error")[0]


def test_sections_and_line_numbers():
    body = "Intro line\n\n## Profile\nPrecise.\n### Detail\nStill profile.\n## Voice\nShort."
    sections = split_sections(body, 10)
    assert [s.title for s in sections] == [None, "Profile", "Voice"]
    assert sections[0].line == 10
    assert sections[1].line == 12
    assert "### Detail" in sections[1].text


def test_headings_inside_code_fences_are_ignored():
    sections = split_sections("## A\n```\n## not a heading\n```\n## B\nx", 1)
    assert [s.title for s in sections] == ["A", "B"]


def test_heading_keeps_inner_hash():
    assert [s.title for s in split_sections("## C# notes\nx", 1)] == ["C# notes"]


def test_comments():
    text = "Keep this.<!-- a note\nover two lines --> And this."
    assert strip_comments(text) == "Keep this. And this."
    blanked = blank_out_comments(text)
    assert blanked.count("\n") == text.count("\n")
    assert "note" not in blanked
    assert is_blank("<!-- only a note -->\n\n")
    assert not is_blank("<!-- note --> words")


def test_read_text_utf8_bom_and_crlf(tmp_path):
    path = tmp_path / "a.md"
    path.write_bytes("﻿line one\r\nline two\r\n".encode("utf-8"))
    text, encoding = read_text(path)
    assert text == "line one\nline two\n" and encoding == "utf-8"


def test_read_text_falls_back_to_windows_1252(tmp_path):
    path = tmp_path / "a.md"
    path.write_bytes("caf\xe9 \x93quoted\x94".encode("latin-1"))
    text, encoding = read_text(path)
    assert encoding == "cp1252"
    assert text == "café “quoted”"


def test_write_text_is_atomic_and_utf8(tmp_path):
    path = tmp_path / "sub" / "b.md"
    write_text(path, "Mara’s watch\n")
    assert path.read_bytes() == "Mara’s watch\n".encode("utf-8")
    assert [p.name for p in path.parent.iterdir()] == ["b.md"]  # no temp files left


def test_count_words():
    assert count_words("Mara's harbour-master said: don't.") == 4
    assert count_words("<!-- scene 1 -->\nOne two three.") == 3
    assert count_words("") == 0
