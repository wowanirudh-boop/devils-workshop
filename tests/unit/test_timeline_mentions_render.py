from devils_workshop.context.mentions import MentionFinder
from devils_workshop.context.render import entry_state, render_entry, render_subplot, scene_updates
from devils_workshop.context.timeline import ACTIVE, FUTURE, IN_THIS_CHAPTER, WritingPoint, visibility
from devils_workshop.project.entries import parse_entry
from devils_workshop.project.issues import IssueList
from devils_workshop.project.positions import Position
from devils_workshop.project.subplots import parse_subplot


def entry(text, path="characters/x.md", folder="characters"):
    issues = IssueList()
    parsed = parse_entry(text, path, folder, issues)
    assert not issues, [i.message for i in issues]
    return parsed


# --- the timeline rule ---------------------------------------------------------------

def test_visibility_scene_mode():
    point = WritingPoint(5, 2)
    assert visibility(Position(4, 3), point) == ACTIVE
    assert visibility(Position(5), point) == ACTIVE
    assert visibility(Position(5, 2), point) == ACTIVE
    assert visibility(Position(5, 3), point) == FUTURE
    assert visibility(Position(6), point) == FUTURE


def test_visibility_whole_chapter_mode():
    point = WritingPoint(5)
    assert visibility(Position(5), point) == ACTIVE
    assert visibility(Position(4, 9), point) == ACTIVE
    assert visibility(Position(5, 1), point) == IN_THIS_CHAPTER
    assert visibility(Position(5, 7), point) == IN_THIS_CHAPTER
    assert visibility(Position(6), point) == FUTURE


# --- mentions ------------------------------------------------------------------------

PEOPLE = [
    entry("---\nname: Mara Venn\naliases: [Mara, the clockmaker]\n---\n", "characters/mara.md"),
    entry("---\nname: Will Harrow\naliases: [Harrow]\ncase_sensitive_aliases: [Will]\n---\n", "characters/will.md"),
    entry("---\nname: The harbour ledger\naliases: [the ledger]\n---\n", "lore/ledger.md", "lore"),
    entry("---\nname: Harbour archive\naliases: [the ledger room]\n---\n", "places/archive.md", "places"),
]


def test_mentions_names_aliases_case_and_possessives():
    counts = MentionFinder(PEOPLE).count(["MARA's lamp. The Clockmaker said so. Mara Venn nodded."])
    assert counts == {"characters/mara.md": 3}  # "Mara Venn" counts once, not as Mara + Mara Venn


def test_mentions_whole_words_only():
    assert MentionFinder(PEOPLE).count(["Maradona and Marabel were not here."]) == {}
    assert MentionFinder(PEOPLE).count(["The ledger-room key turned."]) == {}  # hyphen joins words


def test_case_sensitive_alias():
    finder = MentionFinder(PEOPLE)
    assert finder.count(["She will go. You will see."]) == {}
    assert finder.count(["Will saw the light."]) == {"characters/will.md": 1}
    assert finder.count(["old harrow by the boats"]) == {"characters/will.md": 1}  # normal alias


def test_longest_name_wins_when_names_overlap():
    counts = MentionFinder(PEOPLE).count(["She opened the ledger room, then the ledger."])
    assert counts == {"places/archive.md": 1, "lore/ledger.md": 1}


def test_multi_word_name_across_line_break_and_comments_ignored():
    counts = MentionFinder(PEOPLE).count(["Mara\nVenn", "<!-- Mara -->"])
    assert counts == {"characters/mara.md": 1}


# --- rendering entries "as of" a point -------------------------------------------------

MARA = """---
name: Mara Venn
aliases: [Mara]
fields:
  status: well
---
## Profile
Precise.

## Appearance
Ink-stained fingers.

## @ch2
Carries the watch.

## @ch3 add Appearance
A ring of clock wire on her thumb.

## @ch4.2 replace Appearance
A burn on her palm.

## @ch5 set
status: missing

## secret @ch4.3
She recopied the page.

## secret @ch9
Far-future secret.
"""


def test_entry_state_applies_only_active_changes_in_order():
    mara = entry(MARA)
    state = entry_state(mara, WritingPoint(3))
    appearance = next(s for s in state.sections if s.title == "Appearance")
    assert appearance.text == "Ink-stained fingers.\n\nA ring of clock wire on her thumb."
    assert appearance.changed_at == Position(3)
    assert state.developments == [(Position(2), "Carries the watch.")]
    assert state.fields == {"status": "well"}

    state = entry_state(mara, WritingPoint(5, 1))
    appearance = next(s for s in state.sections if s.title == "Appearance")
    assert appearance.text == "A burn on her palm." and appearance.changed_at == Position(4, 2)
    assert state.fields == {"status": "missing"} and state.field_changes == {"status": Position(5)}
    assert (Position(4, 3), "She recopied the page.") in state.developments
    assert all("Far-future" not in text for _, text in state.developments)


def test_whole_chapter_lists_changes_during_the_chapter():
    text = render_entry(entry(MARA), WritingPoint(4))
    assert "## Changes during this chapter" in text
    assert "- From scene 2, Appearance becomes: A burn on her palm." in text
    assert "- Revealed in scene 3 (don't reveal or hint at it before then): She recopied the page." in text
    assert "## Appearance (as of ch3)" in text  # the scene-2 replacement isn't applied up front
    assert "missing" not in text and "Far-future" not in text


def test_scene_mode_renders_without_later_scene_changes():
    text = render_entry(entry(MARA), WritingPoint(4, 1))
    assert "burn" not in text and "recopied" not in text and "Changes during" not in text
    text = render_entry(entry(MARA), WritingPoint(4, 3))
    assert "A burn on her palm." in text and "She recopied the page." in text


def test_scene_updates_for_a_new_scene():
    mara = entry(MARA)
    assert scene_updates(mara, 4, 1, 2) == ["From this scene on, Appearance becomes: A burn on her palm."]
    assert scene_updates(mara, 4, 2, 3) == ["Revealed in this scene: She recopied the page."]
    assert scene_updates(mara, 4, 3, 4) == []


def test_render_entry_fields_role_and_comments():
    text = render_entry(entry("---\nname: Ada\naliases: [Miss \"Q\"]\nfields:\n  age: 71\n---\n"
                              "## Profile\nKind.<!-- secret note -->\n"), WritingPoint(1), role="point of view")
    assert text.startswith('<entry name="Ada" type="character" aliases="Miss &quot;Q&quot;" role="point of view">')
    assert "age: 71" in text and "secret note" not in text and text.endswith("</entry>")


def test_subplot_render_only_current_chapter_note():
    issues = IssueList()
    subplot = parse_subplot("---\nname: SP\nstatus:\n  1: active\n---\n## Summary\nS.\n## ch1\nNow.\n## ch2\nLater.\n",
                            "subplots/sp.md", issues)
    text = render_subplot(subplot, 1)
    assert 'status="active"' in text and "Now." in text and "Later." not in text
    assert "Later." in render_subplot(subplot, 2) and "Now." not in render_subplot(subplot, 2)
