import asyncio
import json

import httpx
import pytest

from devils_workshop.llm import calibration
from devils_workshop.llm.catalog import Catalog, filter_params, load_catalog, parse_model
from devils_workshop.llm.costs import attempt_cost, format_summary, read_costs, summarize
from devils_workshop.llm.openrouter import StreamResult, parse_retry_after
from devils_workshop.llm.outcomes import (
    CANCELLED, EMPTY, EMPTY_LENGTH, FATAL, MODERATION, OK, REFUSAL, TRANSIENT, UNAVAILABLE,
    classify, load_refusal_patterns, retry_delay,
)
from devils_workshop.llm.sse import COMMENT, DATA, iter_events
from devils_workshop.logs import REDACTED, RequestLog
from devils_workshop.settings import (
    AppSettings, ModelSettings, get_api_key, load_settings, recommended, save_settings, settings_path,
)

from tests.fakes import MODELS, PROSE, TEST_KEY, FakeOpenRouter, broken_stream, chunk, http_error, reply, sse

PATTERNS = load_refusal_patterns()


def run(coro):
    return asyncio.run(coro)


# --- SSE parsing ---------------------------------------------------------------------------

def test_sse_events():
    async def lines():
        for line in [": OPENROUTER PROCESSING", "", "data: {\"a\": 1}", "", "data: line one", "data: line two",
                     "", "event: ignored", "data: [DONE]\r"]:
            yield line

    async def collect():
        return [event async for event in iter_events(lines())]

    assert run(collect()) == [(COMMENT, "OPENROUTER PROCESSING"), (DATA, '{"a": 1}'),
                              (DATA, "line one\nline two"), (DATA, "[DONE]")]


# --- the HTTP client ------------------------------------------------------------------------

def test_stream_happy_path_and_headers():
    fake = FakeOpenRouter().queue(reply(reasoning="Thinking about clocks."))
    seen = []
    result = run(fake.client().stream_chat({"model": "z-ai/glm-5.3", "messages": []}, on_text=seen.append))
    assert not result.failed and result.saw_done
    assert result.text == PROSE and "".join(seen) == PROSE
    assert result.reasoning == "Thinking about clocks."
    assert (result.provider, result.model, result.generation_id, result.finish_reason) == \
        ("Z.ai", "z-ai/glm-5.3", "gen-123", "stop")
    assert result.usage["cost"] == 0.0034 and result.keepalives == 1 and result.first_text_s is not None
    request = fake.requests[0]
    assert request["headers"]["authorization"] == f"Bearer {TEST_KEY}"
    assert request["headers"]["x-openrouter-title"] == "Devil's Workshop"
    assert request["body"]["stream"] is True


def test_http_error_with_retry_after():
    fake = FakeOpenRouter().queue(http_error(429, "Rate limited", retry_after="7"))
    result = run(fake.client().stream_chat({"model": "m", "messages": []}))
    assert (result.error_kind, result.status_code, result.retry_after) == ("http", 429, 7.0)
    assert result.error["message"] == "Rate limited"


def test_mid_stream_error_chunk_even_as_first_event():
    error = {"id": "gen-9", "error": {"code": 502, "message": "Provider died"},
             "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "error"}]}
    fake = FakeOpenRouter().queue(sse(error, done=False))
    result = run(fake.client().stream_chat({"model": "m", "messages": []}))
    assert result.error_kind == "stream" and result.error["message"] == "Provider died"
    assert result.status_code == 200


def test_connection_drop_keeps_partial_text():
    fake = FakeOpenRouter().queue(broken_stream("The harbour clock"))
    result = run(fake.client().stream_chat({"model": "m", "messages": []}))
    assert result.error_kind == "network" and result.text == "The harbour clock"


def test_stream_ending_early_is_incomplete():
    fake = FakeOpenRouter().queue(sse(chunk("Half a"), done=False))
    result = run(fake.client().stream_chat({"model": "m", "messages": []}))
    assert result.error_kind == "incomplete"


def test_connect_failure_and_timeout():
    fake = FakeOpenRouter().queue(httpx.ConnectError("no route"), httpx.ReadTimeout("slow"))
    assert run(fake.client().stream_chat({"model": "m", "messages": []})).error_kind == "network"
    assert run(fake.client().stream_chat({"model": "m", "messages": []})).error_kind == "timeout"


def test_cancel():
    fake = FakeOpenRouter().queue(reply())
    cancel = asyncio.Event()
    cancel.set()
    assert run(fake.client().stream_chat({"model": "m", "messages": []}, cancel=cancel)).error_kind == "cancelled"


def test_retry_after_formats():
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after(None) is None and parse_retry_after("soon") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0  # a date in the past


# --- outcomes -------------------------------------------------------------------------------

def result(**kwargs) -> StreamResult:
    data = {"text": PROSE, "finish_reason": "stop", "saw_done": True}
    data.update(kwargs)
    return StreamResult(**data)


@pytest.mark.parametrize("kwargs, expected", [
    ({}, OK),
    ({"error_kind": "http", "status_code": 429}, TRANSIENT),
    ({"error_kind": "http", "status_code": 503}, TRANSIENT),
    ({"error_kind": "network"}, TRANSIENT),
    ({"error_kind": "stream"}, TRANSIENT),
    ({"error_kind": "incomplete"}, TRANSIENT),
    ({"error_kind": "http", "status_code": 403}, MODERATION),
    ({"finish_reason": "content_filter"}, MODERATION),
    ({"error_kind": "http", "status_code": 404}, UNAVAILABLE),
    ({"error_kind": "http", "status_code": 400}, FATAL),
    ({"error_kind": "http", "status_code": 401}, FATAL),
    ({"error_kind": "http", "status_code": 402}, FATAL),
    ({"error_kind": "cancelled"}, CANCELLED),
    ({"text": ""}, EMPTY),
    ({"text": "Only a few words.", "finish_reason": "length"}, EMPTY_LENGTH),
    ({"text": "I'm sorry, but I can't continue this story."}, REFUSAL),
    ({"text": "I can’t help with writing that. As an AI, I must decline requests like this one."}, REFUSAL),
])
def test_classify(kwargs, expected):
    assert classify(result(**kwargs), 3000, PATTERNS).kind == expected


def test_dialogue_with_i_cant_is_not_a_refusal():
    text = '"I can\'t do this," she said, and set the lamp down. ' + PROSE
    assert classify(result(text=text), 3000, PATTERNS).kind == OK


def test_long_reply_mentioning_policy_is_not_a_refusal():
    # Over 25% of the 3,000-word target, so it is not treated as a short reply at all.
    text = "As an AI would say, the content policy of the harbour was simple. " + " ".join([PROSE] * 20)
    assert classify(result(text=text), 3000, PATTERNS).kind == OK


def test_retry_delays():
    assert [retry_delay(n, None, 2, 60, 0.0) for n in (1, 2, 3)] == [2, 4, 8]
    assert retry_delay(1, None, 2, 60, 0.2) == pytest.approx(2.4)
    assert retry_delay(6, None, 2, 60, 0.0) == 60       # capped
    assert retry_delay(1, 9.0, 2, 60, 0.2) == 9.0       # Retry-After wins
    assert retry_delay(1, 300.0, 2, 60, 0.0) is None    # too long: give up on this model


# --- model catalogue ------------------------------------------------------------------------

def test_parse_model_and_filter_params():
    glm, kimi = (parse_model(m) for m in MODELS)
    assert glm.price_per_million() == pytest.approx((1.4, 4.4))
    assert glm.max_completion_tokens == 131072 and glm.supports("reasoning")
    params = {"temperature": 0.9, "top_p": 0.95, "seed": 1}
    assert filter_params(params, glm) == ({"temperature": 0.9, "top_p": 0.95}, ["seed"])
    assert filter_params(params, kimi) == ({}, ["seed", "temperature", "top_p"])
    assert filter_params(params, None) == (params, [])  # can't check: send as set


def test_check_names_suggests_similar():
    catalog = Catalog([parse_model(m) for m in MODELS])
    assert catalog.check_names(["z-ai/glm-5.3"]) == []
    warning = catalog.check_names(["z-ai/glm-5"])[0]
    assert "isn't in OpenRouter's model list" in warning and "z-ai/glm-5.3" in warning


def test_catalog_cache_and_offline(tmp_path):
    fake = FakeOpenRouter()
    catalog, notes = run(load_catalog(fake.client(), tmp_path))
    assert "z-ai/glm-5.3" in catalog.models and not catalog.from_cache and notes == []
    catalog, _ = run(load_catalog(fake.client(), tmp_path))
    assert catalog.from_cache and len(fake.requests) == 1  # second call used the saved copy

    class Offline:
        async def list_models(self):
            raise httpx.ConnectError("offline")

    catalog, notes = run(load_catalog(Offline(), tmp_path, refresh=True))
    assert catalog.from_cache and "using the saved copy" in notes[0]
    catalog, notes = run(load_catalog(Offline(), tmp_path / "empty"))
    assert catalog.models == {} and "Couldn't get the model list" in notes[0]


# --- settings and the API key ----------------------------------------------------------------

def test_settings_created_with_defaults_and_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("DW_SETTINGS_DIR", str(tmp_path))
    settings = load_settings()
    assert settings_path() == tmp_path / "settings.yaml" and settings_path().is_file()
    assert settings.primary == "z-ai/glm-5.3" and settings.fallback == "moonshotai/kimi-k3"
    assert settings.writer_options == recommended("writer") and settings.planner_options == recommended("planner")
    settings.planner_options.temperature, settings.planner_options.effort = 0.4, None
    settings.writer_options.params = {"min_p": 0.05}
    settings.providers.block = ["SomeProvider"]
    save_settings(settings)
    again = load_settings()
    assert again.planner_options.temperature == 0.4 and again.planner_options.effort is None
    assert again.planner_options.top_p == 0.95 and again.writer_options.params == {"min_p": 0.05}
    assert again.providers.block == ["SomeProvider"] and again.model("unknown/model") == ModelSettings()
    assert "API key is NOT stored here" in settings_path().read_text()


def test_api_key_environment_variable_wins(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "  sk-or-from-env  ")
    assert get_api_key() == "sk-or-from-env"


def test_api_key_stored_and_removed(monkeypatch, credential_store):
    from devils_workshop.settings import api_key_source, delete_api_key, set_api_key
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert get_api_key() is None and api_key_source() == "none"
    set_api_key("  sk-or-stored  ")
    assert credential_store.passwords == {("DevilsWorkshop", "openrouter"): "sk-or-stored"}  # not the real store
    assert get_api_key() == "sk-or-stored" and api_key_source() == "stored"
    delete_api_key()
    delete_api_key()  # nothing left to remove is fine
    assert get_api_key() is None


def test_api_key_missing_credential_store_is_not_an_error(monkeypatch):
    import sys
    import types
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    broken = types.SimpleNamespace(get_password=lambda *a: (_ for _ in ()).throw(RuntimeError("no backend")))
    monkeypatch.setitem(sys.modules, "keyring", broken)
    assert get_api_key() is None


# --- costs, calibration, logs ---------------------------------------------------------------

def test_attempt_cost_reported_or_estimated():
    glm = parse_model(MODELS[0])
    assert attempt_cost({"cost": 0.5}, glm) == (0.5, False)
    cost, estimated = attempt_cost({"prompt_tokens": 1_000_000, "completion_tokens": 100_000}, glm)
    assert estimated and cost == pytest.approx(1.4 + 0.44)
    assert attempt_cost(None, None) == (0.0, True)


def test_cost_summary(tmp_path):
    lines = [{"chapter": 1, "model": "a", "job": "write", "cost": 0.10},
             {"chapter": 1, "model": "b", "job": "write", "cost": 0.05, "cost_estimated": True},
             {"chapter": 2, "model": "a", "job": "write", "cost": 0.20}]
    path = tmp_path / "costs.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n{half a line", encoding="utf-8")
    records = read_costs(tmp_path)
    assert len(records) == 3
    total = summarize(records)
    assert total.total == pytest.approx(0.35) and total.by_chapter == {1: pytest.approx(0.15), 2: 0.20}
    assert summarize(records, chapter=2).calls == 1
    assert "1 estimated from prices" in format_summary(total, "Costs")


def test_calibration_moves_towards_measurement(tmp_path):
    assert calibration.chars_per_token(tmp_path, "m") == 4.0
    assert calibration.update(tmp_path, "m", 3000, 1000) == pytest.approx(3.7)  # 4 + 0.3 * (3 - 4)
    assert calibration.chars_per_token(tmp_path, "m") == pytest.approx(3.7)
    assert calibration.update(tmp_path, "m", 100, 1000) == pytest.approx(3.7)   # 0.1 chars/token: ignored


def test_request_log_numbering_and_scrubbing(tmp_path):
    first = RequestLog(tmp_path, "ch05 write", secret=TEST_KEY)
    second = RequestLog(tmp_path, "ch05 write", secret=TEST_KEY)
    assert first.folder.name == "ch05-write-01" and second.folder.name == "ch05-write-02"
    first.write("x.md", f"key was {TEST_KEY}")
    assert (first.folder / "x.md").read_text() == f"key was {REDACTED}"
