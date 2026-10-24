"""Sending one request: retries, fallback, logging and costs (PLAN.md §6.3–6.4, §10).

    primary model:  attempt -> transient failure? wait 2 s, 4 s ... and retry (3 attempts)
                    empty because reasoning hit the length limit? retry once with double max_tokens
                    refusal / empty / moderation / model not found? switch to the fallback
    fallback model: the same rules
    both failed:    stop and report; nothing is overwritten (D7)
    400/401/402:    stop at once; the other model wouldn't help (unless it is on another service:
                    OpenRouter, Venice, or one of the services added in Settings → Other services)

Progress is reported through on_event, so the UI can show streaming text,
"waiting 4 s before retrying", "switched to the fallback" and so on.
"""

from __future__ import annotations

import asyncio
import random
import time
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from devils_workshop.context.assemble import IMAGE_TOKENS
from devils_workshop.keepawake import awake
from devils_workshop.context.size import estimate_tokens
from devils_workshop.llm import calibration
from devils_workshop.llm import diagnostics
from devils_workshop.llm.catalog import Catalog, ModelInfo, filter_params
from devils_workshop.llm.costs import append_cost, attempt_cost, usage_numbers
from devils_workshop.llm.openrouter import OpenRouterClient, StreamResult
from devils_workshop.llm.pictures import fit_pictures, learn_limit, limit_from_error, picture_limit
from devils_workshop.llm.outcomes import (
    CANCELLED,
    EMPTY_LENGTH,
    EMPTY_WORDS,
    FATAL,
    OK,
    RUNAWAY,
    Outcome,
    TRANSIENT,
    classify,
    load_refusal_patterns,
    retry_delay,
    runaway,
)
from devils_workshop.logs import RequestLog
from devils_workshop.llm.services import OPENROUTER, service_name, service_of
from devils_workshop.settings import EFFORTS, WRITER, AppSettings, JobOptions, ProviderChoice, ProviderPrefs
from devils_workshop.wordcount import count_words

# Event kinds reported to on_event
ATTEMPT = "attempt"      # an attempt is starting
TEXT = "text"            # a piece of prose arrived
REASONING = "reasoning"  # the model is thinking (reasoning text, if returned)
KEEPALIVE = "keepalive"  # OpenRouter says it's still working
DISCARD = "discard"      # the partial text shown so far is being thrown away
WAIT = "wait"            # waiting before a retry
SWITCH = "switch"        # switching to the fallback model
WARNING = "warning"      # something worth showing (e.g. dropped settings)


@dataclass
class Event:
    kind: str
    model: str = ""
    text: str = ""
    message: str = ""
    seconds: float = 0.0


@dataclass
class AttemptRecord:
    number: int
    model: str
    outcome: str
    reason: str
    detail: str
    provider: str | None
    finish_reason: str | None
    words: int
    usage: dict
    cost: float
    cost_estimated: bool
    elapsed_s: float
    first_text_s: float | None
    max_tokens: int
    dropped_params: list[str]
    generation_id: str | None


@dataclass
class GenerationResult:
    ok: bool
    outcome: str
    text: str
    model: str | None
    provider: str | None
    used_fallback: bool
    attempts: list[AttemptRecord]
    total_cost: float
    warnings: list[str]
    failure: str | None
    log_folder: Path
    words: int = 0
    extra: dict = field(default_factory=dict)


def provider_body(prefs: ProviderPrefs, choice: ProviderChoice | None = None) -> dict | None:
    """OpenRouter's provider object: the app-wide preferences (privacy, and any lists from older settings
    files), with the model's own choice on top. "Never" lists add up; the model's prefer/only replace the
    app-wide order/allow lists."""
    body: dict = {}
    only, ignore, order = list(prefs.allow), list(prefs.block), list(prefs.order)
    if choice is not None:
        if choice.mode in ("price", "throughput", "latency"):
            body["sort"] = choice.mode
        if choice.mode == "mine":
            order = list(choice.prefer) or order
            only = list(choice.only) or only
            if not choice.allow_fallbacks and (order or only):
                body["allow_fallbacks"] = False
        ignore += [slug for slug in choice.never if slug not in ignore]
    if only:
        body["only"] = only
    if ignore:
        body["ignore"] = ignore
    if order:
        body["order"] = order
    if prefs.data_collection == "deny":
        body["data_collection"] = "deny"
    if prefs.require_parameters:
        body["require_parameters"] = True
    return body or None


def message_body(message) -> dict:
    """One message for the request: plain text, or text then pictures (as data URLs) when it has pictures."""
    images = getattr(message, "images", None)
    if not images:
        return {"role": message.role, "content": message.content}
    parts = [{"type": "text", "text": message.content}]
    parts += [{"type": "image_url", "image_url": {"url": image.data_url()}} for image in images]
    return {"role": message.role, "content": parts}


def picture_count_in(body: dict) -> int:
    """How many pictures a request body carries."""
    return sum(1 for m in body.get("messages", []) if isinstance(m.get("content"), list)
               for part in m["content"] if part.get("type") == "image_url")


def message_tokens(message) -> int:
    return estimate_tokens(message.content) + IMAGE_TOKENS * len(getattr(message, "images", None) or [])


def build_body(model_id: str, messages, options: JobOptions, info: ModelInfo | None,
               prefs: ProviderPrefs, choice: ProviderChoice | None = None) -> tuple[dict, list[str]]:
    """The request body for one model, and the names of settings left out because it doesn't support them.
    Pictures beyond what the model takes in one request are left out and named in the text (llm/pictures).
    options: the writer's or the planner's (temperature, thinking...); choice: the model's providers for it."""
    messages = fit_pictures(messages, picture_limit(model_id, info))
    params, dropped = filter_params(options.request_params(), info)
    body = {"model": model_id, "messages": [message_body(m) for m in messages], **params}
    max_tokens = options.max_output_tokens
    if info and info.max_completion_tokens:
        max_tokens = min(max_tokens, int(info.max_completion_tokens))
    body["max_tokens"] = max_tokens
    if options.effort:
        thinking = info.thinking if info else "unknown"
        if thinking in ("levels", "unknown") and (info is None or not info.supported_parameters
                                                  or info.supports("reasoning")):
            body["reasoning"] = {"effort": closest_effort(options.effort, info.effort_levels if info else [])}
        elif thinking == "switch":
            if options.effort == "none":
                body["reasoning"] = {"enabled": False}  # it can't take a level, but it can stop thinking
            # any other level: it thinks as it always does (thinking matters more than its exact depth)
        else:
            dropped.append("reasoning")
    provider = provider_body(prefs, choice) if service_of(model_id) == OPENROUTER else None
    if provider:
        body["provider"] = provider
    return body, dropped


def closest_effort(effort: str, levels: list[str]) -> str:
    """The thinking level to send: the one asked for, or the nearest the model takes. Between two equally near, the
    deeper one: "medium" on a model that offers low and high plans at high (more thinking matters more than its
    exact depth, and "none" is never chosen for a level that asked for thinking)."""
    if not levels or effort in levels:
        return effort
    order = list(EFFORTS)
    if effort not in order:
        return levels[0]
    known = [level for level in levels if level in order and (level != "none" or effort == "none")] or \
        [level for level in levels if level in order]
    if not known:
        return effort
    return min(known, key=lambda level: (abs(order.index(level) - order.index(effort)), -order.index(level)))


async def generate(messages, **options) -> GenerationResult:
    """Send one request (see _generate), keeping the PC awake until it is done."""
    with awake():
        return await _generate(messages, **options)


async def _generate(messages, *, client: OpenRouterClient, settings: AppSettings, catalog: Catalog,
                   log_root: Path, label: str, target_words: int,
                   primary: str | None = None, fallback: str | None = None,
                   job: str = "write", role: str = WRITER, chapter: int | None = None,
                   scene: int | None = None, calibration_dir: Path | None = None,
                   on_event: Callable[[Event], None] | None = None,
                   cancel: asyncio.Event | None = None,
                   sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                   jitter: Callable[[], float] = lambda: random.uniform(-0.2, 0.2),
                   secret: str | None = None,
                   patterns: list[re.Pattern] | None = None,
                   min_words: int = EMPTY_WORDS,
                   options: JobOptions | None = None) -> GenerationResult:
    """min_words: a reply shorter than this counts as empty (planning replies can be short).
    role: the writer or the planner; primary and fallback default to that role's models.
    options: how to ask, when not the role's own (the planner's check asks lightly)."""
    emit = on_event or (lambda event: None)
    patterns = patterns if patterns is not None else load_refusal_patterns(settings.refusal_patterns_file)
    log = RequestLog(log_root, label, secret=secret)
    log.write_prompt(messages, [message_tokens(m) for m in messages])

    options = options or settings.options_of(role)
    role_primary, role_fallback = settings.models_of(role)
    primary = primary or role_primary
    fallback = fallback if fallback is not None else role_fallback
    models = [primary] + ([fallback] if fallback and fallback != primary else [])
    attempts: list[AttemptRecord] = []
    warnings: list[str] = []
    last_reason: dict[str, str] = {}
    number = 0
    offline_waits = 0  # waits for the connection to come back (this PC's network, not the model)
    story = diagnostics.story_key(log_root)
    prompt_tokens = sum(message_tokens(m) for m in messages)
    begun = time.monotonic()

    def finish(ok: bool, outcome: str, text: str = "", failure: str | None = None,
               record: AttemptRecord | None = None) -> GenerationResult:
        result = GenerationResult(
            ok=ok, outcome=outcome, text=text, model=record.model if record else None,
            provider=record.provider if record else None,
            used_fallback=bool(record and record.model != primary), attempts=attempts,
            total_cost=sum(a.cost for a in attempts), warnings=warnings, failure=failure,
            log_folder=log.folder, words=count_words(text))
        log.write_json("meta.json", {
            "label": label, "job": job, "role": role, "chapter": chapter, "scene": scene, "target_words": target_words,
            "models": models, "ok": ok, "outcome": outcome, "model_used": result.model,
            "used_fallback": result.used_fallback, "total_cost": result.total_cost,
            "warnings": warnings, "failure": failure, "attempts": [asdict(a) for a in attempts],
        })
        if ok:
            log.write("response.md", text + "\n")
        diagnostics.write({"kind": "request", "story": story, "label": label, "job": job, "role": role,
                           "chapter": chapter, "scene": scene, "ok": ok, "outcome": outcome,
                           "attempts": len(attempts), "models": models, "model_used": result.model,
                           "elapsed_s": round(time.monotonic() - begun, 1), "cost": result.total_cost,
                           "failure": diagnostics.scrub(failure, secret) if failure else None})
        return result

    if not catalog.models:
        warnings.append("Couldn't check which settings the models support (no model list); sending them as set.")

    for index, model_id in enumerate(models):
        if index > 0:
            emit(Event(SWITCH, model_id, message=f"Switching to the fallback model {model_id}: "
                                                 f"{last_reason.get(models[index - 1], '')}"))
        info = catalog.get(model_id)
        if catalog.models and info is None:
            warnings.append(f"Model '{model_id}' isn't in {service_name(model_id, settings.services)}'s "
                            "model list")
        choice = settings.model(model_id).providers.of(role)
        body, dropped = build_body(model_id, messages, options, info, settings.providers, choice)
        if dropped:
            note = f"{model_id} doesn't support {', '.join(dropped)}; left out of the request."
            warnings.append(note)
            emit(Event(WARNING, model_id, message=note))

        transient_failures = 0
        cut_points: list[tuple[str | None, float]] = []  # how and when each failed attempt with this model ended
        doubled = refitted = rerun = lowered = False
        while True:
            number += 1
            emit(Event(ATTEMPT, model_id, message=f"Attempt {number} with {model_id}"))
            result = await client.stream_chat(
                body,
                on_text=lambda text, m=model_id: emit(Event(TEXT, m, text=text)),
                on_reasoning=lambda text, m=model_id: emit(Event(REASONING, m, text=text)),
                on_keepalive=lambda m=model_id: emit(Event(KEEPALIVE, m)),
                cancel=cancel,
            )
            outcome = classify(result, target_words, patterns, min_words)
            if outcome.kind == OK and role == WRITER:
                gone = runaway(result.text, target_words, result.finish_reason)
                if gone:
                    outcome = Outcome(RUNAWAY, gone)
            record = _record(number, model_id, outcome, result, info, body, dropped)
            attempts.append(record)
            _log_attempt(log, number, body, result)
            append_cost(log_root, _cost_line(label, job, chapter, scene, record))

            next_step = "the fallback model" if index + 1 < len(models) else "give up"

            def logged(decision: str, wait: float | None = None) -> None:  # the content-free diagnostics
                diagnostics.write(diagnostics.attempt_entry(
                    story=story, label=label, job=job, role=role, chapter=chapter, scene=scene, number=number,
                    model=model_id, body=body, prompt_tokens=prompt_tokens, result=result, outcome=outcome,
                    record=record, decision=decision, wait_s=wait, secret=secret))

            if outcome.kind == OK:
                if calibration_dir is not None and not any(getattr(m, "images", None) for m in messages):
                    chars = sum(len(m.content) for m in messages)
                    calibration.update(calibration_dir, model_id, chars, record.usage["prompt_tokens"])
                logged("accepted")
                return finish(True, OK, result.text, record=record)

            last_reason[model_id] = outcome.reason
            limit = limit_from_error((result.error or {}).get("message", "")) if result.status_code == 400 else None
            if outcome.kind == FATAL and limit is not None and not refitted:
                refitted = True  # too many pictures: remember the model's limit, and send again within it
                learn_limit(model_id, limit)
                fitted = build_body(model_id, messages, options, info, settings.providers, choice)[0]
                if picture_count_in(fitted) < picture_count_in(body):
                    body = {**body, "messages": fitted["messages"]}
                    note = (f"{model_id} takes at most {limit} picture(s) per request; sending again with "
                            f"{picture_count_in(body)} (the others are named in the text).")
                    warnings.append(note)
                    emit(Event(WARNING, model_id, message=note))
                    logged("send again with fewer pictures")
                    continue
            if result.text:
                emit(Event(DISCARD, model_id, message=outcome.reason))
            other_service = index + 1 < len(models) and service_of(models[index + 1]) != service_of(model_id)
            if outcome.kind == CANCELLED or (outcome.kind == FATAL and not other_service):
                logged("stop" if outcome.kind == CANCELLED else "give up (the fallback is on the same service)")
                return finish(False, outcome.kind, failure=outcome.reason, record=record)
            if outcome.kind == FATAL:
                logged(next_step + " (a key or credit problem with this service)")
                break  # a key or credit problem with one service: the fallback is on another one

            if outcome.kind == TRANSIENT and _offline(result) and offline_waits < len(OFFLINE_WAITS_S):
                # The connection itself failed at once: this PC is offline (or out of connections). Every model
                # would fail the same way, so the request waits for the network to come back, and tries again.
                delay = OFFLINE_WAITS_S[offline_waits]
                offline_waits += 1
                emit(Event(WAIT, model_id, message=f"No connection ({result.exception}). Waiting for the network: "
                                                    f"trying again in {delay:.0f} s", seconds=delay))
                logged("wait for the network", delay)
                await sleep(delay)
                continue
            if outcome.kind == TRANSIENT:
                transient_failures += 1
                repeated = _same_cut(result, cut_points)
                cut_points.append((result.error_kind, result.elapsed_s))
                if repeated:  # trying again the same way would only end the same way: the fallback, if any
                    last_reason[model_id] = f"{outcome.reason} ({repeated})"
                    note = f"{model_id}: {repeated}."
                    warnings.append(note)
                    emit(Event(WARNING, model_id, message=note))
                    logged(f"{next_step} ({repeated})")
                    break
                if transient_failures < settings.retries.attempts_per_model:
                    delay = retry_delay(transient_failures, result.retry_after, settings.retries.base_delay_s,
                                        settings.retries.max_delay_s, jitter())
                    if delay is not None:
                        emit(Event(WAIT, model_id, message=f"{outcome.reason}. Retrying in {delay:.0f} s",
                                   seconds=delay))
                        logged("retry", delay)
                        await sleep(delay)
                        continue
                logged(f"{next_step} (tried {transient_failures} times)")
                break

            if outcome.kind == RUNAWAY and not rerun:
                rerun = True
                note = f"{outcome.reason}. Writing it once more with {model_id}."
                warnings.append(note)
                emit(Event(WARNING, model_id, message=note))
                logged("write it again")
                continue
            lower = _lower_effort(body, info) if outcome.kind == EMPTY_LENGTH and not lowered else None
            if lower is not None:  # it thought until it ran out of room and never answered: think a level lower
                lowered = True
                before = body["reasoning"]["effort"]
                body = {**body, "reasoning": {**body["reasoning"], "effort": lower}}
                note = (f"{outcome.reason}. Asking {model_id} once more, thinking at {lower} instead of {before} "
                        "(more room would only make the same thinking longer).")
                warnings.append(note)
                emit(Event(WARNING, model_id, message=note))
                logged(f"think a level lower ({before} to {lower})")
                continue
            if outcome.kind == EMPTY_LENGTH and not doubled:
                doubled = True
                limit = int(info.max_completion_tokens) if info and info.max_completion_tokens else None
                bigger = body["max_tokens"] * 2 if limit is None else min(body["max_tokens"] * 2, limit)
                if bigger > body["max_tokens"]:
                    body = {**body, "max_tokens": bigger}
                    note = f"{outcome.reason}. Retrying {model_id} with max_tokens {bigger:,}."
                    warnings.append(note)
                    emit(Event(WARNING, model_id, message=note))
                    logged(f"more room ({bigger:,} tokens)")
                    continue
            logged(next_step)
            break  # moderation, model not found, refusal, empty: try the next model

    reasons = "; ".join(f"{model}: {reason}" for model, reason in last_reason.items())
    failure = (f"{'Both models' if len(models) > 1 else 'The model'} failed ({reasons}). "
               f"Nothing was saved. Details: {log.folder}")
    return finish(False, attempts[-1].outcome if attempts else FATAL, failure=failure,
                  record=attempts[-1] if attempts else None)


def _lower_effort(body: dict, info: ModelInfo | None = None) -> str | None:
    """The next thinking level below the one sent that the model takes ("high" to "medium", or to "low" on a model
    with no medium), or None if none was sent, or it is already at the lowest level that still thinks."""
    from devils_workshop.settings import EFFORTS
    effort = (body.get("reasoning") or {}).get("effort") if isinstance(body.get("reasoning"), dict) else None
    if effort not in EFFORTS:
        return None
    taken = [e for e in (info.effort_levels if info and info.effort_levels else EFFORTS) if e in EFFORTS]
    lower = [e for e in taken if EFFORTS.index("low") <= EFFORTS.index(e) < EFFORTS.index(effort)]
    return max(lower, key=EFFORTS.index) if lower else None


OFFLINE_WAITS_S = (5, 10, 20, 40, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60)  # about 15 minutes in all


def _offline(result) -> bool:
    """A request that couldn't even connect (no answer from the service at all, within a few seconds)."""
    return result.error_kind == "network" and result.exception == "ConnectError" and result.elapsed_s < 10


SAME_CUT_AFTER_S = 60  # a reply cut off after at least this long, twice at about the same moment: a time limit


def _same_cut(result: StreamResult, earlier: list[tuple[str | None, float]]) -> str:
    """Why trying the same model again would end the same way ("" if it might not): the stream was cut off at about
    the same moment as before (a time limit on the service's side), or the service was silent for as long as the
    app waits, twice."""
    if result.error_kind == "timeout" and any(kind == "timeout" for kind, _ in earlier):
        return "the service went silent for as long as the app waits, twice"
    if result.error_kind in ("incomplete", "stream", "network") and result.elapsed_s >= SAME_CUT_AFTER_S:
        for kind, at in earlier:
            if kind in ("incomplete", "stream", "network") and abs(result.elapsed_s - at) <= max(15.0, 0.1 * at):
                return (f"cut off after about {result.elapsed_s / 60:.1f} minutes again, as before: probably a "
                        "time limit on the service's side")
    return ""


def _record(number: int, model_id: str, outcome, result: StreamResult, info: ModelInfo | None,
            body: dict, dropped: list[str]) -> AttemptRecord:
    cost, estimated = attempt_cost(result.usage, info)
    return AttemptRecord(
        number=number, model=model_id, outcome=outcome.kind, reason=outcome.reason, detail=outcome.detail,
        provider=result.provider, finish_reason=result.finish_reason, words=count_words(result.text),
        usage=usage_numbers(result.usage), cost=cost, cost_estimated=estimated, elapsed_s=round(result.elapsed_s, 2),
        first_text_s=None if result.first_text_s is None else round(result.first_text_s, 2),
        max_tokens=body["max_tokens"], dropped_params=list(dropped), generation_id=result.generation_id,
    )


def _without_pictures(body: dict) -> dict:
    """The body as logged: each picture's data replaced by its size, so logs stay small and readable."""
    messages = []
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            content = [{**part, "image_url": {"url": f"<picture, {len(part['image_url']['url']) * 3 // 4 // 1024:,} KB>"}}
                       if part.get("type") == "image_url" else part for part in content]
        messages.append({**message, "content": content})
    return {**body, "messages": messages}


def _log_attempt(log: RequestLog, number: int, body: dict, result: StreamResult) -> None:
    log.write_json(f"attempt-{number}.request.json", _without_pictures(body))
    log.write(f"attempt-{number}.response.md", result.text + "\n")
    if result.reasoning:
        log.write(f"attempt-{number}.reasoning.md", result.reasoning + "\n")
    if result.error:
        log.write_json(f"attempt-{number}.error.json", {"kind": result.error_kind, "status": result.status_code,
                                                         "error": result.error})


def _cost_line(label: str, job: str, chapter, scene, record: AttemptRecord) -> dict:
    return {
        "time": datetime.now().isoformat(timespec="seconds"), "label": label, "job": job,
        "chapter": chapter, "scene": scene, "model": record.model, "provider": record.provider,
        "outcome": record.outcome, **record.usage, "cost": record.cost,
        "cost_estimated": record.cost_estimated, "generation_id": record.generation_id,
        "elapsed_s": record.elapsed_s, "first_text_s": record.first_text_s,  # for each provider's measured speed
    }
