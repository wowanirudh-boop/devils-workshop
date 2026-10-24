"""Other OpenAI-compatible services (Featherless, NanoGPT, LM Studio, Ollama...) beside OpenRouter and Venice."""

import asyncio

import httpx
import pytest

from devils_workshop.context.assemble import ChatMessage, ImageRef
from devils_workshop.llm.catalog import Catalog, load_catalog, parse_model
from devils_workshop.llm.clients import Clients
from devils_workshop.llm.costs import read_costs
from devils_workshop.llm.generate import build_body, generate
from devils_workshop.llm.pictures import picture_limit
from devils_workshop.llm.services import (
    bare_model,
    is_service_model,
    service_body,
    service_name,
    service_of,
    service_slug,
    slugify,
    to_openrouter_shape,
)
from devils_workshop.llm.venice import to_openrouter_shape as venice_shape
from devils_workshop.planner.run import FINISHED, PlanRun
from devils_workshop.settings import (
    AppSettings,
    JobOptions,
    ProviderChoice,
    ProviderPrefs,
    ServiceSettings,
    delete_service_key,
    get_service_key,
    load_settings,
    save_settings,
    set_service_key,
)

from tests.fakes import (
    MODELS,
    PROSE,
    SERVICE_MODELS,
    VENICE_MODELS,
    FakeOpenRouter,
    FakeService,
    FakeVenice,
    http_error,
    reply,
    service_reply,
)
from tests.unit.test_planner import approved, planner_answers, story  # noqa: F401

LLAMA = "svc-featherless:meta-llama/Llama-3.3-70B-Instruct"
VISION = "svc-featherless:qwen2.5-vl-72b"
PRICED = "svc-featherless:nano/priced"
OLLAMA = "svc-ollama:llama3:8b"
FEATHERLESS = ServiceSettings("Featherless", "featherless", "https://api.featherless.ai/v1")
CATALOG = Catalog([parse_model(m) for m in MODELS]
                  + [parse_model(venice_shape(m)) for m in VENICE_MODELS]
                  + [parse_model(to_openrouter_shape(m, "Featherless", "svc-featherless:")) for m in SERVICE_MODELS])
MESSAGES = [ChatMessage("system", "You write."), ChatMessage("user", "Write chapter 1.")]


def run(coro):
    return asyncio.run(coro)


def send(clients, settings, tmp_path, primary, fallback=None, messages=MESSAGES, catalog=CATALOG):
    return run(generate(messages, client=clients, settings=settings, catalog=catalog, log_root=tmp_path / "logs",
                        label="t", target_words=100, primary=primary, fallback=fallback,
                        sleep=lambda s: asyncio.sleep(0)))


def test_model_names_keep_each_service_apart():
    assert slugify("Nano GPT") == "nano-gpt" and slugify("  LM Studio (this PC)! ") == "lm-studio-this-pc"
    assert slugify("!!!") == ""
    assert is_service_model(OLLAMA) and service_slug(OLLAMA) == "ollama" and bare_model(OLLAMA) == "llama3:8b"
    assert not is_service_model("z-ai/glm-5.3") and not is_service_model("venice:x") and not is_service_model(None)
    assert [service_of(m) for m in ("z-ai/glm-5.3", "venice:x", LLAMA, OLLAMA)] == \
        ["openrouter", "venice", "svc-featherless", "svc-ollama"]
    assert service_name(LLAMA, [FEATHERLESS]) == "Featherless" and service_name(OLLAMA) == "ollama"
    assert service_name("venice:x") == "Venice" and service_name("z-ai/glm-5.3") == "OpenRouter"


def test_service_models_in_openrouters_shape():
    llama = CATALOG.get(LLAMA)
    assert llama.name == "Featherless: meta-llama/Llama-3.3-70B-Instruct" and llama.context_length == 32768
    assert llama.max_completion_tokens == 4096 and llama.prompt_price is None and llama.completion_price is None
    assert llama.sees_pictures is None  # not reported: unknown
    assert llama.supported_parameters == [] and llama.max_images is None
    vision = CATALOG.get(VISION)
    assert vision.sees_pictures is True and vision.max_images == 2 and picture_limit(VISION, vision) == 2
    priced = CATALOG.get(PRICED)
    assert priced.name == "Featherless: Nano Priced" and priced.context_length == 65536
    assert priced.prompt_price == pytest.approx(0.5e-6) and priced.completion_price == pytest.approx(1.5e-6)
    per_token = to_openrouter_shape({"id": "x", "pricing": {"prompt": "0.000002", "completion": "0.000004"}}, "S", "svc-s:")
    assert parse_model(per_token).prompt_price == pytest.approx(2e-6)  # OpenRouter's own form: per token
    euros = to_openrouter_shape({"id": "x", "pricing": {"prompt": 1, "currency": "EUR"}}, "S", "svc-s:")
    assert parse_model(euros).prompt_price is None
    bare = parse_model(to_openrouter_shape({"id": "llama3:8b"}, "Ollama", "svc-ollama:"))
    assert bare.id == OLLAMA and bare.context_length is None and bare.sees_pictures is None


def test_the_model_list_has_every_service_and_one_failing_doesnt_stop_the_others(tmp_path):
    featherless, ollama = FakeService(), FakeService(models=[{"id": "llama3:8b"}])
    clients = Clients(FakeOpenRouter().client(), FakeVenice().client(),
                      [featherless.client(), ollama.client("Ollama", "ollama", key=None)])
    catalog, notes = run(load_catalog(clients, tmp_path))
    assert {"z-ai/glm-5.3", "venice:mistral-31-24b", LLAMA, PRICED, OLLAMA} <= set(catalog.models)
    assert notes == [] and featherless.requests[0]["path"] == "/v1/models"
    assert featherless.requests[0]["headers"]["authorization"] == "Bearer fl-test-key-0123456789abcdef"
    assert "authorization" not in ollama.requests[0]["headers"]  # a local server without a key

    class Down(FakeService):
        def handler(self, request):
            raise httpx.ConnectError("not running")
    clients = Clients(FakeOpenRouter().client(), None, [Down().client("LM Studio", "lm-studio"), featherless.client()])
    catalog, notes = run(load_catalog(clients, tmp_path, refresh=True))
    assert LLAMA in catalog.models and "z-ai/glm-5.3" in catalog.models
    assert notes == ["Couldn't get LM Studio's model list (ConnectError); LM Studio models may be missing until it "
                     "loads."]


def test_service_request_body():
    options = JobOptions(temperature=0.8, effort="low", max_output_tokens=8000)
    body, dropped = build_body(LLAMA, MESSAGES, options, CATALOG.get(LLAMA), ProviderPrefs(data_collection="deny"),
                               ProviderChoice(mode="price"))
    assert "provider" not in body and dropped == []  # no OpenRouter providers; nothing known to drop
    assert body["max_tokens"] == 4096  # the service's own limit for the model
    sent = service_body(body)
    assert sent["model"] == "meta-llama/Llama-3.3-70B-Instruct" and sent["max_tokens"] == 4096
    assert sent["temperature"] == 0.8 and sent["reasoning_effort"] == "low" and "reasoning" not in sent
    assert sent["stream"] is True and sent["stream_options"] == {"include_usage": True}
    assert "stream_options" not in service_body(body, usage_in_stream=False)
    assert service_body({"model": OLLAMA, "messages": []})["model"] == "llama3:8b"


def test_writing_with_a_service(tmp_path):
    fake = FakeService().queue(service_reply(PROSE))
    clients = Clients(FakeOpenRouter().client(), None, [fake.client()])
    result = send(clients, AppSettings(services=[FEATHERLESS]), tmp_path, LLAMA)
    assert result.ok and result.text.startswith("The harbour clock") and result.provider == "Featherless"
    request = fake.requests[-1]
    assert request["path"] == "/v1/chat/completions" and "x-openrouter-title" not in request["headers"]
    assert request["body"]["model"] == "meta-llama/Llama-3.3-70B-Instruct"
    line = read_costs(tmp_path / "logs")[-1]
    assert line["provider"] == "Featherless" and line["cost"] == 0 and line["cost_estimated"] is True  # price unknown
    assert line["prompt_tokens"] == 1000  # usage from the end of the stream

    fake = FakeService().queue(service_reply(PROSE, model="nano/priced"))
    result = send(Clients(FakeOpenRouter().client(), None, [fake.client()]), AppSettings(), tmp_path, PRICED)
    assert result.ok and result.total_cost == pytest.approx(1000 * 0.5e-6 + 200 * 1.5e-6)  # from its prices


def test_a_server_that_rejects_stream_options_is_asked_again_without(tmp_path):
    fake = FakeService().queue(http_error(400, "Unrecognized request argument supplied: stream_options"),
                               service_reply(), service_reply())
    client = fake.client()
    clients = Clients(FakeOpenRouter().client(), None, [client])
    assert send(clients, AppSettings(fallback=None), tmp_path, LLAMA).ok
    assert send(clients, AppSettings(fallback=None), tmp_path, LLAMA).ok
    first, again, later = fake.chat_bodies()
    assert "stream_options" in first and "stream_options" not in again and "stream_options" not in later


def test_each_service_is_its_own_service_for_fallback(tmp_path):
    settings = AppSettings(services=[FEATHERLESS, ServiceSettings("Ollama", "ollama", "http://localhost:11434/v1")])
    featherless, ollama = FakeService(), FakeService(models=[{"id": "llama3:8b"}])
    featherless.queue(http_error(401, "invalid key"))  # one service fails for good...
    ollama.queue(service_reply(model="llama3:8b"))   # ...so the fallback on another one writes it
    clients = Clients(FakeOpenRouter().client(), None,
                      [featherless.client(), ollama.client("Ollama", "ollama", key=None)])
    result = send(clients, settings, tmp_path, LLAMA, OLLAMA)
    assert result.ok and result.used_fallback and result.model == OLLAMA
    assert "Featherless didn't accept the API key (401). Check it in Settings → Other services." in \
        result.attempts[0].reason

    openrouter, featherless = FakeOpenRouter().queue(reply()), FakeService().queue(http_error(402, "no balance"))
    result = send(Clients(openrouter.client(), None, [featherless.client()]), settings, tmp_path, LLAMA, "z-ai/glm-5.3")
    assert result.ok and result.model == "z-ai/glm-5.3"  # a service, then OpenRouter
    assert "Your Featherless balance is too low" in result.attempts[0].reason

    openrouter, featherless = FakeOpenRouter().queue(http_error(402, "out of credits")), FakeService().queue(service_reply())
    result = send(Clients(openrouter.client(), None, [featherless.client()]), settings, tmp_path, "z-ai/glm-5.3", LLAMA)
    assert result.ok and result.model == LLAMA  # OpenRouter, then a service

    venice, featherless = FakeVenice().queue(http_error(401, "bad")), FakeService().queue(service_reply())
    result = send(Clients(FakeOpenRouter().client(), venice.client(), [featherless.client()]), settings, tmp_path,
                  "venice:mistral-31-24b", LLAMA)
    assert result.ok and result.model == LLAMA  # Venice, then a service


def test_the_same_service_twice_stops_at_a_key_problem(tmp_path):
    fake = FakeService().queue(http_error(401, "invalid key"))
    result = send(Clients(FakeOpenRouter().client(), None, [fake.client()]), AppSettings(services=[FEATHERLESS]),
                  tmp_path, LLAMA, PRICED)
    assert not result.ok and len(fake.chat_bodies()) == 1  # the fallback is on the same service: no use trying
    assert "Featherless didn't accept the API key" in result.failure


def test_a_removed_service_falls_back(tmp_path):
    openrouter = FakeOpenRouter().queue(reply())
    result = send(Clients(openrouter.client()), AppSettings(), tmp_path, LLAMA, "z-ai/glm-5.3")
    assert result.ok and result.model == "z-ai/glm-5.3"
    assert "isn't in Settings → Other services any more" in result.attempts[0].reason
    warnings = send(Clients(FakeOpenRouter().queue(reply()).client()), AppSettings(services=[FEATHERLESS]), tmp_path,
                    "svc-featherless:gone", "z-ai/glm-5.3").warnings
    assert "Model 'svc-featherless:gone' isn't in Featherless's model list" in warnings


def test_pictures_within_the_services_own_limit(tmp_path):
    fake = FakeService().queue(service_reply(model="qwen2.5-vl-72b"))
    pictures = [ImageRef(f"{n}.png", "image/png", b"\x89PNG" + n.encode()) for n in ("a", "b", "c")]
    messages = [MESSAGES[0], ChatMessage("user", "Look.", pictures)]
    assert send(Clients(FakeOpenRouter().client(), None, [fake.client()]), AppSettings(), tmp_path, VISION,
                messages=messages).ok
    parts = fake.chat_bodies()[0]["messages"][1]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url", "image_url"]  # the list says at most 2
    assert "not sent: this model takes at most 2 per request" in parts[0]["text"]

    fake = FakeService().queue(http_error(400, "Maximum of 1 images per request"), service_reply())
    assert send(Clients(FakeOpenRouter().client(), None, [fake.client()]), AppSettings(fallback=None), tmp_path, LLAMA,
                messages=messages).ok
    first, again = fake.chat_bodies()
    assert len(first["messages"][1]["content"]) == 4 and len(again["messages"][1]["content"]) == 2  # learned: 1


def test_services_are_saved_and_their_keys_kept_in_the_credential_store(tmp_path, credential_store):
    path = tmp_path / "settings.yaml"
    settings = AppSettings(services=[FEATHERLESS, ServiceSettings("LM Studio", "lm-studio", "http://localhost:1234/v1")])
    save_settings(settings, path)
    assert "fl-" not in path.read_text(encoding="utf-8")
    assert load_settings(path).services == settings.services
    path.write_text("services:\n- {name: A, slug: a, base_url: 'http://x'}\n- {name: Twice, slug: a, base_url: 'http://y'}"
                    "\n- {name: No address, slug: b}\n- nonsense\n", encoding="utf-8")
    assert load_settings(path).services == [ServiceSettings("A", "a", "http://x")]
    assert load_settings(tmp_path / "new.yaml").services == []

    assert get_service_key("featherless") is None
    set_service_key("featherless", "  fl-key  ")
    assert credential_store.passwords[("DevilsWorkshop", "service:featherless")] == "fl-key"
    assert get_service_key("featherless") == "fl-key"
    delete_service_key("featherless")
    delete_service_key("featherless")  # nothing stored: no error
    assert get_service_key("featherless") is None


def test_planning_a_story_with_a_service_model(story):
    state = approved(story)
    state.model = LLAMA
    state.save()
    fake = FakeService().respond(planner_answers)
    plan_run = PlanRun(story, AppSettings(services=[FEATHERLESS]), CATALOG,
                       lambda: Clients(FakeOpenRouter().client(), None, [fake.client()]),
                       sleep=lambda s: asyncio.sleep(0))
    run(plan_run.run())
    assert plan_run.status == FINISHED, plan_run.reason
    bodies = fake.chat_bodies()
    assert len(bodies) == 9 and all(b["model"] == "meta-llama/Llama-3.3-70B-Instruct" for b in bodies)
