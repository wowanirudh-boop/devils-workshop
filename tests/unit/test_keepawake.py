"""Windows is kept awake while a request, auto-write run or planning run is under way, then let go."""

import asyncio
import sys

import pytest

from devils_workshop import keepawake
from devils_workshop.chat.auto import FINISHED, WHOLE, AutoOptions, AutoRun
from devils_workshop.context.assemble import ChatMessage
from devils_workshop.keepawake import ES_CONTINUOUS, ES_SYSTEM_REQUIRED, awake, holding
from devils_workshop.llm.catalog import Catalog, parse_model
from devils_workshop.llm.generate import generate
from devils_workshop.settings import AppSettings

from tests.fakes import MODELS, PROSE, FakeOpenRouter, http_error, reply

ON, OFF = ES_CONTINUOUS | ES_SYSTEM_REQUIRED, ES_CONTINUOUS
CATALOG = Catalog([parse_model(m) for m in MODELS])


def test_overlapping_holds_ask_once_and_let_go_once(keep_awake_calls):
    assert not holding()
    with awake():
        with awake():  # two requests at the same time
            assert holding()
        assert keep_awake_calls == [ON]
    assert keep_awake_calls == [ON, OFF] and not holding()
    with pytest.raises(RuntimeError):
        with awake():
            raise RuntimeError("a request blew up")
    assert keep_awake_calls == [ON, OFF, ON, OFF]  # let go even then


def test_a_failing_setter_never_stops_a_request(monkeypatch, keep_awake_calls):
    def broken(flags):
        raise OSError("no power API here")
    monkeypatch.setattr(keepawake, "setter", broken)
    with awake():
        pass
    assert not holding()


def held_while_asked(fake: FakeOpenRouter, *responses):
    """Queue replies that note whether the PC was being kept awake when the request arrived."""
    seen = []
    queue = list(responses)

    def answer(body):
        seen.append(holding())
        return queue.pop(0)
    fake.respond(answer)
    return seen


def test_each_request_keeps_it_awake_until_done(tmp_path, keep_awake_calls):
    fake = FakeOpenRouter()
    seen = held_while_asked(fake, reply(), http_error(402, "no credits"))

    def send():
        return asyncio.run(generate([ChatMessage("user", "Write.")], client=fake.client(), settings=AppSettings(),
                                    catalog=CATALOG, log_root=tmp_path, label="t", target_words=50,
                                    sleep=lambda s: asyncio.sleep(0)))
    assert send().ok
    assert not send().ok  # a failure lets go too
    assert seen == [True, True] and keep_awake_calls == [ON, OFF, ON, OFF] and not holding()


def test_auto_write_keeps_it_awake_for_the_whole_run(sample, keep_awake_calls):
    fake = FakeOpenRouter()
    seen = held_while_asked(fake, reply("THREE " + PROSE), reply("FOUR " + PROSE))
    run = AutoRun(sample, AutoOptions([3, 4], mode=WHOLE, continue_short=False, at_once=1), settings=AppSettings(),
                  catalog=CATALOG, client=fake.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(run.run())
    assert run.status == FINISHED and seen == [True, True]
    assert keep_awake_calls == [ON, OFF]  # one hold for the run, not one per request


def test_planning_keeps_it_awake_for_the_whole_run(tmp_path, keep_awake_calls):
    from devils_workshop.planner.output import create_planner_story
    from devils_workshop.planner.run import FINISHED as PLANNED, PlanRun, approve
    from devils_workshop.planner.state import PlannerState
    from tests.unit.test_planner import SEEING, planner_answers
    story = create_planner_story(tmp_path / "Salt", "Salt")
    state = PlannerState.load(story)
    state.plan = "## Title\nSalt"
    approve(state)
    fake = FakeOpenRouter()
    seen = []
    fake.respond(lambda body: (seen.append(holding()), planner_answers(body))[1])
    run = PlanRun(story, AppSettings(), SEEING, fake.client, sleep=lambda s: asyncio.sleep(0))
    asyncio.run(run.run())
    assert run.status == PLANNED and len(seen) == 7 and all(seen)
    assert keep_awake_calls == [ON, OFF] and not holding()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own power API")
def test_the_real_windows_call_is_accepted(monkeypatch):
    calls = []

    def through(flags):
        calls.append(flags)
        keepawake._windows(flags)  # the real SetThreadExecutionState; given back at once
    monkeypatch.setattr(keepawake, "setter", through)
    with awake():
        pass
    assert calls == [ON, OFF]
