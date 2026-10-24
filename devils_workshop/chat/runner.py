"""Sending a prepared chapter request (PLAN.md §6.1).

While text streams in it is also written to chapters/chNN.partial.md, so
nothing is lost if the app is closed mid-chapter. The partial file is removed
when the request finishes; the full record is always in logs/.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from devils_workshop.chat.session import Prepared
from devils_workshop.llm.catalog import Catalog
from devils_workshop.llm.generate import DISCARD, TEXT, Event, GenerationResult, generate
from devils_workshop.llm.openrouter import OpenRouterClient
from devils_workshop.project.project import Project
from devils_workshop.settings import AppSettings


def models_for(project: Project, settings: AppSettings) -> tuple[str, str | None]:
    """The story's own models win over the app-wide defaults."""
    return (project.config.primary_model or settings.primary,
            project.config.fallback_model or settings.fallback)


async def send(prepared: Prepared, *, project: Project, settings: AppSettings, catalog: Catalog,
               client: OpenRouterClient, on_event: Callable[[Event], None] | None = None,
               cancel: asyncio.Event | None = None, secret: str | None = None,
               calibration_dir: Path | None = None, **generate_options) -> GenerationResult:
    primary, fallback = models_for(project, settings)
    label = f"ch{prepared.chapter:02d}" + (f"-s{prepared.scene}" if prepared.scene else "") + f"-{prepared.kind}"
    scene = f".s{prepared.scene}" if prepared.scene else ""  # scenes can be written at the same time
    partial = project.root / "chapters" / f"ch{prepared.chapter:02d}{scene}.partial.md"
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_text("", encoding="utf-8")

    def forward(event: Event) -> None:
        if event.kind == TEXT:
            with partial.open("a", encoding="utf-8") as handle:
                handle.write(event.text)
        elif event.kind == DISCARD:
            partial.write_text("", encoding="utf-8")
        if on_event:
            on_event(event)

    try:
        return await generate(
            prepared.messages, client=client, settings=settings, catalog=catalog,
            log_root=project.root / "logs", label=label, target_words=prepared.target_words,
            primary=primary, fallback=fallback, job=prepared.kind, chapter=prepared.chapter,
            scene=prepared.scene, calibration_dir=calibration_dir, on_event=forward, cancel=cancel,
            secret=secret, **generate_options)
    finally:
        partial.unlink(missing_ok=True)
