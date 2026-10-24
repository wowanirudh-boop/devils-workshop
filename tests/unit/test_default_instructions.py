"""Default instructions: files added once in the app and used by every story."""

from devils_workshop.chat.scenes import prepare_scene_proposal
from devils_workshop.context.assemble import assemble
from devils_workshop.project.default_instructions import (
    BOTH,
    PLANNER_NOTES,
    folder,
    guess_role,
    list_files,
    load_defaults,
    load_roles,
    save_roles,
)
from devils_workshop.project.own_files import EVERY, NOT_SENT, SYSTEM
from devils_workshop.project.project import load_project
from devils_workshop.tools.plancheck import run_plan_check

from tests.conftest import messages, settings, settings_file, write
from tests.unit.test_own_files import make_docx


def add_defaults(files: dict[str, str], roles: dict[str, str] | None = None) -> None:
    for name, text in files.items():
        write(folder(), name, text)
    if roles is not None:
        save_roles(roles)


def test_files_roles_and_guesses():
    add_defaults({"My system prompt.md": "x", "House style.txt": "y", "Voice.json": '{"tone": "dry"}'})
    (folder() / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0 a picture")
    make_docx(folder() / "Rules.docx", ["RULES-MARKER Never explain the joke."])
    (folder() / "Broken.docx").write_bytes(b"not a zip")
    # any text file counts; a picture (and roles.yaml) doesn't
    assert list_files() == ["Broken.docx", "House style.txt", "My system prompt.md", "Rules.docx", "Voice.json"]
    assert guess_role("My system prompt.md") == SYSTEM and guess_role("House style.txt") == BOTH
    save_roles({"House style.txt": NOT_SENT, "Rules.docx": "nonsense"})
    assert load_roles() == {"House style.txt": NOT_SENT}  # an unknown role is dropped (the guess is used)
    found = {f.name: f for f in load_defaults()}
    assert found["Rules.docx"].text.startswith("RULES-MARKER") and found["Rules.docx"].role == BOTH
    assert found["Broken.docx"].problem and not found["Broken.docx"].text


def test_writer_and_planner_files_reach_both(sample):
    from devils_workshop.planner.prompts import planner_instructions
    add_defaults({"01 - Goal.txt": "GOAL-MARKER A slow-burn survival story.",
                  "Prose.md": "PROSE-MARKER Short sentences.", "Planning.md": "PLAN-MARKER Never over 15 chapters."},
                 roles={"Prose.md": EVERY, "Planning.md": PLANNER_NOTES})  # Goal: not chosen, so both
    writer = assemble(load_project(sample), 4, mode="whole").full_text()
    planner = planner_instructions(sample)
    assert "GOAL-MARKER" in writer and "PROSE-MARKER" in writer and "PLAN-MARKER" not in writer
    assert "GOAL-MARKER" in planner and "PLAN-MARKER" in planner and "PROSE-MARKER" not in planner
    assert 'file="01 - Goal.txt"' in planner
    # the writer-only file reaches the planner as the writer's rules to plan within, so nothing it plans asks
    # the writer for what they forbid (a writer-and-planner one is the planner's own instruction already)
    from devils_workshop.planner.prompts import writer_instructions
    told = writer_instructions(sample)
    assert "PROSE-MARKER" in told and "GOAL-MARKER" not in told and "PLAN-MARKER" not in told
    settings(sample, default_instructions=False)  # a story that switches them off: the planner gets none either
    assert planner_instructions(sample) == "" and "PROSE-MARKER" not in writer_instructions(sample)


def test_every_story_gets_them_system_prompt_first(sample):
    add_defaults({"System.md": "DEFAULT-SYSTEM-MARKER You write literary mysteries.",
                  "House style.md": "DEFAULT-STYLE-MARKER Short sentences.",
                  "Unused.md": "DEFAULT-UNUSED-MARKER"},
                 {"System.md": SYSTEM, "House style.md": EVERY, "Unused.md": NOT_SENT})
    project = load_project(sample)
    request = assemble(project, 3, mode="whole")
    system, user = request.messages[0].content, request.messages[1].content
    assert system.index("DEFAULT-SYSTEM-MARKER") < system.index("Saltmarsh Clock")  # before the story's own
    # the writer's instructions come last in the system message, after the built-in ones, so theirs win
    assert system.index("Reply with the prose only") < system.index("DEFAULT-STYLE-MARKER")
    assert "DEFAULT-STYLE-MARKER" not in user and "DEFAULT-UNUSED-MARKER" not in request.full_text()
    section = next(s for s in request.sections if s.key == "doc:default:House style.md")
    assert section.reason == "your default instructions" and section.included
    plan = prepare_scene_proposal(project, 3)  # scene planning gets them too
    assert "DEFAULT-SYSTEM-MARKER" in plan.messages[0].content and "DEFAULT-STYLE-MARKER" in plan.messages[0].content


def test_a_story_can_switch_them_off(sample):
    add_defaults({"House style.md": "DEFAULT-STYLE-MARKER Short sentences."})
    settings(sample, default_instructions=False)
    project = load_project(sample)
    assert not messages(project.issues, "error")
    assert "DEFAULT-STYLE-MARKER" not in assemble(project, 3, mode="whole").full_text()


def test_a_file_the_story_already_has_isnt_sent_twice(tmp_path):
    root = tmp_path / "My Story"
    write(root, "Style.md", "SAME-MARKER Short sentences.\n")
    write(root, "Plot.md", "# Chapter 1\nMara arrives.\n")
    write(root, "story.yaml", 'title: Mine\nfiles:\n  "Style.md": every chapter\n  "Plot.md": chapter outline\n')
    add_defaults({"House style.md": "SAME-MARKER   Short sentences."})
    text = assemble(load_project(root), 1, mode="whole").full_text()
    assert text.count("SAME-MARKER") == 1


def test_no_system_prompt_warning_when_a_default_gives_one(tmp_path):
    root = tmp_path / "My Story"
    write(root, "Plot.md", "# Chapter 1\nMara arrives.\n")
    write(root, "story.yaml", 'title: Mine\nfiles:\n  "Plot.md": chapter outline\n')
    assert "No file is marked as the system prompt" in messages(run_plan_check(load_project(root)).issues)
    add_defaults({"System prompt.md": "You write mysteries."})
    project = load_project(root)
    assert "No file is marked as the system prompt" not in messages(run_plan_check(project).issues)
    assert "This story has no system prompt" not in assemble(project, 1, mode="whole").warnings
