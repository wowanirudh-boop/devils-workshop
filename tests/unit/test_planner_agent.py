"""The agentic planner: it edits the plan's sections itself, records decisions, never repeats a suggestion,
acts on your Yes / No answers, finalizes the plan when you approve it, and is asked again if it said it
changed the plan but didn't."""

import asyncio

import pytest

from devils_workshop.planner import prompts
from devils_workshop.planner.output import create_planner_story
from devils_workshop.planner.run import FILL_GAPS, brainstorm, is_approval
from devils_workshop.planner.state import ACCEPTED, DECLINED, OPEN, PLANNER, PlannerState
from devils_workshop.settings import AppSettings

from tests.fakes import FakeOpenRouter, reply
from tests.unit.test_planner import SEEING

FIRST = """<edit section="Title">
The Two O'Clock Lamp
</edit>
<edit section="Main characters">
- **Maren** (15, my choice), the keeper's daughter.
</edit>
<edit section="Open questions">
- Who is at sea: to decide.
</edit>
<decision>Literary mystery, about 60,000 words, first person, past tense.</decision>
<suggestion>Give Maren a craft habit, mending the lamp wicks, so her hands carry the story.</suggestion>
<suggestion>Open in the narrator's present, looking back, to make the quiet feel deliberate.</suggestion>
<reply>I wrote the first plan and chose her age and the era myself (marked my choice).</reply>"""


@pytest.fixture
def story(tmp_path):
    return create_planner_story(tmp_path / "Lamp", "Lamp")


def turn(state, fake, text=""):
    return asyncio.run(brainstorm(state, text, [], settings=AppSettings(fallback=None), catalog=SEEING,
                                  client=fake.client()))


def last_request(fake) -> str:
    content = fake.chat_bodies()[-1]["messages"][-1]["content"]
    return content if isinstance(content, str) else content[0]["text"]


def test_replies_are_read_as_edits_decisions_and_suggestions():
    got = prompts.parse_agent_reply(FIRST + "\n<ready/>")
    assert [name for name, _ in got.edits] == ["Title", "Main characters", "Open questions"]
    assert got.decisions == ["Literary mystery, about 60,000 words, first person, past tense."]
    assert len(got.suggestions) == 2 and got.ready and got.message.startswith("I wrote the first plan")
    bare = prompts.parse_agent_reply("Just a question: what era?")
    assert bare.message == "Just a question: what era?" and not bare.edits


def test_edits_replace_their_section_and_new_ones_go_before_open_questions():
    plan = "## Title\nLamp\n\n## Length (total words and number of chapters)\n60,000\n\n## Open questions\n- era"
    new, changed = prompts.apply_edits(plan, [("Length", "60,000 words, 24 chapters"), ("Title", "Lamp"),
                                              ("Subplots", "The mother's letters.")])
    assert changed == ["Length (total words and number of chapters)", "Subplots"]  # the same Title isn't a change
    assert new.index("## Subplots") < new.index("## Open questions")
    assert "## Length (total words and number of chapters)\n60,000 words, 24 chapters" in new


def test_a_turn_edits_the_plan_and_records_what_is_new(story):
    fake = FakeOpenRouter().queue(reply(FIRST))
    state = PlannerState.load(story)
    turn(state, fake, "A lighthouse keeper's daughter; the lamp signals at 2 a.m.")
    assert "## Main characters\n- **Maren** (15, my choice)" in state.plan
    planner = state.turns[-1]
    assert planner.role == PLANNER and planner.edited == ["Title", "Main characters", "Open questions"]
    assert [s.id for s in state.suggestions] == planner.suggestions == [1, 2]
    assert state.decisions == ["Literary mystery, about 60,000 words, first person, past tense."]
    request = last_request(fake)
    assert "<current_plan>\n(empty: write the first version)" in request


def test_your_answers_are_sent_and_a_repeated_suggestion_is_dropped(story):
    second = ("<edit section=\"Main characters\">\n- **Maren** (34), home after her mother's death; mends the wicks.\n"
              "</edit>\n<decision>Maren is 34.</decision>\n<decision>Literary mystery, about 60,000 words, first "
              "person, past tense.</decision>\n<suggestion>Give Maren a craft habit: mending the lamp wicks, so her "
              "hands carry the story.</suggestion>\n<suggestion>Let the father keep a second, secret log.</suggestion>"
              "\n<reply>Done.</reply>")
    fake = FakeOpenRouter().queue(reply(FIRST), reply(second))
    state = PlannerState.load(story)
    turn(state, fake, "Idea.")
    state.answer(1, ACCEPTED)
    state.answer(2, DECLINED)
    assert len(state.untold_answers()) == 2
    turn(state, fake, "Make her 34.")
    request = last_request(fake)
    assert "Yes to these suggestions: put them in the plan now.\n- Give Maren a craft habit" in request
    assert "No to these suggestions: leave them out.\n- Open in the narrator's present" in request
    assert "<suggestions_so_far>" in request and "[accepted] Give Maren" in request and "[declined] Open" in request
    assert "<decisions>\n- Literary mystery" in request
    assert state.untold_answers() == []
    assert [s.text for s in state.suggestions][2:] == ["Let the father keep a second, secret log."]  # no repeat
    assert state.decisions == ["Literary mystery, about 60,000 words, first person, past tense.", "Maren is 34."]
    assert "(34)" in state.plan and state.turns[-1].suggestions == [3]


def test_approving_in_chat_finalizes_the_plan(story):
    final = ("<edit section=\"Open questions\">\nNone: the person at sea is the father's brother (my choice).\n"
             "</edit>\n<ready/>\n<reply>Finalized: I settled the last open question.</reply>")
    fake = FakeOpenRouter().queue(reply(FIRST), reply(final))
    state = PlannerState.load(story)
    turn(state, fake, "Idea.")
    assert not state.ready
    turn(state, fake, "This looks good. I approve the plan.")
    assert "<author_approved/>" in last_request(fake)
    assert state.ready and "to decide" not in state.plan and state.turns[-1].edited == ["Open questions"]


def test_if_it_changes_nothing_it_is_asked_once_more(story):
    words_only = "<reply>Great, the plan is approved and locked in. No changes were needed.</reply>"
    edits = "<edit section=\"Open questions\">\nNone left (my choices made).\n</edit>\n<ready/>\n<reply>Settled.</reply>"
    fake = FakeOpenRouter().queue(reply(FIRST), reply(words_only), reply(edits))
    state = PlannerState.load(story)
    turn(state, fake, "Idea.")
    result = turn(state, fake, "Approved, go ahead.")
    assert len(fake.chat_bodies()) == 3  # the planner's turn, then once more for the edits
    follow_up = fake.chat_bodies()[-1]["messages"]
    assert [m["role"] for m in follow_up][-2:] == ["assistant", "user"]
    assert "The plan hasn't changed" in follow_up[-1]["content"]
    assert "None left" in state.plan and state.ready and result.total_cost > 0
    assert state.turns[-1].text.startswith("Great, the plan is approved")


def test_fill_the_gaps_and_nothing_to_change(story):
    fake = FakeOpenRouter().queue(reply(FIRST), reply("<reply>Here is a thought.</reply>"),
                                  reply("<reply>Still nothing.</reply>"))
    state = PlannerState.load(story)
    turn(state, fake, "Idea.")
    turn(state, fake, FILL_GAPS)  # asked to change the plan: one more try when it doesn't
    assert len(fake.chat_bodies()) == 3
    assert state.turns[-1].text == "Here is a thought." and state.turns[-1].edited == []


@pytest.mark.parametrize("text, approved", [
    ("I approve the plan", True), ("Approved.", True), ("looks good, go ahead", True), ("LGTM", True),
    ("That's it, finalize it", True), ("I don't approve of the father's choices yet", False),
    ("This does not look good", False), ("Make her older", False), ("Yes to 2", False),
])
def test_what_counts_as_approving(text, approved):
    assert is_approval(text) is approved


def test_old_saved_chats_still_load(story):
    state = PlannerState.load(story)
    state.plan = "## Title\nLamp"
    state.save()
    raw = state.path_for(story).read_text(encoding="utf-8").replace('"suggestions": [],', "")
    state.path_for(story).write_text(raw, encoding="utf-8")
    again = PlannerState.load(story)
    assert again.suggestions == [] and again.decisions == [] and again.plan == "## Title\nLamp"
    assert all(s.status == OPEN for s in again.suggestions)
