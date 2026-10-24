"""Pictures for the planner: never more in one request than the model takes (Venice takes one)."""

import asyncio

from devils_workshop.context.assemble import ChatMessage, ImageRef
from devils_workshop.llm.catalog import Catalog, parse_model
from devils_workshop.llm.clients import Clients
from devils_workshop.llm.pictures import fit_pictures, limit_from_error, picture_limit
from devils_workshop.llm.venice import to_openrouter_shape
from devils_workshop.planner.inputs import add_input
from devils_workshop.planner.run import FINISHED, PlanRun, brainstorm
from devils_workshop.planner.state import USER, PlannerState, Turn
from devils_workshop.settings import AppSettings

from tests.fakes import MODELS, VENICE_MODELS, FakeOpenRouter, FakeVenice, http_error, reply
from tests.unit.test_planner import PNG, approved, planner_answers, story  # noqa: F401

VENICE = "venice:qwen3-vl-235b"
CATALOG = Catalog([parse_model({**m, "architecture": {"input_modalities": ["text", "image"]}}) for m in MODELS]
                  + [parse_model(to_openrouter_shape(m)) for m in VENICE_MODELS])
TOO_MANY = "At most 1 image(s) may be provided in one prompt. (parameter=image)"
NOT_SENT = "not sent: this model takes at most 1 per request"


def pictures_in(body: dict) -> list[str]:
    """The text of each message that carries pictures, and how many it carries."""
    return [part for m in body["messages"] if isinstance(m["content"], list)
            for part in m["content"] if part["type"] == "image_url"]


def text_of(body: dict) -> str:
    return "\n".join(m["content"] if isinstance(m["content"], str) else m["content"][0]["text"]
                     for m in body["messages"])


def like_venice(answer):
    """Venice's rule: a request with more than one picture is rejected (400), as the author saw it."""
    def respond(body):
        return http_error(400, TOO_MANY) if len(pictures_in(body)) > 1 else answer(body)
    return respond


def three_pictures(story) -> list[str]:
    return [add_input(story, f"{name}.png", PNG + name.encode()) for name in ("Mara", "Harbour", "Clock")]


def test_the_limit_is_known_for_venice_and_read_from_a_rejection():
    assert CATALOG.get(VENICE).max_images == 1 and picture_limit(VENICE, CATALOG.get(VENICE)) == 1
    assert picture_limit(VENICE) == 1  # a model list saved before the limit was known
    assert picture_limit(MODELS[0]["id"], CATALOG.get(MODELS[0]["id"])) is None
    assert limit_from_error(TOO_MANY) == 1 and limit_from_error("Maximum of 4 images per request") == 4
    assert limit_from_error("Invalid temperature") is None


def test_fitting_keeps_the_most_recent_pictures_and_names_the_rest():
    def image(name):
        return ImageRef(name, "image/png", PNG)
    messages = [ChatMessage("system", "Rules"),
                ChatMessage("user", '<picture name="a.png">(attached below)</picture>', [image("a.png")]),
                ChatMessage("assistant", "Nice."),
                ChatMessage("user", '<picture name="b.png">(attached below)</picture>\n'
                                    '<picture name="c.png">(attached below)</picture>', [image("b.png"), image("c.png")])]
    fitted = fit_pictures(messages, 1)
    assert [i.name for m in fitted for i in m.images] == ["c.png"]  # the latest message's last picture
    assert NOT_SENT in fitted[1].content and fitted[1].images == []
    assert '<picture name="b.png">(a picture; ' + NOT_SENT in fitted[3].content
    assert '<picture name="c.png">(attached below)' in fitted[3].content
    assert len(messages[3].images) == 2  # the messages themselves are left as they were
    assert fit_pictures(messages, None) == messages and fit_pictures(messages, 5) == messages
    assert all(not m.images for m in fit_pictures(messages, 0))


def test_each_picture_is_seen_once_in_its_own_request_then_only_its_notes_are_sent(story):
    """A picture serves what you say it is for, in the turn you attach it: one request each (Venice takes one
    picture per request), and from then on the planner works from its notes, never the picture again."""
    names = three_pictures(story)
    venice = FakeVenice().respond(like_venice(planner_answers))
    state = PlannerState.load(story)
    state.model = VENICE
    result = asyncio.run(brainstorm(state, "Mara, the harbour and the clock.", names, settings=AppSettings(),
                                    catalog=CATALOG, client=Clients(FakeOpenRouter().client(), venice.client())))
    assert result.ok and state.plan.startswith("## Title")
    notes, chat = venice.chat_bodies()[:3], venice.chat_bodies()[3]
    assert [len(pictures_in(b)) for b in notes] == [1, 1, 1] and len(venice.chat_bodies()) == 4
    assert all("Mara, the harbour and the clock." in text_of(b) for b in notes)  # with what you said it is for
    assert pictures_in(chat) == [] and text_of(chat).count("NOTES-MARKER on") == 3
    assert sorted(state.notes) == ["Clock.png", "Harbour.png", "Mara.png"]

    asyncio.run(brainstorm(state, "Make her older.", [], settings=AppSettings(), catalog=CATALOG,
                           client=Clients(FakeOpenRouter().client(), venice.client())))
    later = venice.chat_bodies()[4]
    assert len(venice.chat_bodies()) == 5 and pictures_in(later) == [] and "NOTES-MARKER on Mara.png" in text_of(later)


def test_planning_sends_no_pictures_only_the_notes_on_them(story):
    state = approved(story)
    state.turns.append(Turn(USER, "My pictures", three_pictures(story)))
    state.notes = {"Mara.png": "NOTES-MARKER Mara is tall, with salt-grey hair."}
    state.model = VENICE
    state.save()
    venice = FakeVenice().respond(like_venice(planner_answers))
    plan_run = PlanRun(story, AppSettings(), CATALOG, lambda: Clients(FakeOpenRouter().client(), venice.client()),
                       sleep=lambda s: asyncio.sleep(0))
    asyncio.run(plan_run.run())
    assert plan_run.status == FINISHED, plan_run.reason
    bodies = venice.chat_bodies()
    assert len(bodies) == 9 and all(pictures_in(b) == [] for b in bodies)
    planning = [b for b in bodies if "You keep the canon" not in b["messages"][0]["content"]]
    up_to_the_chapters = [b for b in planning if "Write the writer's guide" in b["messages"][0]["content"]
                          or "Design the chapter architecture for the whole novel" in text_of(b)]
    assert len(up_to_the_chapters) == 2 and all("NOTES-MARKER Mara is tall" in text_of(b) for b in up_to_the_chapters)
    after = [b for b in planning if b not in up_to_the_chapters]  # scene lists and briefs plan from the chapters
    assert after and not any("NOTES-MARKER" in text_of(b) for b in after)


def test_a_too_many_pictures_rejection_is_sent_again_within_the_limit_and_remembered(tmp_path):
    """A model with no known limit rejects a request for having too many pictures: it is sent again with the
    most recent one, and later requests to that model start within the limit."""
    from devils_workshop.llm.generate import generate

    def image(name):
        return ImageRef(name, "image/png", PNG + name.encode())

    messages = [ChatMessage("system", "Look."),
                ChatMessage("user", "".join(f'<picture name="{n}">(attached below)</picture>' for n in "abc"),
                            [image(n) for n in "abc"])]
    fake = FakeOpenRouter().queue(http_error(400, TOO_MANY), reply("Seen."), reply("Seen again."))
    for _ in range(2):
        result = asyncio.run(generate(messages, client=fake.client(), settings=AppSettings(), catalog=CATALOG,
                                      log_root=tmp_path, label="t", target_words=10, min_words=1,
                                      sleep=lambda s: asyncio.sleep(0)))
        assert result.ok
    first, again, later = fake.chat_bodies()
    assert len(pictures_in(first)) == 3 and len(pictures_in(again)) == 1 and text_of(again).count(NOT_SENT) == 2
    assert len(pictures_in(later)) == 1  # within the limit from the start
    assert picture_limit(MODELS[0]["id"], CATALOG.get(MODELS[0]["id"])) == 1
