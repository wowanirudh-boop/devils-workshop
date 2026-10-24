"""The OpenRouter HTTP client.

One streaming chat request at a time. This module only talks HTTP and reports
what happened; deciding whether to retry or switch models is outcomes.py and
generate.py. The API key goes in the Authorization header only, and headers
are never logged.
"""

from __future__ import annotations

import asyncio
import email.utils
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from devils_workshop import APP_NAME
from devils_workshop.llm.sse import COMMENT, iter_events

BASE_URL = "https://openrouter.ai/api/v1"
# read: the longest silence between two pieces of a reply. A model thinking without showing its thinking can
# be silent for many minutes before its first word; giving up sooner throws the work away and starts again.
TIMEOUTS = httpx.Timeout(connect=15.0, read=900.0, write=60.0, pool=15.0)
TOTAL_LIMIT_S = 60 * 60  # one attempt, in all: room for a long think and a long reply


_OPENERS, _CLOSERS = ("<think>", "<thinking>"), ("</think>", "</thinking>")


def _partial_tail(text: str, tags: tuple[str, ...]) -> int:
    """How many characters at the end of text could be the start of one of the tags (kept back until known)."""
    return max((n for tag in tags for n in range(1, len(tag)) if text.endswith(tag[:n])), default=0)


class ThinkFilter:
    """Moves <think>…</think> (or <thinking>) blocks that a model writes inside its reply over to its reasoning,
    as the reply streams in, so thinking never ends up in the story or breaks the planner's tags."""

    def __init__(self) -> None:
        self.inside = False
        self.buffer = ""

    def feed(self, chunk: str) -> tuple[str, str]:
        """(reply text, thinking) from the next piece of the reply."""
        self.buffer += chunk
        text, thought = [], []
        while self.buffer:
            tags = _CLOSERS if self.inside else _OPENERS
            lower = self.buffer.lower()
            found = min(((lower.find(tag), tag) for tag in tags if tag in lower), default=None)
            if found:
                at, tag = found
                (thought if self.inside else text).append(self.buffer[:at])
                self.buffer = self.buffer[at + len(tag):]
                self.inside = not self.inside
                continue
            keep = _partial_tail(lower, tags)
            (thought if self.inside else text).append(self.buffer[:len(self.buffer) - keep])
            self.buffer = self.buffer[len(self.buffer) - keep:]
            break
        return "".join(text), "".join(thought)

    def flush(self) -> tuple[str, str]:
        rest, self.buffer = self.buffer, ""
        return ("", rest) if self.inside else (rest, "")


def split_unopened_thinking(text: str) -> tuple[str, str]:
    """(reply, thinking): a reply with a closing </think> but no opening one (the model's template opened it)
    has its thinking before that tag."""
    lower = text.lower()
    for tag in _CLOSERS:
        at = lower.find(tag)
        if at != -1:
            return text[at + len(tag):].lstrip(), text[:at]
    return text, ""


@dataclass
class StreamResult:
    """Everything one attempt produced, success or not."""
    text: str = ""
    reasoning: str = ""
    finish_reason: str | None = None
    native_finish_reason: str | None = None
    usage: dict | None = None
    generation_id: str | None = None
    provider: str | None = None
    model: str | None = None
    status_code: int | None = None
    service: str = "OpenRouter"        # which service answered (Venice models go to Venice)
    error: dict | None = None          # {"code", "message", "metadata"} from the service
    error_kind: str | None = None      # "http" | "stream" | "network" | "timeout" | "cancelled" | "incomplete"
    retry_after: float | None = None
    saw_done: bool = False
    elapsed_s: float = 0.0
    first_text_s: float | None = None  # seconds until the first prose arrived
    keepalives: int = 0
    raw_errors: list[str] = field(default_factory=list)
    # When things happened (seconds from the start), for telling a slow model from a stalled stream
    headers_s: float | None = None          # the service answered (HTTP headers)
    first_reasoning_s: float | None = None  # the first of the model's thinking arrived (if it shows it)
    last_data_s: float | None = None        # the last piece of anything arrived (data or keep-alive)
    events: int = 0                         # pieces of data received
    exception: str | None = None            # the network error's type, when one ended it

    @property
    def failed(self) -> bool:
        return self.error_kind is not None


class OpenRouterClient:
    service = "OpenRouter"

    def __init__(self, api_key: str | None, base_url: str = BASE_URL,
                 transport: httpx.AsyncBaseTransport | None = None,
                 title: str = APP_NAME, referer: str | None = None,
                 timeouts: httpx.Timeout = TIMEOUTS, total_limit_s: float = TOTAL_LIMIT_S):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeouts = timeouts
        self._total_limit_s = total_limit_s
        self._headers = {"X-OpenRouter-Title": title}
        if referer:
            self._headers["HTTP-Referer"] = referer

    def _client(self) -> httpx.AsyncClient:
        headers = dict(self._headers)
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=self._timeouts,
                                 transport=self._transport)

    # --- model catalogue (no key needed) ----------------------------------------------------

    async def list_models(self) -> list[dict]:
        async with self._client() as client:
            response = await client.get("/models")
            response.raise_for_status()
            return list(response.json().get("data") or [])

    async def list_endpoints(self, model_id: str) -> list[dict]:
        """Providers serving a model. (Endpoint not verified in research; failures return [].)"""
        async with self._client() as client:
            response = await client.get(f"/models/{model_id}/endpoints")
            if response.status_code != 200:
                return []
            data = response.json().get("data") or {}
            return list(data.get("endpoints") or []) if isinstance(data, dict) else []

    # --- streaming chat ---------------------------------------------------------------------

    async def stream_chat(self, body: dict,
                          on_text: Callable[[str], None] | None = None,
                          on_reasoning: Callable[[str], None] | None = None,
                          on_keepalive: Callable[[], None] | None = None,
                          cancel: asyncio.Event | None = None) -> StreamResult:
        result = StreamResult(service=self.service)
        started = time.monotonic()
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        thinking = ThinkFilter()  # some models write their thinking into the reply, in <think> tags
        try:
            async with self._client() as client:
                async with client.stream("POST", "/chat/completions", json=self._payload(body)) as response:
                    result.status_code = response.status_code
                    result.headers_s = time.monotonic() - started
                    if response.status_code != 200:
                        await self._read_http_error(response, result)
                        return result
                    async for kind, payload in iter_events(response.aiter_lines()):
                        if cancel is not None and cancel.is_set():
                            result.error_kind = "cancelled"
                            break
                        result.last_data_s = time.monotonic() - started
                        if result.last_data_s > self._total_limit_s:
                            result.error_kind = "timeout"
                            result.error = {"message": f"The request took longer than "
                                                       f"{self._total_limit_s / 60:.0f} minutes"}
                            break
                        if kind == COMMENT:
                            result.keepalives += 1
                            if on_keepalive:
                                on_keepalive()
                            continue
                        if payload.strip() == "[DONE]":
                            result.saw_done = True
                            break
                        result.events += 1
                        self._read_chunk(payload, result, text_parts, reasoning_parts, started,
                                         on_text, on_reasoning, thinking)
                        if result.error_kind == "stream":
                            break
        except httpx.TimeoutException as exc:
            result.error_kind, result.exception = "timeout", type(exc).__name__
            silent = self._timeouts.read if isinstance(exc, httpx.ReadTimeout) else None
            result.error = {"message": f"No response from {self.service} in time ({type(exc).__name__}"
                                       + (f": nothing for {silent / 60:.0f} minutes" if silent else "") + ")"}
        except httpx.TransportError as exc:
            result.error_kind, result.exception = "network", type(exc).__name__
            result.error = {"message": f"Network problem: {type(exc).__name__}"}
        finally:
            text, reasoning = thinking.flush()
            text_parts.append(text)
            reasoning_parts.append(reasoning)
            result.text, before = split_unopened_thinking("".join(text_parts))
            result.reasoning = "".join(reasoning_parts) + before
            result.elapsed_s = time.monotonic() - started

        if result.error_kind is None and not result.saw_done and result.finish_reason is None:
            result.error_kind = "incomplete"
            result.error = {"message": "The stream ended before the reply was complete"}
        return result

    def _payload(self, body: dict) -> dict:
        """The JSON actually sent (Venice's client changes it into Venice's form)."""
        return {**body, "stream": True}

    def _read_chunk(self, payload: str, result: StreamResult, text_parts: list[str],
                    reasoning_parts: list[str], started: float,
                    on_text: Callable[[str], None] | None,
                    on_reasoning: Callable[[str], None] | None,
                    thinking: ThinkFilter | None = None) -> None:
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            result.raw_errors.append(payload[:200])
            return
        result.generation_id = chunk.get("id") or result.generation_id
        result.provider = chunk.get("provider") or result.provider
        result.model = chunk.get("model") or result.model
        if chunk.get("usage"):
            known = (result.usage or {}).get("cost")
            result.usage = {**chunk["usage"], **({"cost": known} if known is not None
                                                  and chunk["usage"].get("cost") is None else {})}
        if isinstance(chunk.get("cost"), dict) and chunk["cost"].get("usd") is not None:  # Venice's reported cost
            result.usage = {**(result.usage or {}), "cost": chunk["cost"]["usd"]}
        if chunk.get("error"):
            # Mid-stream failure: the HTTP status is already 200, so the error comes as a chunk.
            result.error_kind = "stream"
            result.error = _error_dict(chunk["error"])
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            text = delta.get("content")
            inline = ""
            if text and thinking is not None:
                text, inline = thinking.feed(text)
            if text:
                if result.first_text_s is None:
                    result.first_text_s = time.monotonic() - started
                text_parts.append(text)
                if on_text:
                    on_text(text)
            reasoning = (delta.get("reasoning") or delta.get("reasoning_content") or "") + inline  # OpenRouter, Venice
            if reasoning and result.first_reasoning_s is None:
                result.first_reasoning_s = time.monotonic() - started
            if reasoning:
                reasoning_parts.append(reasoning)
                if on_reasoning:
                    on_reasoning(reasoning)
            if choice.get("finish_reason"):
                result.finish_reason = choice["finish_reason"]
                result.native_finish_reason = choice.get("native_finish_reason")
                if choice["finish_reason"] == "error" and result.error_kind is None:
                    result.error_kind = "stream"
                    result.error = {"message": "The provider reported an error mid-stream"}

    async def _read_http_error(self, response: httpx.Response, result: StreamResult) -> None:
        result.error_kind = "http"
        result.retry_after = parse_retry_after(response.headers.get("retry-after"))
        body = await response.aread()
        try:
            data = json.loads(body)
            result.error = _error_dict(data.get("error") or data)
            if data.get("code") and not result.error.get("code"):  # Venice: {"error": "...", "code": "..."}
                result.error["code"] = data["code"]
        except (json.JSONDecodeError, AttributeError):
            result.error = {"message": body.decode("utf-8", "replace")[:300]}
        if not result.error.get("code"):
            result.error["code"] = response.status_code


@dataclass
class Endpoint:
    """One provider serving a model on OpenRouter (GET /models/{id}/endpoints)."""
    slug: str                          # what provider.order/only/ignore take, e.g. "deepinfra/fp4"
    provider: str                      # its name, e.g. "DeepInfra" (as requests report it)
    quantization: str | None = None
    prompt_price: float | None = None  # USD per token
    completion_price: float | None = None
    context_length: int | None = None
    max_completion_tokens: int | None = None
    uptime: float | None = None        # % over the last 30 minutes
    throughput: float | None = None    # output tokens per second, last 30 minutes (often not given)
    latency: float | None = None       # seconds to the first token, last 30 minutes (often not given)
    status: int | None = None          # 0 = working


def parse_endpoint(raw: dict) -> Endpoint:
    pricing = raw.get("pricing") or {}
    name = str(raw.get("provider_name") or raw.get("name") or "")
    return Endpoint(
        slug=str(raw.get("tag") or name.lower()), provider=name, quantization=raw.get("quantization") or None,
        prompt_price=_number(pricing.get("prompt")), completion_price=_number(pricing.get("completion")),
        context_length=_whole(raw.get("context_length")), max_completion_tokens=_whole(raw.get("max_completion_tokens")),
        uptime=_number(raw.get("uptime_last_30m")), throughput=_number(_p50(raw.get("throughput_last_30m"))),
        latency=_seconds(_number(_p50(raw.get("latency_last_30m")))), status=_whole(raw.get("status")))


def _p50(value):
    return value.get("p50") if isinstance(value, dict) else value


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _whole(value) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _seconds(value: float | None) -> float | None:
    """OpenRouter reports latency in milliseconds; a small number is already seconds."""
    if value is None:
        return None
    return value / 1000 if value > 60 else value


def _error_dict(error) -> dict:
    if isinstance(error, dict):
        return {"code": error.get("code"), "message": str(error.get("message", "")),
                "metadata": error.get("metadata") or {}}
    return {"message": str(error)}


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After is either seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max((when - datetime.now(timezone.utc)).total_seconds(), 0.0)
