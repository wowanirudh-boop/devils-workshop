"""The Venice AI client: Venice's text models, beside OpenRouter's.

Venice's API is OpenAI-compatible, so this reuses the OpenRouter client's streaming and error
handling and only changes what differs:
- model names: the app calls them "venice:<id>", so they can't be mixed up with OpenRouter's;
- the request: max_completion_tokens, reasoning_effort, no OpenRouter provider preferences, and
  venice_parameters, with Venice's own system prompt switched off unless you turn it on, thinking switched
  off when asked (disable_thinking), and <think> blocks always kept out of the reply (strip_thinking_response);
- the model list: Venice's /models, turned into OpenRouter's shape so the catalog reads it as it is.
"""

from __future__ import annotations

import httpx

from devils_workshop.llm.openrouter import TIMEOUTS, TOTAL_LIMIT_S, OpenRouterClient

VENICE_URL = "https://api.venice.ai/api/v1"
PREFIX = "venice:"
VENICE_MAX_IMAGES = 1  # "At most 1 image(s) may be provided in one prompt" (see llm/pictures.py)
SAMPLING = ["temperature", "top_p", "top_k", "min_p", "frequency_penalty", "presence_penalty",
            "repetition_penalty", "seed", "stop", "max_tokens"]


def is_venice(model_id: str | None) -> bool:
    return bool(model_id) and model_id.startswith(PREFIX)


class VeniceClient(OpenRouterClient):
    service = "Venice"

    def __init__(self, api_key: str | None, base_url: str = VENICE_URL,
                 transport: httpx.AsyncBaseTransport | None = None, include_venice_system_prompt: bool = False,
                 timeouts: httpx.Timeout = TIMEOUTS, total_limit_s: float = TOTAL_LIMIT_S):
        super().__init__(api_key, base_url=base_url, transport=transport, timeouts=timeouts,
                         total_limit_s=total_limit_s)
        self._headers = {}  # no OpenRouter headers
        self.include_venice_system_prompt = include_venice_system_prompt

    async def list_models(self) -> list[dict]:
        """Venice's text models, in OpenRouter's shape (ids prefixed "venice:")."""
        async with self._client() as client:
            response = await client.get("/models", params={"type": "text"})
            response.raise_for_status()
            return [to_openrouter_shape(m) for m in response.json().get("data") or []
                    if isinstance(m, dict) and not (m.get("model_spec") or {}).get("offline")]

    async def list_endpoints(self, model_id: str) -> list[dict]:
        return []  # Venice runs its models itself: there are no providers to choose

    async def stream_chat(self, body: dict, **options):
        result = await super().stream_chat(body, **options)
        result.provider = result.provider or "Venice"
        return result

    def _payload(self, body: dict) -> dict:
        return venice_body(body, self.include_venice_system_prompt)


def venice_body(body: dict, include_venice_system_prompt: bool = False) -> dict:
    """An OpenRouter-style request body, in Venice's form."""
    out = {k: v for k, v in body.items() if k not in ("provider", "reasoning", "max_tokens", "model")}
    out["model"] = body["model"][len(PREFIX):] if is_venice(body.get("model")) else body.get("model")
    if body.get("max_tokens"):
        out["max_completion_tokens"] = body["max_tokens"]
    reasoning = body.get("reasoning") or {}
    effort = reasoning.get("effort")
    if effort:
        out["reasoning_effort"] = effort
    out["venice_parameters"] = {"include_venice_system_prompt": bool(include_venice_system_prompt),
                                "enable_web_search": "off", "strip_thinking_response": True}
    if effort == "none" or reasoning.get("enabled") is False:
        out["venice_parameters"]["disable_thinking"] = True
    out["stream"] = True
    out["stream_options"] = {"include_usage": True}
    return out


def to_openrouter_shape(raw: dict) -> dict:
    """One model from Venice's list, as OpenRouter would describe it (see catalog.parse_model)."""
    spec = raw.get("model_spec") or {}
    pricing = spec.get("pricing") or {}
    capabilities = spec.get("capabilities") or {}

    def per_token(kind: str):
        usd = (pricing.get(kind) or {}).get("usd")
        try:
            return str(float(usd) / 1_000_000)
        except (TypeError, ValueError):
            return None

    parameters = list(SAMPLING)
    if capabilities.get("supportsReasoningEffort"):
        parameters.append("reasoning")
    levels = capabilities.get("reasoningEffortOptions") if capabilities.get("supportsReasoningEffort") else None
    return {
        "id": PREFIX + str(raw.get("id", "")),
        "name": f"Venice: {spec.get('name') or raw.get('id', '')}",
        "context_length": spec.get("availableContextTokens") or raw.get("context_length"),
        "top_provider": {"max_completion_tokens": spec.get("maxCompletionTokens")},
        "pricing": {"prompt": per_token("input"), "completion": per_token("output")},
        "supported_parameters": parameters,
        "architecture": {"input_modalities": ["text", "image"] if capabilities.get("supportsVision") else ["text"]},
        "max_images": VENICE_MAX_IMAGES,
        "thinks": bool(capabilities.get("supportsReasoning") or capabilities.get("supportsReasoningEffort")),
        "effort_levels": [str(level) for level in levels] if isinstance(levels, list) else [],
    }
