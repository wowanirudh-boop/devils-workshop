import pytest

from devils_workshop.project.newproject import ProjectExistsError, create_project
from devils_workshop.project.project import NotAProjectError, load_project
from devils_workshop.tools.plancheck import format_report, run_plan_check

from tests.conftest import messages, settings, settings_file, write


def check(root):
    return run_plan_check(load_project(root))


# --- the sample story ------------------------------------------------------------

def test_sample_story_loads_completely(sample):
    project = load_project(sample)
    assert project.config.title == "The Saltmarsh Clock"
    assert sorted(e.name for e in project.entries) == [
        "Ada Quill", "Brindle Cove clock tower", "Harbour archive", "Mara Venn",
        "The harbour ledger", "Tobias Reed", "Will Harrow"]
    assert [s.name for s in project.subplots] == ["Old debts", "The forged ledger"]
    assert [c.number for c in project.outline.chapters] == [1, 2, 3, 4]
    assert list(project.chapters) == [1]
    assert project.system_prompt and all(project.bible.values())
    assert project.find_entry("the clockmaker").name == "Mara Venn"
    assert project.find_entry("WILL").name == "Will Harrow"
    assert project.find_entry("nobody") is None


def test_sample_story_passes_plan_check_cleanly(sample):
    report = check(sample)
    assert report.issues == [], format_report(report)
    assert report.ok


def test_sample_overview(sample):
    report = check(sample)
    rows = {r.number: r for r in report.chapters}
    assert (rows[1].target_words, rows[1].mode, rows[1].drafted_words) == (3200, "whole", 248)
    assert (rows[2].mode, rows[2].scenes, rows[2].drafted_words) == ("scenes", 2, None)
    assert dict(report.subplot_matrix) == {"Old debts": ["I", "A", "A", "R"],
                                           "The forged ledger": ["-", "I", "A", "C"]}
    text = format_report(report)
    assert "Errors: 0   Warnings: 0   Info: 0" in text and "The Missing Page" in text


# --- loading -------------------------------------------------------------------------

def test_not_a_project(tmp_path):
    with pytest.raises(NotAProjectError):
        load_project(tmp_path / "missing")
    with pytest.raises(NotAProjectError):
        load_project(tmp_path)


def test_skips_underscore_readme_and_non_markdown(sample):
    write(sample, "characters/_draft.md", "---\nname: Draft\n---\n")
    write(sample, "characters/README.md", "notes")
    write(sample, "characters/photo.jpg", "x")
    project = load_project(sample)
    assert project.find_entry("Draft") is None
    assert any("Not a .md file" in m for m in messages(project.issues, "info"))


def test_windows_1252_file_is_read_with_info(sample):
    (sample / "characters" / "ada-quill.md").write_bytes(
        b"---\nname: Ada Quill\n---\n## Profile\nCaf\xe9 regular.\n")
    project = load_project(sample)
    assert project.find_entry("Ada Quill").sections[0].text == "Café regular."
    assert any("Windows-1252" in m for m in messages(project.issues, "info"))


def test_badly_named_and_duplicate_chapter_files(sample):
    write(sample, "chapters/chapter-two.md", "x")
    write(sample, "chapters/ch1.md", "---\nchapter: 1\n---\nAgain.")
    write(sample, "chapters/ch02.partial.md", "streaming")  # in-progress files are ignored quietly
    project = load_project(sample)
    assert any("Two files for chapter 1" in m for m in messages(project.issues, "error"))
    assert len([m for m in messages(project.issues, "info") if "Not named like" in m]) == 1


# --- plan-check findings ---------------------------------------------------------------

def test_missing_core_files_warn(sample):
    for name in ("system_prompt.md", "bible/world_rules.md", "outline.md"):
        (sample / name).unlink()
    warnings = messages(check(sample).issues, "warning")
    assert any("no system prompt" in m for m in warnings)
    assert any("File missing" in m for m in warnings)
    assert any("no outline" in m for m in warnings)


def test_unknown_and_duplicate_names(sample):
    write(sample, "places/marsh.md", "---\nname: The marsh\naliases: [Mara]\n---\n")
    outline = (sample / "outline.md").read_text(encoding="utf-8")
    outline = outline.replace("characters: [Ada Quill]", "characters: [Ada Quil]\npov: The marsh")
    write(sample, "outline.md", outline)
    warnings = messages(check(sample).issues, "warning")
    assert any("'mara' is a name or alias of 2 entries" in m for m in warnings)
    assert any("'Ada Quil' doesn't match" in m for m in warnings)
    assert any("POV 'The marsh' is a place" in m for m in warnings)


def test_missing_default_pov_warns(sample):
    settings(sample, defaults__pov=None)
    assert any("No default POV" in m for m in messages(check(sample).issues, "warning"))


def test_positions_beyond_outline_and_bad_scene_keys(sample):
    path = sample / "characters" / "tobias-reed.md"
    text = path.read_text(encoding="utf-8")
    text += "\n## @ch12\nRetires.\n\n## @ch3.2\nScene key in a whole chapter.\n\n## @ch2.5\nNo scene 5.\n"
    write(sample, "characters/tobias-reed.md", text)
    warnings = messages(check(sample).issues, "warning")
    assert any("at ch12 is beyond the last outlined chapter (4)" in m for m in warnings)
    assert any("chapter 3 has no scenes" in m for m in warnings)
    assert any("chapter 2 has only 2 scene(s)" in m for m in warnings)


def test_secret_exposed_by_lookahead_warns(sample):
    outline = (sample / "outline.md").read_text(encoding="utf-8").replace("lookahead: false\n", "")
    write(sample, "outline.md", outline)
    issue = next(i for i in check(sample).issues if "secret is revealed at ch4.3" in i.message)
    assert issue.severity == "warning" and "lookahead: false" in issue.hint


def test_subplot_problems(sample):
    write(sample, "subplots/loose-end.md", "---\nname: Loose end\n---\n## ch9\nToo late.\n")
    write(sample, "subplots/later.md", "---\nname: Later\nstatus:\n  7: active\n---\n")
    warnings = messages(check(sample).issues, "warning")
    assert any("never included" in m for m in warnings)
    assert any("Note for chapter 9" in m for m in warnings)
    assert any("Status at chapter 7" in m for m in warnings)


def test_outline_content_checks(sample):
    outline = (sample / "outline.md").read_text(encoding="utf-8")
    outline += "\n# Chapter 5: Empty\n\n# Chapter 6: Huge\nwords: 30000\nmode: scenes\n- One beat.\n"
    write(sample, "outline.md", outline)
    write(sample, "chapters/ch09.md", "Stray chapter.")
    report = check(sample)
    warnings, infos = messages(report.issues, "warning"), messages(report.issues, "info")
    assert any("Chapter 5 has no instructions" in m for m in warnings)
    assert any("'words: 30000' looks unusual" in m for m in warnings)
    assert any("no chapter 9 in the outline" in m for m in warnings)
    assert any("will propose scenes" in m for m in infos)


def test_listed_never_entry_is_info(sample):
    path = sample / "characters" / "ada-quill.md"
    write(sample, "characters/ada-quill.md",
          path.read_text(encoding="utf-8").replace("context: when_mentioned", "context: never"))
    assert any("'context: never' but is listed" in m for m in messages(check(sample).issues, "info"))


def test_errors_make_report_not_ok(sample):
    write(sample, "characters/broken.md", "---\nname: [unclosed\n---\n")
    report = check(sample)
    assert not report.ok and report.issues[0].severity == "error"  # errors are listed first


# --- creating projects ---------------------------------------------------------------

def test_blank_project_has_no_errors(tmp_path):
    root = create_project(tmp_path / "new", title="Devil's Bargain: Part 1")
    report = check(root)
    assert report.ok
    assert report.title == "Devil's Bargain: Part 1"
    assert messages(report.issues, "warning") == ["No default POV character is set"]
    assert load_project(root).entries == []  # the _example files are ignored


def test_create_project_never_overwrites(tmp_path):
    target = tmp_path / "taken"
    write(target, "keep.txt", "mine")
    with pytest.raises(ProjectExistsError):
        create_project(target)
    assert (target / "keep.txt").read_text() == "mine"
    (tmp_path / "empty").mkdir()
    create_project(tmp_path / "empty")  # an empty folder is fine
