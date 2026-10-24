"""Saving every request and response (PLAN.md §10, D26).

One folder per request, e.g. <project>/logs/2026-10-18/ch05-01/:
    prompt.md               the request as you'd read it
    attempt-1.request.json  exact body sent for attempt 1 (no headers, so no key)
    attempt-1.response.md   prose received in attempt 1 (partial text too)
    attempt-1.reasoning.md  only if the model returned reasoning
    response.md             the accepted text, if any
    meta.json               every attempt, its outcome, usage and cost

As a second safety net, the API key string is scrubbed from everything written.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from devils_workshop.project.files import write_text

REDACTED = "[REDACTED]"


class RequestLog:
    def __init__(self, log_root: Path, label: str, secret: str | None = None, now: datetime | None = None):
        now = now or datetime.now()
        day = log_root / now.strftime("%Y-%m-%d")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-") or "request"
        number = 1
        while (day / f"{safe}-{number:02d}").exists():
            number += 1
        self.folder = day / f"{safe}-{number:02d}"
        self.folder.mkdir(parents=True)
        self._secret = secret

    def _scrub(self, text: str) -> str:
        if self._secret and len(self._secret) >= 8:
            text = text.replace(self._secret, REDACTED)
        return text

    def write(self, name: str, text: str) -> Path:
        path = self.folder / name
        write_text(path, self._scrub(text))
        return path

    def write_json(self, name: str, data) -> Path:
        return self.write(name, json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")

    def write_prompt(self, messages, estimates: list[int] | None = None) -> None:
        parts = []
        for index, message in enumerate(messages):
            tokens = f" (about {estimates[index]:,} tokens)" if estimates else ""
            pictures = "".join(f"\n[picture: {image.name}, {len(image.data) // 1024:,} KB]"
                               for image in getattr(message, "images", None) or [])
            parts.append(f"===== {message.role}{tokens} =====\n{message.content}{pictures}")
        self.write("prompt.md", "\n\n".join(parts) + "\n")
