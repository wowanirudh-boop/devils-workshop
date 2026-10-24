"""Auto-write: chapters and scenes written one after another, and every reason a run stops."""

import asyncio
import json

from devils_workshop.chat.auto import (
    DONE,
    FAILED,
    FINISHED,
    SCENES,
    SKIPPED,
    STOPPED,
    WHOLE,
    AutoOptions,
    AutoRun,
)
from devils_workshop.chat.session import ChapterChat
from devils_workshop.llm.catalog import Catalog, parse_model
from devils_workshop.project.project import load_project
from devils_workshop.settings import AppSettings

from tests.fakes import MODELS, PROSE, FakeOpenRouter, reply

CATALOG = Catalog([parse_model(m) for m in MODELS])
SCENE_ONE = "The ledger room smelled of brine and old paper. Ada was already talking. " * 3
SCENE_TWO = "From the archive window the tower was a black thumb against the sky. " * 3


def make_run(root, fake, chapters, **options) -> AutoRun:
    """One request at a time, scenes in order, unless a test asks otherwise (see test_auto_together.py)."""
    extra = {k: options.pop(k) for k in ("sizes", "estimate") if k in options}
    opts = AutoOptions(chapters, **{"continue_short": False, "at_once": 1, "scenes_together": False, **options})
    return AutoRun(root, opts, settings=AppSettings(), catalog=CATALOG, client=fake.client,
                   sleep=lambda s: asyncio.sleep(0), **extra)


def run(auto: AutoRun) -> AutoRun:
    asyncio.run(auto.run())
    return auto


def test_whole_chapters_one_after_another_each_in_a_fresh_chat(sample):
    fake = FakeOpenRouter().queue(reply("THREE-MARKER " + PROSE), reply("FOUR-MARKER " + PROSE))
    auto = run(make_run(sample, fake, [1, 3, 4], mode=WHOLE))
    assert auto.status == FINISHED and [s.status for s in auto.steps] == [SKIPPED, DONE, DONE]
    assert "already written" in auto.steps[0].note  # the sample comes with chapter 1 written
    chapters = load_project(sample).chapters
    assert chapters[3].text.startswith("THREE-MARKER") and chapters[4].text.startswith("FOUR-MARKER")
    second = fake.chat_bodies()[1]["messages"]
    assert [m["role"] for m in second] == ["system", "user"]
    assert "THREE-MARKER" not in json.dumps(second)  # nothing from chapter 3 is carried into chapter 4
    assert auto.spent > 0 and "Finished" in auto.reason


def test_scenes_are_written_in_one_chat_so_each_sees_the_ones_before(sample):
    fake = FakeOpenRouter().queue(reply(SCENE_ONE), reply(SCENE_TWO))
    auto = run(make_run(sample, fake, [2]))  # chapter 2 is set to scene mode, with 2 scenes
    assert auto.status == FINISHED
    assert [s.label for s in auto.steps] == ["Chapter 2, scene 1 of 2", "Chapter 2, scene 2 of 2"]
    assert load_project(sample).chapters[2].scenes == {1: SCENE_ONE.strip(), 2: SCENE_TWO.strip()}
    second = fake.chat_bodies()[1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[2]["content"] == SCENE_ONE.strip()
    assert "Write scene 2 of 2" in second[3]["content"]


def test_chapter_without_scenes_is_planned_then_written(sample):
    plan = json.dumps({"scenes": [
        {"title": "The causeway", "beats": ["They cross the causeway before the water returns.",
                                             "Mara counts the marker posts under her breath."]},
        {"title": "The tower", "beats": ["Tobias climbs the forty-one steps to the clock room.",
                                          "The mechanism is warm, as if it ran an hour ago."]}]})
    fake = FakeOpenRouter().queue(reply(plan), reply(SCENE_ONE), reply(SCENE_TWO))
    auto = run(make_run(sample, fake, [3], mode=SCENES))
    assert auto.status == FINISHED
    assert [s.label for s in auto.steps] == ["Chapter 3: plan scenes", "Chapter 3, scene 1 of 2",
                                             "Chapter 3, scene 2 of 2"]
    project = load_project(sample)
    assert [s.title for s in project.outline.chapter(3).scenes] == ["The causeway", "The tower"]
    assert set(project.chapters[3].scenes) == {1, 2}


def test_without_planning_a_chapter_without_scenes_is_written_whole(sample):
    fake = FakeOpenRouter().queue(reply(PROSE))
    auto = run(make_run(sample, fake, [3], mode=SCENES, plan_scenes=False))
    assert auto.status == FINISHED and load_project(sample).chapters[3].text == PROSE


def test_written_chapters_are_skipped_unless_rewrite(sample):
    fake = FakeOpenRouter().queue(reply(PROSE), reply("NEW " + PROSE))
    run(make_run(sample, fake, [3]))
    auto = run(make_run(sample, fake, [3]))
    assert [s.status for s in auto.steps] == [SKIPPED] and len(fake.chat_bodies()) == 1
    auto = run(make_run(sample, fake, [3], rewrite=True))
    chapter = load_project(sample).chapters[3]
    assert chapter.text.startswith("NEW") and chapter.version == 2  # version 1 is kept


def test_half_written_scene_chapter_is_finished(sample):
    fake = FakeOpenRouter().queue(reply(SCENE_ONE), reply(SCENE_TWO))
    project = load_project(sample)
    chat = ChapterChat.load(sample, 2)
    prepared = chat.prepare_write(project, "scenes")
    chat.apply(project, prepared, SCENE_ONE)
    auto = run(make_run(sample, fake, [2]))
    assert [s.label for s in auto.steps] == ["Chapter 2, scene 2 of 2"]
    assert set(load_project(sample).chapters[2].scenes) == {1, 2}


def test_a_failed_request_stops_the_run_and_saves_nothing(sample):
    fake = FakeOpenRouter().queue(reply("I'm sorry, but I can't help with that."),
                                  reply("As an AI, I must decline.", model="moonshotai/kimi-k3"))
    auto = run(make_run(sample, fake, [3, 4], mode=WHOLE))
    assert auto.status == FAILED and auto.reason.startswith("Chapter 3")
    assert set(load_project(sample).chapters) == {1}
    assert len(fake.chat_bodies()) == 2  # main and fallback for chapter 3; chapter 4 never started


def test_spending_cap_stops_before_a_request_that_could_pass_it(sample):
    fake = FakeOpenRouter().queue(reply(PROSE, cost=0.0034), reply(PROSE, cost=0.0034))
    auto = run(make_run(sample, fake, [3, 4], mode=WHOLE, spend_cap=0.052,
                        estimate=lambda model, tokens, words: (0.01, 0.05)))
    assert auto.status == STOPPED and "cap" in auto.reason
    assert set(load_project(sample).chapters) == {1, 3} and len(fake.chat_bodies()) == 1


def test_over_the_size_limit_stops_without_sending(sample):
    fake = FakeOpenRouter()
    auto = run(make_run(sample, fake, [3], sizes=lambda model: {"size_limit": 10}))
    assert auto.status == STOPPED and "size limit" in auto.reason
    assert fake.chat_bodies() == []


def test_a_short_reply_is_continued_once(sample):
    more = "Below them the tide turned, and somewhere in the town the bell rang twice, " * 2 + "and stopped."
    fake = FakeOpenRouter().queue(reply(PROSE, finish="length"), reply(more))  # cut off: it ran out of room
    auto = run(make_run(sample, fake, [3], continue_short=True))
    assert auto.status == FINISHED and "continued once" in auto.steps[0].note
    assert load_project(sample).chapters[3].text.endswith("and stopped.")
    assert "<continue>" in fake.chat_bodies()[1]["messages"][-1]["content"]


def test_a_short_reply_that_ends_where_it_means_to_is_never_padded(sample):
    """Narrative attention, not word count: a complete scene shorter than planned is kept as it is, so no filler
    (or a second telling of its events) is asked for just to reach the target."""
    fake = FakeOpenRouter().queue(reply(PROSE))
    auto = run(make_run(sample, fake, [3], continue_short=True))
    assert auto.status == FINISHED and "continued" not in auto.steps[0].note
    assert len(fake.chat_bodies()) == 1


def test_stop_after_the_current_request(sample, monkeypatch):
    fake = FakeOpenRouter().queue(reply(SCENE_ONE), reply(SCENE_TWO))
    auto = make_run(sample, fake, [2, 3])
    original = ChapterChat.apply

    def apply_then_stop(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        auto.stop()
        return result

    monkeypatch.setattr(ChapterChat, "apply", apply_then_stop)
    run(auto)
    assert auto.status == STOPPED and len(fake.chat_bodies()) == 1
    assert set(load_project(sample).chapters[2].scenes) == {1}


def test_stop_now_abandons_the_request(sample):
    fake = FakeOpenRouter().queue(reply(PROSE))
    auto = make_run(sample, fake, [3])
    auto.cancel.set()
    run(auto)
    assert auto.status == STOPPED and "Nothing from the unfinished request" in auto.reason
    assert 3 not in load_project(sample).chapters
