"""Experiential Labs (experientiallabs.ai): an OpenAI-compatible gateway in front of many models, some of which
it hosts itself.

It is one of the "other services" (llm/services.py), built in: the app knows its address, so Settings →
Experiential Labs asks only for the key (xpl_...). Its models are "svc-explabs:<slug>". What differs:
- the model list: GET /v1/models says which slugs the key may call, but only the slug; the public catalog
  (GET /api/models, no key needed) has each model's name, context, prices, pictures and thinking levels, so
  the two are put together. Without the catalog the models are still listed, with their price unknown;
- the cost: the gateway reports what each request cost (usage.cost on the last chunk), read as OpenRouter's.
"""

from __future__ import annotations

import logging

import httpx

from devils_workshop.llm.openrouter import TIMEOUTS, TOTAL_LIMIT_S
from devils_workshop.llm.services import EXPLABS_NAME, EXPLABS_SLUG, ServiceClient, model_prefix, to_openrouter_shape

EXPLABS_URL = "https://api.experientiallabs.ai/v1"
CATALOG_URL = "https://api.experientiallabs.ai/api/models"
CATALOG_PAGE = 500
CATALOG_PAGES = 10  # the catalog has about 1,000 models; never more than 5,000 are read

log = logging.getLogger(__name__)


def is_explabs_key(value: str) -> bool:
    """Its keys look like xpl_ and 40 hex characters."""
    return value.startswith("xpl_") and len(value) >= 20


class ExperientialClient(ServiceClient):
    def __init__(self, api_key: str | None, base_url: str = EXPLABS_URL, catalog_url: str = CATALOG_URL,
                 transport: httpx.AsyncBaseTransport | None = None,
                 timeouts: httpx.Timeout = TIMEOUTS, total_limit_s: float = TOTAL_LIMIT_S):
        super().__init__(EXPLABS_NAME, EXPLABS_SLUG, base_url, api_key, transport=transport, timeouts=timeouts,
                         total_limit_s=total_limit_s)
        self.catalog_url = catalog_url

    async def list_models(self) -> list[dict]:
        """The models the key may call, described from the public catalog (ids prefixed "svc-explabs:")."""
        async with self._client() as client:
            response = await client.get("/models")
            response.raise_for_status()
            data = response.json()
            callable_ = [m for m in (data.get("data") if isinstance(data, dict) else data) or []
                         if isinstance(m, dict) and m.get("id")]
            try:
                catalog = await self._catalog(client)
            except Exception as exc:  # noqa: BLE001 - the list still works without names and prices
                log.warning("Couldn't read Experiential Labs' catalog (%s); prices unknown", type(exc).__name__)
                catalog = {}
        return [shape(m, catalog.get(str(m["id"]))) for m in callable_]

    async def _catalog(self, client: httpx.AsyncClient) -> dict[str, dict]:
        rows: dict[str, dict] = {}
        for page in range(CATALOG_PAGES):
            response = await client.get(self.catalog_url, params={"limit": CATALOG_PAGE,
                                                                  "offset": page * CATALOG_PAGE})
            response.raise_for_status()
            data = response.json()
            models = data.get("models") or []
            for row in models:
                slug = (row.get("model") or {}).get("slug") if isinstance(row, dict) else None
                if slug:
                    rows[str(slug)] = row
            if len(models) < CATALOG_PAGE or (page + 1) * CATALOG_PAGE >= int(data.get("total") or 0):
                break
        return rows


def _route(row: dict) -> dict:
    """The provider that serves the model by default: its prices and capabilities are the model's."""
    providers = [p for p in row.get("providers") or [] if isinstance(p, dict)]
    default = row.get("default_provider_ids") or []
    for p in providers:
        if p.get("id") in default and p.get("status") == "active":
            return p
    return next((p for p in providers if p.get("routable") and p.get("status") == "active"),
                next((p for p in providers if p.get("status") == "active"), {}))


def _per_token(nano_usd_per_million) -> str | None:
    """The catalog's prices are nano-dollars per million tokens: 300,000,000 is $0.30 per million."""
    try:
        return str(float(nano_usd_per_million) / 1e15)
    except (TypeError, ValueError):
        return None


def shape(raw: dict, row: dict | None) -> dict:
    """One model the key may call, as OpenRouter would describe it (see catalog.parse_model)."""
    shaped = to_openrouter_shape(raw, EXPLABS_NAME, model_prefix(EXPLABS_SLUG))
    model = (row or {}).get("model") or {}
    if not model:
        return shaped
    route = _route(row)
    caps = route.get("capabilities") if isinstance(route.get("capabilities"), dict) else {}
    params = {name for name, on in (model.get("supported_params") or {}).items() if on}
    params |= {name for name, flag in (("temperature", "supports_temperature"), ("top_p", "supports_top_p"),
                                       ("top_k", "supports_top_k"), ("stop", "supports_stop_sequences"),
                                       ("reasoning", "supports_reasoning"))
               if caps.get(flag)}
    thinks = "reasoning" in params
    levels = caps.get("supported_reasoning_efforts") if thinks else None
    shaped.update({
        "name": f"{EXPLABS_NAME}: {model.get('display_name') or raw['id']}",
        "context_length": model.get("context_window") or shaped["context_length"],
        "top_provider": {"max_completion_tokens": model.get("max_output_tokens")
                         or shaped["top_provider"]["max_completion_tokens"]},
        "pricing": {"prompt": _per_token(route.get("input_nano_usd_per_million")),
                    "completion": _per_token(route.get("output_nano_usd_per_million")),
                    "input_cache_read": _per_token(route.get("cached_input_nano_usd_per_million"))},
        "supported_parameters": sorted(params | {"max_tokens"}) if params else [],
        "architecture": {"input_modalities": [str(m) for m in model.get("input_modalities") or []]},
        "thinks": thinks,
        "effort_levels": [str(level) for level in levels] if isinstance(levels, list) else [],
    })
    return shaped
