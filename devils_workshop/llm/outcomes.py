"""Deciding what an attempt's result means (PLAN.md §6.4).

Every attempt ends in exactly one outcome:

    OK           prose came back
    TRANSIENT    network trouble, timeout, 408/429/5xx, mid-stream error -> retry same model
    MODERATION   HTTP 403 or finish_reason content_filter              -> switch to fallback
    UNAVAILABLE  HTTP 404 (model name not found)                        -> switch to fallback
    REFUSAL      short reply that starts like a refusal                 -> switch to fallback
    EMPTY        under 20 words                                         -> fallback
    EMPTY_LENGTH under 20 words and finish_reason "length"              -> retry once with double max_tokens
    FATAL        400, 401, 402 (bad request, key, credits)              -> stop
    CANCELLED    you pressed Stop                                       -> stop
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from devils_workshop.llm.openrouter import StreamResult
from devils_workshop.wordcount import count_words

OK = "ok"
TRANSIENT = "transient"
MODERATION = "moderation"
UNAVAILABLE = "unavailable"
REFUSAL = "refusal"
EMPTY = "empty"
EMPTY_LENGTH = "empty_length"
FATAL = "fatal"
CANCELLED = "cancelled"
RUNAWAY = "runaway"  # prose that ran far past its length, or fell into repeating itself (generate.py)

EMPTY_WORDS = 20
REFUSAL_MIN_WORDS = 150
REFUSAL_TARGET_SHARE = 0.25
REFUSAL_HEAD_CHARS = 400
TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}
MAX_RETRY_AFTER_S = 120.0

FATAL_MESSAGES = {
    400: "OpenRouter rejected the request (400)",
    401: "OpenRouter didn't accept the API key (401). Check it in Settings.",
    402: "Your OpenRouter account is out of credits (402).",
}
VENICE_FATAL_MESSAGES = {
    400: "Venice rejected the request (400)",
    401: "Venice didn't accept the API key, or the model needs Venice Pro (401). Check it in Settings → Venice AI.",
    402: "Your Venice balance is too low (402).",
}
SERVICE_FATAL_MESSAGES = {  # services added in Settings → Other services
    400: "{service} rejected the request (400)",
    401: "{service} didn't accept the API key (401). Check it in Settings → Other services.",
    402: "Your {service} balance is too low, or the model needs a plan you don't have (402).",
}


@dataclass
class Outcome:
    kind: str
    reason: str          # plain language, shown to you and logged
    detail: str = ""     # e.g. which refusal pattern matched


def load_refusal_patterns(path: str = "default") -> list[re.Pattern]:
    if path == "default":
        text = (resources.files("devils_workshop") / "defaults" / "refusal_patterns.txt").read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    patterns = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(re.compile(line, re.IGNORECASE))
    return patterns


def refusal_match(text: str, target_words: int, patterns: list[re.Pattern]) -> str | None:
    """The pattern that fired, if this short reply reads like a refusal."""
    if count_words(text) >= max(REFUSAL_MIN_WORDS, target_words * REFUSAL_TARGET_SHARE):
        return None
    head = text.strip()[:REFUSAL_HEAD_CHARS].replace("’", "'")
    for pattern in patterns:
        if pattern.search(head):
            return pattern.pattern
    return None


RUNAWAY_TIMES = 4      # a reply this many times its target length is broken, not long
LOOP_WORDS, LOOP_REPEATS = 8, 4  # the same 8 words 4 times over: a model stuck in a loop
BLOCK_WORDS = 40  # the same 40 words twice, word for word: the reply repeated a passage (or all) of itself
BLOCK_VARIED = 25  # ...when they are prose (at least this many different words), not a refrain


def runaway(text: str, target_words: int, finish_reason: str | None) -> str | None:
    """Why a piece of prose can't be kept although it came back (None: it can): it ran on past its length in a
    loop, ran to several times its length, or was cut off by the length limit far past its length."""
    words = re.findall(r"[\w'’-]+", text.lower())
    counts: dict[tuple, int] = {}
    long = target_words >= 100 and len(words) > target_words * 1.5  # a loop shows as length, too
    for at in range(0, max(len(words) - LOOP_WORDS, 0) if long else 0):
        gram = tuple(words[at:at + LOOP_WORDS])
        counts[gram] = counts.get(gram, 0) + 1
        if counts[gram] >= LOOP_REPEATS:
            return "The reply fell into repeating itself"
    seen: set[tuple] = set()
    for at in range(0, max(len(words) - BLOCK_WORDS + 1, 0)):  # the whole reply (or a long stretch) twice over
        block = tuple(words[at:at + BLOCK_WORDS])
        if len(set(block)) < BLOCK_VARIED:
            continue  # a refrain or a chant, not prose written twice
        if block in seen:
            return "The reply repeats a long passage of itself word for word"
        seen.add(block)
    if target_words >= 100 and len(words) > target_words * RUNAWAY_TIMES:
        return f"The reply ran to {len(words):,} words, far past its {target_words:,}"
    if finish_reason == "length" and target_words >= 100 and len(words) > target_words * 2:
        return "The reply ran past its length and was cut off"
    return None


def classify(result: StreamResult, target_words: int, patterns: list[re.Pattern],
             min_words: int = EMPTY_WORDS) -> Outcome:
    if result.error_kind == "cancelled":
        return Outcome(CANCELLED, "Stopped by you")

    if result.error_kind == "http":
        code = result.status_code or 0
        message = (result.error or {}).get("message", "")
        service = getattr(result, "service", "OpenRouter") or "OpenRouter"
        if code == 429 and (result.error or {}).get("code") == "insufficient_quota":  # e.g. Experiential Labs
            return Outcome(FATAL, f"Your {service} credit is used up, or a spend limit was reached (429). "
                                  f"{message}".strip())  # waiting won't help: the fallback is tried
        if code in TRANSIENT_STATUS:
            return Outcome(TRANSIENT, f"{service} returned {code}: {message}".strip(": "))
        if code == 403:
            return Outcome(MODERATION, f"Blocked by moderation or permissions (403): {message}".strip(": "))
        if code == 404:
            return Outcome(UNAVAILABLE, f"Model not found (404): {message}".strip(": "))
        if service == "Venice" and code in VENICE_FATAL_MESSAGES:
            return Outcome(FATAL, f"{VENICE_FATAL_MESSAGES[code]} {message}".strip())
        if service not in ("OpenRouter", "Venice") and code in SERVICE_FATAL_MESSAGES:  # an added service
            return Outcome(FATAL, f"{SERVICE_FATAL_MESSAGES[code].format(service=service)} {message}".strip())
        base = FATAL_MESSAGES.get(code, f"{service} returned {code}")
        return Outcome(FATAL, f"{base} {message}".strip())

    if result.error_kind in ("network", "timeout", "stream", "incomplete"):
        message = (result.error or {}).get("message", "")
        return Outcome(TRANSIENT, message or "The request failed part-way")

    if result.finish_reason == "content_filter":
        return Outcome(MODERATION, "The provider's content filter stopped the reply")

    words = count_words(result.text)
    pattern = refusal_match(result.text, target_words, patterns) if result.text.strip() else None
    if pattern:
        return Outcome(REFUSAL, "The reply looks like a refusal", pattern)
    if words < min_words:
        if result.finish_reason == "length":
            return Outcome(EMPTY_LENGTH, "The reply was empty: the length limit was reached, "
                                         "probably by the model's reasoning")
        return Outcome(EMPTY, "The reply was empty")
    return Outcome(OK, "OK")


def retry_delay(failed_attempts: int, retry_after: float | None, base_s: float, max_s: float,
                jitter: float) -> float | None:
    """Seconds to wait before retrying, or None to give up on this model.

    failed_attempts: how many attempts on this model have failed so far (1, 2, ...).
    jitter: a random number between -0.2 and 0.2.
    """
    if retry_after is not None:
        return retry_after if retry_after <= MAX_RETRY_AFTER_S else None
    delay = base_s * (2 ** (failed_attempts - 1))
    return min(delay * (1 + jitter), max_s)
