"""How many pictures one request may carry, and fitting a request's pictures to that.

Some services take only so many pictures per request: Venice takes one ("At most 1 image(s) may be
provided in one prompt"). A request with more is rejected outright (400), so before sending, the
pictures are cut to the model's limit: the most recent ones are kept (the author's latest message
first, and within a message its last pictures), and each picture left out is named in the text instead,
so the model knows it exists.

The limit is known for Venice; for other models there is none, until a service rejects a request for
having too many: then the limit in its message is remembered for that model until the app closes, and
the request is sent again within it (see generate.py).
"""

from __future__ import annotations

import re
from dataclasses import replace

from devils_workshop.context.render import attr
from devils_workshop.llm.venice import VENICE_MAX_IMAGES, is_venice

ATTACHED = "(attached below)"  # how a picture sent with the message is marked in its text
_LIMIT_IN_ERROR = re.compile(r"\b(?:at most|maximum of|max(?:imum)?|up to|no more than)\s+(\d+)\s+images?\b", re.I)

_learned: dict[str, int] = {}  # model -> the limit a service told us, for this session


def picture_tag(name: str, note: str) -> str:
    return f'<picture name="{attr(name)}">{note}</picture>'


def picture_limit(model_id: str, info=None) -> int | None:
    """The most pictures one request to this model may carry, or None for no known limit."""
    if model_id in _learned:
        return _learned[model_id]
    known = getattr(info, "max_images", None)
    if known is not None:
        return known
    return VENICE_MAX_IMAGES if is_venice(model_id) else None


def learn_limit(model_id: str, limit: int) -> None:
    _learned[model_id] = max(0, limit)


def forget_limits() -> None:
    _learned.clear()


def limit_from_error(message: str) -> int | None:
    """The limit in a "too many pictures" rejection ("At most 1 image(s) may be provided"), if it is one."""
    found = _LIMIT_IN_ERROR.search(message or "")
    return int(found.group(1)) if found else None


def not_sent_note(limit: int) -> str:
    if limit <= 0:
        return "(a picture; not sent: this model takes no pictures in a request)"
    return f"(a picture; not sent: this model takes at most {limit} per request)"


def picture_count(messages) -> int:
    return sum(len(getattr(m, "images", None) or []) for m in messages)


def fit_pictures(messages, limit: int | None) -> list:
    """The messages with at most `limit` pictures in all: the most recent are kept, the rest named in the text."""
    messages = list(messages)
    if limit is None or picture_count(messages) <= limit:
        return messages
    room, out = max(0, limit), []
    for message in reversed(messages):
        images = list(getattr(message, "images", None) or [])
        if not images:
            out.append(message)
            continue
        keep = min(room, len(images))
        room -= keep
        kept, dropped = images[len(images) - keep:], images[:len(images) - keep]
        content = message.content
        for image in dropped:
            marked, note = picture_tag(image.name, ATTACHED), picture_tag(image.name, not_sent_note(limit))
            content = content.replace(marked, note, 1) if marked in content else content + "\n\n" + note
        out.append(replace(message, content=content, images=kept))
    return list(reversed(out))
