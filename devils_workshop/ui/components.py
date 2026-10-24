"""Small building blocks every screen uses, so the screens look and behave alike."""

from __future__ import annotations

import html
import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from nicegui import ui

from devils_workshop.ui.copying import copy_button
from devils_workshop.ui.state import STATE


# --- layout ------------------------------------------------------------------------------------

@contextmanager
def page(width: str = "medium") -> Iterator[ui.column]:
    """The page's content column: narrow, medium, wide or full."""
    with ui.element("div").classes(f"dw-page {width}") as column:
        yield column


@contextmanager
def page_header(title: str, subtitle: str = "", eyebrow: str = "") -> Iterator[ui.row]:
    """Title on the left; whatever is created inside the `with` goes on the right (the page's actions)."""
    with ui.row().classes("dw-page-header w-full items-end no-wrap"):
        with ui.column().classes("gap-1 min-w-0"):
            if eyebrow:
                ui.label(eyebrow).classes("dw-eyebrow")
            ui.label(title).classes("dw-title")
            if subtitle:
                ui.label(subtitle).classes("dw-subtitle")
        ui.space()
        with ui.row().classes("items-center gap-2 no-wrap flex-none") as actions:
            yield actions


@contextmanager
def card(title: str = "", subtitle: str = "", classes: str = "") -> Iterator[ui.card]:
    with ui.card().classes(f"dw-card {classes}").props("flat") as box:
        if title:
            with ui.column().classes("gap-0"):
                ui.label(title).classes("dw-h2")
                if subtitle:
                    ui.label(subtitle).classes("dw-small")
        yield box


@contextmanager
def empty_state(icon: str, title: str, text: str = "") -> Iterator[ui.row]:
    with ui.card().classes("dw-card items-center text-center").props("flat").style("padding: 40px 24px"):
        ui.icon(icon).classes("text-5xl").style("color: var(--dw-faint)")
        ui.label(title).classes("dw-h2")
        if text:
            ui.label(text).classes("dw-subtitle").style("max-width: 520px")
        with ui.row().classes("gap-2 justify-center mt-2") as actions:
            yield actions


# --- buttons -------------------------------------------------------------------------------------

def primary(label: str, on_click: Callable | None = None, icon: str | None = None) -> ui.button:
    return ui.button(label, icon=icon, on_click=on_click).props("color=primary")


def secondary(label: str, on_click: Callable | None = None, icon: str | None = None) -> ui.button:
    # color=None: Quasar's colour classes win over the theme (they are !important in a CSS layer).
    return ui.button(label, icon=icon, on_click=on_click, color=None).props("outline").classes("dw-secondary")


def quiet(label: str, on_click: Callable | None = None, icon: str | None = None) -> ui.button:
    return ui.button(label, icon=icon, on_click=on_click, color=None).props("flat").classes("dw-quiet")


# --- status and feedback -----------------------------------------------------------------------------

STATUS = {  # chapter state -> (chip class, words)
    None: ("idle", "Not started"), "draft": ("draft", "Draft"), "final": ("final", "Final"),
    "writing": ("writing", "Writing…"), "problem": ("problem", "Problem"),
}


def chip(status: str | None, text: str | None = None) -> ui.label:
    kind, words = STATUS.get(status, ("idle", status or "Not started"))
    return ui.label(text or words).classes(f"dw-chip {kind}")


def dot(status: str | None) -> ui.element:
    return ui.element("span").classes(f"dw-dot {STATUS.get(status, ('idle',))[0]}")


def notice(kind: str, text: str, action: str = "", on_click: Callable | None = None) -> ui.row:
    icons = {"info": "info", "warning": "warning_amber", "problem": "error_outline", "success": "check_circle"}
    with ui.element("div").classes(f"dw-notice {kind}") as box:
        ui.icon(icons[kind])
        with ui.column().classes("gap-1 grow"):
            ui.label(text).classes("dw-pre")
            if action and on_click:
                ui.button(action, on_click=on_click).props("flat dense color=primary").classes("self-start -ml-2")
        if kind in ("problem", "warning"):  # copy the details, e.g. to look them up or send them on
            copy_button(text, tip="Copy this message").classes("self-start").mark("copy-notice")
    return box


def toast(message: str, kind: str = "positive") -> None:
    """A short message at the top of the window, out of the way of what you're typing. If the page is shown
    again straight after (smooth_reload), the message goes with it instead of vanishing as it appears."""
    ui.notify(message, type=kind, position="top", timeout=3500 if kind == "positive" else 7000,
              group=False, multi_line=len(message) > 70)
    try:
        client = ui.context.client.id
    except (AttributeError, RuntimeError):
        return
    now = time.monotonic()
    for key in [k for k, items in _RECENT.items() if not items or now - items[-1][0] > 10]:
        _RECENT.pop(key, None)
    _RECENT.setdefault(client, []).append((now, message, kind))


_RECENT: dict[str, list[tuple[float, str, str]]] = {}  # client -> its latest messages, for smooth_reload
CARRY_S = 2.0  # a message this recent goes with the page when it is shown again


def tail(text: str, size: int = 4000) -> str:
    """The end of a text streaming in, starting at a line or word (cut mid-word, the shown text shifts with
    every update)."""
    if len(text) <= size:
        return text
    cut = len(text) - size
    line = text.find("\n", cut)
    space = text.find(" ", cut)
    start = line + 1 if 0 <= line - cut < 400 else space + 1 if space >= 0 else cut
    return "…" + text[start:]


def smooth_reload() -> None:
    """Show this page again, without a flash: the browser crossfades to the new copy where it can (a reload can't
    crossfade, so the page replaces itself), and otherwise the new copy fades in once it is built (theme.py).
    The messages just shown go with it."""
    from nicegui.functions.navigate import Navigate
    try:
        client = ui.context.client.id
    except (AttributeError, RuntimeError):
        client = None
    if client is None or type(ui.navigate) is not Navigate:  # NiceGUI's simulated browser runs no page scripts
        ui.navigate.reload()
        return
    now = time.monotonic()
    carried = [{"message": m, "kind": k} for t, m, k in _RECENT.pop(client, []) if now - t < CARRY_S]
    ui.run_javascript(f"window.dwReload ? window.dwReload({json.dumps(carried)}) : location.reload()")


def stat(value: str, label: str) -> None:
    with ui.column().classes("gap-0"):
        ui.label(value).classes("dw-stat-value")
        ui.label(label).classes("dw-stat-label")


def bar(share: float, kind: str = "") -> ui.html:
    width = max(0.0, min(share, 1.0)) * 100
    return ui.html(f'<div class="dw-bar {kind}"><div style="width:{width:.1f}%"></div></div>', sanitize=False) \
        .classes("w-full")


# --- words ------------------------------------------------------------------------------------------

def model_name(model_id: str | None) -> str:
    """'z-ai/glm-5.3' -> 'GLM 5.3', using OpenRouter's own name when the model list is loaded."""
    if not model_id:
        return "(none)"
    from devils_workshop.llm.venice import PREFIX, is_venice
    if is_venice(model_id):  # 'venice:llama-3.3-70b' -> 'Venice · Llama 3.3 70B'
        info = STATE.catalog.get(model_id)
        name = info.name.split(": ", 1)[-1] if info and info.name else model_name(model_id[len(PREFIX):])
        return f"Venice · {name}"
    from devils_workshop.llm.services import bare_model, is_service_model, service_name
    if is_service_model(model_id):  # 'svc-featherless:meta-llama/Llama-3.3-70B' -> 'Featherless · Llama 3.3 70B'
        info = STATE.catalog.get(model_id)
        name = info.name.split(": ", 1)[-1] if info and info.name else bare_model(model_id)
        return f"{service_name(model_id, STATE.app_settings().services)} · {name}"
    info = STATE.catalog.get(model_id)
    if info and info.name:
        return info.name.split(": ", 1)[-1]
    words = model_id.split("/", 1)[-1].split(":")[0].replace("-", " ").split()
    return " ".join(w.upper() if w.isalpha() and len(w) <= 3 else w[0].upper() + w[1:] if w[0].isalpha() else w
                    for w in words)


def plural(count: int, word: str, many: str | None = None) -> str:
    return f"{count:,} {word if count == 1 else (many or word + 's')}"


def money(value: float) -> str:
    if value == 0:
        return "$0"
    return f"${value:.2f}" if value >= 0.1 else f"${value:.3f}" if value >= 0.001 else "<$0.001"


def prose_html(text: str) -> str:
    """Prose as paragraphs for the chapter view (scene headings kept as small labels)."""
    parts = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("— Scene ") and block.endswith("—"):
            parts.append(f'<div class="dw-scene-head">{html.escape(block.strip("— "))}</div>')
        else:
            parts.append(f"<p>{html.escape(' '.join(block.split()))}</p>")  # a line break inside is a space
    return "".join(parts)
