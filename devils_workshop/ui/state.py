"""What the app keeps while it runs: the open story, settings, the model list.

The app has one window and one user, so this is a single shared object.
Tests replace `client_factory` to talk to a fake OpenRouter.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from devils_workshop.llm import calibration
from devils_workshop.llm.catalog import Catalog, ModelInfo, load_catalog
from devils_workshop.llm.clients import Clients
from devils_workshop.llm.experiential import EXPLABS_URL, ExperientialClient
from devils_workshop.llm.openrouter import BASE_URL, OpenRouterClient
from devils_workshop.llm.services import ServiceClient
from devils_workshop.llm.venice import VENICE_URL, VeniceClient
from devils_workshop.project.project import Project, load_project
from devils_workshop.settings import (
    AppSettings,
    ServiceSettings,
    get_api_key,
    get_explabs_key,
    get_service_key,
    get_venice_key,
    load_settings,
    save_settings,
    settings_dir,
)

MAX_RECENT = 5000  # every story you open or start is in your library


def default_client(api_key: str | None) -> OpenRouterClient:
    # DW_OPENROUTER_BASE_URL is only for testing against a fake server.
    return OpenRouterClient(api_key, base_url=os.environ.get("DW_OPENROUTER_BASE_URL") or BASE_URL)


def default_venice(api_key: str, include_venice_system_prompt: bool) -> VeniceClient:
    return VeniceClient(api_key, base_url=os.environ.get("DW_VENICE_BASE_URL") or VENICE_URL,
                        include_venice_system_prompt=include_venice_system_prompt)


def default_explabs(api_key: str) -> ExperientialClient:
    return ExperientialClient(api_key, base_url=os.environ.get("DW_EXPLABS_BASE_URL") or EXPLABS_URL)


def default_service(service: ServiceSettings, api_key: str | None) -> ServiceClient:
    return ServiceClient(service.name, service.slug, service.base_url, api_key)


@dataclass
class AppState:
    root: Path | None = None                     # the open story's folder
    settings: AppSettings | None = None
    catalog: Catalog = field(default_factory=lambda: Catalog([]))
    catalog_notes: list[str] = field(default_factory=list)
    client_factory: Callable[[str | None], OpenRouterClient] = default_client
    venice_factory: Callable[[str, bool], VeniceClient] = default_venice
    service_factory: Callable[[ServiceSettings, str | None], ServiceClient] = default_service
    explabs_factory: Callable[[str], ServiceClient] = default_explabs
    background: bool = False                     # started with Windows, without a window (ui/main.py)

    # --- settings -------------------------------------------------------------------------

    def app_settings(self) -> AppSettings:
        if self.settings is None:
            self.settings = load_settings()
        return self.settings

    def save_settings(self) -> None:
        save_settings(self.app_settings())

    def api_key(self) -> str | None:
        return get_api_key()

    def has_a_key(self) -> bool:
        """True when requests can go somewhere: an OpenRouter, Venice or Experiential Labs key, or another service
        added in Settings (a model on this PC may need no key)."""
        return bool(get_api_key() or get_venice_key() or get_explabs_key() or self.app_settings().services)

    def client(self) -> Clients:
        """OpenRouter, plus Venice when there is a Venice key, plus Experiential Labs when there is its key, plus
        each added service (Settings → Other services): each request goes to its model's service."""
        settings = self.app_settings()
        venice_key = get_venice_key()
        venice = self.venice_factory(venice_key, settings.venice_system_prompt) if venice_key else None
        explabs_key = get_explabs_key()
        services = ([self.explabs_factory(explabs_key)] if explabs_key else []) + \
            [self.service_factory(s, get_service_key(s.slug)) for s in settings.services]
        return Clients(self.client_factory(self.api_key()), venice, services)

    # --- stories ----------------------------------------------------------------------------

    def open_story(self, root: Path) -> Project:
        project = load_project(root)  # raises NotAProjectError for a wrong folder
        self.root = Path(root)
        settings = self.app_settings()
        path = str(Path(root).resolve())
        settings.recent_projects = [path] + [p for p in settings.recent_projects if p != path][: MAX_RECENT - 1]
        self.save_settings()
        from devils_workshop.library import opened
        opened(path)
        return project

    def project(self) -> Project | None:
        return load_project(self.root) if self.root else None

    def forget_recent(self, path: str) -> None:
        settings = self.app_settings()
        settings.recent_projects = [p for p in settings.recent_projects if p != path]
        self.save_settings()

    # --- models ---------------------------------------------------------------------------------

    async def refresh_catalog(self, force: bool = False) -> Catalog:
        self.catalog, self.catalog_notes = await load_catalog(self.client(), settings_dir(), refresh=force)
        return self.catalog

    def models_for(self, project: Project | None) -> tuple[str, str | None]:
        settings = self.app_settings()
        if project is None:
            return settings.primary, settings.fallback
        return (project.config.primary_model or settings.primary,
                project.config.fallback_model or settings.fallback)

    def sizes_for(self, model_id: str) -> dict:
        """Size limit and learned characters-per-token for building a writing request."""
        return {"size_limit": self.app_settings().writer_options.size_limit_tokens,
                "chars_per_token": calibration.chars_per_token(settings_dir(), model_id)}

    def context_window(self, model_id: str) -> int | None:
        info = self.catalog.get(model_id)
        return info.context_length if info else None

    def estimate_cost(self, model_id: str, prompt_tokens: int, target_words: int) -> tuple[float, float] | None:
        """A rough (low, high) cost for one request; high allows for long reasoning."""
        info: ModelInfo | None = self.catalog.get(model_id)
        if info is None or info.prompt_price is None or info.completion_price is None:
            return None
        output = target_words * 1.35  # tokens for the prose, roughly
        low = prompt_tokens * info.prompt_price + output * info.completion_price
        high = prompt_tokens * info.prompt_price + output * 3 * info.completion_price
        return low, high


STATE = AppState()
