"""Other services: any chat service that speaks OpenAI's chat-completions format, beside OpenRouter and Venice.

For example Featherless (https://api.featherless.ai/v1), NanoGPT (https://nano-gpt.com/api/v1), or a
model running on your own PC in LM Studio (http://localhost:1234/v1) or Ollama (http://localhost:11434/v1).
The author adds each one in Settings → Other services with a name, its address and (if it needs one) a key.

Like Venice's client, this reuses the OpenRouter client's streaming and error handling and changes only
what differs:
- model names: the app calls them "svc-<service>:<id>", so each service's models stay apart;
- the request: a plain chat-completions body (no OpenRouter provider preferences), max_tokens as most
  of these services take it, reasoning_effort, and usage asked for at the end of the stream;
- the model list: the service's GET /models, turned into OpenRouter's shape for the catalog. Most services
  report no prices (a model on your own PC costs nothing), so the price is unknown unless one is given.
"""

from __future__ import annotations

import re

import httpx

from devils_workshop.llm.openrouter import TIMEOUTS, TOTAL_LIMIT_S, OpenRouterClient, StreamResult
from devils_workshop.llm.venice import is_venice

PREFIX = "svc-"
OPENROUTER, VENICE = "openrouter", "venice"
EXPLABS_NAME, EXPLABS_SLUG = "Experiential Labs", "explabs"  # built in: Settings → Experiential Labs
BUILT_IN = {EXPLABS_SLUG: EXPLABS_NAME}  # slugs an added service can't take
_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """'Nano GPT' -> 'nano-gpt': the service's part of its model names."""
    return _SLUG.sub("-", (name or "").lower()).strip("-")[:32]


def model_prefix(slug: str) -> str:
    return f"{PREFIX}{slug}:"


def is_service_model(model_id: str | None) -> bool:
    return bool(model_id) and model_id.startswith(PREFIX) and ":" in model_id


def service_slug(model_id: str | None) -> str | None:
    """'svc-featherless:meta-llama/Llama-3.3-70B' -> 'featherless'."""
    return model_id[len(PREFIX):].split(":", 1)[0] if is_service_model(model_id) else None


def bare_model(model_id: str) -> str:
    """The name the service itself uses: 'svc-ollama:llama3:8b' -> 'llama3:8b'."""
    return model_id.split(":", 1)[1] if is_service_model(model_id) else model_id


def service_of(model_id: str | None) -> str:
    """Which service a model belongs to: "openrouter", "venice", or "svc-<slug>" for an added service."""
    if is_venice(model_id):
        return VENICE
    slug = service_slug(model_id)
    return f"{PREFIX}{slug}" if slug else OPENROUTER


def service_name(model_id: str | None, services=()) -> str:
    """The service's name as the author gave it ("OpenRouter", "Venice", "Featherless"...)."""
    if is_venice(model_id):
        return "Venice"
    slug = service_slug(model_id)
    if slug is None:
        return "OpenRouter"
    if slug in BUILT_IN:
        return BUILT_IN[slug]
    return next((s.name for s in services if s.slug == slug), slug)


class ServiceClient(OpenRouterClient):
    """One added OpenAI-compatible service."""

    def __init__(self, name: str, slug: str, base_url: str, api_key: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None,
                 timeouts: httpx.Timeout = TIMEOUTS, total_limit_s: float = TOTAL_LIMIT_S):
        super().__init__(api_key or None, base_url=base_url, transport=transport, timeouts=timeouts,
                         total_limit_s=total_limit_s)
        self._headers = {}  # no OpenRouter headers
        self.service = name
        self.slug = slug
        self.prefix = model_prefix(slug)
        self.usage_in_stream = True  # off once the service rejects stream_options

    async def list_models(self) -> list[dict]:
        """The service's models, in OpenRouter's shape (ids prefixed "svc-<slug>:")."""
        async with self._client() as client:
            response = await client.get("/models")
            response.raise_for_status()
            data = response.json()
            items = data.get("data") if isinstance(data, dict) else data
            return [to_openrouter_shape(m, self.service, self.prefix) for m in items or []
                    if isinstance(m, dict) and m.get("id")]

    async def list_endpoints(self, model_id: str) -> list[dict]:
        return []  # the service runs its models itself: there are no providers to choose

    async def stream_chat(self, body: dict, **options) -> StreamResult:
        result = await super().stream_chat(body, **options)
        message = str((result.error or {}).get("message", ""))
        if result.status_code in (400, 422) and self.usage_in_stream and "stream_options" in message:
            self.usage_in_stream = False  # an older server: send again without asking for usage
            result = await super().stream_chat(body, **options)
        result.provider = result.provider or self.service
        return result

    def _payload(self, body: dict) -> dict:
        return service_body(body, self.usage_in_stream)


def service_body(body: dict, usage_in_stream: bool = True) -> dict:
    """An OpenRouter-style request body, as a plain OpenAI chat-completions request."""
    out = {k: v for k, v in body.items() if k not in ("provider", "reasoning", "model")}
    out["model"] = bare_model(body.get("model") or "")
    effort = (body.get("reasoning") or {}).get("effort")
    if effort:
        out["reasoning_effort"] = effort
    out["stream"] = True
    if usage_in_stream:
        out["stream_options"] = {"include_usage": True}
    return out


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _whole(*values) -> int | None:
    for value in values:
        number = _number(value)
        if number is not None and number > 0:
            return int(number)
    return None


def _prices(raw: dict) -> tuple[str | None, str | None]:
    """USD per token, if the service reports prices: OpenRouter's form ({"prompt": "0.000001"}, per token),
    or per million tokens when the list says so (NanoGPT: {"prompt": 0.2, "unit": "per_million_tokens"})."""
    pricing = raw.get("pricing")
    if not isinstance(pricing, dict):
        return None, None
    per_million = "million" in str(pricing.get("unit", "")).lower()
    if str(pricing.get("currency", "USD")).upper() != "USD":
        return None, None

    def per_token(*keys):
        for key in keys:
            value = _number(pricing.get(key))
            if value is not None:
                return str(value / 1_000_000 if per_million else value)
        return None
    return per_token("prompt", "input"), per_token("completion", "output")


def to_openrouter_shape(raw: dict, service: str, prefix: str) -> dict:
    """One model from a service's list, as OpenRouter would describe it (see catalog.parse_model).
    Services report different things (or nothing beyond the id); what isn't reported stays unknown."""
    top = raw.get("top_provider") if isinstance(raw.get("top_provider"), dict) else {}
    capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
    architecture = raw.get("architecture") if isinstance(raw.get("architecture"), dict) else {}
    modalities = architecture.get("input_modalities") or raw.get("input_modalities")
    if not modalities:
        vision = capabilities.get("vision", raw.get("supports_vision", raw.get("vision")))
        modalities = (["text", "image"] if vision else ["text"]) if isinstance(vision, bool) else []
    max_images = next((v for v in (raw.get("max_images"), capabilities.get("max_images"))
                       if isinstance(v, int) and not isinstance(v, bool)), None)
    prompt, completion = _prices(raw)
    name = raw.get("name") or raw.get("display_name") or raw["id"]
    shaped = {
        "id": prefix + str(raw["id"]),
        "name": f"{service}: {name}",
        "context_length": _whole(raw.get("context_length"), raw.get("context_window"),
                                 raw.get("max_context_length"), raw.get("max_model_len"),
                                 top.get("context_length")),
        "top_provider": {"max_completion_tokens": _whole(raw.get("max_completion_tokens"),
                                                         raw.get("max_output_tokens"),
                                                         top.get("max_completion_tokens"))},
        "pricing": {"prompt": prompt, "completion": completion},
        "supported_parameters": [str(p) for p in raw.get("supported_parameters") or []],
        "architecture": {"input_modalities": [str(m) for m in modalities]},
    }
    if max_images is not None:
        shaped["max_images"] = max_images
    return shaped
