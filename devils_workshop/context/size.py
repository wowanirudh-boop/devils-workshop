"""Estimating request size in tokens (PLAN.md §4.4).

There is no local tokenizer for the target models, so this is an estimate:
about 4 characters per token for English to start with. From milestone M3 on,
each model gets a correction factor learned from the token counts OpenRouter
reports. Nothing is ever cut automatically: over the limit, you get a warning
and choose what to untick.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_CHARS_PER_TOKEN = 4.0
DEFAULT_SIZE_LIMIT = 60_000


def estimate_tokens(text: str, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> int:
    return math.ceil(len(text) / chars_per_token) if text else 0


@dataclass
class SizeCheck:
    tokens: int
    limit: int
    context_window: int | None = None  # the model's real maximum, when known

    @property
    def over_limit(self) -> bool:
        return self.tokens > self.limit

    @property
    def exceeds_context(self) -> bool:
        """Too big for the model at all: the app refuses to send this."""
        return self.context_window is not None and self.tokens > self.context_window

    def message(self) -> str | None:
        if self.exceeds_context:
            return (f"About {self.tokens:,} tokens: more than this model can accept "
                    f"({self.context_window:,}). Untick some files before sending.")
        if self.over_limit:
            return (f"About {self.tokens:,} tokens, over your size limit of {self.limit:,}. "
                    "Untick some files, or send anyway.")
        return None
