"""The one real, low-cost OpenRouter check (PLAN.md §13, M3).

It answers the questions this build environment couldn't:
  1. Do the configured model names exist? What do they cost, and which settings do they support?
  2. Does the per-model providers list exist?
  3. One tiny request to the cheaper model (well under $0.01): does usage and cost come back,
     does provider "only" work, and does reasoning use up max_tokens?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from devils_workshop.context.assemble import ChatMessage
from devils_workshop.llm.catalog import Catalog, ModelInfo, parse_model
from devils_workshop.llm.costs import attempt_cost, usage_numbers
from devils_workshop.llm.generate import build_body
from devils_workshop.logs import RequestLog
from devils_workshop.settings import PLANNER, WRITER, AppSettings, ProviderPrefs
from devils_workshop.wordcount import count_words

TEST_MAX_TOKENS = 512  # at the listed prices, at most about $0.008 even on the pricier model
TEST_PROMPT = "Write one short sentence about a lighthouse at dawn."


@dataclass
class LiveReport:
    ok: bool = False
    lines: list[str] = field(default_factory=list)

    def add(self, text: str = "") -> None:
        self.lines.append(text)

    def text(self) -> str:
        return "\n".join(self.lines)


def _describe(info: ModelInfo) -> str:
    price_in, price_out = info.price_per_million()
    prices = "unknown prices" if price_in is None else f"${price_in:.2f} in / ${price_out:.2f} out per million tokens"
    context = f"{info.context_length:,}" if info.context_length else "unknown"
    max_out = f"{info.max_completion_tokens:,}" if info.max_completion_tokens else "unknown"
    return f"context {context}, max output {max_out}, {prices}"


async def live_check(client, settings: AppSettings, log_root: Path, api_key: str | None) -> LiveReport:
    report = LiveReport()
    report.add("Devil's Workshop: OpenRouter live check")
    report.add("")

    # 1. Model list
    try:
        raw = await client.list_models()
    except Exception as exc:
        report.add(f"1. Model list: FAILED ({type(exc).__name__}: {exc})")
        report.add("Result: can't reach OpenRouter. Check your internet connection.")
        return report
    catalog = Catalog([parse_model(m) for m in raw])
    report.add(f"1. Model list: {len(catalog.models)} models.")
    uses: dict[str, list[str]] = {}  # each model once, with what it is chosen for
    for name, use in ((settings.primary, "writer's main"), (settings.fallback, "writer's fallback"),
                      (settings.planner_primary, "planner's main"), (settings.planner_fallback, "planner's fallback")):
        if name:
            uses.setdefault(name, []).append(use)
    found: list[ModelInfo] = []
    for name, used_as in uses.items():
        info = catalog.get(name)
        if info is None:
            report.add(f"   {name} ({', '.join(used_as)}): NOT FOUND.")
            for similar in catalog.search(name.split("/")[-1].split("-")[0])[:5]:
                report.add(f"      similar: {similar.id}")
            continue
        found.append(info)
        report.add(f"   {name} ({', '.join(used_as)}): found. {_describe(info)}")
        options = settings.options_of(PLANNER if used_as[0].startswith("planner") else WRITER)
        wanted = sorted({*options.request_params(), *(["reasoning"] if options.reasoning else [])})
        for param in wanted:
            state = "supported" if info.supports(param) else "NOT supported (will be left out)"
            report.add(f"      {param}: {state}")
        report.add(f"      all supported settings: {', '.join(info.supported_parameters) or '(none listed)'}")
    if not found:
        report.add("Result: none of the chosen models exists. Pick names from the similar ones above.")
        return report

    cheapest = min(found, key=lambda m: (m.prompt_price or 1.0) + (m.completion_price or 1.0))

    # 2. Providers for that model
    providers: list[str] = []
    try:
        endpoints = await client.list_endpoints(cheapest.id)
    except Exception as exc:
        report.add(f"2. Providers list for {cheapest.id}: FAILED ({type(exc).__name__})")
    else:
        providers = [str(e.get("provider_name") or e.get("name") or "") for e in endpoints]
        providers = [p for p in providers if p]
        if providers:
            report.add(f"2. Providers serving {cheapest.id}: {', '.join(providers)}")
        else:
            report.add(f"2. Providers list for {cheapest.id}: not available (empty or unsupported)")

    # 3. One tiny request
    if not api_key:
        report.add("3. Test request: SKIPPED, no API key found.")
        report.add("   Set it first, e.g. in PowerShell:  $env:OPENROUTER_API_KEY = \"sk-or-...\"")
        report.add("Result: model checks done; run again with a key for the test request.")
        return report

    messages = [ChatMessage("system", "You are a concise writer."), ChatMessage("user", TEST_PROMPT)]
    role = PLANNER if uses[cheapest.id][0].startswith("planner") else WRITER
    prefs = ProviderPrefs(allow=[providers[0]] if providers else [])
    body, dropped = build_body(cheapest.id, messages, settings.options_of(role), cheapest, prefs)
    body["max_tokens"] = TEST_MAX_TOKENS
    asked = providers[0] if providers else None
    report.add(f"3. Test request to {cheapest.id} (max_tokens {TEST_MAX_TOKENS}"
               + (f", asking for provider {asked} via provider.only)" if asked else ")"))
    if cheapest.prompt_price is not None and cheapest.completion_price is not None:
        worst = 200 * cheapest.prompt_price + TEST_MAX_TOKENS * cheapest.completion_price
        report.add(f"   maximum possible cost of this request: ${worst:.4f}")
    if dropped:
        report.add(f"   left out as unsupported: {', '.join(dropped)}")

    log = RequestLog(log_root, "live-test", secret=api_key)
    log.write_json("request.json", body)
    result = await client.stream_chat(body)
    log.write("response.md", result.text + "\n")
    if result.reasoning:
        log.write("reasoning.md", result.reasoning + "\n")

    if result.failed:
        error = result.error or {}
        report.add(f"   FAILED: {result.error_kind} {result.status_code or ''} {error.get('message', '')}".rstrip())
        if error.get("metadata"):
            report.add(f"   details: {error['metadata']}")
        report.add(f"Result: the test request failed. Log: {log.folder}")
        return report

    numbers = usage_numbers(result.usage)
    cost, estimated = attempt_cost(result.usage, cheapest)
    report.add(f"   served by: {result.provider or '(not reported)'}"
               + ("" if not asked else ("  -> provider.only works" if result.provider == asked
                                        else "  -> provider.only did NOT pin the provider")))
    report.add(f"   reply: {count_words(result.text)} words, finish_reason={result.finish_reason}, "
               f"native_finish_reason={result.native_finish_reason}")
    report.add(f"   reply text: {result.text.strip()[:200]!r}")
    report.add(f"   tokens: prompt {numbers['prompt_tokens']}, completion {numbers['completion_tokens']} "
               f"(reasoning {numbers['reasoning_tokens']}), cached {numbers['cached_tokens']}")
    report.add(f"   usage reported: {'yes' if result.usage else 'NO'}; cost "
               + (f"${cost:.5f} (estimated from prices)" if estimated else f"${cost:.5f} (reported by OpenRouter)"))
    report.add(f"   reasoning text returned: {'yes' if result.reasoning else 'no'}; keep-alive comments: "
               f"{result.keepalives}")
    first = f"{result.first_text_s:.1f} s" if result.first_text_s is not None else "never"
    report.add(f"   first prose after {first}; total {result.elapsed_s:.1f} s")
    if not result.text.strip() and result.finish_reason == "length":
        report.add("   NOTE: empty reply with finish_reason=length: reasoning used up max_tokens.")
    report.ok = bool(result.usage)
    report.add("Result: " + ("OK." if report.ok else "the request worked but no usage was reported.")
               + f" Log: {log.folder}")
    return report


async def venice_check(client, settings: AppSettings, log_root: Path, api_key: str | None) -> LiveReport:
    """Venice: is the key accepted, and does one tiny request to its cheapest text model work?"""
    report = LiveReport()
    report.add("Devil's Workshop: Venice check")
    report.add("")
    try:
        raw = await client.list_models()
    except Exception as exc:
        report.add(f"1. Model list: FAILED ({type(exc).__name__}: {exc})")
        report.add("Result: can't reach Venice. Check your internet connection.")
        return report
    models = [parse_model(m) for m in raw]
    priced = [m for m in models if m.prompt_price is not None and m.completion_price is not None]
    report.add(f"1. Model list: {len(models)} text models.")
    if not priced:
        report.add("Result: Venice listed no text models with prices.")
        return report
    cheapest = min(priced, key=lambda m: m.prompt_price + m.completion_price)
    report.add(f"   cheapest: {cheapest.id}: {_describe(cheapest)}")
    if not api_key:
        report.add("Result: add your Venice key to send the test request.")
        return report
    messages = [ChatMessage("system", "You are a concise writer."), ChatMessage("user", TEST_PROMPT)]
    body, _ = build_body(cheapest.id, messages, settings.writer_options, cheapest, settings.providers)
    body["max_tokens"] = TEST_MAX_TOKENS
    report.add(f"2. Test request to {cheapest.id} (max_tokens {TEST_MAX_TOKENS})")
    log = RequestLog(log_root, "venice-test", secret=api_key)
    log.write_json("request.json", body)
    result = await client.stream_chat(body)
    log.write("response.md", result.text + "\n")
    if result.failed:
        error = result.error or {}
        report.add(f"   FAILED: {result.error_kind} {result.status_code or ''} {error.get('message', '')}".rstrip())
        report.add(f"Result: the test request failed. Log: {log.folder}")
        return report
    numbers = usage_numbers(result.usage)
    cost, estimated = attempt_cost(result.usage, cheapest)
    report.add(f"   reply: {count_words(result.text)} words: {result.text.strip()[:200]!r}")
    report.add(f"   tokens: prompt {numbers['prompt_tokens']}, completion {numbers['completion_tokens']}; cost "
               + (f"${cost:.5f} (estimated from prices)" if estimated else f"${cost:.5f} (reported by Venice)"))
    report.ok = bool(result.text.strip())
    report.add("Result: " + ("OK." if report.ok else "the request worked but the reply was empty.")
               + f" Log: {log.folder}")
    return report
