"""Chapter by chapter with the planner: each chapter's briefs follow what was actually written before it, and
changes asked for while reading are planned by the planner, approved by you, and made by the writer in place."""

import asyncio
import json

from devils_workshop.chat.auto import FINISHED, SCENES, AutoOptions, AutoRun
from devils_workshop.chat.review import ReviewTask, ask, make_changes
from devils_workshop.history import list_snapshots, restore
from devils_workshop.planner.review import REVISE, REWRITE, ChangePlan, SceneChange, apply_to_plan
from devils_workshop.planner.audit import findings_of
from devils_workshop.planner.run import PlanRun
from devils_workshop.planner.state import PlannerState
from devils_workshop.project.project import load_project
from devils_workshop.settings import AppSettings

from tests.fakes import FakeOpenRouter, reply
from tests.unit.test_planner import SEEING, approved, planner_answers, story  # noqa: F401

PROSE = "The lamp turned over the black water and the gulls cried. " * 30


def answers(body: dict):
    """The planner's jobs (as in test_planner), the planner reading a written chapter, and the writer."""
    system = " ".join(body["messages"][0]["content"].split())
    task = body["messages"][-1]["content"]
    if "has been written from the briefs" in system:
        return reply("<continuity>\n- ACTUAL-MARKER Mara is on the causeway, soaked.\n</continuity>\n"
                     "<differences>\n- She kept the ledger, though the brief had her drop it.\n</differences>")
    if "The author has read the chapter" in system:
        if "QUESTION-MARKER" in task:
            return reply("<question>Should she cry at all?</question><reply>One question first.</reply>")
        return reply('<scene number="1" how="revise"><change>CHANGE-MARKER make the argument colder</change>'
                     "<brief>**Coming in:** NEW-BRIEF-1 colder.</brief></scene>"
                     '<scene number="2" how="rewrite"><brief>**Coming in:** NEW-BRIEF-2 from the start.</brief>'
                     "</scene><later>She trusts Tobias less from now on.</later>"
                     "<reply>I'll make scene 1 colder and write scene 2 again.</reply>")
    if any(job in system for job in ("You are the story planner", "You keep the canon", "You check scenes of a novel")):
        return planner_answers(body)
    if "<change_request>" in task:
        return reply("REVISED-PROSE " + PROSE)
    return reply(("WRITTEN-" + ("NEW-BRIEF-2" if "NEW-BRIEF-2" in task else "PROSE") + " ") + PROSE)


def planned(story, jit=True):
    approved(story, jit=jit)
    fake = FakeOpenRouter().respond(answers)
    asyncio.run(PlanRun(story, AppSettings(), SEEING, fake.client, sleep=lambda s: asyncio.sleep(0)).run())
    return fake


def write(story, fake, chapters, **options):
    auto = AutoRun(story, AutoOptions(chapters, mode=SCENES, continue_short=False, **options),
                   settings=AppSettings(), catalog=SEEING, client=fake.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(auto.run())
    return auto


def test_the_next_chapters_briefs_follow_what_was_actually_written(story):
    fake = planned(story)
    first = write(story, fake, [1])
    assert first.status == FINISHED, first.reason
    assert [s.label for s in first.steps] == [  # each scene after the one before (written while the one before
        "Chapter 1, scene 1 of 2", "Chapter 1, scene 2 of 2",  # is checked), each checked as written
        "Chapter 1: checking scene 1 as written", "Chapter 1: checking scene 2 as written"]
    state = PlannerState.load(story)
    assert 1 in state.reconciled  # every scene checked: chapter 1 is followed as written
    assert state.chapters[0].scenes[-1].continuity == "EXIT-MARKER after chapter 1, scene 2"
    before = len(fake.chat_bodies())
    second = write(story, fake, [2])
    assert second.status == FINISHED, second.reason
    assert [s.label for s in second.steps][0] == "Chapter 2: the planner writes its scene briefs"
    bodies = fake.chat_bodies()[before:]
    assert not any("has been written from the briefs" in b["messages"][0]["content"] for b in bodies)
    brief = next(b for b in bodies if "Write the brief for scene 1 of chapter 2" in b["messages"][-1]["content"])
    assert "EXIT-MARKER after chapter 1, scene 2" in brief["messages"][1]["content"]  # coming in, as written
    state = PlannerState.load(story)
    assert state.briefs_through == 2 and all(s.status == "ready" for s in state.chapters[1].scenes)
    assert sorted(load_project(story).chapters[2].scenes) == [1, 2]
    assert "EXIT-MARKER" in (story / "Continuity.md").read_text(encoding="utf-8")


def test_a_chapter_changed_by_hand_is_followed_again_before_the_next_one(story):
    """The planner follows a chapter's text, not its version number: an edit made outside the app (or any screen)
    changes the text it followed, so the chapter is read again before the next chapter's briefs."""
    fake = planned(story)
    write(story, fake, [1])
    path = story / "chapters" / "ch01.md"
    path.write_text(path.read_text(encoding="utf-8").replace("gulls", "herons"), encoding="utf-8")
    before = len(fake.chat_bodies())
    second = write(story, fake, [2])
    assert second.status == FINISHED, second.reason
    assert [s.label for s in second.steps][:2] == ["Chapter 1: checking scenes 1, 2 as written",
                                                    "Chapter 2: the planner writes its scene briefs"]
    bodies = fake.chat_bodies()[before:]
    check = next(b for b in bodies if "You check scenes of a novel" in b["messages"][0]["content"])
    assert "herons" in check["messages"][1]["content"]  # it read chapter 1 as it is now
    assert "not happened yet" in check["messages"][1]["content"]  # its events, as if those scenes weren't written
    state = PlannerState.load(story)
    assert all(e.status == "written" and e.written_in[0] == 1 for e in state.registry.of_chapter(1))  # once, again
    assert not any(f.severity == "hard" for f in findings_of(state, 1))


def test_a_chapter_never_checked_is_followed_from_its_text(story):
    """A chapter written before the checks (or with them out of reach): the planner reads it whole before the next
    chapter's briefs, and the briefs start from how it actually ends."""
    fake = planned(story)
    write(story, fake, [1])
    state = PlannerState.load(story)
    state.audits, state.reconciled, state.reconciled_revisions = {}, [], {}
    state.save()
    before = len(fake.chat_bodies())
    second = write(story, fake, [2])
    assert second.status == FINISHED, second.reason
    assert [s.label for s in second.steps][:2] == ["Chapter 1: how things stand after it, as written",
                                                    "Chapter 2: the planner writes its scene briefs"]
    assert "the ledger" in second.steps[0].note  # where the text departed from the plan
    bodies = fake.chat_bodies()[before:]
    brief = next(b for b in bodies if "Write the brief for scene 1 of chapter 2" in b["messages"][-1]["content"])
    assert "ACTUAL-MARKER Mara is on the causeway" in brief["messages"][1]["content"]


def test_auto_write_pauses_after_each_chapter(story):
    fake = planned(story)
    auto = write(story, fake, [1, 2], pause_after_chapter=True)
    assert auto.status == FINISHED and "Paused after chapter 1" in auto.reason
    assert 2 not in load_project(story).chapters


def test_changes_asked_for_in_the_reader_are_planned_then_made_in_place(story):
    fake = planned(story)
    write(story, fake, [1])
    version = load_project(story).chapters[1].version
    task = ReviewTask(1, "ask")
    plan = asyncio.run(ask(story, 1, "Make the argument colder", settings=AppSettings(), catalog=SEEING,
                           client=fake.client(), task=task))
    assert plan.ready and [(c.number, c.how) for c in plan.scenes] == [(1, REVISE), (2, REWRITE)]
    asked = fake.chat_bodies()[-1]["messages"][1]["content"]
    assert "WRITTEN-PROSE" in asked and "Make the argument colder" in asked and "GUIDE-MARKER" in asked
    state = PlannerState.load(story)
    assert state.review_plans["1"]["scenes"][0]["change"] == "CHANGE-MARKER make the argument colder"
    assert [t["role"] for t in state.reviews["1"]] == ["user", "planner"]

    before = len(fake.chat_bodies())
    made = ReviewTask(1, "make")
    assert asyncio.run(make_changes(story, 1, settings=AppSettings(), catalog=SEEING, client=fake.client(),
                                    task=made))
    assert made.done == [1, 2] and not made.failure
    writer = fake.chat_bodies()[before:]
    revise = next(b for b in writer if "<change_request>" in b["messages"][-1]["content"])
    assert "CHANGE-MARKER" in revise["messages"][-1]["content"]
    assert revise["messages"][-2]["role"] == "assistant" and "WRITTEN-PROSE" in revise["messages"][-2]["content"]
    assert "NEW-BRIEF-1" in revise["messages"][1]["content"]  # its updated brief
    rewrite = next(b for b in writer if "<change_request>" not in b["messages"][-1]["content"])
    assert [m["role"] for m in rewrite["messages"]] == ["system", "user"]  # from its new brief alone

    chapter = load_project(story).chapters[1]
    assert chapter.version == version  # in place: no earlier version kept
    assert chapter.scenes[1].startswith("REVISED-PROSE") and chapter.scenes[2].startswith("WRITTEN-NEW-BRIEF-2")
    state = PlannerState.load(story)
    assert "NEW-BRIEF-1" in state.chapters[0].scenes[0].brief and "1" not in state.review_plans
    assert 1 not in state.reconciled  # followed again before chapter 2's briefs
    assert "NEW-BRIEF-1" in (story / "Chapter outline.md").read_text(encoding="utf-8")
    undo = next(s for s in list_snapshots(story) if s.action == "ch01-review")
    restore(story, undo.id)  # one Undo puts the chapter back
    assert load_project(story).chapters[1].scenes[1].startswith("WRITTEN-PROSE")


def test_a_question_comes_first_and_nothing_waits_for_approval(story):
    fake = planned(story)
    write(story, fake, [1])
    plan = asyncio.run(ask(story, 1, "QUESTION-MARKER less crying", settings=AppSettings(), catalog=SEEING,
                           client=fake.client()))
    assert plan.questions == ["Should she cry at all?"] and not plan.ready
    assert "1" not in PlannerState.load(story).review_plans


def test_a_change_that_reaches_later_chapters_has_their_briefs_written_again_later(story):
    planned(story, jit=False)  # every brief written up front
    state = PlannerState.load(story)
    assert all(s.brief for s in state.chapters[1].scenes)
    apply_to_plan(state, 1, ChangePlan(scenes=[SceneChange(1, REVISE, "colder", "NEW")], later="Tobias is a liar."))
    state = PlannerState.load(story)
    assert state.jit and state.briefs_through == 1
    assert all(not s.brief and s.status == "waiting" for s in state.chapters[1].scenes)
    assert json.loads((story / "planner" / "state.json").read_text(encoding="utf-8"))["reviews"]["1"]
