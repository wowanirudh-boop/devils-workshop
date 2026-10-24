"""Carrying on runs that the app closing (or the PC restarting) cut off.

While a planning run or an Auto-write run is under way, a note of it is kept in runs.json in the app's folder:
which story, what the run was doing (for Auto-write, the chapters still to write), its spending limit and what it
has spent. A run that ends in any way the app sees (finished, stopped by you, failed, paused after a chapter)
takes its note away. So a note still there when the app starts again belongs to a run that was cut off, and the
app starts it again (ui/main.py → carry_on): planning goes on from the next brief or list still to plan (everything
done is saved after each step), Auto-write from the next chapter or scene still to write, within what was left of
its spending limit. A note more than MAX_AGE_H hours old is dropped instead: by then you may not want it.

The request under way when it was cut off is lost and sent again; nothing from it was saved.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import fields
from pathlib import Path

from devils_workshop.project.files import write_text
from devils_workshop.settings import settings_dir

RUNS_FILE = "runs.json"
PLAN, AUTO = "plan", "auto"
MAX_AGE_H = 48

log = logging.getLogger(__name__)


def _path() -> Path:
    return settings_dir() / RUNS_FILE


def _key(root: Path, kind: str) -> str:
    return f"{kind}:{Path(root).resolve()}"


def notes() -> dict[str, dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    try:
        write_text(_path(), json.dumps(data, indent=1))
    except OSError:
        log.exception("Couldn't keep the note of a run")  # carrying on later is a help, never a reason to fail


def note(root: Path, kind: str, **details) -> None:
    """A run of this kind is under way for this story (or has moved on: details replace the earlier ones)."""
    data = notes()
    data[_key(root, kind)] = {"kind": kind, "root": str(Path(root).resolve()), "at": time.time(), **details}
    _save(data)


def forget(root: Path, kind: str) -> None:
    """The run ended in a way the app saw: nothing to carry on."""
    data = notes()
    if data.pop(_key(root, kind), None) is not None:
        _save(data)


def cut_off(now: float | None = None) -> list[dict]:
    """The runs to carry on (recent enough, story folder still there), each taken off the list; older ones are
    dropped."""
    now = time.time() if now is None else now
    data = notes()
    if not data:
        return []
    _save({})
    found = []
    for item in data.values():
        if not isinstance(item, dict) or item.get("kind") not in (PLAN, AUTO):
            continue
        if now - float(item.get("at") or 0) > MAX_AGE_H * 3600:
            log.info("Not carrying on a %s run from more than %s hours ago (%s)", item["kind"], MAX_AGE_H,
                     item.get("root"))
            continue
        if not Path(str(item.get("root") or "")).is_dir():
            continue
        found.append(item)
    return found


def remaining(cap: float | None, spent: float | None) -> float | None:
    """What is left of a spending limit (None: no limit)."""
    if cap is None:
        return None
    return max(0.0, float(cap) - float(spent or 0))


ONLINE_HOSTS = ("openrouter.ai", "api.venice.ai", "api.experientiallabs.ai")
ONLINE_WAIT_S = 120.0


async def online(wait_s: float = ONLINE_WAIT_S, every_s: float = 5.0) -> bool:
    """Wait (up to wait_s) for the internet: after a restart, Wi-Fi often comes a little after the app."""
    loop = asyncio.get_running_loop()
    end = loop.time() + wait_s
    while True:
        for host in ONLINE_HOSTS:
            try:
                _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, 443), timeout=4)
                writer.close()
                return True
            except (OSError, asyncio.TimeoutError):
                continue
        if loop.time() >= end:
            return False
        await asyncio.sleep(every_s)


async def carry_on(wait_for_internet=online) -> list[str]:
    """At start-up (ui/main.py): start again the runs that were cut off. Returns what was started, for the log."""
    runs = cut_off()
    if not runs:
        return []
    if not await wait_for_internet():
        log.warning("No internet yet: carrying on the runs cut off anyway (they wait and try again themselves)")
    from devils_workshop.ui.state import STATE
    await STATE.refresh_catalog()
    started = []
    for item in runs:
        root = Path(item["root"])
        try:
            if item["kind"] == PLAN:
                from devils_workshop.ui.planner import launch
                launch(root, only=tuple(item["only"]) if item.get("only") else None,
                       spend_cap=remaining(item.get("spend_cap"), item.get("spent")))
            else:
                from devils_workshop.chat.auto import AutoOptions
                from devils_workshop.project.project import load_project
                from devils_workshop.ui import auto
                raw = item.get("options") if isinstance(item.get("options"), dict) else {}
                known = {f.name for f in fields(AutoOptions)}
                options = AutoOptions(**{k: v for k, v in raw.items() if k in known})
                if not options.chapters:
                    continue
                options.spend_cap = remaining(options.spend_cap, item.get("spent"))
                auto.start(load_project(root), options)
            started.append(f"{item['kind']}: {root}")
            log.info("Carrying on the %s run that was cut off, for %s", item["kind"], root)
        except Exception:  # noqa: BLE001 - one story's run must not stop the others, or the app
            log.exception("Couldn't carry on the %s run for %s", item.get("kind"), root)
    return started
