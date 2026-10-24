"""The live model list from OpenRouter (GET /api/v1/models; no key needed).

Used to check that configured model names exist, to show prices and context
sizes, and to leave out settings a model doesn't support (PLAN.md D5).
The list is cached in the app folder so the app still works offline.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from devils_workshop.project.files import write_text

CACHE_FILE = "models_cache.json"
CACHE_MAX_AGE_S = 24 * 3600
ALWAYS_SENT = {"model", "messages", "stream", "max_tokens", "provider"}


@dataclass
class ModelInfo:
    id: str
    name: str = ""
    context_length: int | None = None
    max_completion_tokens: int | None = None
    prompt_price: float | None = None       # USD per token
    completion_price: float | None = None   # USD per token
    cache_read_price: float | None = None   # USD per token
    supported_parameters: list[str] = field(default_factory=list)
    input_modalities: list[str] = field(default_factory=list)  # e.g. ["text", "image"]; empty: not known
    max_images: int | None = None  # pictures one request may carry (Venice: 1); None: no known limit
    thinks: bool | None = None      # the model thinks before it answers (Venice says); None: not said
    effort_levels: list[str] = field(default_factory=list)  # the thinking levels it takes; empty: any, or not said

    def price_per_million(self) -> tuple[float | None, float | None]:
        def per_m(price):
            return None if price is None else price * 1_000_000
        return per_m(self.prompt_price), per_m(self.completion_price)

    def supports(self, parameter: str) -> bool:
        return parameter in self.supported_parameters

    @property
    def thinking(self) -> str:
        """How its thinking can be set: "levels" (low, medium...), "switch" (on or off only), "none" (it doesn't
        think), or "unknown"."""
        if self.supports("reasoning"):
            return "levels"
        if self.thinks:
            return "switch"
        if self.thinks is False or self.supported_parameters:
            return "none"
        return "unknown"

    @property
    def sees_pictures(self) -> bool | None:
        """True if the model accepts pictures, False if not, None if OpenRouter didn't say."""
        return "image" in self.input_modalities if self.input_modalities else None


def _price(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_model(raw: dict) -> ModelInfo:
    pricing = raw.get("pricing") or {}
    top = raw.get("top_provider") or {}
    return ModelInfo(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        context_length=raw.get("context_length") or top.get("context_length"),
        max_completion_tokens=top.get("max_completion_tokens"),
        prompt_price=_price(pricing.get("prompt")),
        completion_price=_price(pricing.get("completion")),
        cache_read_price=_price(pricing.get("input_cache_read")),
        supported_parameters=[str(p) for p in raw.get("supported_parameters") or []],
        input_modalities=[str(m) for m in (raw.get("architecture") or {}).get("input_modalities") or []],
        max_images=raw["max_images"] if isinstance(raw.get("max_images"), int) else None,
        thinks=raw["thinks"] if isinstance(raw.get("thinks"), bool) else None,
        effort_levels=[str(e) for e in raw.get("effort_levels") or []],
    )


class Catalog:
    def __init__(self, models: list[ModelInfo], fetched_at: float | None = None, from_cache: bool = False):
        self.models = {m.id: m for m in models}
        self.fetched_at = fetched_at
        self.from_cache = from_cache

    def get(self, model_id: str) -> ModelInfo | None:
        return self.models.get(model_id)

    def search(self, text: str) -> list[ModelInfo]:
        text = text.lower()
        return sorted((m for m in self.models.values() if text in m.id.lower() or text in m.name.lower()),
                      key=lambda m: m.id)

    def check_names(self, names: list[str]) -> list[str]:
        """Warnings for configured model names that don't exist."""
        warnings = []
        for name in names:
            if name and name not in self.models:
                close = [m.id for m in self.search(name.split("/")[-1].split("-")[0])][:3]
                hint = f" Similar: {', '.join(close)}" if close else ""
                warnings.append(f"Model '{name}' isn't in OpenRouter's model list.{hint}")
        return warnings


async def load_catalog(client, cache_dir: Path, refresh: bool = False) -> tuple[Catalog, list[str]]:
    """Fresh list if the cache is old (or refresh=True); falls back to the cache when offline."""
    notes: list[str] = []
    cache = cache_dir / CACHE_FILE
    cached = _read_cache(cache)
    if cached and not refresh and time.time() - cached[1] < CACHE_MAX_AGE_S:
        return Catalog(cached[0], cached[1], from_cache=True), notes
    try:
        raw = await client.list_models()
    except Exception as exc:  # offline, blocked, server error...
        if cached:
            notes.append(f"Couldn't refresh the model list ({type(exc).__name__}); using the saved copy.")
            return Catalog(cached[0], cached[1], from_cache=True), notes
        notes.append(f"Couldn't get the model list from OpenRouter ({type(exc).__name__}).")
        return Catalog([], None), notes
    notes.extend(getattr(client, "notes", []))  # e.g. Venice's list couldn't be loaded
    now = time.time()
    write_text(cache, json.dumps({"fetched_at": now, "data": raw}))
    return Catalog([parse_model(m) for m in raw], now), notes


def _read_cache(path: Path) -> tuple[list[ModelInfo], float] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [parse_model(m) for m in data.get("data") or []], float(data.get("fetched_at", 0))
    except (OSError, ValueError, TypeError):
        return None


def filter_params(params: dict, info: ModelInfo | None) -> tuple[dict, list[str]]:
    """Keep only settings the model supports. Returns (kept, dropped names)."""
    if info is None or not info.supported_parameters:
        return dict(params), []  # can't check; send as configured
    kept = {k: v for k, v in params.items() if k in ALWAYS_SENT or info.supports(k)}
    return kept, sorted(k for k in params if k not in kept)
