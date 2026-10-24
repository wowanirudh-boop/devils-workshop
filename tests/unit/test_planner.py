"""The story planner: brainstorming the plan, then chapters, scenes and one brief per scene."""

import asyncio
import json
import re

import pytest

from devils_workshop.history import list_snapshots, restore
from devils_workshop.llm.catalog import Catalog, parse_model
from devils_workshop.planner import prompts
from devils_workshop.planner.inputs import InputError, add_input, input_text, list_inputs
from devils_workshop.planner.output import (
    ARCHITECTURE_FILE,
    CONTINUITY_FILE,
    GUIDE_FILE,
    OUTLINE_FILE,
    PLAN_FILE,
    create_planner_story,
)
from devils_workshop.planner.run import (
    FAILED_RUN,
    FINISHED,
    STOPPED,
    PlanRun,
    approve,
    brainstorm,
    redo_scene,
)
from devils_workshop.planner.state import BRAINSTORM, DONE, PLANNER, READY, USER, PlannerState
from devils_workshop.project.newproject import ProjectExistsError
from devils_workshop.project.own_files import EVERY, NOT_SENT, OUTLINE
from devils_workshop.project.project import load_project
from devils_workshop.settings import AppSettings

from tests.fakes import MODELS, FakeOpenRouter, reply
from tests.unit.test_own_files import make_docx, make_pdf

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 40
SEEING = Catalog([parse_model({**m, "architecture": {"input_modalities": ["text", "image"]}}) for m in MODELS])
BLIND = Catalog([parse_model({**m, "architecture": {"input_modalities": ["text"]}}) for m in MODELS])

PLAN_REPLY = ("<plan>\n## Title\nSalt and Iron\n## Length\n2 chapters, about 6,000 words\n</plan>\n"
              "<reply>\nI set the length.\n1. Give Mara a secret. It raises the stakes.\nApprove, or tell me what to "
              "change.\n</reply>")
ARCHITECTURE = {"chapters": [
    {"number": 1, "title": "The Stopped Clock", "purpose": "Set up the mystery", "words": 3500, "scenes": 2,
     "beats": ["Mara finds the clock stopped"], "ending": "A page is missing"},
    {"number": 2, "title": "Low Tide", "purpose": "The causeway", "words": "2,500", "pov": "Mara", "scenes": 2},
]}


@pytest.fixture
def story(tmp_path):
    return create_planner_story(tmp_path / "Salt", "Salt and Iron")


def planner_answers(body: dict):
    """Answer each planner job the way a model would."""
    system, task = " ".join(body["messages"][0]["content"].split()), body["messages"][-1]["content"]
    if isinstance(task, list):  # text, then pictures
        task = task[0]["text"]
    if "You check one scene brief" in system:
        return reply("<ok/>")
    if "You keep the canon of a novel" in system:
        return reply('<fact id="F1" kind="relationship" from="1" subject="Mara">CANON-MARKER Mara is the keeper\'s '
                     "niece</fact>")
    if "You check scenes of a novel as they were actually written" in system:
        return reply(audit_reply(task))
    if "Write the notes the planner will work from" in system:
        name = re.search(r'name="([^"]+)"', task).group(1)
        return reply(f"NOTES-MARKER on {name}: a tall woman with salt-grey hair.")
    if "story development partner" in system:
        return reply(PLAN_REPLY)
    if "Write the writer's guide" in system:
        return reply("## Voice and style\nGUIDE-MARKER Close third person, past tense.\n## Characters\n- Mara")
    if "design the novel's chapter architecture" in system:
        return reply("```json\n" + json.dumps(ARCHITECTURE) + "\n```")
    if "Split the chapter" in system:
        number = 1 if "Chapter 1:" in task.split("Plan the scenes for this chapter:")[1] else 2
        count = int(re.search(r"Number of scenes: (\d+)", task).group(1))
        purposes = ["Open", "Turn"] + [f"Then part {n}" for n in range(3, count + 1)]
        return reply(json.dumps({"scenes": [{"title": f"C{number} first", "purpose": "Open", "words": 1500},
                                            {"title": f"C{number} second", "purpose": "Turn"}][:count] +
                                [{"title": f"C{number} part {n}", "purpose": purposes[n - 1]}
                                 for n in range(3, count + 1)]}))
    scene = task.split("Write the brief for scene ")[1].split(":")[0]  # "1 of chapter 2"
    return reply(f"<brief>\n**Coming in:** BRIEF-MARKER for scene {scene}.\n**What happens:**\n"
                 "1. Scene 3 is mentioned in passing\n2. Something happens.\n</brief>\n"
                 f"<continuity>\n- STATE-MARKER after scene {scene}\n</continuity>")


def audit_reply(task: str, extra: dict | None = None) -> str:
    """The check of scenes as written, the way a model would answer when every scene does what was planned: each
    of the chapter's events happens in the scene planned for it (the last scene, if none is). extra: scene number
    -> more lines for that scene's block."""
    chapter = int(re.search(r"of chapter (\d+) as written", task).group(1))
    numbers = [int(n) for n in re.findall(r'<scene number="(\d+)"', task)]
    events = re.findall(r"- \[(E-\d+-\w+)\] \(planned for chapter (\d+)(?:, scene (\d+))?; not happened yet", task)
    last = max(numbers)
    blocks = []
    for n in numbers:
        mine = [e for e, c, s in events if int(c) == chapter and (int(s or 0) == n or (not s and n == last))]
        lines = [f'<event id="{e}" how="happens"/>' for e in mine]
        lines += (extra or {}).get(n, [])
        blocks.append(f'<scene number="{n}">\n' + "\n".join(lines) + f'\n<entry ok="yes"/>\n'
                      f"<exit>EXIT-MARKER after chapter {chapter}, scene {n}</exit>\n</scene>")
    return "\n".join(blocks)


def run_plan(root, fake, catalog=SEEING, at_once: int = 3, **options) -> PlanRun:
    plan_run = PlanRun(root, AppSettings(at_once=at_once), catalog, fake.client, sleep=lambda s: asyncio.sleep(0),
                       **options)
    asyncio.run(plan_run.run())
    return plan_run


def approved(story, jit: bool = False) -> PlannerState:
    """An approved plan. jit=False plans every brief up front (the tests of a whole run); the default for you
    is jit=True, each chapter's briefs just before it is written."""
    state = PlannerState.load(story)
    state.plan = "## Title\nSalt and Iron\n## Rules\nPLAN-MARKER Never kill the dog."
    state.jit = jit
    approve(state)
    return state


# --- the story folder and the planner's state ---------------------------------------------------------

def test_a_new_planner_story(story, tmp_path):
    state = PlannerState.load(story)
    assert state.title == "Salt and Iron" and state.stage == BRAINSTORM
    project = load_project(story)
    assert project.own_files and project.config.mode == "scenes" and project.outline.chapters == []
    with pytest.raises(ProjectExistsError):
        create_planner_story(story, "Again")


def test_inputs_any_text_pdf_word_and_pictures(story, tmp_path):
    assert add_input(story, "Idea.txt", b"A clockmaker's daughter.") == "Idea.txt"
    assert add_input(story, "Idea.txt", b"A second idea.") == "Idea (2).txt"  # nothing earlier is replaced
    add_input(story, "notes.json", b'{"tone": "dry"}')
    add_input(story, "World.pdf", make_pdf(tmp_path / "w.pdf", [["PDF-MARKER the island"]]).read_bytes())
    add_input(story, "Cast.docx", make_docx(tmp_path / "c.docx", ["DOCX-MARKER Mara"]).read_bytes())
    add_input(story, "Mara.png", PNG)
    with pytest.raises(InputError, match="isn't a text file"):
        add_input(story, "song.mp3", b"ID3...")
    with pytest.raises(InputError, match="at most 5 MB"):
        add_input(story, "huge.jpg", b"\xff" * (5 * 1024 * 1024 + 1))
    assert list_inputs(story) == ["Cast.docx", "Idea (2).txt", "Idea.txt", "Mara.png", "notes.json", "World.pdf"]
    assert "PDF-MARKER" in input_text(story, "World.pdf")[0] and "DOCX-MARKER" in input_text(story, "Cast.docx")[0]
    assert load_project(story).outline.chapters == []  # planner/ is the app's own folder: never a story file


def test_reading_replies():
    assert prompts.split_brainstorm(PLAN_REPLY)[0].startswith("## Title")
    assert prompts.split_brainstorm(PLAN_REPLY)[1].startswith("I set the length.")
    assert prompts.split_brainstorm("Just a question: what genre?") == (None, "Just a question: what genre?")
    assert prompts.split_brainstorm("<plan>\n## Title\ncut off") == (None, "")  # an unfinished plan isn't used
    chapters = prompts.chapters_from(prompts.parse_json_list("Here:\n" + json.dumps(ARCHITECTURE), "chapters"))
    assert [(c.number, c.words) for c in chapters] == [(1, 3500), (2, 2500)]
    with pytest.raises(ValueError, match="no chapters"):
        prompts.parse_json_list('{"chapter": []}', "chapters")
    scenes = prompts.scenes_from([{"title": "A", "words": 1000}, {"title": "B"}, {"title": "C"}], 3000)
    assert [s.words for s in scenes] == [1000, 1000, 1000]


# --- stage 1: brainstorming ---------------------------------------------------------------------------

def test_brainstorm_works_from_notes_on_what_you_attach_and_sends_each_picture_once(story):
    add_input(story, "Idea.md", b"IDEA-MARKER A clockmaker's daughter on a tidal island.")
    add_input(story, "Mara.png", PNG)
    fake = FakeOpenRouter().respond(planner_answers)
    state = PlannerState.load(story)
    result = asyncio.run(brainstorm(state, "Here's my idea; this is Mara.", ["Idea.md", "Mara.png"],
                                    settings=AppSettings(), catalog=SEEING, client=fake.client()))
    assert result.ok and state.plan.startswith("## Title\nSalt and Iron")
    assert [t.role for t in state.turns] == [USER, PLANNER] and state.turns[1].text.startswith("I set the length")
    notes_request, first = fake.chat_bodies()  # the short text file needs no request; the picture does
    parts = notes_request["messages"][1]["content"]
    assert "this is Mara" in parts[0]["text"] and parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert state.notes["Idea.md"].startswith("IDEA-MARKER") and "NOTES-MARKER on Mara.png" in state.notes["Mara.png"]
    sent = first["messages"][1]["content"]
    assert isinstance(sent, str) and "IDEA-MARKER" in sent and "NOTES-MARKER on Mara.png" in sent  # from the notes
    assert "(Attached: Idea.md, Mara.png;" in sent
    assert "NOTES-MARKER on Mara.png" in (story / "Source notes.md").read_text(encoding="utf-8")

    state.plan += "\nMY-EDIT-MARKER"  # you edit the plan by hand
    fake.respond(lambda body: reply("Yes. I'll keep it."))
    asyncio.run(brainstorm(state, "Yes to 1.", [], settings=AppSettings(), catalog=SEEING, client=fake.client()))
    second = fake.chat_bodies()[2]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert all(isinstance(m["content"], str) for m in second)  # the picture is never sent again
    assert "<plan>" not in second[2]["content"]  # only the reply is kept in the chat; the plan goes in once
    assert "<current_plan>" in second[3]["content"] and "MY-EDIT-MARKER" in second[3]["content"]
    assert state.turns[-1].text == "Yes. I'll keep it." and "MY-EDIT-MARKER" in state.plan  # a short reply is fine

    saved = (next((story / "logs").rglob("plan-notes-*/attempt-1.request.json"))).read_text(encoding="utf-8")
    assert "base64" not in saved and "<picture," in saved  # logs keep the picture's size, not its data
    assert PlannerState.load(story).turns[-1].text == "Yes. I'll keep it."


def test_a_model_that_cant_see_pictures_gets_their_names(story):
    add_input(story, "Mara.png", PNG)
    fake = FakeOpenRouter().queue(reply(PLAN_REPLY))
    state = PlannerState.load(story)
    asyncio.run(brainstorm(state, "Look", ["Mara.png"], settings=AppSettings(), catalog=BLIND, client=fake.client()))
    content = fake.chat_bodies()[0]["messages"][1]["content"]  # no request for notes: it couldn't see it
    assert isinstance(content, str) and "can't see pictures" in content


def test_a_failed_reply_changes_nothing(story):
    from tests.fakes import http_error
    fake = FakeOpenRouter().queue(*[http_error(400, "bad request")] * 2)
    state = PlannerState.load(story)
    state.plan = "## Title\nKeep me"
    result = asyncio.run(brainstorm(state, "Hello", [], settings=AppSettings(), catalog=SEEING, client=fake.client()))
    assert not result.ok and state.plan == "## Title\nKeep me" and [t.role for t in state.turns] == [USER]


def test_catalog_knows_which_models_see_pictures():
    assert SEEING.get(MODELS[0]["id"]).sees_pictures is True
    assert BLIND.get(MODELS[0]["id"]).sees_pictures is False
    assert parse_model(MODELS[0]).sees_pictures is None  # OpenRouter didn't say


# --- stage 2: chapters, scenes, scene briefs -----------------------------------------------------------

def test_plan_the_whole_story_one_scene_brief_at_a_time(story):
    approved(story)
    assert (story / PLAN_FILE).read_text(encoding="utf-8").startswith("## Title")
    fake = FakeOpenRouter().respond(planner_answers)
    plan_run = run_plan(story, fake, at_once=1)  # Speed 1: every brief one after another
    assert plan_run.status == FINISHED and "ready to write" in plan_run.reason
    labels = [s.label for s in plan_run.steps]
    canon_at = labels.index("The story's canon, and the guide as it stands at each chapter")
    assert [label for label in labels if "canon" not in label] == [
        "The writer's guide", "Chapter architecture", "Chapter 1: scenes", "Chapter 2: scenes",
        "Chapter 1, scene 1: C1 first", "Chapter 1, scene 2: C1 second", "Chapter 2, scene 1: C2 first",
        "Chapter 2, scene 2: C2 second"]
    assert 1 < canon_at < labels.index("Chapter 1, scene 1: C1 first")  # alongside the scene lists, before briefs
    canon = [b for b in fake.chat_bodies() if "You keep the canon" in b["messages"][0]["content"]]
    assert len(canon) == 1 and "GUIDE-MARKER" in canon[0]["messages"][1]["content"]
    bodies = [b for b in fake.chat_bodies() if b not in canon]
    assert all("PLAN-MARKER" in b["messages"][1]["content"] for b in bodies)  # the plan goes with every request
    assert all("the story planner: a development editor" in b["messages"][0]["content"] for b in bodies)  # its own system
    scenes_task = bodies[2]["messages"][1]["content"]  # the chapter's length, shared by what each scene carries
    assert "Number of scenes: 2, about 3,500 words in all: share them by what each scene carries." in scenes_task
    second_brief = bodies[5]["messages"][1]["content"]
    assert "GUIDE-MARKER" in second_brief  # briefs are written knowing what the writer will have
    assert "BRIEF-MARKER for scene 1 of chapter 1" in second_brief and "STATE-MARKER after scene 1 of chapter 1" \
        in second_brief  # the scene before, and how things stand after it
    across = bodies[6]["messages"][1]["content"]  # chapter 2, scene 1 follows on from chapter 1's last scene
    assert "STATE-MARKER after scene 2 of chapter 1" in across and "Length of the brief: about" in across

    state = PlannerState.load(story)
    assert state.stage == DONE and all(s.status == READY for c in state.chapters for s in c.scenes)
    assert state.chapters[0].scenes[0].continuity == "- STATE-MARKER after scene 1 of chapter 1"
    project = load_project(story)
    assert project.config.own_files == {GUIDE_FILE: EVERY, OUTLINE_FILE: OUTLINE, PLAN_FILE: NOT_SENT,
                                        ARCHITECTURE_FILE: NOT_SENT, CONTINUITY_FILE: NOT_SENT,
                                        "Story registry.md": NOT_SENT}
    assert project.config.chapter_words == 3000 and project.config.mode == "scenes"
    assert project.config.in_chapter_context == "independent"
    assert "GUIDE-MARKER" in (story / GUIDE_FILE).read_text(encoding="utf-8")
    assert "STATE-MARKER after scene 2 of chapter 2" in (story / CONTINUITY_FILE).read_text(encoding="utf-8")
    assert "Set up the mystery" in (story / ARCHITECTURE_FILE).read_text(encoding="utf-8")
    one, two = project.outline.chapters
    assert (one.title, one.settings, two.settings) == ("The Stopped Clock", {"words": 3500}, {"words": 2500})
    assert one.text.strip() == ""  # the chapter's purpose and beats aren't the writer's: they are in the briefs
    assert [(s.title, s.settings) for s in one.scenes] == [("C1 first", {"words": 1500}),
                                                           ("C1 second", {"words": 2000})]
    assert "BRIEF-MARKER for scene 2 of chapter 2" in two.scenes[1].text and "STATE-MARKER" not in two.scenes[1].text
    assert "Scene 3 is mentioned" in one.scenes[0].text  # a heading-like line stays inside its scene
    assert not [i for i in project.issues if i.severity != "info"]
    assert [s.action for s in list_snapshots(story)][:1] == ["planner-plan"]  # one Undo for the whole run
    from devils_workshop.llm.costs import read_costs
    costs = read_costs(story / "logs")
    assert len(costs) == 9 and all(c["chapter"] is None for c in costs)  # planning isn't a chapter's spending
    assert {c["job"] for c in costs} == {"plan-guide", "plan-architecture", "plan-scenes", "plan-scene", "plan-canon"}

    # The writer gets the guide and one scene's brief, and nothing else.
    from devils_workshop.context.assemble import assemble
    request = assemble(project, 2, 2)
    sent = request.messages[1].content
    assert len(request.messages) == 2 and "GUIDE-MARKER" in sent and "BRIEF-MARKER for scene 2 of chapter 2" in sent
    for absent in ("scene 1 of chapter 2", "PLAN-MARKER", "STATE-MARKER", "Later in this chapter", "The causeway"):
        assert absent not in sent
    assert "Write it from its brief alone" in sent


def test_stop_then_carry_on_where_it_left_off(story):
    approved(story)
    fake = FakeOpenRouter().respond(planner_answers)
    holder = {}

    def stopping(body):
        if len(fake.chat_bodies()) == 4:
            holder["run"].stop()  # you press Stop while the fourth request is under way
        return planner_answers(body)

    fake.respond(stopping)
    holder["run"] = PlanRun(story, AppSettings(), SEEING, fake.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(holder["run"].run())
    stopped = holder["run"]
    assert stopped.status == STOPPED and len(stopped.steps) == len(fake.chat_bodies())  # under way: finished
    assert all(s.status == "finished" for s in stopped.steps)  # and none started after you pressed Stop
    assert PlannerState.load(story).progress()[0] == 0  # no brief yet

    fake.respond(planner_answers)
    again = run_plan(story, fake)
    assert again.status == FINISHED and again.steps[0].kind in ("scenes", "scene")
    assert len(fake.chat_bodies()) == 9  # nothing was planned twice


def test_spending_cap_and_unreadable_replies(story):
    approved(story)
    fake = FakeOpenRouter().respond(planner_answers)
    capped = run_plan(story, fake, spend_cap=0.01, estimate=lambda model, tokens, words: (0.02, 0.05))
    assert capped.status == STOPPED and "Spending cap" in capped.reason and fake.chat_bodies() == []

    vague = reply("I think there should be twelve chapters, roughly.")
    fake.queue(reply("## Voice and style\nPlain."), vague, vague)  # asked once more for the format, in vain
    broken = run_plan(story, fake)
    assert "couldn't be read" in fake.chat_bodies()[-1]["messages"][-1]["content"]
    assert broken.status == FAILED_RUN and "didn't contain the chapters" in broken.reason
    assert PlannerState.load(story).chapters == []  # nothing saved from it; Continue tries again


def test_redo_one_scene_with_a_note(story):
    approved(story)
    fake = FakeOpenRouter().respond(planner_answers)
    run_plan(story, fake)
    state = PlannerState.load(story)
    redo_scene(state, 2, 1, "NOTE-MARKER make the reveal later")
    fake.respond(lambda body: reply("**Purpose:** REDONE-MARKER the reveal comes later."))
    redo = run_plan(story, fake, only=("scene", 2, 1))
    assert redo.status == FINISHED and [s.label for s in redo.steps] == ["Chapter 2, scene 1: C2 first"]
    assert "NOTE-MARKER" in fake.chat_bodies()[-1]["messages"][1]["content"]
    state = PlannerState.load(story)
    assert state.chapters[1].scenes[0].brief.startswith("**Purpose:** REDONE-MARKER") and state.stage == DONE
    assert "BRIEF-MARKER" in state.chapters[1].scenes[1].brief  # the other scenes are untouched

    restore(story, list_snapshots(story)[0].id)  # Undo in History puts the outline back as it was
    assert "REDONE-MARKER" not in (story / OUTLINE_FILE).read_text(encoding="utf-8")


def test_chapters_are_briefed_side_by_side_each_in_order(story):
    approved(story)
    fake = FakeOpenRouter().respond(planner_answers)
    in_flight, most = [0], [0]

    class Slow(PlanRun):  # each request takes a moment, as a real one does
        async def _request(self, *args, **kwargs):
            in_flight[0] += 1
            most[0] = max(most[0], in_flight[0])
            await asyncio.sleep(0.01)
            try:
                return await super()._request(*args, **kwargs)
            finally:
                in_flight[0] -= 1

    plan_run = Slow(story, AppSettings(at_once=3), SEEING, fake.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(plan_run.run())
    assert plan_run.status == FINISHED and most[0] == 2  # both chapters' briefs at the same time
    briefs = [s.label for s in plan_run.steps if s.kind == "scene"]
    assert sorted(briefs) == ["Chapter 1, scene 1: C1 first", "Chapter 1, scene 2: C1 second",
                              "Chapter 2, scene 1: C2 first", "Chapter 2, scene 2: C2 second"]
    assert briefs.index("Chapter 1, scene 1: C1 first") < briefs.index("Chapter 1, scene 2: C1 second")
    assert briefs.index("Chapter 2, scene 1: C2 first") < briefs.index("Chapter 2, scene 2: C2 second")
    bodies = [b["messages"][1]["content"] for b in fake.chat_bodies()
              if "Write the brief for scene" in b["messages"][1]["content"]]
    first_of_two = next(b for b in bodies if "Write the brief for scene 1 of chapter 2" in b)
    assert "from the chapter architecture: chapter 1" in first_of_two  # from chapter 1's planned ending
    assert "STATE-MARKER" not in first_of_two
    second_of_two = next(b for b in bodies if "Write the brief for scene 2 of chapter 2" in b)
    assert "STATE-MARKER after scene 1 of chapter 2" in second_of_two  # within a chapter, the scene before
    state = PlannerState.load(story)
    assert state.stage == DONE and all(s.status == READY for c in state.chapters for s in c.scenes)


def test_the_briefs_of_unwritten_chapters_can_be_planned_again_longer(story):
    from devils_workshop.planner.run import redo_briefs, unwritten_with_briefs
    from devils_workshop.project.chapters import chapter_file_name, render_chapter_file
    from devils_workshop.project.files import write_text
    approved(story)
    fake = FakeOpenRouter().respond(planner_answers)
    run_plan(story, fake)
    write_text(story / chapter_file_name(1), render_chapter_file(1, "One", 1, "draft", "Chapter one, as written."))
    state = PlannerState.load(story)
    assert [c.number for c in unwritten_with_briefs(state)] == [2]
    state.brief_words = 500
    state.save()
    assert redo_briefs(PlannerState.load(story), "NOTE-MARKER more physical detail") == [2]
    state = PlannerState.load(story)
    assert all(s.status == READY for s in state.chapters[0].scenes)  # the written chapter keeps its briefs
    assert all(not s.brief and s.status != READY for s in state.chapters[1].scenes)
    again = run_plan(story, fake)
    assert again.status == FINISHED
    sent = [b["messages"][1]["content"] for b in fake.chat_bodies()[-2:]
            if "Write the brief for scene" in b["messages"][1]["content"]]
    lengths = [int(re.search(r"Length of the brief: about ([\d,]+) words", b).group(1).replace(",", ""))
               for b in sent]
    targets = sorted(PlannerState.load(story).brief_target(s) for s in PlannerState.load(story).chapters[1].scenes)
    assert sent and sorted(lengths) == targets and min(lengths) >= 500  # the new length (in proportion)
    assert all("NOTE-MARKER" in b and "<brief_to_change>" not in b for b in sent)  # from scratch, with the note
    assert all(s.status == READY for c in PlannerState.load(story).chapters for s in c.scenes)


def test_briefs_choreograph_the_scene_and_scene_lists_share_out_the_beats(story):
    approved(story)
    state = PlannerState.load(story)
    state.chapters = prompts.chapters_from([{"number": 1, "title": "One", "scenes": 2, "words": 1600,
                                             "beats": ["the storm", "the wreck"]}])
    state.chapters[0].scenes = prompts.scenes_from([{"title": "A", "words": 800}, {"title": "B", "words": 800}], 1600)
    brief_request = prompts.scene_messages(state, state.chapters[0], state.chapters[0].scenes[0])
    system = " ".join(brief_request[0].content.split())
    assert "numbered beat sheet" in system and "(turn)" in system and "How they'd behave" in system
    scenes_request = prompts.scenes_messages(state, state.chapters[0])
    assert "Give every one of the chapter's key beats to exactly one scene" in " ".join(
        scenes_request[0].content.split())


def test_a_chapter_planned_alongside_the_one_before_starts_from_its_planned_ending(story):
    approved(story)
    fake = FakeOpenRouter().respond(planner_answers)
    run_plan(story, fake, at_once=3)  # chapters side by side: chapter 2 starts from chapter 1's planned ending
    first = next(b["messages"][1]["content"] for b in fake.chat_bodies()
                 if "Write the brief for scene 1 of chapter 2" in b["messages"][1]["content"])
    assert "from the chapter architecture: chapter 1" in first


ARCHITECTURE_WITH_ARCS = """<chapter number="1" title="The Storm" scenes="2" words="1600">
Purpose: strand them
Summary: SUMMARY-MARKER the ship goes down and they reach the boat.
Point of view: Oren
When and where: night, the ship
Beats:
- BEAT-MARKER the storm hits
- the boat
Ending: ENDING-MARKER adrift at dawn
Arcs: ARC-MARKER Oren still thinks he can protect her
Sets up: PLANT-MARKER the brass compass in his pocket, noticed once
Pays off: nothing
</chapter>
<chapter number="2" title="Adrift" scenes="2" words="1600">
Purpose: LATER-MARKER the boat turns on them
Summary: things go wrong
Ending: the end of chapter 2
Arcs: he begins to doubt
Sets up: nothing
Pays off: the compass
</chapter>"""


def test_chapters_carry_the_long_view_and_briefs_get_only_what_they_need(story):
    approved(story)

    def answers(body):
        system = " ".join(body["messages"][0]["content"].split())
        if "design the novel's chapter architecture" in system:
            assert "Sets up:" in system and "Arcs:" in system
            return reply(ARCHITECTURE_WITH_ARCS)
        return planner_answers(body)

    fake = FakeOpenRouter().respond(answers)
    plan_run = run_plan(story, fake, at_once=1)
    assert plan_run.status == FINISHED
    state = PlannerState.load(story)
    one = state.chapters[0]
    assert one.arcs.startswith("ARC-MARKER") and one.sets_up.startswith("PLANT-MARKER")
    assert state.chapters[1].pays_off == "the compass" and not one.pays_off  # "nothing" is no note
    assert "Pays off" not in prompts.chapter_frame(one)
    assert "PLANT-MARKER" in (story / ARCHITECTURE_FILE).read_text(encoding="utf-8")

    bodies = [b["messages"][1]["content"] for b in fake.chat_bodies()]
    scenes_request = next(b for b in bodies if "Plan the scenes for this chapter" in b and "The Storm" in b)
    assert "ARC-MARKER" in scenes_request and "PLAN-MARKER" in scenes_request  # scene lists see everything
    brief = next(b for b in bodies if "Write the brief for scene 2 of chapter 1" in b)
    assert "ARC-MARKER" in brief and "PLANT-MARKER" in brief and "GUIDE-MARKER" in brief  # the long view, the guide
    assert "STATE-MARKER after scene 1 of chapter 1" in brief  # how things stand coming in
    assert '<this_scene number="2" of="2">' in brief
    for left_out in ("PLAN-MARKER", "<story_plan>", "<source_notes>", "SUMMARY-MARKER", "BEAT-MARKER",
                     "ENDING-MARKER", "LATER-MARKER", "<scene_list>", "<next_scene>", "<previous_scene_brief"):
        assert left_out not in brief, left_out  # nothing about the plan, other scenes or other chapters
    old = next(b for b in bodies if "Write the brief for scene 1 of chapter 1" in b)
    assert len(brief) < len(old) * 3  # (the first scene's request is as small: there is nothing before it)


def test_a_chapter_planned_before_the_long_view_keeps_the_fuller_brief_request(story):
    approved(story)
    fake = FakeOpenRouter().respond(planner_answers)  # an architecture without arcs, set-ups or pay-offs
    run_plan(story, fake, at_once=1)
    brief = next(b["messages"][1]["content"] for b in fake.chat_bodies()
                 if "Write the brief for scene 2 of chapter 1" in b["messages"][1]["content"])
    assert "<story_plan>" in brief and "<scene_list>" in brief and "<previous_scene_brief" in brief


def _tagged_chapter(n: int, close: bool = True, arcs_as_list: bool = False) -> str:
    arcs = ("Arcs:\n- Mara: still trusts the keeper\n- Tobias: hides the ledger" if arcs_as_list
            else "Arcs: Mara still trusts the keeper")
    return (f'<chapter number="{n}" title="Tide {n}" scenes="5" words="4000">\nPurpose: move it on\n'
            f"Summary: things happen in chapter {n}.\nBeats:\n- one\n- two\nEnding: it turns\n{arcs}\n"
            "Sets up: the brass key\nPays off: nothing\n" + ("</chapter>\n" if close else "\n"))


@pytest.mark.parametrize("closed", [
    lambda n: True,          # every chapter closed
    lambda n: n == 9,        # only the last one: every chapter used to merge into chapter 1
    lambda n: n != 4,        # one left open: two chapters used to merge
    lambda n: False,         # none closed: no chapters used to be found at all
])
def test_a_missing_closing_tag_never_merges_or_drops_chapters(closed):
    reply = "".join(_tagged_chapter(n, close=closed(n)) for n in range(1, 10))
    chapters = prompts.chapters_from(prompts.parse_list(reply, "chapters"))
    assert [c.title for c in chapters] == [f"Tide {n}" for n in range(1, 10)]
    assert all(c.summary == f"things happen in chapter {c.number}." and c.beats == ["one", "two"]
               and c.sets_up == "the brass key" for c in chapters)


def test_fields_written_as_a_list_are_kept_and_a_reply_cut_short_keeps_its_last_chapter():
    chapters = prompts.chapters_from(prompts.parse_list(
        "".join(_tagged_chapter(n, arcs_as_list=True) for n in range(1, 4)), "chapters"))
    assert chapters[0].arcs == "- Mara: still trusts the keeper - Tobias: hides the ledger"
    assert chapters[0].beats == ["one", "two"] and chapters[0].ending == "it turns"
    cut = "".join(_tagged_chapter(n) for n in range(1, 3)) + _tagged_chapter(3, close=False)[:90]
    assert [c.title for c in prompts.chapters_from(prompts.parse_list(cut, "chapters"))] == \
        ["Tide 1", "Tide 2", "Tide 3"]
    scenes = prompts.scenes_from(prompts.parse_list(
        '<scene title="A" words="800">opens at the quay\n<scene title="B" words="800">ends at sea</scene>',
        "scenes"), 1600)
    assert [(s.title, s.purpose) for s in scenes] == [("A", "opens at the quay"), ("B", "ends at sea")]





def test_the_briefs_think_as_the_planner_is_set(story):
    approved(story)
    fake = FakeOpenRouter().respond(planner_answers)
    plan_run = run_plan(story, fake, at_once=1)
    assert plan_run.status == FINISHED
    briefs = [b for b in fake.chat_bodies() if "Write the brief for scene" in b["messages"][-1]["content"]]
    assert briefs and all(b["reasoning"] == {"effort": "medium"} for b in briefs)  # as the planner is set
    assert not any("check one scene brief" in b["messages"][0]["content"] for b in fake.chat_bodies())


def test_after_the_chapter_plan_no_step_gets_the_plans_plot_or_the_source_notes(story):
    from devils_workshop.planner import review
    state = PlannerState.load(story)
    state.plan = ("## Title\nSalt and Iron\n## Logline\nLOGLINE-MARKER\n## Premise\nPREMISE-MARKER\n"
                  "## Main characters\n- CHARACTER-MARKER Mara\n## Story arc\nARC-MARKER the whole story\n"
                  "## Ending\nENDING-MARKER how it ends\n## Rules and constraints\nRULE-MARKER never kill the dog")
    state.notes = {"reference.md": "NOTES-MARKER the reference, scene by scene"}
    assert "<source_notes>" in prompts.guide_messages(state)[1].content  # the guide comes first: it has the notes
    approve(state)
    state.chapters = prompts.chapters_from([{"number": 1, "title": "One", "summary": "it begins"}])
    state.chapters[0].scenes = prompts.scenes_from([{"title": "A", "purpose": "first"}], 800)
    plot = ("LOGLINE-MARKER", "PREMISE-MARKER", "ARC-MARKER", "ENDING-MARKER")
    architecture = prompts.architecture_messages(state)[1].content
    assert all(m in architecture for m in plot) and "NOTES-MARKER" in architecture  # the plot's home: everything
    one, scene = state.chapters[0], state.chapters[0].scenes[0]
    after = {"scene list": prompts.scenes_messages(state, one)[1].content,
             "brief": prompts.scene_messages(state, one, scene)[1].content,  # planned before v0.23: no long view
             "guide again": prompts.guide_messages(state)[1].content,
             "as written": review.actual_messages(state, one, {1: "The text."})[1].content,
             "Reader change": review.revise_messages(state, one, {1: "The text."}, "make it colder")[1].content}
    for step, text in after.items():
        assert not any(m in text for m in plot) and "NOTES-MARKER" not in text, step
        assert "CHARACTER-MARKER" in text and "RULE-MARKER" in text and "Salt and Iron" in text, step
    assert prompts.plan_without_plot("## Story arc (beginning, middle, end)\nX\n## Voice and style\nY") == \
        "## Voice and style\nY"


def test_changing_a_brief_revises_the_one_it_had(story):
    approved(story)
    fake = FakeOpenRouter().respond(planner_answers)
    run_plan(story, fake)
    state = PlannerState.load(story)
    before = state.chapters[0].scenes[1].brief
    redo_scene(state, 1, 2, "CHANGE-MARKER she shouldn't cry yet")
    assert PlannerState.load(story).chapters[0].scenes[1].earlier == before
    fake.respond(lambda body: reply("**Purpose:** CHANGED-MARKER she holds it in."))
    run_plan(story, fake, only=("scene", 1, 2))
    sent = fake.chat_bodies()[-1]["messages"][1]["content"]
    assert "<brief_to_change>" in sent and before.strip() in sent and "CHANGE-MARKER" in sent
    assert "Change the brief above as the author's note says" in sent


def test_with_briefs_just_in_time_progress_counts_only_the_briefs_due(story):
    approved(story, jit=True)
    fake = FakeOpenRouter().respond(planner_answers)
    plan_run = run_plan(story, fake)
    state = PlannerState.load(story)
    assert plan_run.status == FINISHED and state.stage == DONE
    assert state.progress() == (2, 2)  # chapter 1's two briefs; chapter 2's come just before it is written
    assert all(s.status != READY for s in state.chapters[1].scenes)


def test_all_the_briefs_can_be_written_up_front(story):
    from devils_workshop.planner.run import all_briefs_now, briefs_to_come
    approved(story, jit=True)
    fake = FakeOpenRouter().respond(planner_answers)
    run_plan(story, fake)
    state = PlannerState.load(story)
    assert briefs_to_come(state) == 2  # chapter 2's two scenes
    all_briefs_now(state)
    finish = run_plan(story, fake)
    assert [s.label for s in finish.steps] == ["Chapter 2, scene 1: C2 first", "Chapter 2, scene 2: C2 second"]
    state = PlannerState.load(story)
    assert state.stage == DONE and not state.jit and briefs_to_come(state) == 0
    assert all(s.status == READY for c in state.chapters for s in c.scenes) and state.progress() == (4, 4)


def test_changing_a_later_chapter_keeps_its_briefs_just_in_time(story):
    from devils_workshop.planner.run import redo_chapter
    approved(story, jit=True)
    fake = FakeOpenRouter().respond(planner_answers)
    run_plan(story, fake)
    state = PlannerState.load(story)
    redo_chapter(state, 2, "CHAPTER-MARKER the storm comes first")
    change = run_plan(story, fake, only=("chapter", 2))
    assert [s.label for s in change.steps] == ["Chapter 2: scenes"]  # the scene list only, no briefs yet
    assert "CHAPTER-MARKER" in fake.chat_bodies()[-1]["messages"][1]["content"]
    state = PlannerState.load(story)
    assert state.chapters[1].scenes and all(s.status != READY for s in state.chapters[1].scenes)
