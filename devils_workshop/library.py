"""Your stories: the library on the All stories screen.

Every story you open or start is in it, in progress or completed (you mark a story completed; the library
shows it under Completed). Each one shows its genre, key characters, chapters and words, from its Story
settings and its chapters. Those details are kept in library.json in the app's folder and read again only
when a story changes, so a long list opens at once.

A completed story can have a new version: a copy of its foundation (settings, plan, decisions, notes, guide,
outline, prompts, and for your own files every file but the chapters), with no chapters, in a folder next to
it. Change the plan or not, and write it again; the first version stays as it is. The library shows a story's
versions together, as one story.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from devils_workshop.llm.costs import folder_total
from devils_workshop.project.files import read_text, write_text
from devils_workshop.project.story_settings import SETTINGS_FILE, is_story_folder, read_settings, update_settings
from devils_workshop.settings import settings_dir

CACHE_FILE = "library.json"
IN_PROGRESS, COMPLETED = "in progress", "completed"
SORTS = {"date": "Date", "title": "Title (A–Z)", "started": "Date started"}
NOT_COPIED = {"chapters", "logs", "sessions", ".history", "exports", ".import-review"}


@dataclass
class StoryMeta:
    path: str
    title: str = ""
    genre: str = ""
    characters: list[str] = field(default_factory=list)
    chapters_planned: int = 0
    chapters_written: int = 0
    words: int = 0
    cost: float = 0.0            # what this version has spent (logs/costs.jsonl)
    status: str = IN_PROGRESS
    completed_on: str = ""
    started: str = ""            # when the folder was made (YYYY-MM-DD)
    opened: str = ""             # when you last opened it (ISO time)
    version: int = 1
    first_version: str = ""      # the first version's folder, for the versions of one story
    missing: bool = False        # the folder can't be found any more
    planning: str = ""           # where planning stands, for a story still being planned

    @property
    def family(self) -> str:
        """The stories that are versions of each other share this."""
        return self.first_version or self.path

    @property
    def completed(self) -> bool:
        return self.status == COMPLETED

    def sort_date(self) -> str:
        return (self.completed_on or self.opened) if self.completed else (self.opened or self.started)


def _cache_path() -> Path:
    return settings_dir() / CACHE_FILE


def _read_cache() -> dict:
    try:
        return json.loads(read_text(_cache_path())[0]) if _cache_path().is_file() else {}
    except ValueError:
        return {}


def _write_cache(cache: dict) -> None:
    write_text(_cache_path(), json.dumps(cache, indent=1, ensure_ascii=False))


def _signature(folder: Path) -> str:
    """Changes when the story's settings, outline or chapters change, or it spends anything."""
    marks = []
    for item in (folder / SETTINGS_FILE, folder / "story.yaml", folder / "outline.md", folder / "Chapter outline.md",
                 folder / "Story plan.md", folder / "planner" / "state.json", folder / "logs" / "costs.jsonl"):
        if item.exists():
            marks.append(f"{item.name}:{item.stat().st_mtime_ns}")
    chapters = folder / "chapters"
    if chapters.is_dir():
        marks += [f"{p.name}:{p.stat().st_mtime_ns}" for p in sorted(chapters.glob("ch*.md"))]
    return "|".join(marks)


def plan_details(plan: str) -> tuple[str, list[str]]:
    """(genre, main characters' names) from a planner story's plan, for its Story settings."""
    sections = {m.group(1).strip().lower(): m.group(2) for m in
                re.finditer(r"^##\s+(.+?)\s*\n(.*?)(?=^##\s|\Z)", plan, re.M | re.S)}
    genre = next((text for name, text in sections.items() if name.startswith("genre")), "")
    genre = re.sub(r"\s*\(my choice\)", "", genre.strip().split("\n")[0]).strip(" -*.") if genre else ""
    people = next((text for name, text in sections.items() if name.startswith("main character")), "")
    names = re.findall(r"\*\*([^*]{2,40})\*\*", people) or re.findall(r"^\s*[-*]\s*([A-Z][\w' -]{1,30}?)(?:[,:(—-]|$)",
                                                                        people, re.M)
    return genre[:80], [n.strip() for n in names][:6]


def read_meta(folder: Path) -> StoryMeta:
    """A story's details, from its files (see story_meta for the cached ones)."""
    from devils_workshop.planner.state import DONE, PlannerState, is_planner_story
    from devils_workshop.project.project import NotAProjectError, load_project

    meta = StoryMeta(str(folder))
    try:
        project = load_project(folder)
    except NotAProjectError:
        meta.missing = True
        meta.title = folder.name
        return meta
    config = project.config
    meta.title, meta.genre, meta.status, meta.completed_on = config.title, config.genre, config.status, \
        config.completed_on
    meta.characters = list(config.characters) or _entry_characters(project)
    meta.chapters_planned = len(project.outline.chapters)
    meta.chapters_written = sum(1 for c in project.chapters.values() if c.text or c.scenes)
    meta.words = sum(c.words for c in project.chapters.values())
    meta.cost = folder_total(folder)
    data = read_settings(folder)
    meta.version = int(data.get("version") or 1) if str(data.get("version") or "1").isdigit() else 1
    first = str(data.get("first_version") or "").strip()
    meta.first_version = str((folder / first).resolve()) if first else ""
    try:
        meta.started = date.fromtimestamp((folder / SETTINGS_FILE).stat().st_ctime).isoformat()
    except OSError:
        meta.started = ""
    if is_planner_story(folder):
        state = PlannerState.load(folder)
        if state.stage != DONE:
            meta.planning = state.summary()
        if not meta.genre or not meta.characters:
            genre, names = plan_details(state.plan)
            meta.genre = meta.genre or genre
            meta.characters = meta.characters or names
    return meta


def _entry_characters(project) -> list[str]:
    names = [e.name for e in project.entries if e.type == "character"]
    pov = project.config.pov
    return ([pov] if pov and pov in names else []) + [n for n in names if n != pov]


def story_meta(path: str, cache: dict | None = None) -> StoryMeta:
    """A story's details, read again only if it changed since they were last read."""
    folder = Path(path)
    own_cache = cache is None
    cache = _read_cache() if cache is None else cache
    entry = cache.get(str(folder), {})
    if not folder.is_dir() or not is_story_folder(folder):
        meta = StoryMeta(str(folder), title=entry.get("meta", {}).get("title") or folder.name, missing=True)
        return meta
    signature = _signature(folder)
    if entry.get("signature") == signature and entry.get("meta"):
        meta = StoryMeta(**{k: v for k, v in entry["meta"].items() if k in StoryMeta.__dataclass_fields__})
    else:
        meta = read_meta(folder)
        entry = {**entry, "signature": signature, "meta": asdict(meta)}
        cache[str(folder)] = entry
        if own_cache:
            _write_cache(cache)
    meta.opened = entry.get("opened", "")
    return meta


def library(paths: list[str]) -> list[StoryMeta]:
    cache = _read_cache()
    before = json.dumps(cache, sort_keys=True)
    items = [story_meta(p, cache) for p in paths]
    if json.dumps(cache, sort_keys=True) != before:
        _write_cache(cache)
    return items


def opened(path: str) -> None:
    """Note when you opened a story, for sorting by date."""
    cache = _read_cache()
    cache.setdefault(str(Path(path)), {})["opened"] = datetime.now().isoformat(timespec="microseconds")
    _write_cache(cache)


def shelf(items: list[StoryMeta], completed: bool, sort: str = "date", search: str = "") -> list[StoryMeta]:
    """One tab of the library: in progress or completed, a story's versions in it shown as its latest one there
    (a completed first version stays under Completed while its second is in progress), searched (title, genre,
    characters) and sorted (date: newest first; title: A–Z; started: newest first)."""
    latest: dict[str, StoryMeta] = {}
    for item in items:
        if item.completed != completed or item.missing:
            continue
        held = latest.get(item.family)
        if held is None or item.version > held.version:
            latest[item.family] = item
    chosen = list(latest.values())
    if search.strip():
        words = search.lower().split()
        chosen = [m for m in chosen if all(w in " ".join([m.title, m.genre, *m.characters]).lower() for w in words)]
    if sort == "title":
        return sorted(chosen, key=lambda m: m.title.lower())
    if sort == "started":
        return sorted(chosen, key=lambda m: m.started, reverse=True)
    return sorted(chosen, key=lambda m: m.sort_date(), reverse=True)


def versions_of(items: list[StoryMeta], meta: StoryMeta) -> list[StoryMeta]:
    return sorted((m for m in items if m.family == meta.family and not m.missing), key=lambda m: m.version)


def story_cost(items: list[StoryMeta], meta: StoryMeta) -> float:
    """What a story has spent: all its versions together."""
    return sum(m.cost for m in versions_of(items, meta)) or meta.cost


def family_of(path: str) -> list[StoryMeta]:
    """Every version of the story in this folder (they sit side by side), first to latest."""
    folder = Path(path)
    data = read_settings(folder)
    first = Path(folder / data["first_version"]).resolve() if data.get("first_version") else folder.resolve()
    found = [m for m in library([str(p) for p in first.parent.iterdir() if p.is_dir()]) if m.family == str(first)]
    return sorted(found, key=lambda m: m.version) or [story_meta(str(folder))]


def set_completed(path: str, completed: bool) -> None:
    update_settings(Path(path), "completed" if completed else "in-progress",
                    status=COMPLETED if completed else IN_PROGRESS,
                    completed_on=date.today().isoformat() if completed else None)


def new_version(path: str) -> Path:
    """A new version of a story, next to it: its foundation without its chapters. The planner's briefs after the
    first chapter are written again, just in time, from what the new version's chapters turn out to be."""
    from devils_workshop.planner.state import PlannerState, is_planner_story

    source = Path(path)
    data = read_settings(source)
    first = Path(source / data["first_version"]).resolve() if data.get("first_version") else source.resolve()
    number = max([m.version for m in family_of(path)] + [int(data.get("version") or 1)]) + 1
    base = re.sub(r" \(version \d+\)$", "", source.name)
    target = source.parent / f"{base} (version {number})"
    while target.exists():
        number += 1
        target = source.parent / f"{base} (version {number})"
    shutil.copytree(source, target, ignore=lambda folder, names: [n for n in names if Path(folder) == source
                                                                   and n in NOT_COPIED])
    update_settings(target, "new-version", version=number, first_version=_relative(first, target),
                    status=IN_PROGRESS, completed_on=None)
    if is_planner_story(target):
        state = PlannerState.load(target)
        for chapter in state.chapters:
            if chapter.number > 1:
                for scene in chapter.scenes:
                    scene.brief, scene.status, scene.continuity = "", "waiting", ""
        state.jit, state.briefs_through, state.reconciled, state.reviews, state.review_plans = True, 1, [], {}, {}
        state.spent = 0.0  # this version's own spending (its logs start empty)
        state.save()
        from devils_workshop.planner.output import write_outputs
        write_outputs(state)
    return target


def _relative(first: Path, target: Path) -> str:
    try:
        return str(Path("..") / first.relative_to(target.parent))
    except ValueError:
        return str(first)
