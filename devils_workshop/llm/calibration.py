"""Learning how many characters make a token, per model (PLAN.md §4.4).

The size estimate starts at 4 characters per token. After each successful
request, the estimate moves part of the way towards what OpenRouter actually
counted, so it gets more accurate the more you use a model.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from devils_workshop.context.size import DEFAULT_CHARS_PER_TOKEN
from devils_workshop.project.files import read_text, write_text

CALIBRATION_FILE = "calibration.yaml"
WEIGHT = 0.3            # how far each new measurement moves the estimate
SANE_RANGE = (1.5, 8.0)  # ignore measurements outside this (e.g. images, cached prompts)


def _load(folder: Path) -> dict:
    path = folder / CALIBRATION_FILE
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(read_text(path)[0]) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def chars_per_token(folder: Path, model_id: str) -> float:
    value = _load(folder).get(model_id)
    return float(value) if isinstance(value, (int, float)) else DEFAULT_CHARS_PER_TOKEN


def update(folder: Path, model_id: str, characters: int, prompt_tokens: int) -> float:
    current = chars_per_token(folder, model_id)
    if prompt_tokens <= 0 or characters <= 0:
        return current
    measured = characters / prompt_tokens
    if not SANE_RANGE[0] <= measured <= SANE_RANGE[1]:
        return current
    new = round(current + WEIGHT * (measured - current), 3)
    data = _load(folder)
    data[model_id] = new
    write_text(folder / CALIBRATION_FILE, yaml.safe_dump(data, sort_keys=True))
    return new
