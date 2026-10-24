"""Experiential Labs, built in beside OpenRouter and Venice, and starting with Windows in the background."""

import asyncio
import json

import httpx
import pytest

from devils_workshop import startup
from devils_workshop.llm.catalog import parse_model
from devils_workshop.llm.clients import Clients
from devils_workshop.llm.experiential import ExperientialClient, is_explabs_key
from devils_workshop.llm.generate import build_body
from devils_workshop.llm.openrouter import StreamResult
from devils_workshop.llm.outcomes import FATAL, TRANSIENT, classify, load_refusal_patterns
from devils_workshop.llm.services import service_body, service_name, service_of
from devils_workshop.settings import (
    AppSettings,
    ProviderPrefs,
    explabs_key_source,
    get_explabs_key,
    load_settings,
    save_settings,
    set_service_key,
)

from tests.fakes import FakeOpenRouter, chunk, sse, usage

KEY = "xpl_" + "0123456789abcdef" * 2 + "01234567"
SLUG = "deepseek-v4.1-flash"
MODEL = "svc-explabs:" + SLUG


def catalog_row(slug: str, nano_in=300_000_000, nano_out=1_200_000_000, efforts=("none", "low", "high", "max")):
    return {
        "model": {"slug": slug, "display_name": "DeepSeek V4.1 Flash 1017", "context_window": 1048576,
                  "max_output_tokens": 393216, "input_modalities": ["text"], "supported_params": {}},
        "default_provider_ids": ["route-1"],
        "providers": [
            {"id": "route-0", "status": "disabled", "routable": False, "input_nano_usd_per_million": 9,
             "output_nano_usd_per_million": 9, "capabilities": {}},
            {"id": "route-1", "status": "active", "routable": True, "input_nano_usd_per_million": nano_in,
             "output_nano_usd_per_million": nano_out, "cached_input_nano_usd_per_million": 30_000_000,
             "capabilities": {"supports_temperature": True, "supports_top_p": True, "supports_top_k": False,
                              "supports_stop_sequences": True, "supports_reasoning": True,
                              "supported_reasoning_efforts": list(efforts)}},
        ],
    }


class FakeGateway:
    """Experiential Labs' gateway: /v1/models (key needed), the public catalog, and chat completions."""

    def __init__(self, callable_=(SLUG, "gpt-unlisted"), catalog_ok=True, page=None):
        self.callable = list(callable_)
        self.catalog_ok = catalog_ok
        self.rows = [catalog_row(SLUG)] + [catalog_row(f"other-{i}") for i in range(page or 0)]
        self.requests: list[httpx.Request] = []
        self.chat: list[httpx.Response] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v1/models":
            if request.headers.get("authorization") != f"Bearer {KEY}":
                return httpx.Response(401, json={"error": {"code": "invalid_key", "message": "bad key"}})
            return httpx.Response(200, json={"object": "list", "data": [{"id": s, "object": "model"}
                                                                        for s in self.callable]})
        if request.url.path == "/api/models":
            if not self.catalog_ok:
                return httpx.Response(503, json={"error": "down"})
            limit, offset = int(request.url.params["limit"]), int(request.url.params["offset"])
            return httpx.Response(200, json={"models": self.rows[offset:offset + limit], "total": len(self.rows),
                                             "limit": limit, "offset": offset})
        if request.url.path == "/v1/chat/completions":
            return self.chat.pop(0)
        return httpx.Response(404)

    def client(self, key=KEY) -> ExperientialClient:
        return ExperientialClient(key, base_url="https://api.experientiallabs.ai/v1",
                                  catalog_url="https://api.experientiallabs.ai/api/models",
                                  transport=httpx.MockTransport(self.handler))


def run(coro):
    return asyncio.run(coro)


def test_models_are_the_callable_ones_described_from_the_catalog():
    gateway = FakeGateway()
    models = {m["id"]: m for m in run(gateway.client().list_models())}
    assert set(models) == {MODEL, "svc-explabs:gpt-unlisted"}
    info = parse_model(models[MODEL])
    assert info.name == "Experiential Labs: DeepSeek V4.1 Flash 1017"
    assert info.context_length == 1048576 and info.max_completion_tokens == 393216
    assert info.price_per_million() == pytest.approx((0.30, 1.20))  # from the default route, not a disabled one
    assert info.thinking == "levels" and info.effort_levels == ["none", "low", "high", "max"]
    assert info.sees_pictures is False and not info.supports("top_k") and info.supports("temperature")
    unlisted = parse_model(models["svc-explabs:gpt-unlisted"])
    assert unlisted.prompt_price is None and unlisted.thinking == "unknown"  # not in the catalog: still callable


def test_the_catalog_is_read_page_by_page_and_its_failure_only_loses_the_details():
    gateway = FakeGateway(callable_=[SLUG, "other-650"], page=700)
    models = {m["id"]: m for m in run(gateway.client().list_models())}
    assert parse_model(models["svc-explabs:other-650"]).prompt_price is not None
    assert sum(1 for r in gateway.requests if r.url.path == "/api/models") == 2

    down = FakeGateway(catalog_ok=False)
    models = {m["id"]: m for m in run(down.client().list_models())}
    assert MODEL in models and parse_model(models[MODEL]).prompt_price is None


def test_a_wrong_key_fails_the_list_but_not_the_other_services():
    clients = Clients(FakeOpenRouter().client(), services=[FakeGateway().client(key="xpl_wrong")])
    models = run(clients.list_models())
    assert models and not any(service_of(m["id"]) == "svc-explabs" for m in models)
    assert "Experiential Labs" in clients.notes[0]


def test_requests_go_to_the_gateway_with_the_thinking_level_it_takes():
    gateway = FakeGateway()
    info = parse_model(next(m for m in run(gateway.client().list_models()) if m["id"] == MODEL))
    settings = AppSettings()
    body, dropped = build_body(MODEL, [], settings.writer_options, info, ProviderPrefs())
    sent = service_body(body)
    assert sent["model"] == SLUG and sent["reasoning_effort"] == "low" and sent["max_tokens"] == 32000
    assert sent["temperature"] == 0.8 and "provider" not in sent and dropped == []
    planned = service_body(build_body(MODEL, [], settings.planner_options, info, ProviderPrefs())[0])
    assert planned["reasoning_effort"] in ("low", "high")  # medium isn't offered: the nearest level

    gateway.chat.append(sse(chunk("The harbour clock stopped.", provider="experiential_cloud"),
                            chunk(finish="stop", usage=usage(cost=0.0012), provider="experiential_cloud")))
    clients = Clients(FakeOpenRouter().client(), services=[gateway.client()])
    result = run(clients.stream_chat({**body, "messages": [{"role": "user", "content": "Write."}]}))
    assert result.text == "The harbour clock stopped." and result.usage["cost"] == 0.0012
    assert result.service == "Experiential Labs" and result.provider == "experiential_cloud"
    chat = next(r for r in gateway.requests if r.url.path == "/v1/chat/completions")
    assert json.loads(chat.content)["model"] == SLUG and chat.headers["authorization"] == f"Bearer {KEY}"


def test_its_name_key_and_slug():
    assert service_name(MODEL, []) == "Experiential Labs"
    assert is_explabs_key(KEY) and not is_explabs_key("sk-or-v1-abc")
    assert get_explabs_key() is None and explabs_key_source() == "none"
    set_service_key("explabs", KEY)
    assert get_explabs_key() == KEY and explabs_key_source() == "stored"


def test_the_key_from_the_environment_wins(monkeypatch):
    monkeypatch.setenv("EXPLABS_API_KEY", "xpl_from_env_0123456789")
    set_service_key("explabs", KEY)
    assert get_explabs_key() == "xpl_from_env_0123456789" and explabs_key_source() == "environment"


def test_an_added_service_cant_take_its_slug(tmp_path):
    path = tmp_path / "settings.yaml"
    save_settings(AppSettings(), path)
    text = path.read_text(encoding="utf-8").replace(
        "services: []", "services:\n- name: Mine\n  slug: explabs\n  base_url: http://localhost:1234/v1")
    path.write_text(text, encoding="utf-8")
    assert load_settings(path).services == []


def test_out_of_credit_is_final_not_a_wait():
    patterns = load_refusal_patterns()
    used_up = StreamResult(service="Experiential Labs", error_kind="http", status_code=429,
                           error={"code": "insufficient_quota", "message": "insufficient_credits"})
    outcome = classify(used_up, 800, patterns)
    assert outcome.kind == FATAL and "Experiential Labs credit" in outcome.reason
    busy = StreamResult(service="Experiential Labs", error_kind="http", status_code=429, error={"message": "slow"})
    assert classify(busy, 800, patterns).kind == TRANSIENT


# --- starting with Windows --------------------------------------------------------------------------------

def test_it_starts_in_the_background():
    assert startup.command().endswith("--background")


def test_a_note_left_by_a_copy_that_stopped_doesnt_count():
    assert startup.running_url() is None  # no note
    startup.note_running(1, background=True)  # nothing answers on port 1
    assert startup.running_url(timeout=0.5) is None
    startup.forget_running()
    assert not startup.running_path().exists()


def test_a_running_copy_is_used_instead_of_starting_another(monkeypatch):
    from devils_workshop.ui import main
    opened = []
    monkeypatch.setattr(startup, "running_url", lambda: "http://127.0.0.1:5555")
    monkeypatch.setattr(main, "open_window", lambda url, browser=False: opened.append(url) or 0)
    assert main.main(["--background"]) == 0 and opened == []  # already running: nothing to do
    assert main.main([]) == 0 and opened == ["http://127.0.0.1:5555"]  # a window onto it
