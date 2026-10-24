"""Ask AI to change: describe a change, see exactly what the planner's model would change, then accept it.

Used beside every editor of your text files (story files, the planner's files, prompts, system prompts,
default instructions). Never for the story's own text: changes to the prose go to the writer.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from pathlib import Path

from nicegui import background_tasks, ui

from devils_workshop.planner.assist import Proposal, diff_lines, propose_edit
from devils_workshop.project.scopes import ASSIST, may_write
from devils_workshop.settings import settings_dir
from devils_workshop.ui.components import money, notice, primary, quiet, toast
from devils_workshop.ui.state import STATE


def ai_edit_button(name: str, get_text: Callable[[], str], accept: Callable[[str], None],
                   root: Path | None = None, mark: str = "ai-edit"):
    """The button: name is the file as you'd call it (its path in a story); accept(new_text) saves the change."""
    if root is not None and not may_write(ASSIST, name):
        return None
    return quiet("Ask AI to change", lambda: ai_edit_dialog(name, get_text(), accept, root), "auto_fix_high") \
        .props("dense").tooltip("Describe a change; the planner's model proposes it, and you see it before it is "
                                "saved").mark(mark)


def diff_html(before: str, after: str) -> str:
    rows = []
    for kind, line in diff_lines(before, after):
        colour = {"+": "var(--dw-final)", "-": "var(--dw-problem)", "@": "var(--dw-faint)"}.get(kind, "inherit")
        background = {"+": "rgba(46, 160, 67, 0.10)", "-": "rgba(207, 34, 46, 0.10)"}.get(kind, "transparent")
        sign = {"+": "+ ", "-": "− ", "@": "", " ": "  "}[kind]
        rows.append(f'<div style="color:{colour};background:{background};white-space:pre-wrap;'
                    f'padding:0 6px">{html.escape(sign + line) or "&nbsp;"}</div>')
    return '<div class="dw-raw" style="padding:6px 0">' + "".join(rows) + "</div>"


def ai_edit_dialog(name: str, current: str, accept: Callable[[str], None], root: Path | None = None) -> None:
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[52rem] max-w-full gap-3"):
        ui.label(f"Ask AI to change {Path(name).name}").classes("dw-h2")
        ui.label("Say what should change. The planner's model proposes the change; you see exactly what it would "
                 "change before anything is saved.").classes("dw-small")
        request = ui.textarea(placeholder="e.g. Make Mara two years older everywhere, and add that she can't swim") \
            .props("autogrow outlined").classes("w-full").mark("ai-edit-request")
        status = ui.row().classes("items-center gap-2")
        result_box = ui.column().classes("w-full gap-2")
        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            propose_button = primary("Propose the change", lambda: propose(), "auto_fix_high").mark("ai-edit-propose")
            accept_button = primary("Accept and save", lambda: take(), "check").mark("ai-edit-accept")
            accept_button.set_visibility(False)
    held: dict = {"proposal": None}

    def take() -> None:
        proposal: Proposal = held["proposal"]
        accept(proposal.text)
        dialog.close()
        toast(f"{Path(name).name} changed: {proposal.summary or 'saved'}")

    def show(proposal: Proposal) -> None:
        status.clear()
        result_box.clear()
        held["proposal"] = proposal
        with result_box:
            if not proposal.ok:
                notice("problem", f"No change was proposed: {proposal.failure}")
            elif not proposal.changed:
                notice("info", proposal.summary or "Nothing needed to change.")
            else:
                if proposal.summary:
                    ui.label(proposal.summary).classes("dw-body").mark("ai-edit-summary")
                with ui.scroll_area().classes("w-full").style("height: 46vh"):
                    ui.html(diff_html(current, proposal.text), sanitize=False).mark("ai-edit-diff")
            ui.label(f"{money(proposal.cost)} spent").classes("dw-small")
        accept_button.set_visibility(proposal.ok and proposal.changed)
        propose_button.set_text("Propose again" if proposal.ok else "Try again")
        propose_button.set_enabled(True)

    def propose() -> None:
        wanted = (request.value or "").strip()
        if not wanted:
            toast("Say what should change first", "warning")
            return
        propose_button.set_enabled(False)
        accept_button.set_visibility(False)
        status.clear()
        with status:
            ui.spinner(size="18px")
            ui.label("The planner's model is working on it…").classes("dw-small")

        async def go() -> None:
            try:
                proposal = await propose_edit(root, name, current, wanted, settings=STATE.app_settings(),
                                              catalog=STATE.catalog, client=STATE.client(),
                                              log_root=(root / "logs") if root is not None else settings_dir() / "logs",
                                              secret=STATE.api_key())
            except Exception as exc:  # noqa: BLE001 - shown in the dialog
                proposal = Proposal(False, failure=str(exc))
            show(proposal)

        background_tasks.create(go(), name="ai-edit")

    dialog.open()
