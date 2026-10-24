"""Which text of each chapter something was made from, and whether it still is.

A chapter's revision is a hash of its text as saved (chapters/chNN.md, scene markers included), so it changes with
every change, including the Reader's changes made in place, which keep the version number. Whatever is made
from the chapters records the revisions it used and can tell when it is out of date:
- an export (exports/): written next to a manifest (<file>.manifest.json) with the revision of every chapter in it;
  it is made from one reading of the chapters, checked against them again before it is saved (a chapter that
  changed meanwhile stops the save), and its text checked to be theirs (export_status() tells you later whether
  it still is);
- the planner's reading of a chapter as written (planner/review.py) and the checks of a chapter (planner/audit.py).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from devils_workshop.project.chapters import ChapterText
from devils_workshop.project.files import write_text

MANIFEST_SUFFIX = ".manifest.json"
CURRENT, STALE, UNKNOWN = "current", "stale", "unknown"
PROBE_WORDS = 10      # words at the start and end of every scene that the export must contain
FOUND_ENOUGH = 0.9    # of those probes (a PDF without the full font set changes a few letters)


class ExportError(Exception):
    """The export doesn't match the chapters it was made from, so it isn't saved."""


def revision(chapter: ChapterText | None) -> str:
    """The chapter's text as saved, as a short hash ("" for no chapter)."""
    if chapter is None:
        return ""
    body = "\n".join(line.rstrip() for line in chapter.text.strip().split("\n"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def revisions(project, only: int | None = None) -> list[dict]:
    """Every written chapter (or just `only`), in order: its number, version, revision and words."""
    result = []
    for number in sorted(project.chapters):
        written = project.chapters[number]
        if (only is not None and number != only) or not (written.scenes or written.text.strip()):
            continue
        result.append({"chapter": number, "version": written.version, "revision": revision(written),
                       "words": written.words, "scenes": sorted(written.scenes)})
    return result


@dataclass
class ExportStatus:
    state: str        # CURRENT | STALE | UNKNOWN
    detail: str = ""


def manifest_path(path: Path) -> Path:
    return path.with_name(path.name + MANIFEST_SUFFIX)


def save_export(project, path: Path, kind: str, build: Callable, only: int | None = None) -> bytes:
    """Make an export from one reading of the chapters, check it, then save it with its manifest. Returns its
    bytes. ExportError (nothing saved) when a chapter changed while it was made, or its text isn't theirs."""
    from devils_workshop.project.project import load_project

    used = revisions(project, only)
    data = build(project)
    if isinstance(data, str):
        data = data.encode("utf-8")
    now = revisions(load_project(project.root), only)
    if now != used:
        changed = sorted({r["chapter"] for r in used} ^ {r["chapter"] for r in now}
                         | {a["chapter"] for a, b in zip(used, now) if a != b})
        raise ExportError("Chapter " + ", ".join(map(str, changed)) + " changed while the story was being saved, "
                          "so it wasn't saved: save it again.")
    verify(data, kind, project, only)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    write_text(manifest_path(path), json.dumps({
        "kind": kind, "made": datetime.now().isoformat(timespec="seconds"), "whole_story": only is None,
        "chapter": only, "content_sha256": hashlib.sha256(data).hexdigest(), "chapters": used,
    }, indent=2) + "\n")
    return data


def export_status(project, path: Path) -> ExportStatus:
    """Whether an export still has the chapters as they are now."""
    manifest = manifest_path(path)
    if not manifest.is_file():
        return ExportStatus(UNKNOWN, "made before exports recorded which text they used: it may be out of date")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except ValueError:
        return ExportStatus(UNKNOWN, "its record can't be read")
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != data.get("content_sha256"):
        return ExportStatus(UNKNOWN, "the file isn't the one the app saved")
    used = {r["chapter"]: r["revision"] for r in data.get("chapters", [])}
    now = {r["chapter"]: r["revision"] for r in revisions(project, data.get("chapter"))}
    changed = sorted(n for n in used if now.get(n) != used[n])
    added = sorted(n for n in now if n not in used)
    if not changed and not added:
        return ExportStatus(CURRENT)
    parts = []
    if changed:
        parts.append(f"chapter{'s' if len(changed) > 1 else ''} {', '.join(map(str, changed))} changed since")
    if added:
        parts.append(f"chapter{'s' if len(added) > 1 else ''} {', '.join(map(str, added))} written since")
    return ExportStatus(STALE, "; ".join(parts))


def exports(project) -> list[tuple[Path, ExportStatus]]:
    folder = Path(project.root) / "exports"
    if not folder.is_dir():
        return []
    return [(p, export_status(project, p)) for p in sorted(folder.iterdir())
            if p.is_file() and p.suffix.lower() in (".pdf", ".html") and not p.name.startswith(".")]


# --- checking an export's text -----------------------------------------------------------------------------

def _letters(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if ch.isascii() and ch.isalnum())


def _exported_text(data: bytes, kind: str) -> str:
    if kind == "pdf":
        import io

        from pypdf import PdfReader
        return "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages)
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


def _probes(text: str) -> list[str]:
    words = re.sub(r"[*_#>]", " ", text).split()
    if not words:
        return []
    return [_letters(" ".join(words[:PROBE_WORDS])), _letters(" ".join(words[-PROBE_WORDS:]))]


def verify(data: bytes, kind: str, project, only: int | None = None) -> None:
    """Every chapter (and scene) in the export, as written: the start and end of each scene are in its text."""
    exported = _letters(_exported_text(data, kind))
    probes, missing = 0, []
    for number in sorted(project.chapters):
        if only is not None and number != only:
            continue
        written = project.chapters[number]
        parts = written.scenes or ({0: written.text} if written.text.strip() else {})
        for scene, text in sorted(parts.items()):
            for probe in _probes(text):
                if not probe:
                    continue
                probes += 1
                if probe not in exported:
                    missing.append(f"chapter {number}" + (f", scene {scene}" if scene else ""))
    if probes and (probes - len(missing)) / probes < FOUND_ENOUGH:
        raise ExportError("The saved file doesn't hold the chapters as written (" + ", ".join(sorted(set(missing)))
                          + "), so it wasn't saved.")
