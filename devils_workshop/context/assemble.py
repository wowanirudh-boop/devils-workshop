"""Building the request for a chapter or scene (PLAN.md §4).

Each chapter is its own chat. The opening message carries the reference
material (style guide, world rules, story bible, the characters/places/lore
that matter here, active subplots) followed by the task. In scene mode, each
later scene is a new message in the same chat: the scene's task, plus any
character changes that start in that scene and any newly mentioned files.

Nothing from other chapters is ever included, and nothing keyed later in the
story than the point being written (see timeline.py).

With in_chapter_context "independent" (stories made by the planner), every scene is
its own chat with only the shared reference material and its own brief: no chapter
instructions, no other scene's brief or text. The planner writes each brief so it
stands on its own, so a chapter's scenes can be written at the same time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from devils_workshop.context.mentions import MentionFinder
from devils_workshop.context.render import attr, bullet, clean, render_entry, render_subplot, scene_updates
from devils_workshop.context.size import DEFAULT_CHARS_PER_TOKEN, DEFAULT_SIZE_LIMIT, SizeCheck, estimate_tokens
from devils_workshop.context.timeline import WritingPoint
from devils_workshop.project.entries import Entry
from devils_workshop.project.instructions import instruction
from devils_workshop.project.markdown import is_blank
from devils_workshop.project.outline import PENDING_BRIEF, OutlineChapter, Scene
from devils_workshop.project.own_files import extract_text
from devils_workshop.project.project import Project

BIBLE_LABELS = {"style_guide": "Style guide", "world_rules": "World rules", "story_bible": "Story bible"}
BIBLE_ORDER = ("style_guide", "world_rules", "story_bible")


class AssemblyError(Exception):
    """The request can't be built as asked (e.g. the chapter isn't in the outline)."""


@dataclass
class ContextSection:
    key: str          # stable id, e.g. "entry:characters/mara-venn.md"
    label: str        # e.g. "Mara Venn"
    kind: str         # "bible" | "default" | "document" | "entry" | "subplot" | "file"
    reason: str       # why it is (or isn't) included
    text: str = ""
    included: bool = True
    turn: int = 1     # which message of the chapter chat it belongs to

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text) if self.included else 0


IMAGE_TOKENS = 1_000  # a rough size for one picture in a request (providers count them differently)
BRIEF_ENDING = re.compile(r"\*\*Ending:\*\*\s*(.+?)(?=\n\s*\*\*[^*\n]{1,40}:\*\*|\Z)", re.S)  # a planner brief's
BRIEF_BEATS = re.compile(r"\*\*What happens:\*\*(.+?)(?=\n\s*\*\*[^*\n]{1,40}:\*\*|\Z)", re.S)
BEAT_LINE = re.compile(r"(?m)^\s*(\d{1,2})[.)]\s+(.*)$")
BEAT_SHARE = re.compile(r"about ([\d,]+) words")
BEAT_ROOM = {"full": 3.0, "some": 1.5, "brief": 0.5}  # a beat's weight when the brief gives it no share of words
PART_WORDS = 1_500  # about as much as a writer writes well in one reply: a longer scene is written in parts


@dataclass
class ScenePart:
    """One reply's worth of a long scene: its beats, as the brief numbers them, and its words."""
    first: int
    last: int
    words: int


def scene_parts(brief: str, target: int) -> list[ScenePart]:
    """A scene longer than a writer writes well in one reply, split at its beats into parts of about PART_WORDS,
    weighed by the beats' own shares of the words (or the room each is marked for); [] when it is one reply."""
    if target < PART_WORDS * 4 // 3:
        return []
    section = BRIEF_BEATS.search(brief)
    beats = [(int(m.group(1)), m.group(2)) for m in BEAT_LINE.finditer(section.group(1) if section else brief)]
    count = min(-(-target // PART_WORDS), len(beats) // 2)  # every part at least two beats
    if count < 2:
        return []
    weights = []
    for _, line in beats:
        share = BEAT_SHARE.search(line[:60])
        room = re.match(r"\((full|some|brief)", line.strip())
        weights.append(float(share.group(1).replace(",", "")) if share else BEAT_ROOM[room.group(1) if room else "some"])
    total, cuts, sofar = sum(weights), [], 0.0
    for at, weight in enumerate(weights):
        sofar += weight
        goal = total * (len(cuts) + 1) / count
        if len(cuts) < count - 1 and sofar >= goal:  # cut at whichever side of this beat is nearer the goal
            nearer_before = goal - (sofar - weight) < sofar - goal and at > (cuts[-1] if cuts else 0)
            cuts.append(at if nearer_before else at + 1)
    cuts = [c for c in cuts if 0 < c < len(beats)]
    bounds = [0, *cuts, len(beats)]
    return [ScenePart(beats[a][0], beats[b - 1][0], max(round(target * sum(weights[a:b]) / total / 10) * 10, 100))
            for a, b in zip(bounds, bounds[1:])]


@dataclass
class ImageRef:
    """A picture sent with a message, for models that can see pictures (the story planner's references)."""
    name: str
    mime: str
    data: bytes

    def data_url(self) -> str:
        import base64
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"


@dataclass
class ChatMessage:
    role: str         # "system" | "user" | "assistant"
    content: str
    images: list[ImageRef] = field(default_factory=list)  # sent after the text; content stays plain text


@dataclass
class Overrides:
    """Choices made in the review screen. They apply to one request only."""
    exclude: set[str] = field(default_factory=set)       # section keys to leave out
    include: set[str] = field(default_factory=set)       # left-out section keys to put back
    extra_files: list[str] = field(default_factory=list)  # project-relative paths to add


@dataclass
class AssembledRequest:
    point: WritingPoint
    mode: str
    chapter_title: str
    target_words: int
    pov: str | None
    person: str
    tense: str
    sections: list[ContextSection]
    messages: list[ChatMessage]
    size: SizeCheck
    warnings: list[str]
    parts: list[ScenePart] = field(default_factory=list)  # a long scene written in parts: this asks for the first

    def included(self) -> list[ContextSection]:
        return [s for s in self.sections if s.included]

    def left_out(self) -> list[ContextSection]:
        return [s for s in self.sections if not s.included]

    def full_text(self) -> str:
        return "\n\n".join(m.content for m in self.messages)


def load_instruction(project: Project, job: str) -> str:
    """A job's instruction: the story's own copy, else yours for every story, else the built-in one."""
    return instruction(project.root, job)


def assemble(project: Project, chapter: int, scene: int | None = None, mode: str | None = None,
             notes: str = "", overrides: Overrides | None = None,
             size_limit: int = DEFAULT_SIZE_LIMIT, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
             context_window: int | None = None, together: bool = False) -> AssembledRequest:
    """Build the full request for a chapter (whole mode) or one of its scenes (scene mode).

    For scene k > 1 with in_chapter_context "full", the chat is rebuilt from
    the scenes already written in chapters/chNN.md, exactly as it would be sent.

    together=True: the scene is written at the same time as the chapter's other
    scenes, so it gets its own fresh chat with the earlier scenes as beats.
    """
    plan = _Plan(project, chapter, mode, overrides or Overrides())
    plan.together = together
    plan.beats_only = plan.beats_only or together
    if plan.mode == "whole":
        if scene is not None:
            raise AssemblyError(f"Chapter {chapter} is in whole-chapter mode; a scene can't be chosen")
        return plan.finish(plan.opening(WritingPoint(chapter), notes), WritingPoint(chapter),
                           size_limit, chars_per_token, context_window)

    scene = scene or 1
    if plan.chapter.scene(scene) is None:
        raise AssemblyError(f"Chapter {chapter} has no scene {scene} in the outline")
    if plan.chapter.scene(scene).text.strip().startswith(PENDING_BRIEF[:20]):
        raise AssemblyError(f"Chapter {chapter}'s scene briefs aren't written yet: the planner writes them just "
                            "before the chapter is written (Write the next chapter, in the Reader or Auto-write)")
    point = WritingPoint(chapter, scene)
    if scene == 1 or plan.beats_only or plan.independent:
        messages = plan.opening(point, notes)
    else:
        messages = plan.opening(WritingPoint(chapter, 1), "")
        written = project.chapters.get(chapter)
        for number in range(2, scene + 1):
            earlier = written.scenes.get(number - 1) if written else None
            if not earlier:
                raise AssemblyError(f"Scene {number - 1} of chapter {chapter} hasn't been written yet; "
                                    "scenes are written in order")
            messages.append(ChatMessage("assistant", earlier))
            messages.append(plan.next_scene(number, notes if number == scene else "",
                                            [m.content for m in messages if m.role == "assistant"]))
    if plan.independent:
        from devils_workshop.context.guard import GuardError, check_scene_request
        own = plan.chapter.scene(scene)
        try:
            check_scene_request(project.root, chapter, scene, own.title, own.text, plan.sections,
                                "\n\n".join(m.content for m in messages))
        except GuardError as exc:
            raise AssemblyError(str(exc)) from exc
    return plan.finish(messages, point, size_limit, chars_per_token, context_window)


def assemble_followup(project: Project, chapter: int, scene: int, history: list[ChatMessage],
                      sent_keys: set[str], notes: str = "", overrides: Overrides | None = None,
                      size_limit: int = DEFAULT_SIZE_LIMIT, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
                      context_window: int | None = None) -> AssembledRequest:
    """Scene mode: the message asking for scene `scene`, added to the chapter's saved chat.

    `history` is the chat as actually sent so far; `sent_keys` the files already in it.
    """
    plan = _Plan(project, chapter, "scenes", overrides or Overrides())
    if plan.chapter.scene(scene) is None:
        raise AssemblyError(f"Chapter {chapter} has no scene {scene} in the outline")
    plan.in_chat = set(sent_keys)
    written = [m.content for m in history if m.role == "assistant"]
    message = plan.next_scene(scene, notes, written)
    return plan.finish(list(history) + [message], WritingPoint(chapter, scene), size_limit, chars_per_token,
                       context_window)


def assemble_change(project: Project, chapter: int, mode: str, opening: list[ChatMessage], current_text: str,
                    request: str, earlier_requests: list[str], sent_keys: set[str], scene: int | None = None,
                    overrides: Overrides | None = None, size_limit: int = DEFAULT_SIZE_LIMIT,
                    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
                    context_window: int | None = None) -> AssembledRequest:
    """A change request in the chapter's chat (D30): the original setup, the current draft,
    your earlier change requests (so they aren't undone) and the new one."""
    plan = _Plan(project, chapter, mode, overrides or Overrides())
    plan.in_chat = set(sent_keys)
    point = WritingPoint(chapter, scene) if scene else WritingPoint(chapter)
    scene_obj = plan.chapter.scene(scene) if scene else None
    if scene and scene_obj is None:
        raise AssemblyError(f"Chapter {chapter} has no scene {scene} in the outline")

    parts = []
    new = [s for s in plan._select_entries(point, [request], set(), plan.pov(scene_obj), turn=2, only_new=True)
           if s.included]
    if new:
        parts.append("<new_reference>\n" + "\n\n".join(s.text for s in new) + "\n</new_reference>")
    lines = ["<change_request>"]
    if earlier_requests:
        lines.append("Earlier changes to this chapter (already made; keep them):")
        lines += [f"{n}. {clean(text)}" for n, text in enumerate(earlier_requests, 1)]
        lines.append("")
    lines += ["Change now:", clean(request), ""]
    if scene_obj is not None:
        name = f' ("{scene_obj.title}")' if scene_obj.title else ""
        lines.append(f"Write the complete new version of scene {scene}{name} only, with this change. "
                     "Keep everything else as it is unless the change needs it. Reply with that scene's prose only.")
    else:
        lines.append("Write the complete new version of the chapter with this change. Keep everything else "
                     "as it is unless the change needs it. Reply with the prose only.")
    lines.append("</change_request>")
    parts.append("\n".join(lines))
    messages = list(opening) + [ChatMessage("assistant", current_text), ChatMessage("user", "\n\n".join(parts))]
    return plan.finish(messages, point, size_limit, chars_per_token, context_window)


CONTINUE_MESSAGE = ("<continue>\nYour reply stopped before the end of the instructions. Continue from exactly where "
                    "it stopped, without repeating anything, until the instructions for this {unit} are complete. "
                    "Reply with the continuation only.\n</continue>")


def assemble_continue(project: Project, chapter: int, mode: str, history: list[ChatMessage],
                      scene: int | None = None, size_limit: int = DEFAULT_SIZE_LIMIT,
                      chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
                      context_window: int | None = None) -> AssembledRequest:
    """Ask the model to carry on a reply that stopped short. `history` ends with that reply."""
    plan = _Plan(project, chapter, mode, Overrides())
    point = WritingPoint(chapter, scene) if scene else WritingPoint(chapter)
    unit = f"scene {scene}" if scene else "chapter"
    messages = list(history) + [ChatMessage("user", CONTINUE_MESSAGE.format(unit=unit))]
    return plan.finish(messages, point, size_limit, chars_per_token, context_window)


def assemble_part(project: Project, chapter: int, scene: int, history: list[ChatMessage], part: ScenePart,
                  ending: str = "", size_limit: int = DEFAULT_SIZE_LIMIT,
                  chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> AssembledRequest:
    """The next part of a scene written in parts (scene_parts). `history` ends with the parts written so far;
    ending: the brief's Ending, for the last part."""
    plan = _Plan(project, chapter, "scenes", Overrides())
    stop = (f"This is the last part: end the scene exactly here, and write nothing after it: {ending}" if ending
            else f"Stop at the end of beat {part.last}: the next part carries on from there.")
    text = (f"<continue>\nCarry straight on from where you stopped, without repeating anything: beats {part.first} "
            f"to {part.last} of the brief, about {part.words:,} words. {stop} Reply with the continuation only."
            "\n</continue>")
    request = plan.finish(list(history) + [ChatMessage("user", text)], WritingPoint(chapter, scene), size_limit,
                          chars_per_token, None)
    request.target_words = part.words
    return request


def brief_ending(brief: str) -> str:
    """The brief's Ending, in one line ("" if it has none)."""
    found = BRIEF_ENDING.search(clean(brief))
    return " ".join(found.group(1).split()) if found else ""


class _Plan:
    """Everything needed to build one chapter's messages."""

    def __init__(self, project: Project, chapter: int, mode: str | None, overrides: Overrides):
        outline_chapter = project.outline.chapter(chapter)
        if outline_chapter is None:
            raise AssemblyError(f"Chapter {chapter} isn't in the outline")
        self.project = project
        self.chapter: OutlineChapter = outline_chapter
        self.mode = mode or outline_chapter.settings.get("mode") or project.config.mode
        if self.mode == "scenes" and not outline_chapter.scenes:
            raise AssemblyError(f"Chapter {chapter} has no scenes in the outline yet. "
                                "Add scenes, or write it in whole-chapter mode.")
        self.overrides = overrides
        self.finder = MentionFinder(project.entries)
        self.sections: list[ContextSection] = []
        self.in_chat: set[str] = set()   # entry keys already sent in this chat
        self.warnings: list[str] = []
        self.beats_only = project.config.in_chapter_context == "beats_only"  # earlier scenes as beats, not text
        self.parts: list[ScenePart] = []  # a long scene written in parts (scene_parts)
        self.independent = project.config.in_chapter_context == "independent"  # each scene from its brief alone
        self.together = False            # written at the same time as the chapter's other scenes

    # --- settings for the chapter / a scene ------------------------------------------

    def setting(self, key: str, scene: Scene | None, default):
        if scene is not None and key in scene.settings:
            return scene.settings[key]
        return self.chapter.settings.get(key, default)

    def chapter_target(self) -> int:
        return self.chapter.settings.get("words", self.project.config.chapter_words)

    def scene_target(self, number: int) -> int:
        """A scene's own 'words:' wins; the rest of the chapter target is split evenly."""
        scenes = self.chapter.scenes
        own = {s.number: s.settings["words"] for s in scenes if "words" in s.settings}
        if number in own:
            return own[number]
        others = len(scenes) - len(own)
        remaining = max(self.chapter_target() - sum(own.values()), 0)
        return max(int(round(remaining / others / 50.0)) * 50, 100)

    def pov(self, scene: Scene | None) -> str | None:
        return self.setting("pov", scene, self.project.config.pov)

    # --- building messages --------------------------------------------------------------

    def opening(self, point: WritingPoint, notes: str) -> list[ChatMessage]:
        scene = self.chapter.scene(point.scene) if point.scene else None
        lookahead = self._lookahead(point.scene) if point.scene else []
        earlier = self._earlier_beats(point.scene) if point.scene and point.scene > 1 else []

        if point.whole_chapter:
            scan = [self.chapter.text, *[s.text for s in self.chapter.scenes], notes]
            listed = self._listed(self.chapter.scenes)
        elif self.independent:
            scan, listed = [scene.text, notes], self._listed([scene])
        else:
            scan = [self.chapter.text, scene.text, *[s.text for s in lookahead], *[s.text for s in earlier], notes]
            listed = self._listed([scene])
        pov = self.pov(scene)

        self._add_bible()
        self._add_documents()
        entries = self._select_entries(point, scan, listed, pov, turn=1)
        self._add_subplots()
        self._add_extra_files()

        parts = [self._wrap("style_guide"), self._wrap("world_rules"), self._wrap("story_bible"),
                 _group("reference_documents", [s for s in self.sections if s.kind == "document" and s.included])]
        characters = [s for s in entries if s.included and self._entry(s).type == "character"]
        others = [s for s in entries if s.included and self._entry(s).type != "character"]
        parts.append(_group("characters", characters))
        parts.append(_group("places_and_lore", others))
        parts.append(_group("subplots", [s for s in self.sections if s.kind == "subplot" and s.included]))
        parts.append(_group("extra_files", [s for s in self.sections if s.kind == "file" and s.included]))
        parts.append(self._task(point, scene, lookahead, earlier, notes, opening=True))

        system = "\n\n".join(p for p in (self._system(), author_instructions(self.sections)) if p)
        user = "\n\n".join(p for p in parts if p)
        return [ChatMessage("system", system), ChatMessage("user", user)]

    def next_scene(self, number: int, notes: str, written: list[str]) -> ChatMessage:
        """Scene mode, full context: the message asking for scene `number` in the same chat."""
        scene = self.chapter.scene(number)
        point = WritingPoint(self.chapter.number, number)
        lookahead = self._lookahead(number)
        parts = []

        updates = []
        for key in sorted(self.in_chat):
            entry = self._entry_by_key(key)
            if entry is None:
                continue  # the file was deleted since it was sent
            for line in scene_updates(entry, self.chapter.number, number - 1, number):
                updates.append(bullet(f"{entry.name}: {line}"))
        if updates:
            parts.append("<updates>\n" + "\n".join(updates) + "\n</updates>")

        scan = [scene.text, *[s.text for s in lookahead], *written, notes]
        new = [s for s in self._select_entries(point, scan, self._listed([scene]), self.pov(scene), turn=number,
                                               only_new=True) if s.included]
        if new:
            parts.append("<new_reference>\n" + "\n\n".join(s.text for s in new) + "\n</new_reference>")

        parts.append(self._task(point, scene, lookahead, [], notes, opening=False))
        return ChatMessage("user", "\n\n".join(parts))

    def finish(self, messages: list[ChatMessage], point: WritingPoint, size_limit: int,
               chars_per_token: float, context_window: int | None) -> AssembledRequest:
        unknown = self.overrides.exclude | self.overrides.include
        unknown -= {s.key for s in self.sections}
        for key in sorted(unknown):
            self.warnings.append(f"'{key}' isn't a section of this request, so that choice was ignored")

        scene = self.chapter.scene(point.scene) if point.scene else None
        pov = self.pov(scene)
        if not self.project.own_files:  # with your own files, the point of view is in them
            if not pov:
                self.warnings.append("No point-of-view character is set for this chapter")
            elif self.project.find_entry(pov) is None:
                self.warnings.append(f"POV '{pov}' doesn't match any character file")
        if not self.project.system_texts():
            self.warnings.append("This story has no system prompt")

        tokens = sum(estimate_tokens(m.content, chars_per_token) for m in messages)
        size = SizeCheck(tokens, size_limit, context_window)
        if size.message():
            self.warnings.append(size.message())
        target = self.chapter_target() if point.whole_chapter else self.scene_target(point.scene)
        return AssembledRequest(
            point=point, mode=self.mode, chapter_title=self.chapter.title, target_words=target, pov=pov,
            person=self.setting("person", scene, self.project.config.person),
            tense=self.setting("tense", scene, self.project.config.tense),
            sections=self.sections, messages=messages, size=size, warnings=self.warnings, parts=self.parts,
        )

    # --- sections ---------------------------------------------------------------------------

    def _add_section(self, section: ContextSection) -> ContextSection:
        if section.included and section.key in self.overrides.exclude:
            section.included = False
            section.reason += " (unticked by you)"
        elif not section.included and section.key in self.overrides.include:
            section.included = True
            section.reason = "added by you"
        self.sections.append(section)
        return section

    def _add_bible(self) -> None:
        for key in BIBLE_ORDER:
            text = self.project.bible.get(key)
            if text is not None and not is_blank(text):
                self._add_section(ContextSection(f"bible:{key}", BIBLE_LABELS[key], "bible",
                                                 "always included", clean(text)))

    def _add_documents(self) -> None:
        """Your own files that go with every chapter (stories kept in your own files)."""
        for doc in self.project.documents:
            body = doc.text
            if self.independent and doc.key == "Writer guide.md":  # the guide as it stands at this chapter
                from devils_workshop.planner.canon import guide_for
                body = guide_for(self.project.root, self.chapter.number, body)
            default = doc.key.startswith("default:")
            tag, name = ("instructions", "file") if default else ("document", "name")
            text = f'<{tag} {name}="{attr(doc.label)}">\n{clean(body)}\n</{tag}>'
            self._add_section(ContextSection(f"doc:{doc.key}", doc.label, "default" if default else "document",
                                             "your default instructions" if default else "every chapter", text))

    def _select_entries(self, point: WritingPoint, scan: list[str], listed: set[str], pov: str | None,
                        turn: int, only_new: bool = False) -> list[ContextSection]:
        counts = self.finder.count(scan)
        pov_entry = self.project.find_entry(pov) if pov else None
        chosen = []
        for entry in self.project.entries:
            key = f"entry:{entry.path}"
            if only_new and key in self.in_chat:
                continue  # already sent earlier in this chapter's chat
            mentioned = counts.get(entry.path, 0)
            if entry is pov_entry:
                included, reason = True, "POV character"
            elif entry.name.lower() in listed:
                included, reason = True, "listed in the outline"
            elif entry.context == "always":
                included, reason = True, "always included"
            elif entry.context == "never":
                included, reason = False, "context: never"
            elif mentioned:
                included, reason = True, f"mentioned {mentioned}x"
            else:
                included, reason = False, "not mentioned"
            if only_new:
                if not included and key not in self.overrides.include:
                    continue  # later scenes only add what's newly needed
                # It was left out of an earlier message; this message adds it.
                self.sections = [s for s in self.sections if s.key != key]
            role = "point of view" if entry is pov_entry else ""
            section = self._add_section(ContextSection(key, entry.name, "entry", reason,
                                                       render_entry(entry, point, role), included, turn))
            if section.included:
                self.in_chat.add(key)
            chosen.append(section)
        return sorted(chosen, key=lambda s: (s.reason != "POV character", s.label.lower()))

    def _add_subplots(self) -> None:
        for subplot in self.project.subplots:
            status = subplot.status_at(self.chapter.number)
            included = subplot.included_at(self.chapter.number)
            reason = f"status: {status}" if status else "not started yet"
            self._add_section(ContextSection(f"subplot:{subplot.path}", subplot.name, "subplot", reason,
                                             render_subplot(subplot, self.chapter.number), included))

    def _add_extra_files(self) -> None:
        root = self.project.root.resolve()
        for name in self.overrides.extra_files:
            path = (self.project.root / name).resolve()
            if root not in path.parents:
                self.warnings.append(f"'{name}' is outside the project folder, so it wasn't added")
                continue
            if not path.is_file():
                self.warnings.append(f"'{name}' doesn't exist, so it wasn't added")
                continue
            rel = path.relative_to(root).as_posix()
            content = extract_text(path)[0]
            text = f'<file path="{attr(rel)}">\n{clean(content)}\n</file>'
            self._add_section(ContextSection(f"file:{rel}", rel, "file", "added by you", text))

    def _wrap(self, key: str) -> str:
        section = next((s for s in self.sections if s.key == f"bible:{key}"), None)
        if section is None or not section.included:
            return ""
        return f"<{key}>\n{section.text}\n</{key}>"

    def _entry(self, section: ContextSection) -> Entry:
        return self._entry_by_key(section.key)

    def _entry_by_key(self, key: str) -> Entry | None:
        path = key.split(":", 1)[1]
        return next((e for e in self.project.entries if e.path == path), None)

    def _listed(self, scenes: list[Scene]) -> set[str]:
        """Names listed under characters: for the chapter and the given scenes, as entry names."""
        names = list(self.chapter.settings.get("characters", []))
        for scene in scenes:
            names += scene.settings.get("characters", [])
        listed = set()
        for name in names:
            entry = self.project.find_entry(name)
            if entry is not None:
                listed.add(entry.name.lower())
        return listed

    def _lookahead(self, number: int) -> list[Scene]:
        if self.independent:
            return []
        return [s for s in self.chapter.scenes if s.number > number and s.settings.get("lookahead", True)]

    def _earlier_beats(self, number: int) -> list[Scene]:
        """beats_only mode: earlier scenes are described by their beats, not their text."""
        if not self.beats_only or self.independent:
            return []
        return [s for s in self.chapter.scenes if s.number < number]

    def _overused(self, scene: Scene) -> list[str]:
        return worn_phrases(self.project, self.chapter.number, scene.number, scene.text)

    def _voice(self, scene: Scene | None) -> str:
        """The point-of-view line. With your own files, only what story.yaml or a scene sets explicitly."""
        pov = self.pov(scene)
        person = self.setting("person", scene, self.project.config.person)
        tense = self.setting("tense", scene, self.project.config.tense)
        if not self.project.own_files:
            return f"Point of view: {pov or '(not set)'}, {person} person, {tense} tense."

        def given(key: str) -> bool:
            return key in self.project.config.explicit or key in self.chapter.settings or \
                (scene is not None and key in scene.settings)

        parts = [p for p, key in ((pov, "pov"), (f"{person} person", "person"), (f"{tense} tense", "tense"))
                 if p and given(key)]
        return f"Point of view: {', '.join(parts)}." if parts else ""

    def _system(self) -> str:
        parts = [clean(text) for text in self.project.system_texts()]
        parts.append(clean(load_instruction(self.project, "write")))
        return "\n\n".join(parts)

    # --- the task block ------------------------------------------------------------------------

    def _task(self, point: WritingPoint, scene: Scene | None, lookahead: list[Scene], earlier: list[Scene],
              notes: str, opening: bool) -> str:
        chapter = self.chapter
        title = f', "{chapter.title}"' if chapter.title else ""
        lines = ["<task>"]
        if point.whole_chapter:
            lines.append(f"Write chapter {chapter.number}{title}.")
            target = self.chapter_target()
        else:
            scene_title = f' ("{scene.title}")' if scene.title else ""
            count = len(chapter.scenes)
            if opening:
                lines.append(f"Write scene {scene.number} of {count}{scene_title} of chapter {chapter.number}{title}.")
                if self.independent:
                    lines.append("Write it from its brief alone: begin and end exactly where the brief says, and "
                                 "carry every beat it gives, in order, as continuous prose: no numbers, marks, word "
                                 "counts or labels. Give each beat the room the brief marks for it: a (full) beat "
                                 "lived through at length, a (brief) one in a line or two. Word counts in a brief "
                                 "are rough guides, not quotas.")
            else:
                lines.append(f"Write scene {scene.number} of {count}{scene_title}. "
                             "Continue directly from where the previous scene ended.")
            target = self.scene_target(scene.number)

        voice = self._voice(scene)
        if voice:
            lines.append(voice)
        if self.independent and scene is not None:  # a budget for the scene's developments, never a quota
            lines.append(f"Target length: about {target:,} words, spent where the brief puts the weight. Don't "
                         "stretch a brief moment or add incident to reach it.")
            worn = self._overused(scene)
            if worn:  # the writer never sees the other scenes, so it can't know what the book keeps saying
                lines += ["", "Phrases this book has already used too often: find fresh wording, and use one again "
                              "only where nothing else will do: " + "; ".join(f'"{p}"' for p in worn) + "."]
        else:
            lines.append(f"Target length: about {target:,} words.")

        if opening and not is_blank(chapter.text) and not (self.independent and scene is not None):
            heading = "Instructions for this chapter:" if point.whole_chapter else "Instructions for the whole chapter:"
            lines += ["", heading, clean(chapter.text)]
        if point.whole_chapter and chapter.scenes:
            lines += ["", "Scenes, in order:"]
            for s in chapter.scenes:
                lines.append(_scene_heading(s))
                if not is_blank(s.text):
                    lines.append(clean(s.text))
        if earlier:
            lines += ["", "Earlier in this chapter (already written; shown as beats only):"]
            for s in earlier:
                lines += [_scene_heading(s), clean(s.text)]
            if self.together:
                lines += ["", f"Scene {earlier[-1].number} is being written separately. Begin this scene so it "
                              f"follows on naturally from where scene {earlier[-1].number} ends."]
        if scene is not None and not is_blank(scene.text):
            lines += ["", "This scene:", clean(scene.text)]
            ending = BRIEF_ENDING.search(clean(scene.text)) if self.independent else None
            self.parts = scene_parts(clean(scene.text), target) if self.independent and opening else []
            if self.parts:  # longer than one reply holds: written in parts, the first now
                first = self.parts[0]
                lines += ["", f"This scene is long, so it is written in {len(self.parts)} parts, one reply each. "
                              f"Write only the first part now: beats {first.first} to {first.last}, about "
                              f"{first.words:,} words, and stop at the end of beat {first.last}: the next part "
                              "carries on from there."]
            elif ending:  # the last thing read is where to stop: a scene that runs on makes the next one wrong
                lines += ["", "End the scene exactly here, and write nothing after it: "
                          + " ".join(ending.group(1).split())]
        if scene is not None and not self.independent:
            hidden = len([s for s in chapter.scenes if s.number > scene.number]) - len(lookahead)
            if lookahead:
                lines += ["", "Later in this chapter (do not write yet):"]
                for s in lookahead:
                    lines.append(_scene_heading(s))
                    if not is_blank(s.text):
                        lines.append(clean(s.text))
            if hidden == 1:
                lines += ["", "(1 more scene follows later in this chapter.)"]
            elif hidden:
                lines += ["", f"({hidden} more scenes follow later in this chapter.)"]
        if notes.strip():
            lines += ["", "Notes for this request:", clean(notes)]
        lines.append("</task>")
        return "\n".join(lines)


def _scene_heading(scene: Scene) -> str:
    return f"Scene {scene.number}: {scene.title}" if scene.title else f"Scene {scene.number}"


def worn_phrases(project: Project, chapter: int, scene: int, brief: str = "") -> list[str]:
    """The phrases the book as written so far uses too often (this scene's own earlier text left out), never the
    story's own words (its places, garments, names: context/phrases.py). brief: this scene's, if it has one."""
    from devils_workshop.context.phrases import overused, own_words
    texts = []
    for number, written in sorted(project.chapters.items()):
        if written.scenes:
            texts += [t for n, t in sorted(written.scenes.items()) if not (number == chapter and n == scene)]
        elif written.text and number != chapter:
            texts.append(written.text)
    guide = next((d.text for d in project.documents if d.key == "Writer guide.md"), "")
    own = own_words(brief, [s.text for c in project.outline.chapters for s in c.scenes],
                    [d.text for d in project.documents if d.key != "Writer guide.md"], guide)
    return overused(texts, own)


def author_instructions(sections: list[ContextSection]) -> str:
    """Your default instructions for the writer, for the end of the system message: instructions to follow in
    every line, read last, so where they differ from the built-in ones, yours are the ones the writer follows."""
    texts = [s.text for s in sections if s.kind == "default" and s.included]
    return ("The author's own instructions, for every story: follow them in every line you write. Where they differ "
            "from the instructions above, the author's instructions are the ones to follow. Only this book's own "
            "guide comes before them, where it gives a rule for this book.\n"
            + "\n\n".join(texts)) if texts else ""


def _group(tag: str, sections: list[ContextSection]) -> str:
    if not sections:
        return ""
    return f"<{tag}>\n" + "\n\n".join(s.text for s in sections) + f"\n</{tag}>"


# --- preview (CLI) -------------------------------------------------------------------------------

def format_preview(request: AssembledRequest, show_text: bool = True) -> str:
    point = request.point
    title = f' "{request.chapter_title}"' if request.chapter_title else ""
    mode = "whole-chapter mode" if point.whole_chapter else "scene mode"
    lines = [
        f"Preview: {point.label()}{title} ({mode})",
        f"Target: about {request.target_words:,} words. POV {request.pov or '(not set)'}, "
        f"{request.person} person, {request.tense} tense",
        f"Size: about {request.size.tokens:,} tokens (limit {request.size.limit:,}), "
        f"{len(request.messages)} messages",
        "",
        "Included",
    ]
    for s in request.included():
        turn = f"  [message {s.turn}]" if s.turn > 1 else ""
        lines.append(f"  {s.tokens:>7,}  {s.label:<28} {s.reason}{turn}")
    if request.left_out():
        lines += ["", "Left out"]
        for s in request.left_out():
            lines.append(f"           {s.label:<28} {s.reason}")
    if request.warnings:
        lines += ["", "Warnings"]
        lines += [f"  - {w}" for w in request.warnings]
    if show_text:
        for message in request.messages:
            lines += ["", f"===== {message.role} =====", message.content]
    return "\n".join(lines)
