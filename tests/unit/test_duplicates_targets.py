"""A document sent twice (e.g. a handoff as .md and as .docx), lengths stated in the outline, and scenes
written one after another by default."""

from devils_workshop.history import list_snapshots, restore
from devils_workshop.project.duplicates import find_duplicates, similarity
from devils_workshop.project.file_ops import add_own_file, duplicate_of, stop_sending
from devils_workshop.project.own_files import NOT_SENT, OUTLINE, stated_words, settings_text
from devils_workshop.project.project import load_project
from devils_workshop.settings import load_settings

from tests.conftest import write
from tests.unit.test_own_files import make_docx

HANDOFF = ["# Thirst handoff", "Read this first: the story contract and the chapter briefs.",
           "## Chapter 01 — The Bench Facing Forward",
           "**DAY 1 · I · THE WRECK AND THE WATCH · Target 5,000 words**",
           "Purpose: open on the lifeboat at dawn and set up who holds the water.",
           "### Scene 01: Consciousness in salt", "*Pre-dawn · stern-facing fixed bench · 900 words*",
           "Oren wakes on the bench with salt in his mouth and counts the oars he can see.",
           "### Scene 02: The ship in broken flashes", "*Dawn · the bench · 1,100 words*",
           "Memories of the sinking come back in pieces between the waves and the shouting.",
           "## Chapter 02 — The Mathematics of Water", "**DAY 2 · Target 4,700 words**",
           "Purpose: rationing turns into the boat's first politics, quietly and then not.",
           "### Scene 01: A visible inventory", "*Morning · supplies in view · 800 words*",
           "The water tins are lined up where everyone can see them and nobody touches them."]


def handoff_story(tmp_path, roles: dict[str, str]):
    root = tmp_path / "Thirst"
    write(root, "Handoff.md", "\n\n".join(HANDOFF))
    make_docx(root / "Handoff.docx", [line.lstrip("#* ").rstrip("*") for line in HANDOFF])
    write(root, "World.md", "The sea, the two lifeboats and the people in them. " * 20)
    write(root, "story.yaml", settings_text(roles, "Thirst", 3000))
    return root


def test_the_same_document_in_two_formats():
    text = "\n".join(HANDOFF)
    assert similarity(text, text.replace("**", "").replace("#", "")) > 0.95
    assert similarity(text, "Something else entirely about gardens and bees. " * 30) < 0.2
    assert similarity("too short", "too short") == 0.0  # a near-empty file says nothing
    found = find_duplicates({"Handoff.docx": text.replace("#", ""), "Handoff.md": text, "Other.md": "bees " * 80})
    assert [(f.keep, f.copy) for f in found] == [("Handoff.md", "Handoff.docx")]  # the .md is kept


def test_both_copies_sent_is_a_warning_with_a_one_click_fix(tmp_path):
    root = handoff_story(tmp_path, {"Handoff.md": OUTLINE, "Handoff.docx": OUTLINE, "World.md": "every chapter"})
    project = load_project(root)
    assert [(d.keep, d.copy) for d in project.duplicates] == [("Handoff.md", "Handoff.docx")]
    warning = next(i for i in project.issues if i.file == "Handoff.docx" and i.severity == "warning")
    assert "Same document as 'Handoff.md'" in warning.message and "scenes twice" in warning.message
    assert len(project.outline.chapter(1).scenes) == 4  # each scene twice: what the warning is about

    stop_sending(root, "Handoff.docx")
    fixed = load_project(root)
    assert fixed.config.own_files["Handoff.docx"] == NOT_SENT and fixed.duplicates == []
    assert [s.title for s in fixed.outline.chapter(1).scenes] == ["Consciousness in salt", "The ship in broken flashes"]
    restore(root, list_snapshots(root)[0].id)  # Undo in History
    assert load_project(root).config.own_files["Handoff.docx"] == OUTLINE


def test_a_copy_added_later_starts_as_not_sent(tmp_path):
    root = handoff_story(tmp_path, {"Handoff.md": OUTLINE, "World.md": "every chapter"})
    data = (root / "Handoff.docx").read_bytes()
    (root / "Handoff.docx").unlink()
    rel, role = add_own_file(root, "Handoff copy.docx", data)
    assert role == NOT_SENT and duplicate_of(root, rel, load_project(root).config.own_files) == "Handoff.md"
    _, role = add_own_file(root, "Notes.md", b"Ideas for later chapters, nothing like the handoff at all. " * 10)
    assert role != NOT_SENT


def test_lengths_stated_in_the_outline_are_used(tmp_path):
    root = handoff_story(tmp_path, {"Handoff.md": OUTLINE})
    outline = load_project(root).outline
    one, two = outline.chapters
    assert one.settings == {"words": 5000} and two.settings == {"words": 4700}
    assert [s.settings for s in one.scenes] == [{"words": 900}, {"words": 1100}]
    assert two.scenes[0].settings == {"words": 800}


def test_what_counts_as_a_stated_length():
    assert stated_words("**DAY 2 · I · THE WRECK · Target 4,700 words**\nPurpose: …", 4, "chapter") == {"words": 4700}
    assert stated_words("Target length: about 5000 words", 4, "chapter") == {"words": 5000}
    assert stated_words("*Pre-dawn · stern-facing fixed bench · 900 words*", 2, "scene") == {"words": 900}
    assert stated_words("Night (1,200 words)", 2, "scene") == {"words": 1200}
    assert stated_words("He had sent her 500 words of apology.", 2, "scene") == {}  # a sentence, not a length
    assert stated_words("*Morning · 900 words*", 2, "chapter") == {}  # chapters need the word Target
    assert stated_words("Intro\nMore\nStill more\nYet more\nTarget 3,000 words", 4, "chapter") == {}  # too far down
    assert stated_words("Target 50 words", 4, "chapter") == {}  # not a believable chapter


def test_an_explicit_words_line_wins(tmp_path):
    root = tmp_path / "S"
    write(root, "Outline.md", "Chapter 1: One\nwords: 2000\nTarget 5,000 words\nSomething happens.\n\n"
                              "Chapter 2: Two\nOther things happen, at length.\n")
    write(root, "story.yaml", settings_text({"Outline.md": OUTLINE}, "S", 3000))
    one, two = load_project(root).outline.chapters
    assert one.settings == {"words": 2000} and two.settings == {}  # chapter 2: the story's words per chapter


def test_scenes_one_after_another_from_now_on(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("scenes_together: true\n", encoding="utf-8")  # chosen (or defaulted) before v0.13
    assert load_settings(path).scenes_together is False
    path.write_text("scenes_together: true\nsettings_version: 2\n", encoding="utf-8")  # chosen again since
    assert load_settings(path).scenes_together is True
    fresh = load_settings(tmp_path / "new.yaml")
    assert fresh.scenes_together is False and fresh.settings_version == 3
