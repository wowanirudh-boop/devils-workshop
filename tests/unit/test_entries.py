from devils_workshop.project.entries import parse_entry
from devils_workshop.project.positions import Position

from tests.conftest import messages

FULL = """---
name: Mara Venn
aliases: [Mara, the clockmaker]
case_sensitive_aliases: [MV]
context: always
fields:
  occupation: Clockmaker
  status: well
---
# Mara Venn
Untitled opening text.

## Profile
Precise.

## Appearance
Ink-stained fingers.

## @ch3
Wears the watch.

## @ch4 add Habits
Hums while working.

## @ch5.2 replace Appearance
Burn scar.

## @ch7 set
status: missing

## secret @ch8
The ledger.
"""


HEAD = "---\nname: Mara Venn\n---\n"


def parse(text, issues, folder="characters", path="characters/mara-venn.md"):
    return parse_entry(text, path, folder, issues)


def test_full_entry(issues):
    entry = parse(FULL, issues)
    assert not issues, messages(issues)
    assert entry.name == "Mara Venn" and entry.type == "character" and entry.context == "always"
    assert entry.all_names() == ["Mara Venn", "Mara", "the clockmaker", "MV"]
    assert entry.fields == {"occupation": "Clockmaker", "status": "well"}
    assert [s.title for s in entry.sections] == [None, "Profile", "Appearance"]
    assert entry.sections[0].text == "Untitled opening text."  # '# Mara Venn' line dropped

    p = entry.progressions
    assert [(x.position, x.verb, x.section) for x in p] == [
        (Position(3), "add", None),
        (Position(4), "add", "Habits"),
        (Position(5, 2), "replace", "Appearance"),
        (Position(7), "set", None),
    ]
    assert p[0].text == "Wears the watch."
    assert p[3].fields == {"status": "missing"}
    assert entry.secrets[0].position == Position(8) and entry.secrets[0].text == "The ledger."
    assert entry.secrets[0].line == 31


def test_defaults_and_name_from_filename(issues):
    entry = parse("## Description\nA tower.", issues, folder="places", path="places/clock-tower.md")
    assert entry.name == "Clock Tower" and entry.type == "place" and entry.context == "when_mentioned"
    assert "No 'name:' set" in messages(issues, "warning")[0]


def test_unknown_setting_warns_with_hint(issues):
    parse("---\nname: A\nage: 34\n---\n", issues)
    assert issues[0].severity == "warning" and "fields:" in issues[0].hint


def test_bad_values(issues):
    parse("---\nname: A\ncontext: sometimes\ntype: monster\naliases: {a: 1}\nfields: [x]\n---\n", issues)
    errors = messages(issues, "error")
    assert any("context" in m for m in errors)
    assert any("type" in m for m in errors)
    assert any("aliases" in m for m in errors)
    assert any("fields" in m for m in errors)


def test_type_folder_mismatch_warns(issues):
    entry = parse("---\nname: A\ntype: place\n---\n", issues)
    assert entry.type == "place"
    assert "is in 'characters/'" in messages(issues, "warning")[0]


def test_single_string_alias_is_accepted(issues):
    assert parse("---\nname: A\naliases: Ace\n---\n", issues).aliases == ["Ace"]


def test_malformed_progressions(issues):
    text = ("## @5\nx\n## @ch5 remove\nx\n## @ch5 replace\nx\n## @ch5 set Appearance\nx: 1\n"
            "## @ch5 set\nnot a mapping\n## @ch0\nx\n")
    entry = parse(text, issues)
    assert entry.progressions == []
    assert len(messages(issues, "error")) == 6


def test_missing_at_sign_warns_and_keeps_section(issues):
    entry = parse(HEAD + "## ch5 add\nx\n## secret ch8\ny\n", issues)
    assert [s.title for s in entry.sections] == ["ch5 add", "secret ch8"]
    assert len([m for m in messages(issues, "warning") if "missing '@'" in m]) == 2


def test_ordinary_headings_starting_with_secret_or_chapter_are_sections(issues):
    entry = parse(HEAD + "## Secret history\nx\n## Chapter 3 thoughts\ny\n", issues)
    assert [s.title for s in entry.sections] == ["Secret history", "Chapter 3 thoughts"]
    assert not issues


def test_secret_with_bad_position(issues):
    entry = parse(HEAD + "## secret @soon\nx\n", issues)
    assert entry.secrets == [] and "Secret heading" in messages(issues, "error")[0]


def test_replace_unknown_section_warns(issues):
    parse(HEAD + "## Appearance\nx\n## @ch5 replace Apperance\ny\n", issues)
    assert "no section with that name" in messages(issues, "warning")[0]


def test_empty_progression_and_duplicate_section_warn(issues):
    parse(HEAD + "## Voice\na\n## Voice\nb\n## @ch2\n", issues)
    warnings = messages(issues, "warning")
    assert any("appears twice" in m for m in warnings)
    assert any("has no text" in m for m in warnings)


def test_headings_in_comments_are_ignored(issues):
    entry = parse(HEAD + "## Profile\nx\n<!--\n## @ch9\nold idea\n-->\n", issues)
    assert entry.progressions == [] and not issues
