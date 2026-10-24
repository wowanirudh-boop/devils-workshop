"""Story settings.md (once story.yaml), who may change which file, and Ask AI to change."""

import asyncio

import pytest

from devils_workshop.history import list_snapshots
from devils_workshop.planner.assist import diff_lines, propose_edit, read_reply
from devils_workshop.project.newproject import create_project
from devils_workshop.project.project import load_project
from devils_workshop.project.scopes import ASSIST, PLANNER, WRITER, ScopeError, check, may_write
from devils_workshop.project.story_settings import (
    LEGACY_FILE,
    SETTINGS_FILE,
    parse_settings,
    settings_markdown,
    update_settings,
)
from devils_workshop.settings import AppSettings

from tests.conftest import write
from tests.fakes import FakeOpenRouter, reply
from tests.unit.test_planner import SEEING

OLD = """# Settings for this story.
title: "Devil's Bargain: Part 1"
language: English
defaults:
  pov: Mara Venn
  person: first
  chapter_words: 2500
  mode: scenes
  in_chapter_context: beats_only
models:
  primary: z-ai/glm-5.3
files:
  "World: notes.md": every chapter
default_instructions: off
"""


def test_story_settings_read_and_write_as_markdown():
    data = parse_settings(OLD)
    text = settings_markdown({**data, "genre": "Gothic", "characters": ["Mara Venn", "Tobias"]})
    assert "- Title: Devil's Bargain: Part 1" in text and "- Written as: scene by scene" in text
    assert "- What a scene sees: earlier scenes' beats" in text and "- Default instructions: off" in text
    assert "- World: notes.md: every chapter" in text and "- Key characters: Mara Venn, Tobias" in text
    again = parse_settings(text)
    assert again["defaults"] == data["defaults"] and again["files"] == {"World: notes.md": "every chapter"}
    assert again["default_instructions"] is False and again["characters"] == ["Mara Venn", "Tobias"]
    assert again["models"] == {"primary": "z-ai/glm-5.3"} and again["title"] == "Devil's Bargain: Part 1"
    edited = text.replace("- Tense: (not set)", "- Tense: present").replace("- Genre: Gothic", "- Genre: (not set)")
    assert parse_settings(edited)["defaults"]["tense"] == "present" and "genre" not in parse_settings(edited)


def test_an_old_story_yaml_becomes_story_settings_md_on_first_open(tmp_path):
    root = tmp_path / "Old"
    write(root, LEGACY_FILE, OLD)
    write(root, "World: notes.md", "Notes.")
    project = load_project(root)
    assert not (root / LEGACY_FILE).exists() and (root / SETTINGS_FILE).is_file()
    config = project.config
    assert (config.title, config.pov, config.person, config.chapter_words) == \
        ("Devil's Bargain: Part 1", "Mara Venn", "first", 2500)
    assert config.mode == "scenes" and config.in_chapter_context == "beats_only" and not config.default_instructions
    assert config.primary_model == "z-ai/glm-5.3" and config.own_files == {"World: notes.md": "every chapter"}
    assert [s.action for s in list_snapshots(root)] == ["settings-to-markdown"]  # the old file is in History
    assert not [i for i in project.issues if i.severity == "error"]


def test_new_stories_have_story_settings_and_the_library_details(tmp_path):
    root = create_project(tmp_path / "S", sample=True)
    config = load_project(root).config
    assert (root / SETTINGS_FILE).is_file() and config.genre == "Cosy mystery" and "Mara Venn" in config.characters
    assert config.status == "in progress"
    update_settings(root, status="completed", completed_on="2026-10-20")
    config = load_project(root).config
    assert config.status == "completed" and config.completed_on == "2026-10-20"
    named = create_project(tmp_path / "N", title='A "quoted": title')
    assert load_project(named).config.title == 'A "quoted": title'


def test_an_unknown_setting_is_a_warning(tmp_path):
    root = tmp_path / "S"
    write(root, SETTINGS_FILE, "# Story settings\n\n## Story\n- Title: X\n- Mood: dark\n")
    project = load_project(root)
    assert project.config.title == "X" and any("Mood" in i.message for i in project.issues)


# --- who may change what --------------------------------------------------------------------------

def test_the_planner_never_changes_the_story_and_the_writer_never_the_planners_files():
    assert may_write(WRITER, "chapters/ch01.md") and may_write(WRITER, "chapters/versions/ch01.v1.md")
    assert not may_write(WRITER, "Writer guide.md") and not may_write(WRITER, "Chapter outline.md")
    assert may_write(PLANNER, "Writer guide.md") and may_write(PLANNER, "planner/state.json")
    assert not may_write(PLANNER, "chapters/ch01.md") and not may_write(PLANNER, "characters/mara.md")
    assert may_write(ASSIST, "characters/mara.md") and not may_write(ASSIST, "chapters/ch02.md")
    with pytest.raises(ScopeError, match="the story's text is the writer's"):
        check(PLANNER, "chapters/ch01.md")


# --- Ask AI to change --------------------------------------------------------------------------------

def test_the_change_comes_back_whole_or_by_section():
    current = "# Mara\n\n## Look\nTall.\n\n## Voice\nDry.\n"
    whole, summary = read_reply(current, "<file>\n# Mara\nShort.\n</file><summary>Shorter.</summary>")
    assert whole == "# Mara\nShort.\n" and summary == "Shorter."
    sectioned, _ = read_reply(current, '<edit section="Voice">\nWarm, and slow.\n</edit>')
    assert "## Look\nTall." in sectioned and "## Voice\nWarm, and slow." in sectioned
    with pytest.raises(ValueError):
        read_reply(current, "Sure! I made her warmer.")
    assert ("+", "Warm, and slow.") in diff_lines(current, sectioned) and ("-", "Dry.") in diff_lines(current, sectioned)


def test_asking_ai_to_change_a_file_uses_the_planners_model_and_never_the_storys_text(tmp_path):
    root = create_project(tmp_path / "S", sample=True)
    fake = FakeOpenRouter().queue(reply("<edit section=\"Appearance\">\nSalt-grey hair, AGE-MARKER 40.\n</edit>"
                                        "<summary>Made her 40.</summary>"))
    settings = AppSettings(planner_primary="moonshotai/kimi-k3")
    current = "# Mara Venn\n\n## Appearance\nDark hair, 32.\n"
    proposal = asyncio.run(propose_edit(root, "characters/mara-venn.md", current, "Make her 40", settings=settings,
                                        catalog=SEEING, client=fake.client(), log_root=root / "logs"))
    assert proposal.ok and "AGE-MARKER 40" in proposal.text and proposal.summary == "Made her 40."
    body = fake.chat_bodies()[0]
    assert body["model"] == "moonshotai/kimi-k3" and "Make her 40" in body["messages"][1]["content"]
    with pytest.raises(ScopeError):
        asyncio.run(propose_edit(root, "chapters/ch01.md", "Text.", "Shorter", settings=settings, catalog=SEEING,
                                 client=fake.client(), log_root=root / "logs"))
