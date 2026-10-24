"""A fake OpenRouter for tests: scripted replies, every request recorded. No network."""

from __future__ import annotations

import json

import httpx

from devils_workshop.llm.openrouter import OpenRouterClient

TEST_KEY = "sk-or-v1-test-key-0123456789abcdef"

PROSE = ("The harbour clock had stopped in the night, and the town woke to a silence that "
         "felt like a held breath. Mara climbed the iron steps with her tool roll under one arm "
         "and the cold brass smell of the tower already in her mouth.")

MODELS = [
    {"id": "z-ai/glm-5.3", "name": "Z.ai: GLM 5.3", "context_length": 1048576,
     "pricing": {"prompt": "0.0000014", "completion": "0.0000044", "input_cache_read": "0.00000026"},
     "top_provider": {"context_length": 1048576, "max_completion_tokens": 131072},
     "supported_parameters": ["max_tokens", "temperature", "top_p", "reasoning", "stop"]},
    {"id": "moonshotai/kimi-k3", "name": "MoonshotAI: Kimi K3", "context_length": 1048576,
     "pricing": {"prompt": "0.0000026", "completion": "0.000013"},
     "top_provider": {"context_length": 1048576, "max_completion_tokens": 131072},
     "supported_parameters": ["max_tokens", "reasoning"]},
]


def chunk(text: str = "", finish: str | None = None, usage: dict | None = None, model: str = "z-ai/glm-5.3",
          provider: str = "Z.ai", reasoning: str = "") -> dict:
    delta = {}
    if text:
        delta["content"] = text
    if reasoning:
        delta["reasoning"] = reasoning
    data = {"id": "gen-123", "provider": provider, "model": model, "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish,
                         "native_finish_reason": finish}]}
    if usage is not None:
        data["usage"] = usage
    return data


def usage(prompt: int = 1200, completion: int = 400, reasoning: int = 100, cost: float | None = 0.0034) -> dict:
    data = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": reasoning}}
    if cost is not None:
        data["cost"] = cost
    return data


def sse(*chunks: dict, done: bool = True, keepalives: int = 1) -> httpx.Response:
    lines = [": OPENROUTER PROCESSING\n\n"] * keepalives
    lines += [f"data: {json.dumps(c)}\n\n" for c in chunks]
    if done:
        lines.append("data: [DONE]\n\n")
    return httpx.Response(200, content="".join(lines).encode("utf-8"))


def reply(text: str = PROSE, finish: str = "stop", model: str = "z-ai/glm-5.3", provider: str = "Z.ai",
          cost: float | None = 0.0034, reasoning: str = "", prompt_tokens: int = 1200) -> httpx.Response:
    """A complete streamed reply: the text in a few pieces, then finish_reason and usage."""
    words = text.split(" ")
    pieces = [" ".join(words[i:i + 8]) + (" " if i + 8 < len(words) else "") for i in range(0, len(words), 8)]
    chunks = [chunk(reasoning=reasoning, model=model, provider=provider)] if reasoning else []
    chunks += [chunk(piece, model=model, provider=provider) for piece in pieces if piece]
    chunks.append(chunk(finish=finish, usage=usage(prompt=prompt_tokens, cost=cost), model=model, provider=provider))
    return sse(*chunks)


def http_error(status: int, message: str = "error", retry_after: str | None = None,
               metadata: dict | None = None) -> httpx.Response:
    headers = {"retry-after": retry_after} if retry_after else {}
    return httpx.Response(status, json={"error": {"code": status, "message": message, "metadata": metadata or {}}},
                          headers=headers)


def broken_stream(text: str = "The harbour clock") -> httpx.Response:
    """Sends a little text, then the connection drops."""
    async def body():
        yield f"data: {json.dumps(chunk(text))}\n\n".encode()
        raise httpx.ReadError("connection reset")
    return httpx.Response(200, content=body())


class FakeOpenRouter:
    def __init__(self, models: list[dict] | None = None, endpoints: dict | None = None):
        self.models = MODELS if models is None else models
        self.endpoints = endpoints or {}
        self.replies: list = []
        self.requests: list[dict] = []   # {"path", "headers", "body"}
        self.responder = None            # body -> response (or a coroutine giving one), once the queue is empty

    def queue(self, *responses) -> FakeOpenRouter:
        self.replies.extend(responses)
        return self

    def respond(self, responder) -> FakeOpenRouter:
        """Answer by what is asked, for requests sent at the same time (their order isn't fixed)."""
        self.responder = responder
        return self

    def chat_bodies(self) -> list[dict]:
        return [r["body"] for r in self.requests if r["path"].endswith("/chat/completions")]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append({"path": path, "headers": dict(request.headers), "body": body})
        if path.endswith("/endpoints"):
            model_id = path.split("/models/", 1)[1].rsplit("/endpoints", 1)[0]
            if model_id not in self.endpoints:
                return httpx.Response(404, json={"error": {"message": "not found"}})
            return httpx.Response(200, json={"data": {"id": model_id, "endpoints": self.endpoints[model_id]}})
        if path.endswith("/models"):
            return httpx.Response(200, json={"data": self.models})
        if path.endswith("/chat/completions"):
            if not self.replies and self.responder is not None:
                return self.responder(body)  # MockTransport awaits it if it is a coroutine
            if not self.replies:
                raise AssertionError("FakeOpenRouter: no reply queued for this request")
            item = self.replies.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return httpx.Response(404)

    def client(self, key: str | None = TEST_KEY) -> OpenRouterClient:
        return OpenRouterClient(key, transport=httpx.MockTransport(self.handler))


# --- Venice ------------------------------------------------------------------------------------------

VENICE_KEY = "venice-test-key-0123456789abcdef"
VENICE_MODELS = [  # as Venice's GET /models?type=text lists them
    {"id": "mistral-31-24b", "object": "model", "type": "text", "owned_by": "venice.ai",
     "model_spec": {"name": "Mistral 3.1 24B", "availableContextTokens": 32768, "maxCompletionTokens": 8192,
                    "pricing": {"input": {"usd": 0.2}, "output": {"usd": 0.9}},
                    "capabilities": {"supportsVision": False, "supportsReasoning": False}}},
    {"id": "qwen3-vl-235b", "object": "model", "type": "text", "owned_by": "venice.ai",
     "model_spec": {"name": "Qwen 3 VL 235B", "availableContextTokens": 131072, "maxCompletionTokens": 32768,
                    "pricing": {"input": {"usd": 0.5}, "output": {"usd": 2.5}},
                    "capabilities": {"supportsVision": True, "supportsReasoning": True,
                                     "supportsReasoningEffort": True}}},
    {"id": "retired-model", "object": "model", "type": "text", "owned_by": "venice.ai",
     "model_spec": {"name": "Retired", "offline": True, "pricing": {"input": {"usd": 1}, "output": {"usd": 1}}}},
]


def venice_reply(text: str = PROSE, reasoning: str = "", usd: float | None = 0.0021) -> httpx.Response:
    """A streamed Venice reply: reasoning_content, the text, then a usage chunk (and Venice's cost)."""
    def piece(delta: dict, finish: str | None = None) -> dict:
        return {"id": "chatcmpl-v", "model": "mistral-31-24b",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    chunks = [piece({"reasoning_content": reasoning})] if reasoning else []
    words = text.split(" ")
    chunks += [piece({"content": " ".join(words[i:i + 8]) + (" " if i + 8 < len(words) else "")})
               for i in range(0, len(words), 8)]
    chunks.append(piece({}, "stop"))
    final = {"id": "chatcmpl-v", "model": "mistral-31-24b", "choices": [],
             "usage": {"prompt_tokens": 900, "completion_tokens": 120, "total_tokens": 1020}}
    if usd is not None:
        final["cost"] = {"usd": usd, "diem": 0}
    chunks.append(final)
    return sse(*chunks)


class FakeVenice(FakeOpenRouter):
    """Venice's API has the same paths as OpenRouter's, so the fake is the same with Venice's model list."""

    def __init__(self, models: list[dict] | None = None):
        super().__init__(models=VENICE_MODELS if models is None else models)

    def client(self, key: str | None = VENICE_KEY, include_venice_system_prompt: bool = False):
        from devils_workshop.llm.venice import VeniceClient
        return VeniceClient(key, transport=httpx.MockTransport(self.handler),
                            include_venice_system_prompt=include_venice_system_prompt)


# --- other OpenAI-compatible services (Settings → Other services) -------------------------------------

SERVICE_KEY = "fl-test-key-0123456789abcdef"
SERVICE_URL = "http://localhost:1234/v1"
SERVICE_MODELS = [  # as such services list them: some say a lot, some only the id
    {"id": "meta-llama/Llama-3.3-70B-Instruct", "object": "model", "owned_by": "featherless",
     "context_length": 32768, "max_completion_tokens": 4096},
    {"id": "qwen2.5-vl-72b", "object": "model", "capabilities": {"vision": True, "max_images": 2}},
    {"id": "nano/priced", "name": "Nano Priced", "context_length": 65536,
     "pricing": {"prompt": 0.5, "completion": 1.5, "currency": "USD", "unit": "per_million_tokens"}},
    {"id": "llama3:8b", "object": "model", "owned_by": "library"},
]


def service_reply(text: str = PROSE, model: str = "meta-llama/Llama-3.3-70B-Instruct",
                  reasoning: str = "") -> httpx.Response:
    """A plain OpenAI-style stream: the text, finish_reason, then a usage chunk (no provider, no cost)."""
    def piece(delta: dict, finish: str | None = None) -> dict:
        return {"id": "chatcmpl-s", "object": "chat.completion.chunk", "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    chunks = [piece({"reasoning_content": reasoning})] if reasoning else []
    words = text.split(" ")
    chunks += [piece({"content": " ".join(words[i:i + 8]) + (" " if i + 8 < len(words) else "")})
               for i in range(0, len(words), 8)]
    chunks.append(piece({}, "stop"))
    chunks.append({"id": "chatcmpl-s", "object": "chat.completion.chunk", "model": model, "choices": [],
                   "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}})
    return sse(*chunks, keepalives=0)


class FakeService(FakeOpenRouter):
    """An added OpenAI-compatible service: the same paths, its own model list."""

    def __init__(self, models: list[dict] | None = None):
        super().__init__(models=SERVICE_MODELS if models is None else models)
        self.keys: list[str | None] = []  # the key each client was made with

    def client(self, name: str = "Featherless", slug: str = "featherless", key: str | None = SERVICE_KEY,
               base_url: str = SERVICE_URL):
        from devils_workshop.llm.services import ServiceClient
        self.keys.append(key)
        return ServiceClient(name, slug, base_url, key, transport=httpx.MockTransport(self.handler))
