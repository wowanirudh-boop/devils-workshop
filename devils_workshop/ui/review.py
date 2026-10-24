"""Review before sending (PLAN.md §5, D20, D32; docs/ui-redesign.md).

Shown before every request you start by hand: what's included and why, then the
request itself as readable sections. Tick or untick files, add any story file,
or edit the exact text. Choices apply to this request only; files don't change.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from nicegui import ui

from devils_workshop.chat.session import Prepared
from devils_workshop.context.assemble import ChatMessage, ContextSection, Overrides
from devils_workshop.project.own_files import looks_binary
from devils_workshop.ui.copying import copy_button
from devils_workshop.ui.components import model_name, money, notice, primary, quiet, toast
from devils_workshop.ui.state import STATE
from devils_workshop.wordcount import count_words

_ROLE_LINE = re.compile(r"^===== (system|user|assistant) =====$", re.MULTILINE)
_BLOCK = re.compile(r"^<([a-z_]+)(?:\s[^>]*)?>\n(.*?)\n</\1>$", re.S | re.M)

GROUPS = [  # section kind -> heading in "What's included"
    ("bible", "Always sent"),
    ("default", "Your default instructions"),
    ("document", "Your files"),
    ("entry", "Characters, places and lore"),
    ("subplot", "Subplots"),
    ("file", "Added by you"),
]
BLOCK_LABELS = {
    "style_guide": "Style guide", "world_rules": "World rules", "story_bible": "Story bible",
    "reference_documents": "Your files", "characters": "Characters", "places_and_lore": "Places and lore",
    "subplots": "Subplots", "extra_files": "Files you added", "task": "The task",
    "updates": "Changes since the last scene", "new_reference": "New reference material",
    "change_request": "The change you asked for", "continue": "Carry on",
}
OPEN_BLOCKS = {"task", "change_request", "continue"}


def messages_to_text(messages: list[ChatMessage]) -> str:
    return "\n\n".join(f"===== {m.role} =====\n{m.content}" for m in messages)


def text_to_messages(text: str) -> list[ChatMessage]:
    parts = _ROLE_LINE.split(text)
    messages = []
    for index in range(1, len(parts) - 1, 2):
        messages.append(ChatMessage(parts[index], parts[index + 1].strip("\n")))
    if not messages or messages[-1].role != "user":
        raise ValueError("The text must keep the '===== role =====' lines and end with a user message")
    return messages


def project_files(root: Path) -> list[str]:
    skip = {"logs", "sessions", ".history", "chapters", "exports"}
    files = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_file() and rel.parts[0] not in skip and not looks_binary(path):
            files.append(rel.as_posix())
    return files


def blocks(content: str) -> list[tuple[str, str]]:
    """A user message as (tag, text) parts; text outside any tag gets the tag ''."""
    parts, at = [], 0
    for match in _BLOCK.finditer(content):
        if content[at:match.start()].strip():
            parts.append(("", content[at:match.start()].strip()))
        parts.append((match.group(1), match.group(2)))
        at = match.end()
    if content[at:].strip():
        parts.append(("", content[at:].strip()))
    return parts


def _words(text: str) -> str:
    return f"{count_words(text):,} words"


async def review(prepared: Prepared, rebuild: Callable[[Overrides], Prepared], model_id: str,
                 title: str, subtitle: str = "") -> Prepared | None:
    """Show the request; return it (maybe changed) to send, or None if cancelled."""
    overrides = Overrides()
    current = {"prepared": prepared, "edited": None, "view": "readable"}
    root = STATE.root

    with ui.dialog().props("maximized") as dialog, ui.card().classes("w-full h-full gap-0 p-0 no-wrap dw-review"):
        with ui.row().classes("dw-dialog-head w-full items-center no-wrap gap-3"):
            with ui.column().classes("gap-0"):
                ui.label(f"Review before sending · {title}").classes("dw-h2")
                if subtitle:
                    ui.label(subtitle).classes("dw-small")
            ui.space()
            quiet("Cancel", lambda: dialog.submit(None))
            send_button = primary(f"Send to {model_name(model_id)}", icon="send").mark("send")

        @ui.refreshable
        def body() -> None:
            request = current["prepared"].request
            tokens = request.size.tokens
            estimate = STATE.estimate_cost(model_id, tokens, request.target_words)
            with ui.row().classes("w-full gap-8 items-center px-6 pt-4"):
                _fact("Aims for", f"about {request.target_words:,} words")
                _fact("Estimated cost", f"{money(estimate[0])}–{money(estimate[1])}" if estimate else "unknown")
                share = tokens / request.size.limit if request.size.limit else 0
                _fact("Length", f"{share:.0%} of your limit" if share >= 0.01 else "well within your limit").tooltip(
                    f"{tokens:,} of {request.size.limit:,} tokens (a token is about three quarters of a word). "
                    "The limit is set in Settings.")
            with ui.column().classes("w-full px-6 pt-3 gap-2"):
                for warning in request.warnings:
                    notice("warning", warning)
                if current["edited"] is not None and current["view"] != "edit":
                    notice("info", "You edited the exact text, so your edited version will be sent.",
                           "Discard my edits", discard_edits)
            if request.size.exceeds_context:
                send_button.disable()
            else:
                send_button.enable()

            with ui.row().classes("w-full no-wrap gap-6 items-start px-6 py-4 dw-wrap-phone"):
                with ui.column().classes("shrink-0 gap-4 dw-full-phone").style("width: 340px"):
                    ui.label("What's included").classes("dw-h3")
                    included = request.included()
                    for kind, heading in GROUPS:
                        rows = [s for s in included if s.kind == kind]
                        if rows:
                            with ui.column().classes("gap-1 w-full"):
                                ui.label(heading).classes("dw-eyebrow")
                                for section in rows:
                                    _section_row(section, True)
                    left_out = request.left_out()
                    if left_out:
                        with ui.expansion(f"Not included ({len(left_out)})").classes("dw-section-block"):
                            with ui.column().classes("gap-1 px-3 pb-3"):
                                for section in left_out:
                                    _section_row(section, False)
                    choices = [f for f in project_files(root) if f"file:{f}" not in {s.key for s in request.sections}]
                    ui.select(choices, label="Add a file from the story", with_input=True,
                              on_change=lambda e: add_file(e.value)).classes("w-full")
                with ui.column().classes("grow min-w-0 gap-3"):
                    with ui.row().classes("items-center w-full gap-2") as request_header:
                        ui.label("The request").classes("dw-h3")
                        ui.space()
                        ui.toggle({"readable": "Readable", "exact": "Exact text", "edit": "Edit"},
                                  value=current["view"], on_change=lambda e: set_view(e.value))
                    if current["view"] == "edit":
                        ui.label("Keep the '===== role =====' lines. Your edits apply to this request only.") \
                            .classes("dw-small")
                        edited = ui.textarea(value=current["edited"],
                                             on_change=lambda e: current.update(edited=e.value)) \
                            .props("autogrow").classes("w-full dw-mono")
                        with request_header:  # your edited text, as it is in the box
                            copy_button(source=edited, label="Copy", tip="Copy the request").mark("copy-request")
                    elif current["view"] == "exact":
                        ui.label(messages_to_text(request.messages)).classes("dw-raw")
                    else:
                        readable(request.messages)
                    if current["view"] != "edit":
                        with request_header:  # the exact text that will be sent
                            copy_button(messages_to_text(request.messages), label="Copy",
                                        tip="Copy the request").mark("copy-request")

        def _fact(label: str, value: str):
            with ui.column().classes("gap-0") as box:
                ui.label(label).classes("dw-small")
                ui.label(value).classes("dw-body").style("font-weight: 600")
            return box

        def _section_row(section: ContextSection, included: bool) -> None:
            with ui.row().classes("w-full items-start no-wrap gap-2"):
                ui.checkbox(value=included, on_change=lambda e, s=section: toggle(s.key, e.value)).classes("mt-1")
                with ui.column().classes("gap-0 min-w-0"):
                    ui.label(section.label).classes("dw-body")
                    words = count_words(section.text)
                    note = section.reason + (f" · {words:,} words" if included and words else "")
                    ui.label(note).classes("dw-small")

        def readable(messages: list[ChatMessage]) -> None:
            for message in messages:
                if message.role == "system":
                    with ui.expansion("Instructions for the model", icon="tune",
                                      caption=_words(message.content)).classes("dw-section-block"):
                        ui.label(message.content).classes("dw-pre dw-small px-4 pb-4").style("color: var(--dw-text)")
                elif message.role == "assistant":
                    with ui.expansion("Text written earlier (sent back so the model can carry on)", icon="article",
                                      caption=_words(message.content)).classes("dw-section-block"):
                        ui.label(message.content).classes("dw-pre px-4 pb-4 dw-serif")
                else:
                    for tag, text in blocks(message.content):
                        label = BLOCK_LABELS.get(tag, tag.replace("_", " ").capitalize() or "Message")
                        icon = "flag" if tag in OPEN_BLOCKS else "description"
                        with ui.expansion(label, icon=icon, caption=_words(text), value=tag in OPEN_BLOCKS) \
                                .classes("dw-section-block"):
                            ui.label(text).classes("dw-pre px-4 pb-4").style("font-size: 13.5px; line-height: 1.6")

        def rebuild_now() -> None:
            current["prepared"] = rebuild(overrides)
            current["edited"] = None
            if current["view"] == "edit":
                current["view"] = "readable"
            body.refresh()

        def toggle(key: str, value: bool) -> None:
            (overrides.include if value else overrides.exclude).add(key)
            (overrides.exclude if value else overrides.include).discard(key)
            rebuild_now()

        def add_file(name: str | None) -> None:
            if name and name not in overrides.extra_files:
                overrides.extra_files.append(name)
                rebuild_now()

        def set_view(view: str) -> None:
            current["view"] = view
            if view == "edit" and current["edited"] is None:
                current["edited"] = messages_to_text(current["prepared"].messages)
            body.refresh()

        def discard_edits() -> None:
            current["edited"] = None
            body.refresh()

        def submit() -> None:
            prepared_now = current["prepared"]
            if current["edited"] is not None:
                try:
                    prepared_now.request.messages = text_to_messages(current["edited"])
                except ValueError as exc:
                    toast(str(exc), "negative")
                    return
            dialog.submit(prepared_now)

        send_button.on_click(submit)
        with ui.scroll_area().classes("w-full grow"):
            body()

    result = await dialog
    dialog.delete()  # don't leave closed dialogs piling up on the page
    return result
