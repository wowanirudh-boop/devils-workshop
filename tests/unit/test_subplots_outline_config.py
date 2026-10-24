from devils_workshop.project.chapters import parse_chapter_file
from devils_workshop.project.outline import parse_outline
from devils_workshop.project.story_config import parse_story_config
from devils_workshop.project.subplots import parse_subplot

from tests.conftest import messages

# --- subplots -----------------------------------------------------------------

SUBPLOT = """---
name: The forged ledger
aliases: [SP1]
status:
  2: introduced
  ch4: active
  9: climax
  10: resolved
---
## Summary
A page was recopied.

## ch2
Ada mentions it.

## Chapter 4
The page is found.
"""


def test_subplot_status_carries_forward(issues):
    subplot = parse_subplot(SUBPLOT, "subplots/a.md", issues)
    assert not issues, messages(issues)
    assert [subplot.status_at(n) for n in (1, 2, 3, 4, 8, 9, 10, 11)] == [
        None, "introduced", "introduced", "active", "active", "climax", "resolved", "resolved"]
    assert [n for n in range(1, 12) if subplot.included_at(n)] == [2, 3, 4, 5, 6, 7, 8, 9]
    assert subplot.chapter_notes == {2: "Ada mentions it.", 4: "The page is found."}
    assert [s.title for s in subplot.sections] == ["Summary"]


def test_subplot_bad_status(issues):
    parse_subplot("---\nname: X\nstatus:\n  2: brewing\n  soon: active\n---\n", "subplots/x.md", issues)
    assert len(messages(issues, "error")) == 2


def test_subplot_duplicate_notes_are_joined(issues):
    subplot = parse_subplot("---\nname: X\n---\n## ch2\na\n## ch2\nb\n", "subplots/x.md", issues)
    assert subplot.chapter_notes[2] == "a\n\nb"
    assert "joined" in messages(issues, "warning")[0]


# --- outline ------------------------------------------------------------------

OUTLINE = """Some planning notes before chapter 1.
# Chapter 1: The Stopped Clock
words: 3,200
characters: [Tobias Reed]
Note: keep the tone quiet.
- Mara finds the scratch.

# Chapter 2 - Ink and Salt
mode: scenes
Chapter-level instruction.
## Scene 1: The archive
characters: Ada Quill, Will Harrow
lookahead: no
- Ada shows Mara the room.
## Scene 2
pov: Ada Quill

- A light in the tower.
### Not a scene, just a sub-heading
More beats.
"""


def test_outline_structure(issues):
    outline = parse_outline(OUTLINE, "outline.md", issues)
    assert not issues, messages(issues)
    assert outline.preamble == "Some planning notes before chapter 1."
    assert [c.number for c in outline.chapters] == [1, 2] and outline.last_chapter == 2

    ch1, ch2 = outline.chapters
    assert ch1.title == "The Stopped Clock"
    assert ch1.settings == {"words": 3200, "characters": ["Tobias Reed"]}
    assert ch1.text.startswith("Note: keep the tone quiet.")  # unknown key stays a beat
    assert ch1.scenes == []

    assert ch2.title == "Ink and Salt" and ch2.settings == {"mode": "scenes"}
    assert ch2.text == "Chapter-level instruction."
    s1, s2 = ch2.scenes
    assert s1.settings == {"characters": ["Ada Quill", "Will Harrow"], "lookahead": False}
    assert s2.title == "" and s2.settings == {"pov": "Ada Quill"}
    assert "### Not a scene" in s2.text
    assert ch2.scene(2) is s2 and ch2.scene(3) is None


def test_outline_errors_and_warnings(issues):
    text = ("# Prologue\nx\n# Chapter 1\nwords: lots\nmode: sideways\nword: 3000\nlookahead: false\n"
            "## Scene 1\n## Scene 1\n## Scene 4\n## Scene two\n## Notes\n# Chapter 1\n")
    parse_outline(text, "outline.md", issues)
    errors, warnings = messages(issues, "error"), messages(issues, "warning")
    assert any("isn't a chapter heading" in m for m in errors)       # "# Prologue"
    assert any("words: lots" in m for m in errors)
    assert any("mode: sideways" in m for m in errors)
    assert any("two scenes numbered 1" in m for m in errors)
    assert any("isn't a scene heading" in m for m in errors)          # "## Scene two"
    assert any("Chapter 1 appears twice" in m for m in errors)
    assert any("'word:' isn't a setting" in m for m in warnings)      # typo with hint
    assert any("only works for scenes" in m for m in warnings)        # lookahead on a chapter
    assert any("expected scene 3" in m for m in warnings)
    assert any("'## Notes'" in m for m in messages(issues, "info"))
    hint = next(i.hint for i in issues if "'word:'" in i.message)
    assert "words" in hint


def test_outline_text_after_bad_heading_warns_once(issues):
    parse_outline("# Chapter 1\nx\n# Epilogue\na\nb\nc\n", "outline.md", issues)
    assert len([m for m in messages(issues, "warning") if "isn't under any chapter" in m]) == 1


def test_outline_comments_hide_headings(issues):
    outline = parse_outline("<!--\n# Chapter 9: Cut\n-->\n# Chapter 1\n- beat\n", "outline.md", issues)
    assert [c.number for c in outline.chapters] == [1] and outline.chapters[0].line == 4


# --- story.yaml ---------------------------------------------------------------

def test_story_config_values(issues):
    config = parse_story_config(
        "title: The Saltmarsh Clock\ndefaults:\n  pov: Mara Venn\n  tense: Present\n  chapter_words: 2500\n"
        "  mode: scenes\n  in_chapter_context: beats_only\nmodels:\n  primary: z-ai/glm-5.3\n",
        "story.yaml", issues)
    assert not issues, messages(issues)
    assert (config.title, config.pov, config.tense, config.chapter_words, config.mode) == \
        ("The Saltmarsh Clock", "Mara Venn", "present", 2500, "scenes")
    assert config.in_chapter_context == "beats_only"
    assert config.primary_model == "z-ai/glm-5.3" and config.fallback_model is None


def test_story_config_defaults_when_empty(issues):
    config = parse_story_config("", "story.yaml", issues)
    assert (config.person, config.tense, config.chapter_words, config.mode) == ("third", "past", 3000, "whole")
    assert "No 'title:' set" in messages(issues, "warning")


def test_story_config_bad_values(issues):
    parse_story_config("title: X\ncolour: red\ndefaults:\n  person: fourth\n  chapter_words: lots\n"
                       "  pov: 7\nmodels:\n  primary: 3\n  extra: x\n", "story.yaml", issues)
    assert len(messages(issues, "error")) == 4
    assert len(messages(issues, "warning")) == 2  # 'colour' and 'models.extra'


def test_story_config_invalid_yaml(issues):
    parse_story_config("title: [unclosed\n", "story.yaml", issues)
    assert "isn't valid YAML" in messages(issues, "error")[0]


# --- chapter files -------------------------------------------------------------

def test_chapter_file_with_scenes(issues):
    text = ("---\nchapter: 2\ntitle: Ink and Salt\nversion: 3\nstatus: final\n---\n"
            "<!-- scene 1 -->\nThe ledger room smelled of brine.\n<!-- scene 2 -->\nA light moved.\n")
    chapter = parse_chapter_file(text, "chapters/ch02.md", 2, issues)
    assert not issues
    assert (chapter.title, chapter.version, chapter.status) == ("Ink and Salt", 3, "final")
    assert chapter.scenes == {1: "The ledger room smelled of brine.", 2: "A light moved."}
    assert chapter.words == 9


def test_chapter_file_mismatches_warn(issues):
    chapter = parse_chapter_file("---\nchapter: 3\nstatus: done\nversion: two\n---\nText.", "chapters/ch02.md",
                                 2, issues)
    assert chapter.number == 2 and chapter.status == "draft" and chapter.version == 1
    assert len(messages(issues, "warning")) == 3
