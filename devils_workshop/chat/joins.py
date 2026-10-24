"""Smoothing the joins between scenes written at the same time (D43).

When a chapter's scenes are written at the same time, each one is written from
the scene plan rather than from the text of the scene before it. One small
request then shows the model where each scene ends and how the next begins,
and asks for a new opening (the first paragraph or two) for scenes 2 onwards,
so each follows on from the one before. Nothing else in the scenes changes.
"""

from __future__ import annotations

import re

from devils_workshop.context.assemble import AssembledRequest, ChatMessage, assemble, load_instruction
from devils_workshop.context.render import attr, clean
from devils_workshop.context.size import DEFAULT_CHARS_PER_TOKEN, DEFAULT_SIZE_LIMIT, SizeCheck, estimate_tokens
from devils_workshop.project.markdown import is_blank
from devils_workshop.project.project import Project
from devils_workshop.wordcount import count_words

ENDING_PARAGRAPHS = 3
_OPENING_RE = re.compile(r'<opening\s+scene="?(\d+)"?\s*>(.*?)</opening>', re.DOTALL | re.IGNORECASE)


def paragraphs(text: str) -> tuple[list[str], str]:
    """The text's paragraphs and the separator between them (a blank line, or a single line break)."""
    text = text.strip()
    if re.search(r"\n\s*\n", text):
        return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()], "\n\n"
    return [p.strip() for p in text.split("\n") if p.strip()], "\n"


def opening_size(count: int) -> int:
    """How many paragraphs make a scene's opening: two in a longer scene, one in a short one, none in a
    one-paragraph scene (it would be rewritten entirely)."""
    return 2 if count >= 5 else 1 if count >= 2 else 0


def opening(text: str) -> str:
    parts, sep = paragraphs(text)
    return sep.join(parts[:opening_size(len(parts))])


def ending(text: str) -> str:
    parts, sep = paragraphs(text)
    return sep.join(parts[-ENDING_PARAGRAPHS:])


def to_smooth(scenes: dict[int, str]) -> list[int]:
    """The scenes whose opening can be rewritten: from the second on, with more than one paragraph."""
    numbers = sorted(scenes)
    return [n for n in numbers[1:] if opening(scenes[n])]


def prepare_join(project: Project, chapter: int, scenes: dict[int, str],
                 size_limit: int = DEFAULT_SIZE_LIMIT,
                 chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> AssembledRequest:
    """The request: the story's system prompt and style guide, then each scene's ending and opening."""
    base = assemble(project, chapter, 1, mode="scenes", together=True)  # for the chapter's sections and title
    outline = project.outline.chapter(chapter)
    system = [clean(text) for text in project.system_texts()]
    system.append(clean(load_instruction(project, "join")))

    parts = []
    style = project.bible.get("style_guide")
    if style and not is_blank(style):
        parts.append(f"<style_guide>\n{clean(style)}\n</style_guide>")
    title = f' title="{attr(outline.title)}"' if outline.title else ""
    lines = [f'<chapter number="{chapter}"{title}>']
    numbers = sorted(scenes)
    for number in numbers:
        plan = outline.scene(number)
        name = f' title="{attr(plan.title)}"' if plan and plan.title else ""
        lines.append(f'<scene number="{number}"{name}>')
        if plan and not is_blank(plan.text):
            lines += ["<plan>", clean(plan.text), "</plan>"]
        if number != numbers[0] and opening(scenes[number]):
            lines += ["<opening>", opening(scenes[number]), "</opening>"]
        if number != numbers[-1]:
            lines += ["<ending>", ending(scenes[number]), "</ending>"]
        lines.append("</scene>")
    lines.append("</chapter>")
    parts.append("\n".join(lines))
    wanted = to_smooth(scenes)
    parts.append("<task>\nRewrite the opening of " + _scene_list(wanted) + " so each follows on from the ending "
                 "of the scene before it. Reply with one <opening scene=\"N\"> block for each.\n</task>")

    messages = [ChatMessage("system", "\n\n".join(system)), ChatMessage("user", "\n\n".join(parts))]
    tokens = sum(estimate_tokens(m.content, chars_per_token) for m in messages)
    target = max(sum(count_words(opening(scenes[n])) for n in wanted), 50)
    return AssembledRequest(point=base.point, mode="scenes", chapter_title=outline.title, target_words=target,
                            pov=base.pov, person=base.person, tense=base.tense, sections=[], messages=messages,
                            size=SizeCheck(tokens, size_limit, None), warnings=[])


def parse_join(reply: str, scenes: dict[int, str]) -> tuple[dict[int, str], list[int]]:
    """({scene: new opening}, scenes whose new opening was left out because it doesn't look right).

    An opening is used only for a scene that had one shown, and only if its length is in proportion
    to the opening it replaces (a reply that ran on into the rest of the scene would duplicate it)."""
    found, rejected = {}, []
    numbers = sorted(scenes)
    for match in _OPENING_RE.finditer(reply):
        number, text = int(match.group(1)), match.group(2).strip()
        if number not in scenes or number == numbers[0] or not opening(scenes[number]):
            continue
        old = count_words(opening(scenes[number]))
        new = count_words(text)
        if not text or new > max(old * 3, old + 120) or new < min(old * 0.3, max(old - 10, 1)):
            rejected.append(number)
            continue
        found[number] = text
    return found, rejected


def apply_openings(scenes: dict[int, str], openings: dict[int, str]) -> dict[int, str]:
    """The scenes with their openings replaced."""
    result = dict(scenes)
    for number, new in openings.items():
        parts, sep = paragraphs(scenes[number])
        size = opening_size(len(parts))
        if not size:
            continue
        new_parts, _ = paragraphs(new)
        result[number] = sep.join(new_parts + parts[size:])
    return result


def _scene_list(numbers: list[int]) -> str:
    if len(numbers) == 1:
        return f"scene {numbers[0]}"
    return "scenes " + ", ".join(str(n) for n in numbers[:-1]) + f" and {numbers[-1]}"
