"""Story files → Problems, and History (every saved change, in plain words)."""

from __future__ import annotations

import re
from datetime import datetime
from itertools import groupby
from pathlib import Path
from urllib.parse import quote

from nicegui import ui

from devils_workshop.history import list_snapshots, restore
from devils_workshop.project.issues import ERROR, INFO, WARNING
from devils_workshop.project.project import Project
from devils_workshop.tools.plancheck import run_plan_check
from devils_workshop.ui.components import (
    empty_state,
    notice,
    page,
    page_header,
    plural,
    primary,
    quiet,
    toast,
    smooth_reload,
)
from devils_workshop.ui.layout import frame, need_story

SEVERITY = {ERROR: ("error_outline", "var(--dw-problem)"), WARNING: ("warning_amber", "var(--dw-draft)"),
            INFO: ("info", "var(--dw-faint)")}
TEXT_FILES = (".md", ".markdown", ".txt", ".yaml")


def friendly_day(moment: datetime) -> str:
    today = datetime.now().date()
    if moment.date() == today:
        return "Today"
    if (today - moment.date()).days == 1:
        return "Yesterday"
    return moment.strftime("%A %d %B %Y").replace(" 0", " ")


# --- problems -------------------------------------------------------------------------------------

def problems_panel(project: Project) -> None:
    report = run_plan_check(project)
    serious = [i for i in report.issues if i.severity in (ERROR, WARNING)]
    notes = [i for i in report.issues if i.severity == INFO]
    if not serious:
        notice("success", "No problems found. Everything the app needs from your story files is in order.")
    else:
        ui.label(plural(len(serious), "thing") + " to look at. Nothing is sent while you fix them; they only "
                 "affect what the model is told.").classes("dw-subtitle")
    for title, issues in (("", serious), ("Notes", notes)):
        if not issues:
            continue
        if title:
            ui.label(title).classes("dw-h3 mt-2")
        for file, group in groupby(sorted(issues, key=lambda i: (i.file or "", i.line or 0)), key=lambda i: i.file):
            group = list(group)
            with ui.element("div").classes("dw-list"):
                with ui.element("div").classes("dw-row").style("background: var(--dw-sunken)"):
                    ui.label(file or "The story as a whole").classes("dw-h3 grow")
                    if file and (project.root / file).is_file() and Path(file).suffix.lower() in TEXT_FILES:
                        quiet("Open", lambda f=file: ui.navigate.to(f"/story/files?path={quote(f)}"),
                              "edit").props("dense")
                for issue in group:
                    icon, colour = SEVERITY[issue.severity]
                    with ui.element("div").classes("dw-row items-start"):
                        ui.icon(icon).classes("text-xl").style(f"color: {colour}")
                        with ui.column().classes("gap-0 grow"):
                            ui.label(issue.message).classes("dw-body")
                            if issue.hint:
                                ui.label(issue.hint).classes("dw-small dw-pre")
                        if issue.line:
                            ui.label(f"line {issue.line}").classes("dw-small")


# --- history -------------------------------------------------------------------------------------------

def describe(action: str) -> str:
    """The snapshot's action code in plain words."""
    match = re.fullmatch(r"ch(\d+)-(\w+?)(?:-s(\d+))?(?:-v(\d+))?", action)
    if match:
        number, kind, scene, version = match.groups()
        chapter = f"Chapter {int(number)}"
        where = f", scene {int(scene)}" if scene else ""
        return {
            "write": f"{chapter} written", "scene": f"{chapter}{where} written", "change": f"{chapter}{where} changed",
            "continue": f"{chapter}{where} continued", "final": f"{chapter} marked final",
            "draft": f"{chapter} marked as draft", "scenes": f"Scenes planned for {chapter.lower()}",
            "restore": f"{chapter}: version {version} restored",
            "together": f"{chapter}: scenes written together", "join": f"{chapter}: joins between scenes smoothed",
        }.get(kind, f"{chapter}: {kind}")
    for prefix, words in (("edit-system-prompt", "System prompt edited"), ("your-files", "What's sent updated"),
                          ("edit-", "Edited "), ("new-", "Created "), ("reset-", "Back to the built-in "),
                          ("add-", "Added "), ("delete-", "Deleted "), ("rename-", "Renamed "),
                          ("not-sent-", "Stopped sending ")):
        if action == prefix or (prefix.endswith("-") and action.startswith(prefix)):
            rest = action[len(prefix):] if prefix.endswith("-") else ""
            return words + rest.replace("-instruction", " instruction")
    planner = {"planner-plan-approved": "Planner: story plan approved", "planner-plan": "Planner: chapters and "
               "scenes planned", "planner-chapter": "Planner: a chapter re-planned", "planner-scene": "Planner: a "
               "scene brief written again"}
    if action in planner:
        return planner[action]
    if action.startswith("default-instructions-"):
        return "Default instructions switched " + action.rsplit("-", 1)[1] + " for this story"
    if action.startswith("before-restore"):
        return "Saved just before a restore"
    return action


def history_page() -> None:
    project = frame("/story/history")
    if project is None:
        need_story()
        return
    snapshots = [s for s in list_snapshots(project.root)  # version files go with a chapter change; skip them
                 if not all(f.startswith("chapters/versions/") for f in s.files + s.missing)]
    with page("medium"):
        with page_header("History", "Every change, newest first. Undo puts the files back as they were just "
                                    "before that change; an undo can itself be undone."):
            pass
        if not snapshots:
            with empty_state("history", "No changes yet.",
                             "Each time a chapter is written or a file is saved, the earlier version is kept here."):
                pass
            return
        for day, group in groupby(snapshots[:400], key=lambda s: _when(s.time).date()):
            group = list(group)
            ui.label(friendly_day(_when(group[0].time))).classes("dw-h3 mt-2")
            with ui.element("div").classes("dw-list"):
                for snap in group:
                    with ui.element("div").classes("dw-row"):
                        ui.label(_when(snap.time).strftime("%H:%M")).classes("dw-small").style("width: 44px")
                        with ui.column().classes("gap-0 grow min-w-0"):
                            ui.label(describe(snap.action)).classes("dw-body")
                            files = [_file_name(f) for f in snap.files] + [f"{_file_name(f)} (created)"
                                                                            for f in snap.missing]
                            ui.label(", ".join(files)).classes("dw-small")
                        quiet("Undo", lambda s=snap: confirm_restore(project.root, s.id, describe(s.action)),
                              "undo").props("dense").tooltip("Put these files back as they were before this change")


def _when(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.now()


def _file_name(rel: str) -> str:
    match = re.fullmatch(r"chapters/ch(\d+)\.md", rel)
    if match:
        return f"chapter {int(match.group(1))} text"
    return {"outline.md": "outline", "Story settings.md": "story settings", "story.yaml": "story settings", "system_prompt.md": "system prompt"}.get(rel, rel)


def confirm_restore(root: Path, snapshot_id: str, what: str) -> None:
    with ui.dialog() as dialog, ui.card().classes("dw-card w-[30rem] gap-3"):
        ui.label("Undo this change?").classes("dw-h2")
        ui.label(f"The files go back to how they were just before: {what}. Any later changes to the same files "
                 "are undone too. Everything as it is now is saved first, so you can undo this as well.").classes(
            "dw-small")

        def go() -> None:
            restore(root, snapshot_id)
            dialog.close()
            toast("Undone")
            smooth_reload()

        with ui.row().classes("w-full justify-end gap-2"):
            quiet("Cancel", dialog.close)
            primary("Undo", go)
    dialog.open()
