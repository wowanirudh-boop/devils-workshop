"""The planner builds the foundation from files, the writer writes each scene on its own, and every prompt
can be edited: the agenda of open decisions, Decisions.md and Story plan.md as the planner's memory, the
writer's guide, independent scenes written at the same time, prompt copies, and PDF/Word files as text."""

import asyncio

import pytest

from devils_workshop.chat.auto import FINISHED, AutoOptions, AutoRun
from devils_workshop.llm.generate import generate
from devils_workshop.planner import prompts
from devils_workshop.planner.output import GUIDE_FILE
from devils_workshop.planner.run import FILL_GAPS, PlanRun, approve, brainstorm, plan_mark
from devils_workshop.planner.state import (
    TOGETHER,
    DECISIONS_FILE,
    DECLINED,
    PLAN_FILE,
    PlannerState,
    ScenePlan,
    Suggestion,
    parse_decisions,
)
from devils_workshop.project import instructions
from devils_workshop.project.default_instructions import load_roles, make_text_copy as default_text_copy
from devils_workshop.project.own_files import NOT_SENT, SYSTEM, make_text_copy
from devils_workshop.project.project import load_project
from devils_workshop.settings import PLANNER, AppSettings

from tests.conftest import settings, settings_file
from tests.fakes import FakeOpenRouter, reply
from tests.unit.test_own_files import make_pdf
from tests.unit.test_planner import SEEING, approved, planner_answers, story  # noqa: F401

FIRST = """<edit section="Premise">
A lighthouse keeper's daughter hears her missing father in the 2 a.m. signal.
</edit>
<decision>Literary mystery for adults.</decision>
<agenda>
- **The ending:** is the father alive?
- **Point of view:** Maren's first person, or close third?
- **Length:** how many chapters?
</agenda>
<ready/>
<reply>1. Is he alive? I recommend yes. 2. First person or close third? 3. How long?</reply>"""


def chat(state, fake, text):
    return asyncio.run(brainstorm(state, text, [], settings=AppSettings(fallback=None), catalog=SEEING,
                                  client=fake.client()))


# --- the agenda, and the files as the planner's memory --------------------------------------------------

def test_the_agenda_is_kept_in_decisions_md_and_the_plan_is_not_ready_while_it_is_open(story):
    fake = FakeOpenRouter().queue(reply(FIRST))
    state = PlannerState.load(story)
    chat(state, fake, "A lighthouse, a missing father.")
    assert state.agenda == ["**The ending:** is the father alive?",
                            "**Point of view:** Maren's first person, or close third?", "**Length:** how many chapters?"]
    assert state.ready is False  # it said ready, but three decisions are still open
    text = (story / DECISIONS_FILE).read_text(encoding="utf-8")
    assert "## Settled\n- Literary mystery for adults." in text and "- **The ending:** is the father alive?" in text
    assert "lighthouse keeper's daughter" in (story / PLAN_FILE).read_text(encoding="utf-8")
    found = parse_decisions(text)
    assert (found["settled"], found["open"]) == (state.decisions, state.agenda)

    sent = fake.chat_bodies()[0]["messages"]
    assert "You are the story planner" in sent[0]["content"] and "story development partner" in sent[0]["content"]
    assert "(empty: build it from what the author has said)" in sent[1]["content"]


def test_the_planner_works_from_the_files_your_edits_included_and_only_the_latest_messages(story):
    state = PlannerState.load(story)
    fake = FakeOpenRouter().respond(lambda body: reply("<agenda>\n- **Length:** how many chapters?\n</agenda>"
                                                       "<reply>Noted.</reply>"))
    for number in range(4):
        chat(state, fake, f"MESSAGE-{number}")
    # You edit the files in the story folder between replies.
    (story / PLAN_FILE).write_text("## Title\nFILE-EDIT-MARKER The Two O'Clock Lamp\n", encoding="utf-8")
    decisions = (story / DECISIONS_FILE).read_text(encoding="utf-8")
    (story / DECISIONS_FILE).write_text(decisions.replace("- (nothing yet)", "- MY-DECISION-MARKER first person"),
                                        encoding="utf-8")
    state = PlannerState.load(story)
    assert state.decisions == ["MY-DECISION-MARKER first person"] and "FILE-EDIT-MARKER" in state.plan
    chat(state, fake, "MESSAGE-4")
    last = fake.chat_bodies()[-1]["messages"]
    everything = "\n".join(m["content"] for m in last)
    assert "FILE-EDIT-MARKER" in everything and "MY-DECISION-MARKER" in everything
    assert "- **Length:** how many chapters?" in everything  # the agenda goes with it
    assert "MESSAGE-0" not in everything and "MESSAGE-4" in everything  # only the latest messages
    assert len(last) == 1 + prompts.RECENT_TURNS and "Only the latest 4 messages are shown" in everything


def test_filling_the_gaps_settles_the_agenda(story):
    state = PlannerState.load(story)
    state.agenda = ["**The ending:** alive or not?"]
    state.save()
    fake = FakeOpenRouter().queue(reply('<edit section="Ending">\nHe is alive (my choice).\n</edit>'
                                        "<decision>He is alive (my choice).</decision><agenda>\n</agenda>"
                                        "<ready/><reply>I settled the ending.</reply>"))
    chat(state, fake, FILL_GAPS)
    assert "<author_approved/>" in fake.chat_bodies()[0]["messages"][-1]["content"]
    assert state.agenda == [] and state.ready and "He is alive" in state.plan


def test_the_writers_system_prompt_goes_to_the_planner_as_instructions_not_as_its_own(story):
    (story / "Voice.md").write_text("WRITER-VOICE-MARKER Never use the word 'suddenly'.", encoding="utf-8")
    settings(story, files={"Voice.md": "system prompt"})
    assert load_project(story).system_texts()
    fake = FakeOpenRouter().queue(reply("<reply>Hello.</reply>"))
    chat(PlannerState.load(story), fake, "Hi")
    system, user = fake.chat_bodies()[0]["messages"]
    assert "WRITER-VOICE-MARKER" not in system["content"]
    assert "<writer_instructions>\nWRITER-VOICE-MARKER" in user["content"]


# --- planning: the guide, scene counts, brief lengths ------------------------------------------------------

def test_a_new_plan_gets_a_new_writers_guide(story):
    state = approved(story)
    fake = FakeOpenRouter().respond(planner_answers)
    asyncio.run(PlanRun(story, AppSettings(), SEEING, fake.client, sleep=lambda s: asyncio.sleep(0)).run())
    state = PlannerState.load(story)
    assert state.guide.startswith("## Voice and style") and state.guide_plan == plan_mark(state.plan)
    approve(state)  # approved again, unchanged: the guide stays
    assert PlannerState.load(story).guide
    state.plan += "\n## Ending\nA new ending."
    approve(state)
    assert PlannerState.load(story).guide == ""  # written again from the new plan
    assert (story / GUIDE_FILE).is_file()  # the old one stays until then


def test_brief_length_follows_the_scene_length():
    state = PlannerState(root=None)
    assert state.brief_target(ScenePlan(1, "A", words=800)) == 250  # what the writer can't infer, not every move
    assert state.brief_target(ScenePlan(1, "A", words=1200)) == 380  # in proportion (1,200 / 800 x 250, to the nearest 10)
    assert state.brief_target(ScenePlan(1, "A")) == 250
    state.scene_words, state.brief_words = 1000, 150
    assert state.brief_target(ScenePlan(1, "A", words=1000)) == 150


def test_the_planners_options_go_with_its_requests(tmp_path):
    settings = AppSettings()
    settings.planner_options.temperature = 0.42
    fake = FakeOpenRouter().queue(reply("Planned."))
    asyncio.run(generate([prompts.ChatMessage("user", "Plan.")], client=fake.client(), settings=settings,
                         catalog=SEEING, log_root=tmp_path, label="t", target_words=10, role=PLANNER, min_words=1,
                         sleep=lambda s: asyncio.sleep(0)))
    body = fake.chat_bodies()[0]
    assert body["temperature"] == 0.42 and body["reasoning"] == {"effort": "medium"} and body["max_tokens"] == 64000


# --- the writer: independent scenes, at the same time ----------------------------------------------------

def test_a_planned_chapters_scenes_are_written_at_the_same_time_with_no_joins_to_smooth(story):
    state = approved(story)
    state.scene_order = TOGETHER  # chosen for this story: a chapter's scenes at the same time
    state.save()
    fake = FakeOpenRouter().respond(planner_answers)
    asyncio.run(PlanRun(story, AppSettings(), SEEING, fake.client, sleep=lambda s: asyncio.sleep(0)).run())

    writer = FakeOpenRouter().respond(lambda body: planner_answers(body) if "You check scenes" in
                                      body["messages"][0]["content"] else
                                      reply("SCENE-PROSE " + "The lamp turned over the water. " * 60))
    auto = AutoRun(story, AutoOptions([1], continue_short=False, scenes_together=False), settings=AppSettings(),
                   catalog=SEEING, client=writer.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(auto.run())
    assert auto.status == FINISHED, auto.reason
    assert [s.label for s in auto.steps] == ["Chapter 1, scene 1 of 2", "Chapter 1, scene 2 of 2",  # no smoothing
                                             "Chapter 1: checking scenes 1, 2 as written"]  # then checked together
    for body in [b for b in writer.chat_bodies() if "You check scenes" not in b["messages"][0]["content"]]:
        assert [m["role"] for m in body["messages"]] == ["system", "user"]  # each scene its own chat
        assert "GUIDE-MARKER" in body["messages"][1]["content"]
    written = load_project(story).chapters[1]
    assert sorted(written.scenes) == [1, 2]


# --- every prompt can be edited -------------------------------------------------------------------------

def test_a_prompt_is_this_storys_copy_else_yours_else_the_built_in_one(story):
    assert instructions.source(story, "plan_scene") == instructions.BUILT_IN
    instructions.save_app_copy("plan_scene", "MY-PROMPT-MARKER for every story {scene_words}")
    assert instructions.source(story, "plan_scene") == instructions.MINE
    assert prompts.job_text(story, "plan_scene", 900) == "MY-PROMPT-MARKER for every story 900"
    instructions.story_copy(story, "plan_scene").parent.mkdir(parents=True, exist_ok=True)
    instructions.story_copy(story, "plan_scene").write_text("STORY-PROMPT-MARKER", encoding="utf-8")
    assert instructions.source(story, "plan_scene") == instructions.STORY
    assert prompts.job_text(story, "plan_scene") == "STORY-PROMPT-MARKER"
    instructions.remove_app_copy("plan_scene")
    assert instructions.source(None, "plan_scene") == instructions.BUILT_IN
    assert set(instructions.JOBS) >= {"write", "scenes", "join", "planner_system", "plan_brainstorm", "plan_guide",
                                      "plan_architecture", "plan_scenes", "plan_scene"}
    for job in instructions.JOBS:
        assert instructions.builtin_text(job).strip()


def test_a_pdf_system_prompt_can_be_edited_as_text(story, tmp_path):
    make_pdf(story / "Voice.pdf", [["PDF-VOICE-MARKER Short sentences."]])
    settings(story, files={"Voice.pdf": "system prompt"})
    copy = make_text_copy(story, "Voice.pdf")
    assert copy == "Voice.md" and "PDF-VOICE-MARKER" in (story / copy).read_text(encoding="utf-8")
    roles = load_project(story).config.own_files
    assert roles["Voice.md"] == SYSTEM and roles["Voice.pdf"] == NOT_SENT
    assert make_text_copy(story, "Voice.pdf") == "Voice (text).md"  # never over an existing file


def test_a_pdf_default_instruction_can_be_edited_as_text(tmp_path):
    from devils_workshop.project.default_instructions import folder
    folder().mkdir(parents=True, exist_ok=True)
    make_pdf(folder() / "House style.pdf", [["HOUSE-MARKER No adverbs."]])
    copy = default_text_copy("House style.pdf")
    assert copy == "House style.md" and "HOUSE-MARKER" in (folder() / copy).read_text(encoding="utf-8")
    roles = load_roles()
    assert roles["House style.pdf"] == NOT_SENT and roles["House style.md"] != NOT_SENT


# --- files first: ruled out, preferences, your edits read back --------------------------------------------

def test_what_you_reject_and_prefer_is_kept_in_decisions_md(story):
    state = PlannerState.load(story)
    state.suggestions = [Suggestion(1, "Make the father a smuggler."), Suggestion(2, "Add a dog.")]
    state.answer(1, DECLINED)
    fake = FakeOpenRouter().queue(reply("<ruled_out>A happy ending.</ruled_out>"
                                        "<preference>Keep it bleak and quiet.</preference><reply>Noted.</reply>"))
    chat(state, fake, "No happy ending, please. I like it bleak and quiet.")
    assert state.ruled_out == ["Make the father a smuggler.", "A happy ending."]
    assert state.preferences == ["Keep it bleak and quiet."]
    text = (story / DECISIONS_FILE).read_text(encoding="utf-8")
    assert "## Ruled out\n- Make the father a smuggler.\n- A happy ending." in text
    assert "## Your preferences\n- Keep it bleak and quiet." in text
    again = PlannerState.load(story)
    assert again.ruled_out == state.ruled_out and again.preferences == state.preferences
    fake.queue(reply("<reply>Fine.</reply>"))
    chat(again, fake, "Next.")
    sent = fake.chat_bodies()[-1]["messages"][-1]["content"]
    assert "<ruled_out>\n- Make the father a smuggler." in sent and "<preferences>\n- Keep it bleak" in sent


def test_your_edits_to_the_guide_briefs_and_continuity_are_read_back(story):
    approved(story)
    fake = FakeOpenRouter().respond(planner_answers)
    asyncio.run(PlanRun(story, AppSettings(), SEEING, fake.client, sleep=lambda s: asyncio.sleep(0)).run())
    outline = (story / "Chapter outline.md").read_text(encoding="utf-8")
    (story / "Chapter outline.md").write_text(outline.replace("BRIEF-MARKER for scene 1 of chapter 1",
                                                              "MY-BRIEF-EDIT for scene 1"), encoding="utf-8")
    continuity = (story / "Continuity.md").read_text(encoding="utf-8")
    (story / "Continuity.md").write_text(continuity.replace("STATE-MARKER after scene 1 of chapter 1",
                                                            "MY-CONTINUITY-EDIT"), encoding="utf-8")
    guide = (story / GUIDE_FILE).read_text(encoding="utf-8")
    (story / GUIDE_FILE).write_text(guide + "\nMY-GUIDE-EDIT Never use semicolons.\n", encoding="utf-8")

    state = PlannerState.load(story)
    first = state.chapters[0].scenes[0]
    assert "MY-BRIEF-EDIT" in first.brief and first.continuity == "- MY-CONTINUITY-EDIT"
    assert "MY-GUIDE-EDIT" in state.guide
    assert "BRIEF-MARKER for scene 2 of chapter 1" in state.chapters[0].scenes[1].brief  # untouched
    from devils_workshop.planner.run import redo_scene
    redo_scene(state, 1, 2)
    asyncio.run(PlanRun(story, AppSettings(), SEEING, fake.client, only=("scene", 1, 2),
                        sleep=lambda s: asyncio.sleep(0)).run())
    sent = next(b for b in reversed(fake.chat_bodies()) if "Write the brief for scene 2" in
                b["messages"][-1]["content"])["messages"][1]["content"]
    assert "MY-BRIEF-EDIT" in sent and "MY-CONTINUITY-EDIT" in sent and "MY-GUIDE-EDIT" in sent


def test_your_planner_instructions_go_with_every_planner_request(story):
    from devils_workshop.project.default_instructions import PLANNER_NOTES, folder, save_roles
    folder().mkdir(parents=True, exist_ok=True)
    (folder() / "Planning.md").write_text("PLANNER-PREF-MARKER I like morally grey leads.", encoding="utf-8")
    save_roles({"Planning.md": PLANNER_NOTES})
    fake = FakeOpenRouter().queue(reply("<reply>Hi.</reply>"))
    chat(PlannerState.load(story), fake, "Hello")
    system, user = fake.chat_bodies()[0]["messages"]
    assert "PLANNER-PREF-MARKER" in system["content"] and "PLANNER-PREF-MARKER" not in user["content"]
    assert load_project(story).system_texts() == []  # not the writer's
    from devils_workshop.project.instructions import builtin_text
    job = " ".join(builtin_text("plan_brainstorm").split()[-12:])  # the end of the built-in job instruction
    content = " ".join(system["content"].split())
    assert content.index(job) < content.index("PLANNER-PREF-MARKER")  # yours come last: they win where they differ
    assert "Where they differ from the instructions above, the author's instructions are the ones to follow" in content


def test_a_decision_that_changes_an_earlier_one_replaces_it(story):
    state = PlannerState.load(story)
    state.decisions = ["The family surname is Venn.", "The lighthouse is on the north shore."]
    state.save()
    fake = FakeOpenRouter().queue(reply('<decision replaces="The family surname is Venn.">The family surname is '
                                        'Marsh: Mara Marsh, Tobias Marsh.</decision><reply>Changed it.</reply>'))
    chat(PlannerState.load(story), fake, "Call them Marsh instead.")
    assert PlannerState.load(story).decisions == ["The lighthouse is on the north shore.",
                                                  "The family surname is Marsh: Mara Marsh, Tobias Marsh."]
    assert 'replaces="' in fake.chat_bodies()[0]["messages"][0]["content"]  # the planner is told how


def test_a_state_the_story_later_changes_is_canon_only_until_then():
    from devils_workshop.planner.canon import read_canon
    facts, _ = read_canon('<fact id="F1" kind="decision" from="1" until="3" subject="Mara">Mara has not agreed '
                          'to sell</fact><fact id="F2" kind="kinship" from="1">Mara has one brother</fact>', "")
    assert [f.true_at(3) for f in facts] == [True, True] and [f.true_at(4) for f in facts] == [False, True]
    canon = " ".join(instructions.builtin_text("plan_canon").split())
    assert "the state of bodies and clothes" in canon and "never canon" in canon  # continuity, which briefs carry
    assert "how someone behaves" in canon and "where two decisions disagree" in canon


def test_the_notes_on_a_long_file_say_how_long_it_is_and_keep_their_own_headings(story):
    from devils_workshop.planner.inputs import add_input
    from devils_workshop.planner.state import notes_text, parse_notes
    add_input(story, "Source.md", ("## Part one\n" + "word " * 3000).encode("utf-8"))
    fake = FakeOpenRouter().respond(lambda body: reply("## 1. Premise\nNOTES-MARKER\n## 2. Cast\nTwo people.")
                                    if "notes" in body["messages"][0]["content"].lower()[:4000] and
                                    "<file " in body["messages"][-1]["content"] else reply("<reply>Hi.</reply>"))
    state = PlannerState.load(story)
    asyncio.run(brainstorm(state, "Adapt this.", ["Source.md"], settings=AppSettings(), catalog=SEEING,
                           client=fake.client()))
    notes = PlannerState.load(story).notes
    assert list(notes) == ["Source.md"] and notes["Source.md"].startswith("(The file is 3,003 words long.)")
    asked = next(b for b in fake.chat_bodies() if "<file " in b["messages"][-1]["content"])
    assert '<file name="Source.md" words="3,003">' in asked["messages"][-1]["content"]  # never left to estimate
    assert parse_notes(notes_text(notes)) == notes  # its "##" sections stay inside it, not separate "files"
    old_style = "# Source notes\n\n## Source.md\nOVERVIEW\n\n## 1. Premise\nPREMISE-MARKER\n\n## 2. Cast\nCAST-MARKER\n"
    (story / "Source notes.md").write_text(old_style, encoding="utf-8")  # saved by an earlier version
    mended = PlannerState.load(story).notes
    assert list(mended) == ["Source.md"] and "## 1. Premise\nPREMISE-MARKER" in mended["Source.md"] \
        and "CAST-MARKER" in mended["Source.md"]


# --- just-in-time briefs, the reveal check, readable formats ----------------------------------------------

def test_with_just_in_time_briefs_only_the_first_chapter_is_briefed(story):
    from devils_workshop.context.assemble import AssemblyError, assemble
    from devils_workshop.project.outline import PENDING_BRIEF
    approved(story, jit=True)
    fake = FakeOpenRouter().respond(planner_answers)
    asyncio.run(PlanRun(story, AppSettings(), SEEING, fake.client, sleep=lambda s: asyncio.sleep(0)).run())
    state = PlannerState.load(story)
    assert state.stage == "done" and [s.status for s in state.chapters[0].scenes] == ["ready", "ready"]
    assert [s.status for s in state.chapters[1].scenes] == ["waiting", "waiting"]  # every scene list, though
    project = load_project(story)
    assert project.outline.chapter(2).scenes[0].text.strip() == PENDING_BRIEF
    assert "BRIEF-MARKER" in assemble(project, 1, 1).messages[1].content
    with pytest.raises(AssemblyError, match="aren't written yet"):
        assemble(project, 2, 1)  # never written from a placeholder
    state.briefs_through = 2
    state.save()
    asyncio.run(PlanRun(story, AppSettings(), SEEING, fake.client, sleep=lambda s: asyncio.sleep(0)).run())
    assert all(s.status == "ready" for s in PlannerState.load(story).chapters[1].scenes)





def test_chapters_and_scenes_are_read_from_tags_or_json():
    tagged = prompts.parse_list('''<chapter number="1" title="The Lamp" scenes="7" words="5600">
Purpose: Set up the mystery
Summary: Maren hears the signal.
It repeats at two.
Point of view: Maren
Beats:
- The light fails
- She climbs
Ending: A boat with no crew
</chapter>''', "chapters")
    chapter = prompts.chapters_from(tagged)[0]
    assert (chapter.title, chapter.scene_count, chapter.words, chapter.pov) == ("The Lamp", 7, 5600, "Maren")
    assert chapter.summary == "Maren hears the signal. It repeats at two." and chapter.beats == ["The light fails",
                                                                                                "She climbs"]
    scenes = prompts.scenes_from(prompts.parse_list('<scene title="Fog" words="800">She waits.</scene>\n'
                                                    '<scene title="Bell">The bell.</scene>', "scenes"), 1600)
    assert [(s.title, s.words, s.purpose) for s in scenes] == [("Fog", 800, "She waits."), ("Bell", 800, "The bell.")]
    assert prompts.parse_list('{"scenes": [{"title": "Old"}]}', "scenes") == [{"title": "Old"}]
    with pytest.raises(ValueError):
        prompts.parse_list("Twelve chapters, roughly.", "chapters")
