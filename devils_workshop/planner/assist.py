"""Ask AI to change: the planner's model edits one of your files as you ask, and you see the change first.

Any of your text files can be edited this way (story files, the planner's files, prompts, system prompts,
default instructions), but never the story's own text (chapters/): changes to the prose go to the writer
(project/scopes.py). Nothing is saved here: you see what would change, and accepting saves it.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

from devils_workshop.context.assemble import ChatMessage
from devils_workshop.context.render import attr
from devils_workshop.llm.generate import generate
from devils_workshop.planner.prompts import apply_edits, system_message
from devils_workshop.project.scopes import ASSIST, check
from devils_workshop.settings import PLANNER, AppSettings

EDIT_JOB = "edit_file"


@dataclass
class Proposal:
    ok: bool
    text: str = ""          # the file as it would be
    summary: str = ""       # what the model says it changed
    failure: str = ""
    cost: float = 0.0

    @property
    def changed(self) -> bool:
        return bool(self.text)


def read_reply(current: str, reply: str) -> tuple[str, str]:
    """(the new file, the summary) from a reply: a whole <file>, or <edit section> blocks applied to the file."""
    summary = re.search(r"<summary>(.*?)(?:</summary>|$)", reply, re.DOTALL | re.I)
    summary_text = summary.group(1).strip() if summary else ""
    whole = re.search(r"<file>\n?(.*?)\n?</file>", reply, re.DOTALL | re.I)
    if whole:
        return whole.group(1).rstrip() + "\n", summary_text
    edits = [(m.group(1).strip(), m.group(2).strip()) for m in
             re.finditer(r"<edit\s+section\s*=\s*[\"']([^\"']+)[\"']\s*>(.*?)</edit>", reply, re.DOTALL | re.I)]
    if edits:
        return apply_edits(current, edits)[0].rstrip() + "\n", summary_text
    raise ValueError("The reply had neither a <file> nor any <edit section> block")


async def propose_edit(root: Path | None, name: str, current: str, request: str, *, settings: AppSettings,
                       catalog, client, log_root: Path, **options) -> Proposal:
    """What the file would be with your change, made by the planner's models (with their options)."""
    if root is not None:
        check(ASSIST, name)
    messages = [system_message(root, EDIT_JOB),
                ChatMessage("user", f'<file name="{attr(name)}">\n{current}\n</file>\n\n'
                                    f"<request>\n{request.strip()}\n</request>")]
    label = "edit-" + re.sub(r"[^\w.-]+", "-", Path(name).stem)[:40]
    result = await generate(messages, client=client, settings=settings, catalog=catalog, log_root=log_root,
                            label=label, target_words=max(len(current.split()), 50), job="edit", role=PLANNER,
                            patterns=[], min_words=1, **options)
    if not result.ok:
        return Proposal(False, failure=result.failure or "the request failed", cost=result.total_cost)
    try:
        text, summary = read_reply(current, result.text)
    except ValueError as exc:
        return Proposal(False, failure=f"{exc}. The reply is in Activity.", cost=result.total_cost)
    if text.strip() == current.strip():
        return Proposal(True, "", summary or "Nothing needed to change.", cost=result.total_cost)
    return Proposal(True, text, summary, cost=result.total_cost)


def diff_lines(before: str, after: str, context: int = 2) -> list[tuple[str, str]]:
    """The change, line by line, as (kind, line): kind is "+", "-", " " (context) or "@" (a gap)."""
    lines = []
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=context):
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            lines.append(("@", "…"))
        else:
            lines.append((line[:1] or " ", line[1:]))
    return lines
