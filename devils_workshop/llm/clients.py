"""The services the app talks to, as one client: OpenRouter, Venice when you have a Venice key, and any
other OpenAI-compatible service you added (Settings → Other services).

Each request goes to the service its model belongs to ("venice:..." to Venice, "svc-<name>:..." to that
added service, everything else to OpenRouter), so a story can write with one and fall back to another.
The model list is all their lists.
"""

from __future__ import annotations

from devils_workshop.llm.openrouter import OpenRouterClient, StreamResult
from devils_workshop.llm.services import ServiceClient, is_service_model, service_slug
from devils_workshop.llm.venice import VeniceClient, is_venice


class Clients:
    def __init__(self, openrouter: OpenRouterClient, venice: VeniceClient | None = None,
                 services: list[ServiceClient] | None = None):
        self.openrouter = openrouter
        self.venice = venice
        self.services = {s.slug: s for s in services or []}
        self.notes: list[str] = []   # e.g. Venice's list couldn't be loaded

    async def stream_chat(self, body: dict, **options) -> StreamResult:
        model = body.get("model")
        if is_service_model(model):
            service = self.services.get(service_slug(model))
            if service is None:
                return StreamResult(service=service_slug(model) or "", status_code=404, error_kind="http",
                                    error={"message": f"The service '{service_slug(model)}' isn't in Settings → "
                                                      "Other services any more."})
            return await service.stream_chat(body, **options)
        if not is_venice(model):
            return await self.openrouter.stream_chat(body, **options)
        if self.venice is None:
            return StreamResult(service="Venice", status_code=401, error_kind="http",
                                error={"message": "No Venice key yet: add it in Settings → Venice AI."})
        return await self.venice.stream_chat(body, **options)

    async def list_models(self) -> list[dict]:
        """OpenRouter's models, then Venice's (if you have a Venice key), then each added service's. Venice or
        a service failing doesn't stop the others; OpenRouter failing raises, as before."""
        models = await self.openrouter.list_models()
        self.notes = []
        others = ([("Venice", self.venice)] if self.venice is not None else []) + \
            [(s.service, s) for s in self.services.values()]
        for name, client in others:
            try:
                models = models + await client.list_models()
            except Exception as exc:  # noqa: BLE001 - offline, blocked, server error, not running
                self.notes.append(f"Couldn't get {name}'s model list ({type(exc).__name__}); {name} models may "
                                  "be missing until it loads.")
        return models

    async def list_endpoints(self, model_id: str) -> list[dict]:
        if is_venice(model_id) or is_service_model(model_id):
            return []
        return await self.openrouter.list_endpoints(model_id)
