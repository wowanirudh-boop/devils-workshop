"""A plan the author made elsewhere (another tool), brought in as it is and shown as their instructions want."""

import asyncio

import pytest

from devils_workshop.planner import canon, prompts
from devils_workshop.planner.inputs import add_input
from devils_workshop.planner.output import create_planner_story
from devils_workshop.planner.registry import IMPORT
from devils_workshop.planner.run import PLAN_NOTE, brainstorm, next_job
from devils_workshop.planner.state import PlannerState
from devils_workshop.settings import AppSettings

from tests.fakes import FakeOpenRouter, reply
from tests.unit.test_planner import SEEING

# An invented plan from another tool: long enough to be summarised were it an ordinary file, and holding back
# what the story needs on the page ("off-page").
BIBLE = "# Story bible\nMara Venn, a courier. Tobias, the man who hires her. " + "The harbour is cold. " * 400
OUTLINE = ("# Outline\n## Chapter 1: The Pier\nMara takes the parcel; the courier before her is shot (off-page).\n"
           "## Chapter 2: The Lamp\nTobias confronts her in the lamp room; the fight happens off-page.\n")
CONVERTED = ('<edit section="Title">Salt and Iron</edit>\n<edit section="Chapters (proposed)">1. The Pier: the '
             "courier is shot on the pier, on the page.\n2. The Lamp: the fight in the lamp room, blow by blow."
             "</edit>\n<decision>Two chapters, as the author's plan has them</decision>\n<agenda>\n</agenda>\n<ready/>\n"
             "<reply>I carried your plan in. I show the shooting and the fight on the page.</reply>")


@pytest.fixture
def imported(tmp_path):
    root = create_planner_story(tmp_path / "Salt", "Salt and Iron")
    add_input(root, "Bible.md", BIBLE.encode())
    add_input(root, "Outline.md", OUTLINE.encode())
    state = PlannerState.load(root)
    state.registry.kind, state.registry.plan_files = IMPORT, ["Bible.md", "Outline.md"]
    state.registry.reference = "Bible.md"
    state.save()
    return root


def test_your_plan_goes_in_whole_is_carried_into_the_story_plan_then_planned_with_you_as_usual(imported):
    fake = FakeOpenRouter().respond(lambda body: reply(CONVERTED))
    state = PlannerState.load(imported)
    result = asyncio.run(brainstorm(state, "", ["Bible.md", "Outline.md"], settings=AppSettings(), catalog=SEEING,
                                    client=fake.client()))
    assert result.ok and "on the page" in state.plan and state.ready
    first, = fake.chat_bodies()  # no notes request: the plan is never summarised
    assert state.notes["Bible.md"] == PLAN_NOTE and state.notes["Outline.md"] == PLAN_NOTE
    assert "made this story's plan elsewhere" in first["messages"][0]["content"]  # the conversion, not brainstorming
    sent = first["messages"][-1]["content"]
    assert sent.index('<file name="Bible.md">') < sent.index('<file name="Outline.md">')  # in the order chosen
    assert BIBLE.strip() in sent and "the fight happens off-page" in sent  # in full

    fake.respond(lambda body: reply("<reply>Done.</reply>"))
    asyncio.run(brainstorm(state, "Make Tobias older.", [], settings=AppSettings(), catalog=SEEING,
                           client=fake.client()))
    later = fake.chat_bodies()[-1]["messages"]
    assert "story development partner" in later[0]["content"] and "<your_plan>" not in later[-1]["content"]


def test_its_events_are_weighed_by_the_story_plan_and_the_chapters_keep_it_one_for_one(imported):
    state = PlannerState.load(imported)
    state.plan = "## Chapters (proposed)\n1. The Pier: the shooting, shown in full."
    assert state.registry.follows_reference() and next_job(state) == ("guide",)
    state.guide = "## Voice and style\nPlain."
    assert next_job(state) == ("sources",)  # its events first, as for an adaptation, then the map check holds them
    events = canon.source_events_messages(state, state.registry.reference)[1].content
    assert "<your_plan>" in events and "<story_plan>" in events and "shown in full" in events
    assert "List the events of the author's plan" in events
    chapters = prompts.architecture_messages(state)[1].content
    assert "the fight happens off-page" in chapters and "<story_plan>" in chapters
    other = PlannerState.load(imported)
    other.registry.kind = "original"
    assert prompts.your_plan(other) == "" and not prompts.converting(other)
