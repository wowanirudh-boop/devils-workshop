"""Venice AI beside OpenRouter, and choosing OpenRouter's providers for each model."""

import asyncio
import json

import pytest

from devils_workshop.context.assemble import ChatMessage, ImageRef
from devils_workshop.llm.catalog import Catalog, load_catalog, parse_model
from devils_workshop.llm.clients import Clients
from devils_workshop.llm.costs import read_costs
from devils_workshop.llm.generate import build_body, generate, provider_body
from devils_workshop.llm.openrouter import parse_endpoint
from devils_workshop.llm.speed import measured_speeds
from devils_workshop.llm.venice import is_venice, to_openrouter_shape, venice_body
from devils_workshop.settings import (
    PLANNER,
    AppSettings,
    JobOptions,
    ModelSettings,
    ProviderChoice,
    ProviderPrefs,
    RoleProviders,
    get_venice_key,
    load_settings,
    save_settings,
    set_venice_key,
)

from tests.fakes import MODELS, PROSE, VENICE_MODELS, FakeOpenRouter, FakeVenice, http_error, reply, venice_reply

VENICE = "venice:mistral-31-24b"
CATALOG = Catalog([parse_model(m) for m in MODELS] + [parse_model(to_openrouter_shape(m)) for m in VENICE_MODELS])
MESSAGES = [ChatMessage("system", "You write."), ChatMessage("user", "Write chapter 1.")]


def run(coro):
    return asyncio.run(coro)


def send(clients, settings, tmp_path, primary, fallback=None, messages=MESSAGES):
    return run(generate(messages, client=clients, settings=settings, catalog=CATALOG, log_root=tmp_path / "logs",
                        label="t", target_words=100, primary=primary, fallback=fallback,
                        sleep=lambda s: asyncio.sleep(0)))


# --- Venice -------------------------------------------------------------------------------------------

def test_venice_models_join_the_catalog():
    venice = [parse_model(to_openrouter_shape(m)) for m in VENICE_MODELS]
    plain = next(m for m in venice if m.id == VENICE)
    assert plain.name == "Venice: Mistral 3.1 24B" and plain.context_length == 32768
    assert plain.max_completion_tokens == 8192 and plain.sees_pictures is False
    assert plain.prompt_price == pytest.approx(0.2e-6) and plain.completion_price == pytest.approx(0.9e-6)
    vision = next(m for m in venice if m.id == "venice:qwen3-vl-235b")
    assert vision.sees_pictures is True and vision.supports("reasoning") and not plain.supports("reasoning")


def test_the_model_list_has_both_and_venice_trouble_doesnt_stop_openrouter(tmp_path):
    fake_venice = FakeVenice()
    catalog, notes = run(load_catalog(Clients(FakeOpenRouter().client(), fake_venice.client()), tmp_path))
    assert {"z-ai/glm-5.3", VENICE, "venice:qwen3-vl-235b"} <= set(catalog.models)
    assert "venice:retired-model" not in catalog.models and notes == []  # offline models are left out
    assert fake_venice.requests[0]["path"].endswith("/models")

    class Broken(FakeVenice):
        def handler(self, request):
            raise ConnectionError("down")
    catalog, notes = run(load_catalog(Clients(FakeOpenRouter().client(), Broken().client()), tmp_path, refresh=True))
    assert "z-ai/glm-5.3" in catalog.models and VENICE not in catalog.models
    assert "Couldn't get Venice's model list" in notes[0]
    catalog, _ = run(load_catalog(Clients(FakeOpenRouter().client()), tmp_path, refresh=True))
    assert VENICE not in catalog.models  # no Venice key: no Venice models


def test_venice_request_body():
    options = JobOptions(temperature=0.8, max_output_tokens=4000, params={"logit_bias": {}})
    body, dropped = build_body(VENICE, MESSAGES, options, CATALOG.get(VENICE), ProviderPrefs(
        data_collection="deny"), ProviderChoice(mode="price"))
    assert "provider" not in body and dropped == ["logit_bias"]  # no OpenRouter providers for Venice models
    sent = venice_body(body)
    assert sent["model"] == "mistral-31-24b" and sent["max_completion_tokens"] == 4000
    assert "max_tokens" not in sent and sent["temperature"] == 0.8 and sent["stream"] is True
    assert sent["venice_parameters"] == {"include_venice_system_prompt": False, "enable_web_search": "off",
                                         "strip_thinking_response": True}  # thinking never lands in the text
    assert sent["stream_options"] == {"include_usage": True}
    effort = venice_body({"model": "venice:qwen3-vl-235b", "messages": [], "reasoning": {"effort": "low"}}, True)
    assert effort["reasoning_effort"] == "low" and "reasoning" not in effort
    assert effort["venice_parameters"]["include_venice_system_prompt"] is True


def test_writing_with_venice_streams_text_reasoning_and_cost(tmp_path):
    fake = FakeVenice().queue(venice_reply(PROSE, reasoning="Thinking about the harbour."))
    clients = Clients(FakeOpenRouter().client(), fake.client())
    picture = ImageRef("Mara.png", "image/png", b"\x89PNG....")
    messages = [MESSAGES[0], ChatMessage("user", "Describe her.", [picture])]
    result = send(clients, AppSettings(), tmp_path, VENICE, messages=messages)
    assert result.ok and result.text.startswith("The harbour clock") and result.provider == "Venice"
    assert result.total_cost == pytest.approx(0.0021)  # Venice's own figure
    request = fake.requests[-1]
    assert request["path"].endswith("/chat/completions") and request["headers"]["authorization"].startswith("Bearer ")
    assert "x-openrouter-title" not in request["headers"]
    assert request["body"]["model"] == "mistral-31-24b"
    assert request["body"]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    line = read_costs(tmp_path / "logs")[-1]
    assert line["provider"] == "Venice" and line["elapsed_s"] is not None


def test_venice_errors_name_venice(tmp_path):
    fake = FakeVenice().queue(__import__("httpx").Response(402, json={"error": "Insufficient balance",
                                                                       "code": "INSUFFICIENT_BALANCE"}))
    alone = AppSettings(fallback=None)
    result = send(Clients(FakeOpenRouter().client(), fake.client()), alone, tmp_path, VENICE)
    assert not result.ok and "Your Venice balance is too low (402)" in result.failure
    no_key = send(Clients(FakeOpenRouter().client()), alone, tmp_path, VENICE)
    assert "add it in Settings → Venice AI" in no_key.failure


def test_mixing_services_main_and_fallback(tmp_path):
    settings = AppSettings()
    openrouter, venice = FakeOpenRouter(), FakeVenice()
    openrouter.queue(http_error(402, "out of credits"))  # OpenRouter fails for good...
    venice.queue(venice_reply())                           # ...so the Venice fallback writes it
    result = send(Clients(openrouter.client(), venice.client()), settings, tmp_path, "z-ai/glm-5.3", VENICE)
    assert result.ok and result.used_fallback and result.model == VENICE

    openrouter, venice = FakeOpenRouter().queue(reply()), FakeVenice().queue(http_error(401, "bad key"))
    result = send(Clients(openrouter.client(), venice.client()), settings, tmp_path, VENICE, "z-ai/glm-5.3")
    assert result.ok and result.model == "z-ai/glm-5.3"  # and the other way round


def test_venice_key_is_kept_like_the_openrouter_key(monkeypatch, credential_store):
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    assert get_venice_key() is None
    set_venice_key("  venice-key-123  ")
    assert credential_store.passwords[("DevilsWorkshop", "venice")] == "venice-key-123"
    monkeypatch.setenv("VENICE_API_KEY", "from-env")
    assert get_venice_key() == "from-env"
    assert is_venice(VENICE) and not is_venice("z-ai/glm-5.3") and not is_venice(None)


# --- OpenRouter providers, per model --------------------------------------------------------------------

def test_provider_object_for_each_choice():
    privacy = ProviderPrefs(data_collection="deny", block=["badco"])
    assert provider_body(privacy, ProviderChoice()) == {"ignore": ["badco"], "data_collection": "deny"}
    assert provider_body(ProviderPrefs(), ProviderChoice(mode="price")) == {"sort": "price"}
    assert provider_body(ProviderPrefs(), ProviderChoice(mode="throughput", never=["slowco"])) == \
        {"sort": "throughput", "ignore": ["slowco"]}
    mine = ProviderChoice(mode="mine", prefer=["deepinfra/fp4", "novita/bf16"], never=["badco"],
                          allow_fallbacks=False)
    assert provider_body(privacy, mine) == {"ignore": ["badco"], "order": ["deepinfra/fp4", "novita/bf16"],
                                            "allow_fallbacks": False, "data_collection": "deny"}
    only = ProviderChoice(mode="mine", only=["z-ai/fp8"])
    assert provider_body(ProviderPrefs(allow=["other"]), only) == {"only": ["z-ai/fp8"]}  # the model's list wins
    assert provider_body(ProviderPrefs(), ProviderChoice(mode="mine")) is None  # nothing marked yet
    assert provider_body(ProviderPrefs(allow=["a"], order=["b"])) == {"only": ["a"], "order": ["b"]}  # old files


def test_each_models_choice_goes_with_its_requests(tmp_path):
    settings = AppSettings()
    settings.models["z-ai/glm-5.3"] = ModelSettings(providers=RoleProviders(
        writer=ProviderChoice(mode="mine", prefer=["deepinfra/fp4"]), planner=ProviderChoice(mode="price")))
    fake = FakeOpenRouter().queue(reply())
    send(Clients(fake.client()), settings, tmp_path, "z-ai/glm-5.3")
    assert fake.chat_bodies()[0]["provider"] == {"order": ["deepinfra/fp4"]}


def test_the_planner_sends_its_own_models_and_providers(tmp_path):
    """The same model can run on different providers for the writer and the planner."""
    settings = AppSettings(planner_primary="moonshotai/kimi-k3", planner_fallback="z-ai/glm-5.3")
    settings.models["moonshotai/kimi-k3"].providers.planner = ProviderChoice(mode="throughput")
    settings.models["z-ai/glm-5.3"].providers = RoleProviders(writer=ProviderChoice(mode="price"),
                                                              planner=ProviderChoice(mode="latency"))
    fake = FakeOpenRouter().queue(http_error(404, "No endpoints found"), reply())
    result = run(generate(MESSAGES, client=Clients(fake.client()), settings=settings, catalog=CATALOG,
                          log_root=tmp_path / "logs", label="t", target_words=100, role=PLANNER,
                          sleep=lambda s: asyncio.sleep(0)))
    assert result.ok and result.used_fallback
    first, second = fake.chat_bodies()
    assert first["model"] == "moonshotai/kimi-k3" and first["provider"] == {"sort": "throughput"}
    assert second["model"] == "z-ai/glm-5.3" and second["provider"] == {"sort": "latency"}  # not the writer's
    meta = json.loads((result.log_folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["role"] == PLANNER and meta["models"] == ["moonshotai/kimi-k3", "z-ai/glm-5.3"]


def test_provider_choices_are_saved(tmp_path):
    path = tmp_path / "settings.yaml"
    settings = AppSettings()
    settings.models["z-ai/glm-5.3"].providers.writer = ProviderChoice(mode="mine", prefer=["a/fp8", "b"], only=[],
                                                                      never=["c"], allow_fallbacks=False)
    settings.models["z-ai/glm-5.3"].providers.planner = ProviderChoice(mode="price", never=["d"])
    save_settings(settings, path)
    again = load_settings(path)
    assert again.model("z-ai/glm-5.3").providers == settings.models["z-ai/glm-5.3"].providers
    assert again.model("moonshotai/kimi-k3").providers == RoleProviders()
    path.write_text("models:\n  x/y:\n    providers: {mode: nonsense}\n", encoding="utf-8")
    assert load_settings(path).model("x/y").providers.writer.mode == "auto"


def test_settings_from_before_the_planner_had_its_own_models(tmp_path):
    """Older files had one main and fallback, and one provider choice per model: the planner keeps using them."""
    path = tmp_path / "settings.yaml"
    path.write_text("primary: a/main\nfallback: b/backup\nmodels:\n  a/main:\n"
                    "    providers: {mode: mine, prefer: [x/fp8], allow_fallbacks: false}\n", encoding="utf-8")
    old = load_settings(path)
    assert old.models_of(PLANNER) == ("a/main", "b/backup") == old.models_of("writer")
    mine = ProviderChoice(mode="mine", prefer=["x/fp8"], allow_fallbacks=False)
    assert old.model("a/main").providers == RoleProviders(writer=mine, planner=mine)
    old.planner_primary, old.planner_fallback = "c/planner", None
    save_settings(old, path)
    again = load_settings(path)
    assert again.models_of(PLANNER) == ("c/planner", None) and again.models_of("writer") == ("a/main", "b/backup")


def test_endpoints_and_measured_speed(tmp_path):
    endpoint = parse_endpoint({"provider_name": "DeepInfra", "tag": "deepinfra/fp4", "quantization": "fp4",
                               "pricing": {"prompt": "0.0000004", "completion": "0.0000016"},
                               "context_length": 202752, "max_completion_tokens": 131072, "uptime_last_30m": 99.5,
                               "throughput_last_30m": {"p50": 61.2}, "latency_last_30m": {"p50": 850}, "status": 0})
    assert (endpoint.slug, endpoint.provider, endpoint.throughput, endpoint.latency) == \
        ("deepinfra/fp4", "DeepInfra", 61.2, 0.85)
    assert parse_endpoint({"provider_name": "Novita", "tag": "novita/bf16", "throughput_last_30m": None}).throughput \
        is None

    logs = tmp_path / "logs"
    logs.mkdir()
    lines = [{"model": "z-ai/glm-5.3", "provider": "DeepInfra", "outcome": "ok", "completion_tokens": 3000,
              "elapsed_s": 62.0, "first_text_s": 2.0},
             {"model": "z-ai/glm-5.3", "provider": "DeepInfra", "outcome": "ok", "completion_tokens": 2000,
              "elapsed_s": 42.0, "first_text_s": 2.0},
             {"model": "z-ai/glm-5.3", "provider": "DeepInfra", "outcome": "refusal", "completion_tokens": 900,
              "elapsed_s": 1.0, "first_text_s": 0.5},
             {"model": "z-ai/glm-5.3", "provider": "Novita", "outcome": "ok", "completion_tokens": 10,
              "elapsed_s": 5.0, "first_text_s": 1.0}]
    (logs / "costs.jsonl").write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    speeds = measured_speeds([logs])
    deepinfra = speeds[("z-ai/glm-5.3", "DeepInfra")]
    assert deepinfra.tokens_per_s == pytest.approx(50.0) and deepinfra.first_text_s == 2.0 and deepinfra.requests == 2
    assert ("z-ai/glm-5.3", "Novita") not in speeds  # too short to say


def test_activity_names_the_planners_requests():
    from devils_workshop.ui.activity import describe, role_of
    assert describe({"job": "plan-brainstorm", "label": "plan-brainstorm"}) == "Planner · Story plan"
    assert describe({"job": "plan-scenes", "label": "plan-scenes-ch03"}) == "Planner · Plan the scenes · chapter 3"
    assert describe({"job": "plan-scene", "label": "plan-scene-ch03-s2"}) == \
        "Planner · Scene brief · chapter 3, scene 2"
    assert role_of("plan-architecture") == "Planner" and role_of("write") == role_of(None) == "Writer"


# --- thinking: levels, the off switch, and thinking written into the reply --------------------------------

def test_what_each_venice_model_does_with_thinking():
    from devils_workshop.llm.catalog import parse_model

    def shaped(capabilities):
        return parse_model(to_openrouter_shape({"id": "m", "model_spec": {"capabilities": capabilities}}))

    levels = shaped({"supportsReasoning": True, "supportsReasoningEffort": True,
                     "reasoningEffortOptions": ["none", "low", "high"]})
    assert levels.thinking == "levels" and levels.effort_levels == ["none", "low", "high"]
    assert shaped({"supportsReasoning": True}).thinking == "switch"  # e.g. a GLM-based model without levels
    assert shaped({}).thinking == "none"


def test_a_level_it_doesnt_offer_is_the_nearest_one_and_off_switches_thinking_off():
    from devils_workshop.llm.catalog import ModelInfo
    from devils_workshop.llm.generate import closest_effort
    assert closest_effort("medium", ["none", "low", "high"]) == "high"  # ties go to the deeper one
    assert closest_effort("medium", ["none", "low", "high", "max"]) == "high"  # DeepSeek on Experiential Labs
    assert closest_effort("low", ["none", "high"]) == "high"  # thinking asked for: never "none"
    assert closest_effort("none", ["none", "high"]) == "none"
    assert closest_effort("xhigh", ["low", "high"]) == "high" and closest_effort("low", []) == "low"

    switch = ModelInfo("venice:glm-4.7", supported_parameters=["temperature"], thinks=True)
    off = JobOptions(effort="none")
    body, dropped = build_body(switch.id, MESSAGES, off, switch, ProviderPrefs())
    assert body["reasoning"] == {"enabled": False} and dropped == []
    sent = venice_body(body)
    assert sent["venice_parameters"]["disable_thinking"] is True and "reasoning_effort" not in sent
    for level in ("low", "medium"):
        body, dropped = build_body(switch.id, MESSAGES, JobOptions(effort=level), switch, ProviderPrefs())
        assert "reasoning" not in body and dropped == []  # it thinks as it always does: thinking stays on
    assert "disable_thinking" not in venice_body(body)["venice_parameters"]

    levels = ModelInfo("venice:qwen", supported_parameters=["reasoning"], thinks=True, effort_levels=["low", "high"])
    body = build_body(levels.id, MESSAGES, JobOptions(effort="medium"), levels, ProviderPrefs())[0]
    assert venice_body(body)["reasoning_effort"] == "high"  # medium between low and high: the deeper one
    none = ModelInfo("venice:plain", supported_parameters=["temperature"], thinks=False)
    assert build_body(none.id, MESSAGES, JobOptions(effort="low"), none, ProviderPrefs())[1] == ["reasoning"]


def test_thinking_written_into_the_reply_is_moved_out_of_it():
    from devils_workshop.llm.openrouter import ThinkFilter, split_unopened_thinking
    pieces = ["<thi", "nk>Plan: she ", "hesitates.</th", "ink>The door ", "opened. <", "b>"]
    thinking = ThinkFilter()
    text, thought = "", ""
    for piece in pieces:
        t, r = thinking.feed(piece)
        text, thought = text + t, thought + r
    t, r = thinking.flush()
    assert text + t == "The door opened. <b>" and thought + r == "Plan: she hesitates."
    assert split_unopened_thinking("Plan first.</think>\n\nThe door opened.") == ("The door opened.", "Plan first.")
    assert split_unopened_thinking("No tags here.") == ("No tags here.", "")


def test_a_reply_with_thinking_inside_is_saved_without_it(tmp_path):
    fake = FakeOpenRouter().queue(reply("<think>I should open on the harbour.</think>" + PROSE))
    result = send(Clients(fake.client()), AppSettings(), tmp_path, "z-ai/glm-5.3")
    assert result.ok and result.text == PROSE.strip() and "<think>" not in result.text
    saved = next((tmp_path / "logs").rglob("attempt-1.reasoning.md")).read_text(encoding="utf-8")
    assert "open on the harbour" in saved
