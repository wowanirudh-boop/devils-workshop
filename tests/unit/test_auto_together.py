"""Auto-write with several requests at the same time: chapters side by side, a chapter's scenes
written together and their joins smoothed, and the run's limits still holding."""

import asyncio
import json

import httpx

from devils_workshop.chat.auto import FAILED, FINISHED, SKIPPED, STOPPED, WHOLE, AutoOptions, AutoRun
from devils_workshop.chat.joins import apply_openings, opening, parse_join, prepare_join, to_smooth
from devils_workshop.chat.session import ChapterChat
from devils_workshop.context.render import clean
from devils_workshop.history import list_snapshots
from devils_workshop.llm.catalog import Catalog, parse_model
from devils_workshop.project.project import load_project
from devils_workshop.settings import AppSettings

from tests.fakes import MODELS, PROSE, FakeOpenRouter, reply

CATALOG = Catalog([parse_model(m) for m in MODELS])
SCENE_ONE = ("The archive smelled of brine and old paper, and Ada was already talking when Mara came in.\n\n"
             "They worked through the ledgers by lamplight, page after page of tides and tonnage.\n\n"
             "By midnight Mara had found the gap: three days in 1911 that nobody had written down.")
SCENE_TWO = ("Mara woke in her own bed with no memory of walking home, the ledger still under her arm.\n\n"
             "The harbour at night was all black water and the slap of rope against the masts.\n\n"
             "Someone was standing under the stopped clock, watching the boats, and did not turn round.")
NEW_OPENING = ("SMOOTHED-OPENING: She left the archive with the ledger under her arm and walked the long way "
               "round, down to the harbour, where the night was all black water.")
REFUSAL = "I'm sorry, but I can't help with that."


def last_user(body: dict) -> str:
    return body["messages"][-1]["content"]


def make_run(root, fake, chapters, **options) -> AutoRun:
    extra = {k: options.pop(k) for k in ("sizes", "estimate") if k in options}
    opts = AutoOptions(chapters, **{"continue_short": False, "scenes_together": True, **options})  # this mode
    return AutoRun(root, opts, settings=AppSettings(), catalog=CATALOG, client=fake.client,
                   sleep=lambda s: asyncio.sleep(0), **extra)


def run(auto: AutoRun) -> AutoRun:
    asyncio.run(auto.run())
    return auto


def story_responder(body: dict):
    """Scenes by number, the joins, whole chapters by number."""
    task = last_user(body)
    if "Rewrite the opening of" in task:
        return reply(f'<opening scene="2">\n{NEW_OPENING}\n</opening>')
    if "Write scene 1 of 2" in task:
        return reply(SCENE_ONE)
    if "Write scene 2 of 2" in task:
        return reply(SCENE_TWO)
    for number in (2, 3, 4):
        if f"Write chapter {number}" in task:
            return reply(f"CHAPTER-{number} " + PROSE)
    raise AssertionError("unexpected request: " + task[-200:])


def test_scenes_are_written_together_then_the_joins_are_smoothed(sample):
    fake = FakeOpenRouter().respond(story_responder)
    auto = run(make_run(sample, fake, [2]))  # chapter 2 is set to scene mode, with 2 scenes
    assert auto.status == FINISHED
    assert [s.label for s in auto.steps] == ["Chapter 2, scene 1 of 2", "Chapter 2, scene 2 of 2",
                                             "Chapter 2: smooth the joins between scenes"]
    assert "new opening for scene 2" in auto.steps[2].note
    assert "2 parts written" in auto.reason  # the join isn't counted as a part

    bodies = fake.chat_bodies()
    scene_two = next(b for b in bodies if "Write scene 2 of 2" in last_user(b))
    assert [m["role"] for m in scene_two["messages"]] == ["system", "user"]  # a fresh chat of its own
    assert "shown as beats only" in last_user(scene_two) and "being written separately" in last_user(scene_two)
    assert SCENE_ONE.split("\n")[0] not in json.dumps(scene_two)  # it never saw scene 1's text
    join = next(b for b in bodies if "Rewrite the opening of" in last_user(b))
    assert "nobody had written down" in last_user(join)            # scene 1's ending
    assert "no memory of walking home" in last_user(join)          # scene 2's opening

    scenes = load_project(sample).chapters[2].scenes
    assert scenes[1] == SCENE_ONE
    assert scenes[2].startswith("SMOOTHED-OPENING") and scenes[2].endswith("did not turn round.")
    assert "no memory of walking home" not in scenes[2]  # the old opening paragraph was replaced
    actions = [s.action for s in list_snapshots(sample)]
    assert "ch02-join" in actions and "ch02-together" in actions  # both can be undone in History

    chat = ChapterChat.load(sample, 2)  # rebuilt, so changes and continuations see the chapter as it is
    assert chat.mode == "scenes" and chat.turns[-1].content.startswith("SMOOTHED-OPENING")


def test_finishing_a_half_written_chapter_writes_only_the_missing_scenes(sample):
    project = load_project(sample)
    chat = ChapterChat.load(sample, 2)
    chat.apply(project, chat.prepare_write(project, "scenes"), SCENE_ONE)
    fake = FakeOpenRouter().respond(story_responder)
    auto = run(make_run(sample, fake, [2]))
    assert [s.label for s in auto.steps] == ["Chapter 2, scene 2 of 2", "Chapter 2: smooth the joins between scenes"]
    chapter = load_project(sample).chapters[2]
    assert chapter.version == 1 and chapter.scenes[1] == SCENE_ONE  # added to the same version
    assert chapter.scenes[2].startswith("SMOOTHED-OPENING")


def test_a_join_reply_that_cant_be_used_leaves_the_scenes_as_written(sample):
    def responder(body):
        if "Rewrite the opening of" in last_user(body):
            return reply("The scenes already flow into one another well, so there is nothing that needs to be "
                         "changed in any of the openings shown here at all.")
        return story_responder(body)
    auto = run(make_run(sample, FakeOpenRouter().respond(responder), [2]))
    assert auto.status == FINISHED and "couldn't be used" in auto.steps[-1].note
    assert load_project(sample).chapters[2].scenes == {1: SCENE_ONE, 2: SCENE_TWO}


def test_chapters_are_written_at_the_same_time_up_to_the_limit(sample):
    for limit, expected in ((2, 2), (1, 1)):
        in_flight, most = [0], [0]

        async def responder(body):
            in_flight[0] += 1
            most[0] = max(most[0], in_flight[0])
            await asyncio.sleep(0.02)
            in_flight[0] -= 1
            return story_responder(body)

        auto = run(make_run(sample, FakeOpenRouter().respond(responder), [2, 3, 4], mode=WHOLE, at_once=limit,
                            rewrite=True))
        assert auto.status == FINISHED and most[0] == expected
        chapters = load_project(sample).chapters
        assert all(chapters[n].text.startswith(f"CHAPTER-{n}") for n in (2, 3, 4))


def test_earlier_chapters_go_first(sample):
    fake = FakeOpenRouter().respond(story_responder)
    run(make_run(sample, fake, [4, 3, 2], mode=WHOLE, at_once=1))
    order = [next(n for n in (2, 3, 4) if f"Write chapter {n}" in last_user(b)) for b in fake.chat_bodies()]
    assert order == [2, 3, 4]


def test_a_failure_lets_requests_under_way_finish_and_starts_no_more(sample):
    async def responder(body):
        task = last_user(body)
        if "Write chapter 3" in task:
            return reply(REFUSAL, model=body["model"])
        await asyncio.sleep(0.05)  # chapter 2 is still being written when chapter 3 fails
        return story_responder(body)
    fake = FakeOpenRouter().respond(responder)
    auto = run(make_run(sample, fake, [2, 3, 4], mode=WHOLE, at_once=2))
    assert auto.status == FAILED and auto.reason.startswith("Chapter 3")
    assert set(load_project(sample).chapters) == {1, 2}  # chapter 2 was finished and saved
    assert not any("Write chapter 4" in last_user(b) for b in fake.chat_bodies())
    chapter_four = next(s for s in auto.steps if s.chapter == 4)
    assert chapter_four.status == SKIPPED and "not started" in chapter_four.note


def test_the_spending_cap_counts_requests_under_way(sample):
    async def responder(body):
        await asyncio.sleep(0.02)
        return story_responder(body)
    fake = FakeOpenRouter().respond(responder)
    auto = run(make_run(sample, fake, [3, 4], mode=WHOLE, at_once=2, spend_cap=0.05,
                        estimate=lambda model, tokens, words: (0.01, 0.03)))
    # Chapter 3 could cost up to $0.03; starting chapter 4 alongside it could take the run to $0.06.
    assert auto.status == STOPPED and "cap" in auto.reason
    assert len(fake.chat_bodies()) == 1 and set(load_project(sample).chapters) == {1, 3}


def test_stop_now_abandons_every_request_under_way(sample):
    def responder(body):
        async def keepalives():  # OpenRouter keeps a waiting stream open with comments like these
            for _ in range(250):
                yield b": OPENROUTER PROCESSING\n\n"
                await asyncio.sleep(0.02)
        return httpx.Response(200, content=keepalives())

    async def scenario():
        auto = make_run(sample, FakeOpenRouter().respond(responder), [3, 4], mode=WHOLE, at_once=2)
        task = asyncio.create_task(auto.run())
        await asyncio.sleep(0.05)
        assert len(auto.writing) == 2
        auto.stop(now=True)
        await asyncio.wait_for(task, 3)
        return auto

    auto = asyncio.run(scenario())
    assert auto.status == STOPPED and set(load_project(sample).chapters) == {1}


# --- the joins on their own -------------------------------------------------------------------------

def test_openings_and_what_is_replaced():
    long_scene = "\n\n".join(f"Paragraph {n} of a longer scene." for n in range(1, 7))
    assert opening(long_scene) == "Paragraph 1 of a longer scene.\n\nParagraph 2 of a longer scene."  # two
    assert opening(SCENE_TWO) == SCENE_TWO.split("\n\n")[0]                                            # one
    assert opening("Just one paragraph.") == ""                                                        # none
    scenes = {1: SCENE_ONE, 2: long_scene, 3: "Just one paragraph."}
    assert to_smooth(scenes) == [2]
    result = apply_openings(scenes, {2: "A new start."})
    assert result[2].split("\n\n") == ["A new start."] + long_scene.split("\n\n")[2:]
    single_lines = "Line one.\nLine two.\nLine three."
    assert apply_openings({1: "x", 2: single_lines}, {2: "New one."})[2] == "New one.\nLine two.\nLine three."


def test_new_openings_out_of_proportion_are_not_used():
    scenes = {1: SCENE_ONE, 2: SCENE_TWO}
    runaway = " ".join(["word"] * 400)
    found, rejected = parse_join(f'<opening scene="2">{runaway}</opening><opening scene="1">no</opening>', scenes)
    assert found == {} and rejected == [2]  # far too long (it ran on into the scene); scene 1 has no opening
    found, _ = parse_join(f'<opening scene=2>\n{NEW_OPENING}\n</opening>', scenes)
    assert found == {2: NEW_OPENING}


def test_the_join_request_carries_the_system_prompt_and_style_guide(sample):
    project = load_project(sample)
    request = prepare_join(project, 2, {1: SCENE_ONE, 2: SCENE_TWO})
    system, user = request.messages[0].content, request.messages[1].content
    assert clean(project.system_prompt)[:40] in system and "Rewrite each opening" in system
    assert "<style_guide>" in user and "<ending>" in user and "<opening>" in user
    assert user.index("<ending>") < user.index("<opening>")  # scene 1's ending, then scene 2's opening


def test_scene_plans_saved_at_the_same_time_dont_overwrite_each_other(sample):
    with (sample / "outline.md").open("a", encoding="utf-8") as handle:
        handle.write("\n# Chapter 5: The Return\nMara brings the ledger back to the archive.\n"
                     "\n# Chapter 6: High Water\nThe town floods; the clock starts again.\n")

    def plan(number):
        return json.dumps({"scenes": [
            {"title": f"Plan-{number}-A", "beats": ["Mara crosses the causeway before the water comes back in.",
                                                    "She counts the marker posts under her breath."]},
            {"title": f"Plan-{number}-B", "beats": ["Tobias climbs the forty-one steps to the clock room.",
                                                    "The mechanism is warm, as if it ran an hour ago."]}]})

    async def responder(body):
        task = last_user(body)
        for number in (3, 5):
            if f"Plan the scenes for chapter {number}" in task:
                await asyncio.sleep(0.01 if number == 3 else 0.03)
                return reply(plan(number))
        return reply(SCENE_ONE if "scene 1 of" in task else SCENE_TWO)

    auto = run(make_run(sample, FakeOpenRouter().respond(responder), [3, 5], mode="scenes", at_once=3))
    assert auto.status == FINISHED, auto.reason
    outline = load_project(sample).outline
    assert [s.title for s in outline.chapter(3).scenes] == ["Plan-3-A", "Plan-3-B"]
    assert [s.title for s in outline.chapter(5).scenes] == ["Plan-5-A", "Plan-5-B"]
    assert "Mara brings the ledger back" in outline.chapter(5).text and not outline.chapter(6).scenes
    assert set(load_project(sample).chapters) >= {3, 5}


def test_joins_left_unsmoothed_are_smoothed_by_the_next_run(sample):
    def refuse_joins(body):
        if "Rewrite the opening of" in last_user(body):
            return reply(REFUSAL, model=body["model"])
        return story_responder(body)
    auto = run(make_run(sample, FakeOpenRouter().respond(refuse_joins), [2]))
    assert auto.status == FINISHED  # the scenes are saved; only the smoothing didn't happen
    assert auto.steps[-1].status == SKIPPED and "Nothing from this request was saved" in auto.steps[-1].note
    assert ChapterChat.load(sample, 2).smooth_pending

    fake = FakeOpenRouter().respond(story_responder)
    auto = run(make_run(sample, fake, [2]))
    assert [s.label for s in auto.steps] == ["Chapter 2: smooth the joins between scenes"]
    assert len(fake.chat_bodies()) == 1 and not ChapterChat.load(sample, 2).smooth_pending
    assert load_project(sample).chapters[2].scenes[2].startswith("SMOOTHED-OPENING")


def test_a_failure_elsewhere_still_lets_a_chapter_finish_its_joins(sample):
    async def responder(body):
        task = last_user(body)
        if "Write chapter 3" in task:
            await asyncio.sleep(0.01)
            return reply(REFUSAL, model=body["model"])
        if "Write scene" in task:
            await asyncio.sleep(0.08)  # chapter 3 fails while chapter 2's scenes are being written
        return story_responder(body)
    auto = run(make_run(sample, FakeOpenRouter().respond(responder), [2, 3], mode="as set", at_once=3,
                        rewrite=True))
    assert auto.status == FAILED and auto.reason.startswith("Chapter 3")
    assert load_project(sample).chapters[2].scenes[2].startswith("SMOOTHED-OPENING")
