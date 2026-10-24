import asyncio
import json

import httpx
import pytest

from devils_workshop.context.assemble import ChatMessage
from devils_workshop.llm.catalog import Catalog, parse_model
from devils_workshop.llm.generate import DISCARD, SWITCH, WAIT, WARNING, build_body, generate, provider_body
from devils_workshop.llm.livecheck import live_check
from devils_workshop.settings import AppSettings, ProviderPrefs

from tests.fakes import MODELS, PROSE, TEST_KEY, FakeOpenRouter, broken_stream, http_error, reply

PRIMARY, FALLBACK = "z-ai/glm-5.3", "moonshotai/kimi-k3"
MESSAGES = [ChatMessage("system", "Write well."), ChatMessage("user", "<task>Write chapter 1.</task>")]


def catalog():
    return Catalog([parse_model(m) for m in MODELS])


def run_generate(fake, tmp_path, settings=None, **kwargs):
    events, sleeps = [], []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    result = asyncio.run(generate(
        MESSAGES, client=fake.client(), settings=settings or AppSettings(), catalog=kwargs.pop("catalog", catalog()),
        log_root=tmp_path / "logs", label="ch01", target_words=kwargs.pop("target_words", 3000), chapter=1,
        calibration_dir=tmp_path, on_event=events.append, sleep=fake_sleep, jitter=lambda: 0.0,
        secret=TEST_KEY, **kwargs))
    return result, events, sleeps


def kinds(events, kind):
    return [e for e in events if e.kind == kind]


# --- request bodies ------------------------------------------------------------------------

def test_body_drops_unsupported_settings_and_caps_max_tokens():
    settings = AppSettings()
    glm, kimi = (parse_model(m) for m in MODELS)
    body, dropped = build_body(PRIMARY, MESSAGES, settings.writer_options, glm, ProviderPrefs())
    assert body["temperature"] == 0.8 and body["top_p"] == 0.95 and body["reasoning"] == {"effort": "low"}
    assert body["max_tokens"] == 32000 and dropped == [] and "provider" not in body
    assert body["messages"][1] == {"role": "user", "content": "<task>Write chapter 1.</task>"}
    planned = build_body(PRIMARY, MESSAGES, settings.planner_options, glm, ProviderPrefs())[0]
    assert planned["temperature"] == 0.5 and planned["reasoning"] == {"effort": "medium"}
    assert planned["max_tokens"] == 64000  # the planner: room for long plans (the model's own maximum if less)

    body, dropped = build_body(FALLBACK, MESSAGES, settings.writer_options, kimi, ProviderPrefs())
    assert "temperature" not in body and dropped == ["temperature", "top_p"]  # the job's options

    settings.writer_options.max_output_tokens = 500_000
    assert build_body(PRIMARY, MESSAGES, settings.writer_options, glm, ProviderPrefs())[0]["max_tokens"] == 131072
    settings.writer_options.temperature = settings.writer_options.effort = None  # the model's own defaults
    body = build_body(PRIMARY, MESSAGES, settings.writer_options, glm, ProviderPrefs())[0]
    assert "temperature" not in body and "reasoning" not in body and body["top_p"] == 0.95


def test_provider_preferences():
    assert provider_body(ProviderPrefs()) is None
    assert provider_body(ProviderPrefs(allow=["A"], block=["B"], order=["C"], data_collection="deny",
                                       require_parameters=True)) == {
        "only": ["A"], "ignore": ["B"], "order": ["C"], "data_collection": "deny", "require_parameters": True}


# --- the attempt loop ------------------------------------------------------------------------

def test_ok_first_time_logs_costs_and_calibrates(tmp_path):
    fake = FakeOpenRouter().queue(reply(prompt_tokens=20))
    result, events, _ = run_generate(fake, tmp_path)
    assert result.ok and result.text == PROSE and result.model == PRIMARY and not result.used_fallback
    assert result.total_cost == pytest.approx(0.0034) and len(result.attempts) == 1
    files = sorted(p.name for p in result.log_folder.iterdir())
    assert files == ["attempt-1.request.json", "attempt-1.response.md", "meta.json", "prompt.md", "response.md"]
    meta = json.loads((result.log_folder / "meta.json").read_text())
    assert meta["ok"] and meta["attempts"][0]["usage"]["reasoning_tokens"] == 100
    cost_line = json.loads((tmp_path / "logs" / "costs.jsonl").read_text())
    assert cost_line["cost"] == 0.0034 and cost_line["chapter"] == 1 and cost_line["outcome"] == "ok"
    assert (tmp_path / "calibration.yaml").is_file()


def test_transient_failures_are_retried_with_growing_waits(tmp_path):
    fake = FakeOpenRouter().queue(http_error(503), broken_stream(), reply())
    result, events, sleeps = run_generate(fake, tmp_path)
    assert result.ok and len(result.attempts) == 3 and sleeps == [2.0, 4.0]
    assert len(kinds(events, WAIT)) == 2 and len(kinds(events, DISCARD)) == 1  # the broken stream's text
    assert result.model == PRIMARY


def test_no_connection_waits_for_the_network_without_using_up_the_models_tries(tmp_path):
    offline = [httpx.ConnectError("all ports in use") for _ in range(4)]
    fake = FakeOpenRouter().queue(*offline, http_error(502), http_error(502), reply())
    result, events, sleeps = run_generate(fake, tmp_path)
    assert result.ok and result.model == PRIMARY and not result.used_fallback  # the fallback would fail the same way
    assert sleeps == [5, 10, 20, 40, 2.0, 4.0]  # waits for the network, then the model's own tries as usual
    assert all("Waiting for the network" in e.message for e in kinds(events, WAIT)[:4])


def test_three_transient_failures_switch_to_fallback(tmp_path):
    fake = FakeOpenRouter().queue(http_error(502), http_error(502), http_error(502), reply(model=FALLBACK))
    result, events, sleeps = run_generate(fake, tmp_path)
    assert result.ok and result.used_fallback and result.model == FALLBACK
    assert [b["model"] for b in fake.chat_bodies()] == [PRIMARY] * 3 + [FALLBACK]
    assert "temperature" not in fake.chat_bodies()[-1]  # Kimi doesn't support it
    assert any("doesn't support" in w and "temperature" in w for w in result.warnings)
    assert len(kinds(events, SWITCH)) == 1 and sleeps == [2.0, 4.0]


@pytest.mark.parametrize("first", [
    reply("I'm sorry, but I can't continue this story."),          # refusal
    http_error(403, "Flagged by moderation", metadata={"reasons": ["x"]}),
    http_error(404, "No such model"),
    reply(""),                                                     # empty
])
def test_refusal_moderation_missing_model_or_empty_switch_at_once(tmp_path, first):
    fake = FakeOpenRouter().queue(first, reply(model=FALLBACK))
    result, _, sleeps = run_generate(fake, tmp_path)
    assert result.ok and result.used_fallback and sleeps == []
    assert len(result.attempts) == 2


def test_refusal_reason_and_pattern_are_logged(tmp_path):
    fake = FakeOpenRouter().queue(reply("I'm sorry, but I can't continue this story."), reply(model=FALLBACK))
    result, _, _ = run_generate(fake, tmp_path)
    first = result.attempts[0]
    assert first.outcome == "refusal" and first.detail.startswith("^")
    assert (result.log_folder / "attempt-1.response.md").read_text().startswith("I'm sorry")


def test_empty_because_of_length_retries_with_double_max_tokens(tmp_path):
    fake = FakeOpenRouter().queue(reply("", finish="length"), reply())
    result, events, _ = run_generate(fake, tmp_path)
    assert result.ok and not result.used_fallback
    assert [b["max_tokens"] for b in fake.chat_bodies()] == [32000, 64000]
    assert any("max_tokens 64,000" in e.message for e in kinds(events, WARNING))


def test_both_models_fail(tmp_path):
    fake = FakeOpenRouter().queue(reply("I'm sorry, but I can't help with that."),
                                  reply("As an AI, I must decline.", model=FALLBACK))
    result, _, _ = run_generate(fake, tmp_path)
    assert not result.ok and result.outcome == "refusal"
    assert result.failure.startswith("Both models failed") and PRIMARY in result.failure and FALLBACK in result.failure
    assert not (result.log_folder / "response.md").exists()
    assert len(json.loads((result.log_folder / "meta.json").read_text())["attempts"]) == 2


def test_fatal_error_stops_without_trying_fallback(tmp_path):
    fake = FakeOpenRouter().queue(http_error(401, "Invalid key"))
    result, _, _ = run_generate(fake, tmp_path)
    assert not result.ok and result.outcome == "fatal" and "API key" in result.failure
    assert len(fake.chat_bodies()) == 1


def test_long_retry_after_gives_up_on_that_model(tmp_path):
    fake = FakeOpenRouter().queue(http_error(429, retry_after="300"), reply(model=FALLBACK))
    result, _, sleeps = run_generate(fake, tmp_path)
    assert result.ok and result.used_fallback and sleeps == []


def test_cancel_stops_everything(tmp_path):
    fake = FakeOpenRouter().queue(reply())
    cancel = asyncio.Event()
    cancel.set()
    result, _, _ = run_generate(fake, tmp_path, cancel=cancel)
    assert not result.ok and result.outcome == "cancelled" and len(fake.chat_bodies()) == 1


def test_no_fallback_configured(tmp_path):
    settings = AppSettings(fallback=None)
    fake = FakeOpenRouter().queue(reply("I'm sorry, but I can't help with that."))
    result, _, _ = run_generate(fake, tmp_path, settings=settings)
    assert not result.ok and result.failure.startswith("The model failed")


def test_missing_cost_is_estimated_and_offline_catalog_warns(tmp_path):
    fake = FakeOpenRouter().queue(reply(cost=None))
    result, _, _ = run_generate(fake, tmp_path)
    assert result.attempts[0].cost_estimated and result.total_cost == pytest.approx(1200 * 1.4e-6 + 400 * 4.4e-6)
    fake = FakeOpenRouter().queue(reply())
    result, _, _ = run_generate(fake, tmp_path, catalog=Catalog([]))
    assert any("no model list" in w for w in result.warnings)
    assert fake.chat_bodies()[0]["temperature"] == 0.8  # sent as set, since it couldn't be checked


def test_api_key_never_written_anywhere(tmp_path):
    fake = FakeOpenRouter().queue(http_error(502), reply("I'm sorry, but I can't help."), reply(model=FALLBACK))
    run_generate(fake, tmp_path)
    assert fake.requests[0]["headers"]["authorization"].endswith(TEST_KEY)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert TEST_KEY not in path.read_text(encoding="utf-8"), path


# --- the live check, against the fake -----------------------------------------------------------

def test_live_check_report(tmp_path):
    fake = FakeOpenRouter(endpoints={PRIMARY: [{"provider_name": "Z.ai"}, {"provider_name": "DeepInfra"}]})
    fake.queue(reply("A lighthouse woke the sea with one slow gold blink, and the gulls answered."))
    report = asyncio.run(live_check(fake.client(), AppSettings(), tmp_path, TEST_KEY))
    text = report.text()
    assert report.ok
    assert "z-ai/glm-5.3 (writer's main, planner's main): found." in text
    assert "temperature: NOT supported" in text  # for Kimi
    assert "Providers serving z-ai/glm-5.3: Z.ai, DeepInfra" in text
    assert "provider.only works" in text and "(reported by OpenRouter)" in text
    assert "maximum possible cost of this request: $0.0025" in text  # 200 x 1.4e-6 + 512 x 4.4e-6
    body = fake.chat_bodies()[0]
    assert body["model"] == PRIMARY and body["provider"] == {"only": ["Z.ai"]} and body["max_tokens"] == 512


def test_live_check_without_key_or_models(tmp_path):
    report = asyncio.run(live_check(FakeOpenRouter().client(None), AppSettings(), tmp_path, None))
    assert not report.ok and "SKIPPED, no API key" in report.text()
    report = asyncio.run(live_check(FakeOpenRouter(models=[]).client(), AppSettings(), tmp_path, TEST_KEY))
    assert "NOT FOUND" in report.text() and not report.ok



def test_prose_that_runs_away_is_written_once_more_and_never_saved(tmp_path):
    """Seen with a GLM-based model on Venice: a scene of 7,586 words of fragments, cut off by the length limit."""
    from devils_workshop.llm.outcomes import runaway
    loop = "PAUSE HALT STOP CEASE DESIST DISCONTINUE QUIT ABANDON " * 40
    assert runaway(loop, 100, "stop") == "The reply fell into repeating itself"
    varied = " ".join(f"word{n}" for n in range(900))
    assert runaway(varied, 200, "stop").startswith("The reply ran to 900 words")
    assert runaway(" ".join(varied.split()[:450]), 200, "length") == "The reply ran past its length and was cut off"
    assert runaway("A good scene. " * 60, 500, "stop") is None  # a refrain within its length is fine
    scene = " ".join(f"word{n}" for n in range(700))  # varied prose, as a real scene is
    assert runaway(scene + " " + scene, 2500, "stop") == "The reply repeats a long passage of itself word for word"
    assert runaway(scene, 2500, "stop") is None  # written once: fine, whatever its length
    good = "The lamp turned. Maren counted the flashes and wrote them down, one after another. " * 20
    fake = FakeOpenRouter().queue(reply(loop * 10, finish="length"), reply(good))
    result, events, _ = run_generate(fake, tmp_path, target_words=500)  # a 500-word scene
    assert result.ok and result.text.startswith("The lamp turned.") and len(fake.chat_bodies()) == 2
    assert any("Writing it once more" in e.message for e in kinds(events, WARNING))


def test_out_of_room_while_thinking_asks_once_more_a_level_lower_not_with_more_room(tmp_path):
    """A model that thought until it ran out of room and never answered would only think longer with more room:
    it is asked once more a thinking level lower (with no level to lower, more room, as above)."""
    from dataclasses import replace
    settings = AppSettings()
    settings.planner_options = replace(settings.planner_options, effort="high")
    fake = FakeOpenRouter().queue(reply("", finish="length"), reply())
    result, events, _ = run_generate(fake, tmp_path, settings=settings, role="planner")
    assert result.ok and not result.used_fallback
    sent = fake.chat_bodies()
    assert [b["reasoning"]["effort"] for b in sent] == ["high", "medium"]
    assert sent[0]["max_tokens"] == sent[1]["max_tokens"]  # the same room
    assert any("thinking at medium instead of high" in e.message for e in kinds(events, WARNING))


def test_a_level_lower_is_one_the_model_takes():
    from devils_workshop.llm.catalog import ModelInfo
    from devils_workshop.llm.generate import _lower_effort
    no_medium = ModelInfo("m", effort_levels=["none", "low", "high", "max"])
    assert _lower_effort({"reasoning": {"effort": "high"}}, no_medium) == "low"
    assert _lower_effort({"reasoning": {"effort": "max"}}, no_medium) == "high"
    assert _lower_effort({"reasoning": {"effort": "low"}}, no_medium) is None  # no lower level that still thinks
    assert _lower_effort({"reasoning": {"effort": "high"}}) == "medium"  # levels not known: the usual ones
    assert _lower_effort({"max_tokens": 10}) is None  # no thinking level sent
