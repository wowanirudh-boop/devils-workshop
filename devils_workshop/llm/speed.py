"""How fast each provider has been for you: from the timings of your own requests (logs/costs.jsonl).

OpenRouter's own speed figures are often missing, so the providers screen also shows these: for one
model and provider, the median output speed (tokens per second, after the first text arrived) and the
median wait for the first text, over your recent successful requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import median

from devils_workshop.llm.costs import read_costs

MIN_TOKENS = 50  # shorter replies say little about speed
RECENT = 30      # the last this many requests per model and provider


@dataclass
class Speed:
    tokens_per_s: float
    first_text_s: float | None
    requests: int


def measured_speeds(log_roots: list[Path]) -> dict[tuple[str, str], Speed]:
    """(model, provider name) -> your median speed with it."""
    samples: dict[tuple[str, str], list[tuple[float, float | None]]] = {}
    for root in log_roots:
        for record in read_costs(Path(root)):
            if record.get("outcome") != "ok" or not record.get("provider") or not record.get("model"):
                continue
            tokens = int(record.get("completion_tokens") or 0)
            elapsed, first = record.get("elapsed_s"), record.get("first_text_s")
            if tokens < MIN_TOKENS or not elapsed:
                continue
            writing = float(elapsed) - float(first or 0)
            if writing <= 0:
                continue
            samples.setdefault((record["model"], record["provider"]), []).append(
                (tokens / writing, float(first) if first is not None else None))
    speeds = {}
    for key, values in samples.items():
        values = values[-RECENT:]
        firsts = [f for _, f in values if f is not None]
        speeds[key] = Speed(median(v for v, _ in values), median(firsts) if firsts else None, len(values))
    return speeds
