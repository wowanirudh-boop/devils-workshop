"""Reading a Server-Sent Events stream, line by line.

OpenRouter streams lines like:
    : OPENROUTER PROCESSING          <- keep-alive comment; never JSON
    data: {"choices": [...]}
    (blank line ends the event)
    data: [DONE]
"""

from __future__ import annotations

from collections.abc import AsyncIterator

COMMENT = "comment"
DATA = "data"


async def iter_events(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, str]]:
    """Yield (COMMENT, text) for keep-alives and (DATA, payload) for each event."""
    data: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data:
                yield DATA, "\n".join(data)
                data = []
            continue
        if line.startswith(":"):
            yield COMMENT, line[1:].strip()
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data.append(value)
        # other fields (event:, id:, retry:) aren't used by OpenRouter's chat stream
    if data:
        yield DATA, "\n".join(data)
