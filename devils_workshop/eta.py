"""How long what is left will take, from how long it has taken you: rough, rounded, corrected as a run goes.

Every request's time is in its story's logs/costs.jsonl (elapsed_s). A run of Auto-write measures its own pace
(minutes per 1,000 words, by the clock, with everything that overlaps) once it has written a scene, and keeps it
for the next run's first estimate (pace.json in the app's folder). A planning run adds up how long each step still
to come usually takes; a single request says how long that kind usually takes. Nothing here sends a request.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from devils_workshop.llm.costs import read_costs
from devils_workshop.settings import settings_dir

PACE_FILE = "pace.json"
FIRST_PACE = 2.5     # minutes per 1,000 words before any run has measured one (about 24,000 words an hour)
RECENT = 40          # the latest this many requests of a kind
STEP_GUESS = {"plan-guide": 120, "plan-sources": 150, "plan-architecture": 300, "plan-map": 60, "plan-canon": 180,
              "plan-scenes": 60, "plan-scene": 100, "plan-brainstorm": 180, "plan-import": 300}  # seconds


def saved_pace() -> float:
    try:
        return float(json.loads((settings_dir() / PACE_FILE).read_text(encoding="utf-8"))["minutes_per_1000"])
    except (OSError, ValueError, KeyError, TypeError):
        return FIRST_PACE


def save_pace(minutes_per_1000: float) -> None:
    try:
        (settings_dir() / PACE_FILE).write_text(json.dumps({"minutes_per_1000": round(minutes_per_1000, 3)}),
                                                encoding="utf-8")
    except OSError:
        pass  # only a better first guess next time


def typical(roots: list[Path], job: str) -> float:
    """How long a request of this kind usually takes you, in seconds (a guess until you have made some)."""
    times = []
    for root in roots:
        times += [float(r["elapsed_s"]) for r in read_costs(root / "logs")
                  if r.get("job") == job and r.get("outcome") == "ok" and r.get("elapsed_s")]
    return median(times[-RECENT:]) if times else STEP_GUESS.get(job, 120)


def duration(seconds: float) -> str:
    """5 min steps under an hour, 10 min steps above it."""
    minutes = max(1, round(seconds / 60))
    if minutes > 5:
        minutes = round(minutes / 5) * 5
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(round(minutes / 10) * 10, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def left_text(seconds: float | None, now: datetime | None = None) -> str:
    """ "About 1 h 20 min left · done around 02:30" ("" when nothing is left to estimate)."""
    if seconds is None or seconds <= 0:
        return ""
    if seconds < 180:
        return "A few minutes left"
    done = (now or datetime.now()) + timedelta(seconds=seconds)
    return f"About {duration(seconds)} left · done around {done:%H:%M}"


def usually_text(seconds: float) -> str:
    return f"usually about {duration(seconds)}"


def writing_left(remaining_words: int, written_words: int = 0, elapsed_s: float = 0.0) -> float:
    """Seconds to write remaining_words: at this run's own pace once it has written a scene (written_words in
    elapsed_s), at the last run's before that."""
    seconds_per_word = elapsed_s / written_words if written_words >= 600 else saved_pace() * 60 / 1000
    return max(remaining_words, 0) * seconds_per_word


def usual_times(roots: list[Path]) -> dict[str, float]:
    """How long each planning step usually takes you, in seconds, from these stories' logs (read once)."""
    return {job: typical(roots, job) for job in STEP_GUESS}


def story_roots(root: Path, recent: list[str], most: int = 12) -> list[Path]:
    """This story and your other recent ones: where the timings come from."""
    found = [root] + [Path(p) for p in recent if Path(p) != root]
    return [p for p in found[:most] if (p / "logs").is_dir()]


def planning_left(state, usual: dict[str, float]) -> float:
    """Seconds still to go for a planning run: each step still to come at how long that kind usually takes."""
    from devils_workshop.planner.state import READY
    registry, chapters = state.registry, state.chapters
    count = {"plan-guide": not state.guide.strip(),
             "plan-sources": registry.follows_reference() and bool(registry.reference) and not registry.sources,
             "plan-architecture": not chapters,
             "plan-map": registry.follows_reference() and not registry.map_mark,
             "plan-canon": not registry.canon_mark,
             "plan-scenes": sum(1 for c in chapters if not c.scenes) if chapters else 8,
             "plan-scene": sum(1 for c in chapters if not state.jit or c.number <= state.briefs_through
                               for s in c.scenes if s.status != READY) if chapters else 4}
    return sum(int(n) * usual.get(job, 120) for job, n in count.items())
