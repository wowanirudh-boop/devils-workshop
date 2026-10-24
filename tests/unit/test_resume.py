"""Carrying on planning and Auto-write runs that the app closing (or the PC restarting) cut off."""

import asyncio
import time

from devils_workshop import resume
from devils_workshop.chat.auto import AutoOptions, AutoRun
from devils_workshop.llm.catalog import Catalog
from devils_workshop.planner.run import FINISHED, PlanRun
from devils_workshop.planner.state import READY, PlannerState
from devils_workshop.settings import AppSettings

from tests.fakes import FakeOpenRouter
from tests.unit.test_planner import SEEING, approved, planner_answers, story  # noqa: F401


async def always_online() -> bool:
    return True


def test_notes_are_kept_dropped_and_handed_back_once(tmp_path):
    folder = tmp_path / "Story"
    folder.mkdir()
    resume.note(folder, resume.PLAN, only=None, spend_cap=1.0, spent=0.25)
    resume.note(folder, resume.AUTO, options={"chapters": [3]}, spent=0.0)
    resume.note(tmp_path / "Gone", resume.PLAN)  # its folder isn't there any more
    assert len(resume.notes()) == 3
    resume.forget(folder, resume.AUTO)
    found = resume.cut_off()
    assert [(f["kind"], f["spend_cap"], f["spent"]) for f in found] == [(resume.PLAN, 1.0, 0.25)]
    assert resume.cut_off() == []  # handed back once: a run that is carried on keeps a note of its own

    resume.note(folder, resume.PLAN)
    assert resume.cut_off(now=time.time() + (resume.MAX_AGE_H + 1) * 3600) == []  # too old: dropped
    assert resume.remaining(1.0, 0.25) == 0.75 and resume.remaining(None, 5) is None


def test_a_planning_run_keeps_a_note_until_it_ends(story):
    approved(story)
    fake = FakeOpenRouter().respond(planner_answers)
    seen = []

    class Watched(PlanRun):
        async def _request(self, *args, **kwargs):
            seen.append(dict(resume.notes()))
            return await super()._request(*args, **kwargs)

    plan_run = Watched(story, AppSettings(), SEEING, fake.client, sleep=lambda s: asyncio.sleep(0), spend_cap=2.0)
    asyncio.run(plan_run.run())
    assert plan_run.status == FINISHED
    during = list(seen[-1].values())[0]
    assert during["kind"] == resume.PLAN and during["spend_cap"] == 2.0 and during["spent"] >= 0
    assert resume.notes() == {}  # it ended, so there is nothing to carry on


def test_a_cut_off_planning_run_carries_on_at_start_up(story, monkeypatch):
    from devils_workshop.planner import run as planner_run
    from devils_workshop.ui import planner as planner_screen
    from devils_workshop.ui.state import STATE
    state = approved(story)
    fake = FakeOpenRouter().respond(planner_answers)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    STATE.settings, STATE.catalog = AppSettings(), Catalog([])
    STATE.client_factory = lambda key: fake.client(key)
    monkeypatch.setattr(planner_screen, "model_name", lambda model: model)
    resume.note(story, resume.PLAN, only=None, spend_cap=None, spent=0.0)  # the app closed mid-run
    assert not any(s.status == READY for c in state.chapters for s in c.scenes)

    async def go():
        started = await resume.carry_on(wait_for_internet=always_online)
        assert started == [f"plan: {story.resolve()}"]
        run = planner_run.current_run(story)
        while run.running:
            await asyncio.sleep(0.01)
        return run

    run = asyncio.run(go())
    assert run.status == FINISHED
    state = PlannerState.load(story)
    assert all(s.status == READY for c in state.chapters for s in c.scenes)
    assert resume.notes() == {}
    planner_run.RUNS.clear()


def test_auto_write_notes_the_chapters_still_to_write(tmp_path):
    run = AutoRun(tmp_path, AutoOptions([2, 3, 4], spend_cap=5.0), AppSettings(), Catalog([]), lambda: None)
    run._finished.add(2)
    run.spent = 1.5
    run._note()
    item = list(resume.notes().values())[0]
    assert item["kind"] == resume.AUTO and item["options"]["chapters"] == [3, 4] and item["spent"] == 1.5
    run._end("stopped", "Stopped by you.")
    assert resume.notes() == {}


def test_a_cut_off_auto_write_carries_on_with_what_was_left(tmp_path, monkeypatch):
    from devils_workshop.project.newproject import create_project
    from devils_workshop.ui import auto as auto_screen
    from devils_workshop.ui.state import STATE
    root = create_project(tmp_path / "Story", sample=True)
    STATE.settings, STATE.catalog = AppSettings(), Catalog([])
    STATE.client_factory = lambda key: FakeOpenRouter().client(key)
    started = []
    monkeypatch.setattr(auto_screen, "start", lambda project, options: started.append((project.root, options)))
    resume.note(root, resume.AUTO, options={"chapters": [3, 4], "mode": "scenes", "spend_cap": 5.0, "at_once": 2,
                                            "unknown_option": True}, spent=1.25)
    asyncio.run(resume.carry_on(wait_for_internet=always_online))
    assert len(started) == 1
    where, options = started[0]
    assert where == root and options.chapters == [3, 4] and options.mode == "scenes" and options.at_once == 2
    assert options.spend_cap == 3.75  # what was left of the limit


def test_nothing_to_carry_on_asks_for_nothing():
    async def never() -> bool:
        raise AssertionError("no runs: it shouldn't wait for the internet")
    assert asyncio.run(resume.carry_on(wait_for_internet=never)) == []
