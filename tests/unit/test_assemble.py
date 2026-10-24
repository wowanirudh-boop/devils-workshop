import os
import shutil
from pathlib import Path

import pytest

from devils_workshop.context.assemble import AssemblyError, Overrides, assemble, format_preview
from devils_workshop.project.project import load_project

from tests.conftest import settings, settings_file, write

FIXTURES = Path(__file__).parent.parent / "fixtures"
GOLDEN = Path(__file__).parent.parent / "golden"


def with_written_scenes(root: Path) -> Path:
    for name in ("ch02.md", "ch04.md"):
        shutil.copy(FIXTURES / name, root / "chapters" / name)
    return root


def reasons(request):
    return {s.label: (s.included, s.reason) for s in request.sections}


# --- what gets included ------------------------------------------------------------------

def test_chapter_one_selection(sample):
    request = assemble(load_project(sample), 1)
    got = reasons(request)
    assert got["Mara Venn"] == (True, "POV character")
    assert got["Tobias Reed"] == (True, "listed in the outline")
    assert got["Brindle Cove clock tower"] == (True, "mentioned 2x")
    assert got["Ada Quill"] == (False, "not mentioned")
    assert got["Old debts"] == (True, "status: introduced")
    assert got["The forged ledger"] == (False, "status: dormant")
    assert [m.role for m in request.messages] == ["system", "user"]
    assert request.target_words == 3200 and request.warnings == []


def test_always_and_never_context(sample):
    path = sample / "characters" / "ada-quill.md"
    write(sample, "characters/ada-quill.md", path.read_text().replace("when_mentioned", "always"))
    path = sample / "places" / "brindle-cove-clock-tower.md"
    write(sample, "places/brindle-cove-clock-tower.md", path.read_text().replace("when_mentioned", "never"))
    got = reasons(assemble(load_project(sample), 1))
    assert got["Ada Quill"] == (True, "always included")
    assert got["Brindle Cove clock tower"] == (False, "context: never")


def test_system_message_has_story_prompt_then_instruction(sample):
    system = assemble(load_project(sample), 1).messages[0].content
    assert system.startswith('You are writing "The Saltmarsh Clock"')
    assert "Reply with the prose only" in system
    assert "<!--" not in system


def test_story_can_override_the_writing_instruction(sample):
    write(sample, "prompts/jobs/write.md", "Custom instruction.")
    assert assemble(load_project(sample), 1).messages[0].content.endswith("Custom instruction.")


def test_section_order_and_task_last(sample):
    user = assemble(load_project(sample), 1).messages[1].content
    order = [user.index(tag) for tag in ("<style_guide>", "<world_rules>", "<story_bible>", "<characters>",
                                         "<places_and_lore>", "<subplots>", "<task>")]
    assert order == sorted(order) and user.endswith("</task>")
    assert user.index('name="Mara Venn"') < user.index('name="Tobias Reed"')  # POV first


# --- overrides from the review screen -------------------------------------------------------

def test_overrides_exclude_include_and_extra_file(sample):
    project = load_project(sample)
    request = assemble(project, 1, overrides=Overrides(
        exclude={"bible:style_guide", "entry:characters/tobias-reed.md"},
        include={"entry:characters/ada-quill.md"},
        extra_files=["notes/ideas.md", "../outside.md", "notes/missing.md"]))
    text = request.full_text()
    assert "<style_guide>" not in text and 'name="Tobias Reed"' not in text
    assert 'name="Ada Quill"' in text and '<file path="notes/ideas.md">' in text
    got = reasons(request)
    assert got["Style guide"] == (False, "always included (unticked by you)")
    assert got["Ada Quill"] == (True, "added by you")
    assert any("outside the project folder" in w for w in request.warnings)
    assert any("doesn't exist" in w for w in request.warnings)


def test_unknown_override_key_warns(sample):
    request = assemble(load_project(sample), 1, overrides=Overrides(exclude={"entry:nobody.md"}))
    assert any("'entry:nobody.md' isn't a section" in w for w in request.warnings)


# --- scene mode ---------------------------------------------------------------------------

def test_scene_targets_split_evenly_and_respect_own_words(sample):
    project = load_project(sample)
    assert assemble(project, 4, scene=1).target_words == 1000
    outline = (sample / "outline.md").read_text().replace("## Scene 1: The key\n", "## Scene 1: The key\nwords: 1400\n")
    write(sample, "outline.md", outline)
    project = load_project(with_written_scenes(sample))
    assert assemble(project, 4, scene=1).target_words == 1400
    assert assemble(project, 4, scene=2).target_words == 800  # (3000 - 1400) / 2


def test_scene_chat_is_rebuilt_from_written_scenes(sample):
    project = load_project(with_written_scenes(sample))
    request = assemble(project, 4, scene=3, notes="Keep it quiet.")
    roles = [m.role for m in request.messages]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user"]
    opening, scene2, scene3 = request.messages[1], request.messages[3], request.messages[5]
    assert "A fresh burn" not in opening.content and "Years ago she helped" not in opening.content
    assert "<updates>\n- Mara Venn: From this scene on, Appearance becomes: A fresh burn" in scene2.content
    assert "- Mara Venn: Revealed in this scene: Years ago she helped her father recopy" in scene3.content
    assert "\n  has always suspected he changed a name" in scene3.content  # wrapped line stays in the bullet
    assert request.messages[2].content.startswith("The ledger-room key turned")
    assert "Continue directly from where the previous scene ended." in scene3.content
    assert "Notes for this request:\nKeep it quiet." in scene3.content
    assert "Keep it quiet." not in scene2.content


def test_later_scene_adds_newly_mentioned_files(sample):
    project = load_project(with_written_scenes(sample))
    # Will Harrow is in scene 2's beats, which scene 1 already sees as lookahead, so he is
    # in the opening message. Tobias is only mentioned in this request's notes.
    request = assemble(project, 2, scene=2, notes="Tobias watches from the harbour office.")
    opening, scene2 = request.messages[1].content, request.messages[3].content
    assert 'name="Will Harrow"' in opening and 'name="Will Harrow"' not in scene2
    assert "<new_reference>" in scene2 and 'name="Tobias Reed"' in scene2
    assert 'name="Tobias Reed"' not in opening
    tobias = next(s for s in request.sections if s.label == "Tobias Reed")
    assert tobias.included and tobias.turn == 2 and tobias.reason == "mentioned 1x"
    assert len([s for s in request.sections if s.label == "Tobias Reed"]) == 1


def test_beats_only_mode(sample):
    settings(sample, defaults__in_chapter_context="beats_only")
    request = assemble(load_project(sample), 4, scene=3)
    assert [m.role for m in request.messages] == ["system", "user"]
    user = request.messages[1].content
    assert "Earlier in this chapter (already written; shown as beats only):" in user
    assert "A fresh burn" in user and "recopy" in user  # rendered as of scene 3


def test_assembly_errors(sample):
    project = load_project(sample)
    with pytest.raises(AssemblyError, match="isn't in the outline"):
        assemble(project, 9)
    with pytest.raises(AssemblyError, match="whole-chapter mode"):
        assemble(project, 1, scene=1)
    with pytest.raises(AssemblyError, match="no scenes in the outline"):
        assemble(project, 1, mode="scenes")
    with pytest.raises(AssemblyError, match="no scene 5"):
        assemble(project, 4, scene=5)
    with pytest.raises(AssemblyError, match="hasn't been written yet"):
        assemble(project, 4, scene=2)


def test_size_warnings(sample):
    project = load_project(sample)
    request = assemble(project, 1, size_limit=100)
    assert request.size.over_limit and any("over your size limit of 100" in w for w in request.warnings)
    request = assemble(project, 1, context_window=50)
    assert request.size.exceeds_context and any("more than this model can accept" in w for w in request.warnings)


def test_missing_pov_and_system_prompt_warn(sample):
    (sample / "system_prompt.md").unlink()
    settings(sample, defaults__pov=None)
    request = assemble(load_project(sample), 1)
    assert "No point-of-view character is set for this chapter" in request.warnings
    assert "This story has no system prompt" in request.warnings
    assert request.messages[0].content.startswith("How to use this request")


# --- the no-leak guarantees ------------------------------------------------------------------

SCENES = {2: 2, 4: 3}  # chapters with scenes in the sample outline -> number of scenes


def positions():
    for chapter in (1, 2, 3, 4):
        yield chapter, 0
        for scene in range(1, SCENES.get(chapter, 0) + 1):
            yield chapter, scene


def marked_project(root: Path):
    """Add a unique marker for a progression and a secret at every position, plus subplot notes."""
    mara = root / "characters" / "mara-venn.md"
    text = mara.read_text()
    for chapter, scene in [*positions(), (9, 0)]:
        key = f"ch{chapter}.{scene}" if scene else f"ch{chapter}"
        text += f"\n## @{key}\nMARK-P-{chapter}-{scene}\n\n## secret @{key}\nMARK-S-{chapter}-{scene}\n"
    write(root, "characters/mara-venn.md", text)
    ledger = root / "subplots" / "the-forged-ledger.md"
    notes = "".join(f"\n## ch{c}\nMARK-N-{c}\n" for c in range(1, 6))
    write(root, "subplots/the-forged-ledger.md", ledger.read_text() + notes)
    return load_project(with_written_scenes(root))


def writing_points():
    for chapter in (1, 2, 3, 4):
        yield chapter, None
        for scene in range(1, SCENES.get(chapter, 0) + 1):
            yield chapter, scene


@pytest.mark.parametrize("chapter, scene", list(writing_points()))
def test_nothing_from_later_in_the_story_is_sent(sample, chapter, scene):
    project = marked_project(sample)
    mode = "whole" if scene is None else "scenes"
    text = assemble(project, chapter, scene, mode=mode).full_text()
    writing = (chapter, scene or 0)

    for c, s in [*positions(), (9, 0)]:
        expected = (c, s) <= writing or (scene is None and c == chapter)
        for kind in ("P", "S"):
            marker = f"MARK-{kind}-{c}-{s}"
            assert (marker in text) == expected, f"{marker} while writing {writing} ({mode})"

    included_subplot = chapter in (2, 3, 4)  # The forged ledger: introduced/active/climax
    for c in range(1, 6):
        assert (f"MARK-N-{c}" in text) == (c == chapter and included_subplot), f"note {c} at {writing}"


@pytest.mark.parametrize("chapter, scene", list(writing_points()))
def test_no_prose_from_other_chapters_is_sent(sample, chapter, scene):
    project = load_project(with_written_scenes(sample))
    text = assemble(project, chapter, scene, mode="whole" if scene is None else "scenes").full_text()
    other_chapters = {1: "Brindle Cove woke to the wrong kind of quiet.",
                      2: "Different ink,\" Ada said at last",
                      4: "The ledger-room key turned"}
    for number, sentence in other_chapters.items():
        if number != chapter:
            assert sentence not in text


# --- golden previews (reviewed by hand; regenerate with DW_UPDATE_GOLDEN=1) --------------------

GOLDEN_CASES = {
    "ch1_whole.txt": (1, None, None),
    "ch2_scene1.txt": (2, 1, None),
    "ch2_scene2.txt": (2, 2, None),
    "ch4_whole.txt": (4, None, "whole"),
    "ch4_scene3.txt": (4, 3, None),
}


@pytest.mark.parametrize("name", sorted(GOLDEN_CASES))
def test_golden_preview(sample, name):
    chapter, scene, mode = GOLDEN_CASES[name]
    project = load_project(with_written_scenes(sample))
    preview = format_preview(assemble(project, chapter, scene, mode=mode)) + "\n"
    golden = GOLDEN / name
    if os.environ.get("DW_UPDATE_GOLDEN") == "1":
        golden.write_text(preview, encoding="utf-8")
    assert golden.read_text(encoding="utf-8") == preview, f"{name} changed; review and rerun with DW_UPDATE_GOLDEN=1"
