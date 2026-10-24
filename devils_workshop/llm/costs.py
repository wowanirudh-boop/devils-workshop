"""Cost tracking (PLAN.md §10, D8: warn only, never block).

Every attempt adds one line to <project>/logs/costs.jsonl. The cost comes
from OpenRouter's usage report; if it's missing, it is estimated from the
model's live prices and marked "estimated".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from devils_workshop.llm.catalog import ModelInfo

COSTS_FILE = "costs.jsonl"


def usage_numbers(usage: dict | None) -> dict:
    usage = usage or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "reasoning_tokens": int(completion_details.get("reasoning_tokens") or 0),
        "cached_tokens": int(prompt_details.get("cached_tokens") or 0),
    }


def attempt_cost(usage: dict | None, info: ModelInfo | None) -> tuple[float, bool]:
    """(cost in USD, estimated?). Reasoning tokens are counted inside completion tokens."""
    if usage and usage.get("cost") is not None:
        try:
            return float(usage["cost"]), False
        except (TypeError, ValueError):
            pass
    numbers = usage_numbers(usage)
    if info is None or info.prompt_price is None or info.completion_price is None:
        return 0.0, True
    cost = numbers["prompt_tokens"] * info.prompt_price + numbers["completion_tokens"] * info.completion_price
    return cost, True


def append_cost(log_root: Path, record: dict) -> None:
    log_root.mkdir(parents=True, exist_ok=True)
    with (log_root / COSTS_FILE).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_costs(log_root: Path) -> list[dict]:
    path = log_root / COSTS_FILE
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a half-written line from a crash is skipped, not fatal
    return records


def folder_total(folder: Path) -> float:
    """Everything a story's folder (one version of a story) has spent, from its cost log."""
    return sum(float(record.get("cost") or 0.0) for record in read_costs(folder / "logs"))


@dataclass
class CostSummary:
    total: float = 0.0
    calls: int = 0
    estimated_calls: int = 0
    by_chapter: dict = field(default_factory=dict)   # chapter number (or "-") -> cost
    by_model: dict = field(default_factory=dict)
    by_job: dict = field(default_factory=dict)


def summarize(records: list[dict], chapter: int | None = None) -> CostSummary:
    summary = CostSummary()
    for record in records:
        if chapter is not None and record.get("chapter") != chapter:
            continue
        cost = float(record.get("cost") or 0.0)
        summary.total += cost
        summary.calls += 1
        summary.estimated_calls += bool(record.get("cost_estimated"))
        for bucket, key in ((summary.by_chapter, record.get("chapter") or "-"),
                            (summary.by_model, record.get("model") or "?"),
                            (summary.by_job, record.get("job") or "?")):
            bucket[key] = bucket.get(key, 0.0) + cost
    return summary


def format_summary(summary: CostSummary, title: str) -> str:
    lines = [title, f"Total: ${summary.total:.4f} over {summary.calls} request attempt(s)"]
    if summary.estimated_calls:
        lines.append(f"  ({summary.estimated_calls} estimated from prices: OpenRouter didn't report their cost)")
    for label, bucket in (("By chapter", summary.by_chapter), ("By model", summary.by_model),
                          ("By job", summary.by_job)):
        if bucket:
            lines.append(label)
            for key, cost in sorted(bucket.items(), key=lambda kv: str(kv[0])):
                lines.append(f"  {str(key):<40} ${cost:.4f}")
    return "\n".join(lines)
