"""Providers…: choose which OpenRouter providers run one model, seeing each one's price and speed. The writer
and the planner each choose their own, even for the same model.

Prices, context and uptime come from OpenRouter's list for the model. Speed is OpenRouter's figure for
the last 30 minutes when it gives one, otherwise how fast that provider has been for your own requests.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from devils_workshop.llm.openrouter import Endpoint, parse_endpoint
from devils_workshop.llm.speed import Speed, measured_speeds
from devils_workshop.llm.services import OPENROUTER, service_name, service_of
from devils_workshop.settings import PLANNER, WRITER, ModelSettings, ProviderChoice, settings_dir
from devils_workshop.ui.components import model_name, money, notice, primary, quiet, toast
from devils_workshop.ui.state import STATE

MODES = {"auto": "Let OpenRouter choose", "price": "Cheapest first", "throughput": "Fastest first",
         "latency": "Quickest to start", "mine": "My choice"}
MODE_HELP = {
    "auto": "OpenRouter spreads requests over the providers, favouring cheaper ones that are working well.",
    "price": "Always the cheapest provider that's working; others only if it fails.",
    "throughput": "The provider writing the most tokens per second lately.",
    "latency": "The provider that starts answering soonest lately.",
    "mine": "Mark providers below: Prefer (tried in the order you marked them), Only, or Never.",
}
CHOICES = {"": "—", "prefer": "Prefer", "only": "Only", "never": "Never"}
REQUEST_TOKENS = 15_000  # a typical chapter request: story files, instructions and the outline
ROLE_NAMES = {WRITER: "the writer", PLANNER: "the planner"}


def log_roots() -> list[Path]:
    """Where your requests' timings are: the app's own logs and your recent stories' logs."""
    roots = [settings_dir() / "logs"]
    roots += [Path(p) / "logs" for p in STATE.app_settings().recent_projects]
    return [r for r in roots if r.is_dir()]


def chapter_cost(endpoint: Endpoint, words: int) -> tuple[float, float] | None:
    if endpoint.prompt_price is None or endpoint.completion_price is None:
        return None
    output = words * 1.35
    low = REQUEST_TOKENS * endpoint.prompt_price + output * endpoint.completion_price
    return low, REQUEST_TOKENS * endpoint.prompt_price + output * 3 * endpoint.completion_price


def speed_text(endpoint: Endpoint, mine: Speed | None) -> str:
    if endpoint.throughput:
        first = f", first text after {endpoint.latency:.1f} s" if endpoint.latency else ""
        return f"{endpoint.throughput:.0f} tok/s{first} (OpenRouter, last 30 min)"
    if mine:
        first = f", first text after {mine.first_text_s:.1f} s" if mine.first_text_s is not None else ""
        return f"{mine.tokens_per_s:.0f} tok/s{first} (your {mine.requests} request{'s' if mine.requests != 1 else ''})"
    return "speed: no data yet"


async def providers_dialog(model_id: str, role: str = WRITER, words: int = 3000) -> None:
    settings = STATE.app_settings()
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[60rem] max-w-full gap-3"):
        with ui.column().classes("gap-0"):
            ui.label(f"Providers for {model_name(model_id)}").classes("dw-h2")
            ui.label(f"For {ROLE_NAMES[role]}. {'The writer' if role == PLANNER else 'The planner'} chooses its "
                     "own for this model.").classes("dw-small").mark("providers-role")
        if service_of(model_id) != OPENROUTER:
            ui.label(f"{service_name(model_id, settings.services)} runs this model itself, so there are no "
                     "providers to choose.").classes("dw-small")
            with ui.row().classes("w-full justify-end"):
                primary("Close", dialog.close)
            dialog.open()
            return
        loading = ui.row().classes("items-center gap-2")
        with loading:
            ui.spinner(size="18px")
            ui.label("Getting the providers from OpenRouter…").classes("dw-small")
        body = ui.column().classes("w-full gap-3")
    dialog.open()

    try:
        raw = await STATE.client().openrouter.list_endpoints(model_id)
    except Exception:  # noqa: BLE001 - shown below
        raw = []
    endpoints = sorted((parse_endpoint(e) for e in raw), key=lambda e: (e.completion_price is None,
                                                                         e.completion_price or 0))
    loading.set_visibility(False)
    current = settings.model(model_id).providers.of(role)
    marks = {e.slug: "prefer" if e.slug in current.prefer else "only" if e.slug in current.only else
             "never" if e.slug in current.never else "" for e in endpoints}
    order = [slug for slug in current.prefer if slug in marks]  # the order you marked them in
    mine = measured_speeds(log_roots())

    with body:
        if not endpoints:
            notice("warning", "OpenRouter didn't list providers for this model (it may be offline, or the "
                              "internet connection is down). Your current choice stays as it is.")
        mode = ui.radio(MODES, value=current.mode, on_change=lambda: draw()).props("inline").mark("providers-mode")
        mode_help = ui.label().classes("dw-small")
        fallbacks = ui.switch("If my choices fail, allow other providers", value=current.allow_fallbacks) \
            .mark("providers-fallbacks")
        if settings.providers.data_collection == "deny":
            ui.label("Your privacy setting also applies: only providers that don't store or train on your text.") \
                .classes("dw-small")
        rows = ui.column().classes("w-full gap-0 dw-list")
        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary("Save", lambda: save(), "save").mark("providers-save")

    def set_mark(slug: str, value: str) -> None:
        marks[slug] = value
        if value == "prefer" and slug not in order:
            order.append(slug)
        if value != "prefer" and slug in order:
            order.remove(slug)
        draw()

    def draw() -> None:
        mode_help.text = MODE_HELP[mode.value]
        fallbacks.set_visibility(mode.value == "mine")
        rows.clear()
        with rows:
            for endpoint in endpoints:
                with ui.element("div").classes("dw-row").mark("providers-row"):
                    with ui.column().classes("gap-0 grow min-w-0"):
                        title = endpoint.provider + (f" · {endpoint.quantization}" if endpoint.quantization else "")
                        if endpoint.status not in (None, 0):
                            title += " · not working right now"
                        ui.label(title).classes("dw-body")
                        facts = []
                        if endpoint.prompt_price is not None and endpoint.completion_price is not None:
                            facts.append(f"${endpoint.prompt_price * 1e6:.2f} in / ${endpoint.completion_price * 1e6:.2f} "
                                         "out per million")
                        cost = chapter_cost(endpoint, words)
                        if cost:
                            facts.append(f"about {money(cost[0])}–{money(cost[1])} per {words:,}-word chapter")
                        if endpoint.context_length:
                            facts.append(f"context {endpoint.context_length:,}")
                        if endpoint.uptime is not None:
                            facts.append(f"up {endpoint.uptime:.0f}%")
                        ui.label(" · ".join(facts)).classes("dw-small")
                        ui.label(speed_text(endpoint, mine.get((model_id, endpoint.provider)))).classes("dw-small")
                    rank = order.index(endpoint.slug) + 1 if endpoint.slug in order else None
                    options = CHOICES if mode.value == "mine" else {"": "—", "never": "Never"}
                    value = marks[endpoint.slug] if marks[endpoint.slug] in options else ""
                    select = ui.select(options, value=value,
                                       on_change=lambda e, s=endpoint.slug: set_mark(s, e.value or "")) \
                        .classes("w-36").mark("providers-choice")
                    if rank and mode.value == "mine":
                        select.props(f'label="Prefer #{rank}"')

    def save() -> None:
        choice = ProviderChoice(mode=mode.value, prefer=list(order) if mode.value == "mine" else [],
                                only=[s for s, m in marks.items() if m == "only"] if mode.value == "mine" else [],
                                never=[s for s, m in marks.items() if m == "never"],
                                allow_fallbacks=bool(fallbacks.value))
        if not endpoints:  # nothing was shown: keep what was there
            choice.prefer, choice.only, choice.never = current.prefer, current.only, current.never
        settings.models.setdefault(model_id, ModelSettings()).providers.set(role, choice)
        STATE.save_settings()
        dialog.close()
        toast(f"Providers saved for {model_name(model_id)} ({ROLE_NAMES[role]})")

    draw()
