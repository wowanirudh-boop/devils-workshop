"""The phrases a book has already used too often, for the writer, who never sees the book's other scenes.

A scene written from its brief alone can't know that the last ten scenes all had "her pitch climbing": the app
counts the phrases of three to six words that what is written so far keeps using (leaving out the ordinary ones,
made of words like "she" and "of the", and the story's own words, such as a place or a garment) and sends the
writer the most used, to find fresh wording for. No request is needed.

The story's own words are what this scene's brief says, what the other briefs say outside quotes, and the facts
in the documents (in the writer's guide, its characters and world). A sound or a line the briefs keep quoting, or
the guide's own phrasing for how to write ("nothing left but noise"), is listed once the book has worn it out:
the prose copies them more than anything else.
"""

from __future__ import annotations

import re
from collections import Counter

MIN_USES = 3          # a phrase used this often is a tic, not a choice...
WORDS_PER_USE = 10_000  # ...or once per this many words of the book, if that is more
MAX_LISTED = 15
SIZES = (3, 4, 5, 6)
QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|(?<!\*)\*(?!\*)[^*\n]+(?<!\*)\*(?!\*)')  # said, or thought in italics
GUIDE_FACTS = ("characters", "world and setting")  # the writer's guide's sections of facts, not instructions
ORDINARY = set("""a an the and or but if then so as at by for from in into of off on onto out over to up with
without about after before through under again back down away there here this that these those it its it's he
him his she her hers they them their we us our you your i me my was were is are be been being had has have do did
does not no nor just only all any some more most very too than when while where what which who whom how one two
said says say go went going get got let like""".split())


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z’']+", text.lower())


def _overlap(one: str, other: str) -> bool:
    """The same phrase in another form: one contains the other, or they share two words end to start ("she could
    feel" and "could feel it")."""
    if one in other or other in one:
        return True
    a, b = one.split(), other.split()
    return a[-2:] == b[:2] or b[-2:] == a[:2]


def own_words(brief: str, briefs: list[str], documents: list[str], guide: str = "") -> str:
    """The story's own words, never a tic: this scene's brief; the other briefs, without what they quote or give in
    italics (lines to be said or thought, which the book can wear out); the
    documents; and the guide's characters and world, without its instructions on how to write."""
    sections = re.split(r"(?m)^## ", guide)[1:]
    facts = [part for part in sections if part.split("\n", 1)[0].strip().lower() in GUIDE_FACTS]
    return " ".join([brief, *(QUOTED.sub(" ", b) for b in briefs), *documents, *facts])


def overused(texts: list[str], own: str = "") -> list[str]:
    """The phrases used too often across these texts, most used first. own: the story's own words (own_words): a
    phrase found there is the story's, never a tic."""
    words = _words(" ".join(texts))
    owned = " " + " ".join(_words(own)) + " "
    counts: Counter = Counter()
    for size in SIZES:
        for at in range(len(words) - size + 1):
            gram = words[at:at + size]
            if sum(1 for w in gram if w not in ORDINARY) >= 2:
                counts[" ".join(gram)] += 1
    least = max(MIN_USES, len(words) // WORDS_PER_USE)
    kept: list[str] = []
    for gram, _ in sorted(((g, n) for g, n in counts.items() if n >= least), key=lambda x: (-x[1], -len(x[0]))):
        if f" {gram} " in owned:
            continue
        if any(_overlap(gram, other) for other in kept):  # one form of it is enough
            continue
        kept.append(gram)
        if len(kept) == MAX_LISTED:
            break
    return kept
