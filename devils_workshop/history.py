"""Automatic snapshots, so any change to a story file can be undone (PLAN.md §10, D25).

Before the app changes project files, it copies them to
    .history/<time>_<action>/<same relative paths>
with a manifest.json listing them. Restoring copies them back, and first takes
a snapshot of the current files, so a restore can itself be undone. Files
that didn't exist at snapshot time are removed again on restore.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from devils_workshop.project.files import write_text

HISTORY_DIR = ".history"


@dataclass
class Snapshot:
    id: str          # folder name
    time: str
    action: str
    files: list[str]    # files that existed and were copied
    missing: list[str]  # files that didn't exist yet


def snapshot(root: Path, rel_paths: list[str], action: str) -> Snapshot:
    root = Path(root)
    base = root / HISTORY_DIR
    now = datetime.now()
    # The Windows clock can return the same time for snapshots taken in quick succession.
    # Move on a microsecond until the time is unused, so names stay unique and in time order.
    while base.is_dir() and any(base.glob(f"{now.strftime('%Y%m%d-%H%M%S-%f')}_*")):
        now += timedelta(microseconds=1)
    slug = re.sub(r"[^A-Za-z0-9-]+", "-", action).strip("-")[:40] or "change"
    folder_id = f"{now.strftime('%Y%m%d-%H%M%S-%f')}_{slug}"
    folder = base / folder_id
    folder.mkdir(parents=True)
    files, missing = [], []
    for rel in dict.fromkeys(rel_paths):  # keep order, drop duplicates
        source = root / rel
        if source.is_file():
            target = folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            files.append(rel)
        else:
            missing.append(rel)
    manifest = {"action": action, "time": now.isoformat(timespec="seconds"), "files": files, "missing": missing}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return Snapshot(folder_id, manifest["time"], action, files, missing)


def write_project_file(root: Path, rel_path: str, text: str, action: str) -> None:
    """The one way the app changes a story file: snapshot first, then write."""
    snapshot(root, [rel_path], action)
    write_text(Path(root) / rel_path, text)


def list_snapshots(root: Path) -> list[Snapshot]:
    base = Path(root) / HISTORY_DIR
    if not base.is_dir():
        return []
    result = []
    for folder in sorted(base.iterdir(), reverse=True):
        manifest = folder / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except ValueError:
            continue
        result.append(Snapshot(folder.name, data.get("time", ""), data.get("action", ""),
                               list(data.get("files", [])), list(data.get("missing", []))))
    return result


def restore(root: Path, snapshot_id: str) -> Snapshot:
    """Put the files back as they were in the snapshot. Returns the safety snapshot taken first."""
    root = Path(root)
    chosen = next((s for s in list_snapshots(root) if s.id == snapshot_id), None)
    if chosen is None:
        raise FileNotFoundError(f"No snapshot called {snapshot_id}")
    safety = snapshot(root, chosen.files + chosen.missing, f"before-restore-{chosen.action}")
    folder = root / HISTORY_DIR / snapshot_id
    for rel in chosen.files:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(folder / rel, target)
    for rel in chosen.missing:
        (root / rel).unlink(missing_ok=True)
    return safety
