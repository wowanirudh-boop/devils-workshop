"""How long what is left will take, from how long it has taken (devils_workshop/eta.py)."""

import json
from datetime import datetime

from devils_workshop import eta
from devils_workshop.chat.auto import DONE, AutoOptions, AutoRun, Step
from devils_workshop.planner.output import create_planner_story
from devils_workshop.planner.state import PlannerState
from devils_workshop.settings import AppSettings


def test_time_left_is_rounded_and_says_when_it_will_be_done():
    at = datetime(2026, 10, 25, 1, 0)
    assert eta.left_text(90, at) == "A few minutes left"
    assert eta.left_text(1500, at) == "About 25 min left · done around 01:25"
    assert eta.left_text(7900, at) == "About 2 h 10 min left · done around 03:11"
    assert eta.left_text(0) == "" and eta.usually_text(240) == "usually about 4 min"


def test_writing_goes_at_this_runs_own_pace_once_it_has_written_a_scene_and_keeps_it_for_the_next():
    first = eta.writing_left(10_000)  # no run has measured a pace yet
    assert first == 10_000 * eta.FIRST_PACE * 60 / 1000
    assert eta.writing_left(10_000, written_words=2_000, elapsed_s=600) == 3_000  # 0.3 s a word, by the clock
    eta.save_pace(4.0)
    assert eta.saved_pace() == 4.0 and eta.writing_left(1_000) == 240


def test_a_planning_run_adds_up_each_step_still_to_come_at_its_usual_time(tmp_path):
    root = create_planner_story(tmp_path / "Lamp", "The Lamp")
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / "costs.jsonl").write_text("\n".join(json.dumps({"job": "plan-guide", "outcome": "ok",
                                                                     "elapsed_s": s}) for s in (100, 140, 300)),
                                               encoding="utf-8")
    usual = eta.usual_times(eta.story_roots(root, []))
    assert usual["plan-guide"] == 140  # the median of your own
    state = PlannerState.load(root)
    left = eta.planning_left(state, usual)  # nothing planned yet: guide, chapters, canon, scene lists, briefs
    assert left == 140 + usual["plan-architecture"] + usual["plan-canon"] + 8 * usual["plan-scenes"] \
        + 4 * usual["plan-scene"]
    state.guide = "## Voice"
    assert eta.planning_left(state, usual) == left - 140


def test_a_scene_written_again_counts_once_in_what_a_run_has_written(tmp_path):
    run = AutoRun(tmp_path, AutoOptions([1]), settings=AppSettings(), catalog=None, client=None)
    run.steps = [Step(1, "Chapter 1, scene 1 of 2", DONE, words=900),
                 Step(1, "Chapter 1, scene 1 of 2", DONE, words=950),  # written again: it replaces itself
                 Step(1, "Chapter 1, scene 2 of 2", DONE, words=1000),
                 Step(1, "Chapter 1, scene 2: put right what the check found", DONE, words=1010),
                 Step(1, "Chapter 1: plan scenes", DONE, kind="plan", words=0)]
    assert run.written_words() == 1950
