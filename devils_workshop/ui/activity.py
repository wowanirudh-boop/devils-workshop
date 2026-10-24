"""Activity: every request sent for this story, what it cost, and its full text (replaces Costs and Logs)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from nicegui import ui

from devils_workshop.library import family_of
from devils_workshop.llm.costs import read_costs, summarize
from devils_workshop.project.files import read_text
from devils_workshop.ui.copying import UNLESS_SELECTING, copy_button
from devils_workshop.ui.components import (
    card,
    empty_state,
    model_name,
    money,
    page,
    page_header,
    plural,
    quiet,
)
from devils_workshop.ui.layout import frame, need_story
from devils_workshop.ui.records import friendly_day

JOBS = {"write": "Write", "scene": "Write scene", "change": "Change", "continue": "Continue",
        "scenes": "Plan scenes", "join": "Smooth the joins between scenes",
        "plan-brainstorm": "Planner · Story plan", "plan-architecture": "Planner · Design the chapters",
        "plan-scenes": "Planner · Plan the scenes", "plan-scene": "Planner · Scene brief",
        "plan-notes": "Planner · Notes on a file", "plan-import": "Planner · Bring in your plan",
        "plan-actual": "Planner · Follow what was written", "plan-revise": "Planner · Plan your changes",
        "edit": "Planner · Edit a file for you"}
_PLANNED = re.compile(r"-ch(\d+)(?:-s(\d+))?")  # the planner's labels name the chapter and scene it planned


def role_of(job: str | None) -> str:
    return "Planner" if str(job or "").startswith("plan-") else "Writer"


def describe(meta: dict) -> str:
    job = meta.get("job") or ""
    chapter, scene = meta.get("chapter"), meta.get("scene")
    what = JOBS.get(job, job.capitalize() or "Request")
    planned = _PLANNED.search(meta.get("label") or "") if job.startswith("plan-") else None
    if planned:  # its costs aren't the chapter's, so the chapter is named only in the label
        return what + f" · chapter {int(planned[1])}" + (f", scene {planned[2]}" if planned[2] else "")
    if job == "scene" and scene:
        what = f"Write scene {scene}"
    elif scene and job in ("change", "continue"):
        what += f" (scene {scene})"
    return f"Chapter {chapter} · {what}" if chapter else what


def result_text(meta: dict) -> tuple[str, str]:
    """(words, kind) for the result column."""
    if meta.get("ok"):
        return "Saved", "final"
    if meta.get("outcome") == "cancelled":
        return "Stopped by you", "idle"
    return "Failed: " + (meta.get("failure") or meta.get("outcome") or "unknown").split(" Details:")[0], "problem"


def requests(root: Path) -> list[tuple[Path, dict, datetime]]:
    logs = root / "logs"
    if not logs.is_dir():
        return []
    found = []
    for folder in logs.glob("*/*"):
        meta_path = folder / "meta.json"
        if not folder.is_dir() or not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        found.append((folder, meta, datetime.fromtimestamp(meta_path.stat().st_mtime)))
    return sorted(found, key=lambda item: item[2], reverse=True)


def activity_page() -> None:
    project = frame("/story/activity")
    if project is None:
        need_story()
        return
    summary = summarize(read_costs(project.root / "logs"))
    items = requests(project.root)
    versions = family_of(str(project.root))
    drawer = ui.right_drawer(value=False, fixed=True).props("width=640 overlay bordered").style(
        "background: var(--dw-surface)")
    with page("wide"):
        with page_header("Activity", "Every request sent for this story: what it was for, which model wrote it, "
                                     "and what it cost."):
            pass
        if not items and len(versions) < 2:
            with empty_state("receipt_long", "No requests yet.",
                             "When the planner plans or the writer writes, each request appears here with its "
                             "cost and full text."):
                pass
            return
        with ui.element("div").classes("grid gap-4 w-full").style(
                "grid-template-columns: repeat(auto-fit, minmax(240px, 1fr))"):
            with card():
                ui.label(money(summary.total)).classes("dw-stat-value")
                ui.label(f"spent on {plural(summary.calls, 'request')}"
                         + (" in this version" if len(versions) > 1 else "")).classes("dw-small")
                if summary.estimated_calls:
                    ui.label(f"{summary.estimated_calls} estimated from prices (OpenRouter didn't report them)") \
                        .classes("dw-small")
            roles: dict[str, float] = {}
            for job, cost in summary.by_job.items():
                roles[role_of(job)] = roles.get(role_of(job), 0.0) + cost
            if len(versions) > 1:  # the story's total, every version together, and each version's share
                here = project.root.resolve()
                _breakdown(f"All {len(versions)} versions: {money(sum(v.cost for v in versions))}",
                           {f"Version {v.version}" + (" (this one)" if Path(v.path).resolve() == here else ""): v.cost
                            for v in versions})
            if roles.get("Planner"):
                _breakdown("Planner and writer", dict(sorted(roles.items(), key=lambda kv: kv[0] != "Planner")))
            _breakdown("By chapter", {f"Chapter {k}" if k not in (None, "") else "Other": v
                                      for k, v in sorted(summary.by_chapter.items(),
                                                         key=lambda kv: (kv[0] in (None, ""), str(kv[0]).zfill(4)))})
            _breakdown("By model", {model_name(k): v for k, v in summary.by_model.items()})

        if not items:
            return  # a new version, nothing sent from it yet: the story's costs are above
        with ui.element("div").classes("dw-list"):
            with ui.element("div").classes("dw-table-row head"):
                for label in ("When", "Request", "Model", "Result", "Words", "Cost"):
                    ui.label(label)
            for folder, meta, moment in items[:400]:
                words = sum(a.get("words", 0) for a in meta.get("attempts", []) if a.get("outcome") == "ok") or \
                    (meta.get("attempts") or [{}])[-1].get("words", 0)
                result, kind = result_text(meta)
                model = model_name(meta.get("model_used")) if meta.get("model_used") else "-"
                if meta.get("used_fallback"):
                    model += " (fallback)"
                with ui.element("div").classes("dw-table-row clickable").on(
                        "click", lambda f=folder, m=meta: open_request(drawer, f, m), js_handler=UNLESS_SELECTING)                         .mark("request-row"):
                    ui.label(f"{friendly_day(moment)} {moment:%H:%M}").classes("dw-small")
                    ui.label(describe(meta)).classes("dw-body")
                    ui.label(model).classes("dw-small")
                    with ui.element("div").classes("min-w-0"):
                        ui.label(result).classes(f"dw-chip {kind}").style(
                            "max-width:100%; overflow:hidden; text-overflow:ellipsis").tooltip(result)
                    ui.label(f"{words:,}" if words else "").classes("dw-small")
                    ui.label(money(meta.get("total_cost") or 0)).classes("dw-small")


def _breakdown(title: str, values: dict) -> None:
    with card(title):
        for key, cost in list(values.items())[:8]:
            with ui.row().classes("w-full no-wrap"):
                ui.label(str(key)).classes("dw-body grow")
                ui.label(money(cost)).classes("dw-small")


def open_request(drawer, folder: Path, meta: dict) -> None:
    drawer.clear()
    with drawer:
        with ui.column().classes("w-full gap-3 p-5"):
            with ui.row().classes("w-full items-center"):
                ui.label(describe(meta)).classes("dw-h2")
                ui.space()
                quiet("Close", drawer.hide, "close")
            result, kind = result_text(meta)
            ui.label(result).classes(f"dw-chip {kind} self-start")
            with ui.tabs().classes("w-full") as tabs:
                ui.tab("reply", "Reply")
                ui.tab("request", "What was sent")
                ui.tab("details", "Details")
            with ui.tab_panels(tabs, value="reply").classes("w-full").style("background: transparent"):
                with ui.tab_panel("reply"):
                    reply = folder / "response.md"
                    if not reply.is_file():
                        attempts = sorted(folder.glob("attempt-*.response.md"))
                        reply = attempts[-1] if attempts else reply
                    text = read_text(reply)[0] if reply.is_file() else "(no text came back)"
                    copy_button(text, label="Copy", tip="Copy the reply").mark("copy-reply")
                    ui.label(text[:200_000]).classes("dw-prose dw-pre").style("font-size: 16px")
                with ui.tab_panel("request"):
                    prompt = folder / "prompt.md"
                    sent = read_text(prompt)[0] if prompt.is_file() else "(not saved)"
                    copy_button(sent, label="Copy", tip="Copy what was sent").mark("copy-sent")
                    ui.label(sent[:200_000]).classes("dw-raw")
                with ui.tab_panel("details"):
                    for attempt in meta.get("attempts", []):
                        with ui.card().classes("dw-card tight gap-1").props("flat"):
                            ui.label(f"Attempt {attempt.get('number')} · {model_name(attempt.get('model'))}"
                                     f" via {attempt.get('provider') or 'unknown provider'}").classes("dw-h3")
                            ui.label(f"{attempt.get('reason')} · {attempt.get('words', 0):,} words · "
                                     f"{money(attempt.get('cost') or 0)} · {attempt.get('elapsed_s', 0):.0f} s") \
                                .classes("dw-small")
                    failures = [f"Attempt {a.get('number')} ({a.get('model')}): {a.get('reason')}"
                                for a in meta.get("attempts", []) if a.get("outcome") != "ok"]
                    if meta.get("failure") or failures:
                        copy_button("\n".join(([meta["failure"]] if meta.get("failure") else []) + failures),
                                    label="Copy the error details", tip="What went wrong, to keep or send on") \
                            .mark("copy-failure")
                    ui.label(f"Saved in {folder}").classes("dw-small mt-2")
                    with ui.expansion("Raw details (meta.json)").classes("w-full"):
                        ui.label(json.dumps(meta, indent=2)[:100_000]).classes("dw-raw")
    drawer.show()
