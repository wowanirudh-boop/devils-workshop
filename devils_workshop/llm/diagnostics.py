"""Every attempt of every request, without its content, for finding out why requests fail.

One line per attempt in the app's folder (diagnostics/requests.jsonl, next to settings.yaml), and one per request
when it ends. A line has what the request was (its job, chapter and scene numbers, model, service and provider,
thinking level, room for the reply, its size in tokens), what happened (when the service answered, when thinking
and text began, when the last data came, keep-alives, how many pieces, how long the thinking and the text were in
characters, the tokens used, the finish reason, the HTTP status, the error's kind and the service's own short
message), and what the app decided (retry after how long, more room, the fallback model, give up).

Never any of the prompt, the reply or the thinking: only counts and timings. A story is named by a short hash of
its folder, so lines can be grouped by story without its name. The file is kept to about 5 MB (the older half is
moved to requests.1.jsonl).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime
from pathlib import Path

FOLDER, FILE, OLD = "diagnostics", "requests.jsonl", "requests.1.jsonl"
MAX_BYTES = 5_000_000
MESSAGE_CHARS = 200   # of the service's own error message (never the request's content)
_LOCK = threading.Lock()
log = logging.getLogger(__name__)


def path() -> Path:
    from devils_workshop.settings import settings_dir
    return settings_dir() / FOLDER / FILE


def story_key(log_root: Path | None) -> str:
    """A short, stable name for the story a request belongs to (its folder hashed), or "app"."""
    if log_root is None:
        return "app"
    root = Path(log_root)
    if root.name == "logs":
        root = root.parent
    from devils_workshop.settings import settings_dir
    if root.resolve() == settings_dir().resolve():
        return "app"
    return hashlib.sha256(str(root.resolve()).lower().encode("utf-8")).hexdigest()[:10]


def scrub(text: str | None, secret: str | None = None) -> str:
    text = (text or "")[:MESSAGE_CHARS]
    if secret and len(secret) >= 8:
        text = text.replace(secret, "[REDACTED]")
    return text


def write(entry: dict) -> None:
    """Add one line. Never fails the request it describes."""
    try:
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"time": datetime.now().isoformat(timespec="seconds"), **entry}, ensure_ascii=False)
        with _LOCK:
            if target.is_file() and target.stat().st_size > MAX_BYTES:
                target.replace(target.with_name(OLD))
            with target.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError:
        log.exception("Couldn't write the request diagnostics")


def attempt_entry(*, story: str, label: str, job: str, role: str, chapter, scene, number: int, model: str,
                  body: dict, prompt_tokens: int, result, outcome, record, decision: str, wait_s: float | None,
                  secret: str | None) -> dict:
    """The line for one attempt: what was asked, what happened, what the app does next. Counts and timings only."""
    usage = record.usage or {}
    error = result.error or {}

    def seconds(value):
        return None if value is None else round(value, 1)

    return {
        "kind": "attempt", "story": story, "label": label, "job": job, "role": role, "chapter": chapter,
        "scene": scene, "attempt": number, "model": model, "service": getattr(result, "service", ""),
        "provider": result.provider, "effort": (body.get("reasoning") or {}).get("effort")
        if isinstance(body.get("reasoning"), dict) else body.get("reasoning_effort"),
        "thinking_off": (body.get("reasoning") or {}).get("enabled") is False
        if isinstance(body.get("reasoning"), dict) else None,
        "max_tokens": body.get("max_tokens"), "prompt_tokens_estimate": prompt_tokens,
        "outcome": outcome.kind, "reason": scrub(outcome.reason, secret),
        "http_status": result.status_code, "error_kind": result.error_kind,
        "error_code": error.get("code"), "error_message": scrub(str(error.get("message") or ""), secret),
        "exception": getattr(result, "exception", None),
        "finish_reason": result.finish_reason, "native_finish_reason": result.native_finish_reason,
        "elapsed_s": seconds(result.elapsed_s), "headers_s": seconds(getattr(result, "headers_s", None)),
        "first_reasoning_s": seconds(getattr(result, "first_reasoning_s", None)),
        "first_text_s": seconds(result.first_text_s), "last_data_s": seconds(getattr(result, "last_data_s", None)),
        "keepalives": result.keepalives, "events": getattr(result, "events", None),
        "reasoning_chars": len(result.reasoning or ""), "text_chars": len(result.text or ""),
        "words": record.words, "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"), "reasoning_tokens": usage.get("reasoning_tokens"),
        "cost": record.cost, "generation_id": result.generation_id,
        "decision": decision, "wait_s": None if wait_s is None else round(wait_s, 1),
    }
