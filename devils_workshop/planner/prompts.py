"""The planner's requests: what is sent for each job, and reading the replies.

Jobs: plan_brainstorm (the chat), plan_guide (the writer's guide), plan_architecture (all chapters),
plan_scenes (one chapter's scene list) and plan_scene (one scene's brief and the continuity after it).
Every request starts with the planner's own system prompt (planner_system), then the job's instruction;
both can be edited (project/instructions.py). The writer's system prompt isn't the planner's: it is sent
as <writer_instructions>, so the planner plans within it.

The planner works from the files in the story folder, not from the whole conversation: the story plan,
the decisions (settled, still open, ruled out, your preferences), the source notes on what you attached,
and the continuity after each scene go with every request; the conversation is cut to its latest
messages. A file or picture you attach is used once, in its own request in the turn you attach it
(plan_notes), and from then on only the planner's notes on it are sent, never the file again. Your
default instructions marked "Planner" follow the planner's system prompt in every request.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from devils_workshop.context.assemble import ChatMessage
from devils_workshop.context.render import attr, clean
from devils_workshop.llm.pictures import ATTACHED, picture_tag
from devils_workshop.planner.inputs import input_text, is_picture, picture
from devils_workshop.planner.state import FULL, PLANNER, ChapterPlan, PlannerState, ScenePlan, new_id
from devils_workshop.project.instructions import instruction

SYSTEM_JOB, BRAINSTORM_JOB, GUIDE_JOB, ARCHITECTURE_JOB, SCENES_JOB, SCENE_JOB = (
    "planner_system", "plan_brainstorm", "plan_guide", "plan_architecture", "plan_scenes", "plan_scene")
NOTES_JOB, IMPORT_JOB = "plan_notes", "plan_import"
SHORT_FILE_WORDS = 1500  # a text file this short goes into the notes as it is, with no request


def job_text(root: Path, job: str, scene_words: int = 800) -> str:
    """A job's instruction (the story's copy, yours, or the built-in one), with {scene_words} filled in."""
    return clean(instruction(root, job)).replace("{scene_words}", f"{scene_words:,}")


def planner_instructions(root: Path | None = None) -> str:
    """Your default instructions for the planner (marked "Planner" or "Writer and planner"), each with its file's
    name; none when the story switches default instructions off."""
    from devils_workshop.project.default_instructions import PLANNER_ROLES, load_defaults
    if root is not None:
        from devils_workshop.project.project import NotAProjectError, load_project
        try:
            if not load_project(root).config.default_instructions:
                return ""
        except NotAProjectError:
            pass
    texts = [f'<instructions file="{attr(d.name)}">\n{clean(d.text)}\n</instructions>' for d in load_defaults()
             if d.role in PLANNER_ROLES and d.text.strip()]
    return ("The author's own instructions, for every story: follow them in everything you plan (those marked for "
            "the writer and the planner are sent to the writer too). Where they differ from the instructions above, "
            "the author's instructions are the ones to follow.\n" + "\n\n".join(texts)) if texts else ""


def system_message(root: Path, job: str, scene_words: int = 800) -> ChatMessage:
    """The planner's own system prompt, the job's instruction, then your instructions for the planner (last, so
    where they differ from the built-in ones, yours are what the planner reads last and follows)."""
    parts = [job_text(root, SYSTEM_JOB), job_text(root, job, scene_words), planner_instructions(root)]
    return ChatMessage("system", "\n\n".join(p for p in parts if p))


def writer_instructions(root: Path) -> str:
    """What the writer is told that the planner isn't told itself: the writer's system prompt (your default
    instructions marked System prompt, and the story's own) and your default instructions marked Writer only,
    for the planner to plan within, so the guide and the briefs never ask for what they forbid; empty if none.
    (Those marked Writer and planner are the planner's own instructions already.)"""
    from devils_workshop.project.default_instructions import EVERY, load_defaults
    from devils_workshop.project.project import NotAProjectError, load_project
    try:
        project = load_project(root)
        texts = [clean(t) for t in project.system_texts()]
        if project.config.default_instructions:
            texts += [f'<instructions file="{attr(d.name)}">\n{clean(d.text)}\n</instructions>'
                      for d in load_defaults() if d.role == EVERY and d.text.strip()]
    except NotAProjectError:
        texts = []
    texts = [t for t in texts if t.strip()]
    return "<writer_instructions>\n" + "\n\n".join(texts) + "\n</writer_instructions>" if texts else ""


def _listed(tag: str, items: list[str]) -> str:
    return f"<{tag}>\n" + "\n".join(f"- {item}" for item in items) + f"\n</{tag}>" if items else ""


def decisions_block(state: PlannerState, notes: bool = True) -> str:
    """What is settled, ruled out and preferred (Decisions.md), and the source notes (Source notes.md)."""
    parts = [_listed("decisions", state.decisions), _listed("ruled_out", state.ruled_out),
             _listed("preferences", state.preferences)]
    if notes and state.notes:
        parts.append("<source_notes>\n" + "\n\n".join(f'<notes file="{attr(name)}">\n{text.strip()}\n</notes>'
                                                       for name, text in state.notes.items()) + "\n</source_notes>")
    return "\n\n".join(p for p in parts if p)


# --- what is sent ---------------------------------------------------------------------------------

CANT_SEE = "(a picture; this model can't see pictures)"


def notes_messages(state: PlannerState, name: str, author_message: str,
                   sees_pictures: bool) -> list[ChatMessage] | None:
    """The request for the planner's notes on one attached file or picture; None when there is nothing to send
    (a picture the model can't see)."""
    task = f'<author_message>\n{author_message.strip() or "(no message)"}\n</author_message>'
    if is_picture(name):
        image = picture(state.root, name)
        if image is None or not sees_pictures:
            return None
        return [system_message(state.root, NOTES_JOB),
                ChatMessage("user", f"{task}\n\n{picture_tag(name, ATTACHED)}", [image])]
    text, problem = input_text(state.root, name)
    body = clean(text) if text.strip() else f"(couldn't be read: {problem or 'no text'})"
    words = f' words="{len(text.split()):,}"' if text.strip() else ""
    return [system_message(state.root, NOTES_JOB),
            ChatMessage("user", f'{task}\n\n<file name="{attr(name)}"{words}>\n{body}\n</file>')]


def note_without_request(state: PlannerState, name: str, sees_pictures: bool) -> str | None:
    """The notes for a file that needs no request: a short text file as it is, an unreadable file, or a picture
    the model can't see. None: ask the planner (notes_messages)."""
    if is_picture(name):
        return None if sees_pictures and picture(state.root, name) else \
            "(A picture. The planner's model can't see pictures, so only its name is known: describe it here.)"
    text, problem = input_text(state.root, name)
    if not text.strip():
        return f"(It couldn't be read: {problem or 'no text'}.)"
    return clean(text) if len(text.split()) <= SHORT_FILE_WORDS else None


RECENT_TURNS = 4  # the latest messages; the plan, decisions, agenda and suggestions carry the rest


def converting(state: PlannerState) -> bool:
    """A plan the author made elsewhere, not carried into the story plan yet: the next turn carries it in."""
    from devils_workshop.planner.registry import IMPORT
    return state.registry.kind == IMPORT and bool(state.registry.plan_files) and not state.plan.strip()


def your_plan(state: PlannerState) -> str:
    """A plan the author made elsewhere: its files, in full and in their order ("" for any other story)."""
    from devils_workshop.planner.registry import IMPORT
    if state.registry.kind != IMPORT:
        return ""
    files = []
    for name in state.registry.plan_files:
        text, problem = input_text(state.root, name)
        body = clean(text) if text.strip() else f"(couldn't be read: {problem or 'no text'})"
        files.append(f'<file name="{attr(name)}">\n{body}\n</file>')
    return "<your_plan>\n" + "\n\n".join(files) + "\n</your_plan>" if files else ""


def brainstorm_messages(state: PlannerState, sees_pictures: bool = False,
                        approved: bool = False) -> list[ChatMessage]:
    """The planner's request: its instruction, the latest messages, and with the author's latest message the
    files that are its memory: the plan as it is now, the decisions (settled, ruled out, preferences), the source
    notes, the agenda, and every suggestion already made with your answer to it, so nothing is asked or
    suggested twice. Attached files are named; what they hold is in the source notes."""
    messages = [system_message(state.root, IMPORT_JOB if converting(state) else BRAINSTORM_JOB, state.scene_words)]
    for turn in state.turns[-RECENT_TURNS:]:
        if turn.role == PLANNER:
            changed = f"\n(Changed in the plan: {', '.join(turn.edited)}.)" if turn.edited else ""
            messages.append(ChatMessage("assistant", turn.text + changed))
            continue
        content = turn.text.strip() or "(no message; see the attached files)"
        if turn.attachments:
            content += "\n\n(Attached: " + ", ".join(turn.attachments) + "; your notes on them are in <source_notes>.)"
        messages.append(ChatMessage("user", content))
    if messages[-1].role == "user":
        context = [writer_instructions(state.root), your_plan(state) if converting(state) else "",
                   f"<current_plan>\n{state.plan.strip() or '(empty: write the first version)'}\n</current_plan>",
                   decisions_block(state),
                   "<agenda>\n" + ("\n".join(f"- {a}" for a in state.agenda) if state.agenda else
                                   "(empty: build it from what the author has said)" if not state.decisions else
                                   "(nothing open)") + "\n</agenda>"]
        if len(state.turns) > RECENT_TURNS:
            context.append(f"<note>Only the latest {RECENT_TURNS} messages are shown; everything settled before "
                           "them is in the plan, the decisions and the notes.</note>")
        if state.suggestions:
            context.append("<suggestions_so_far>\n" + "\n".join(
                f"- [{ {'open': 'not answered yet', 'accepted': 'accepted', 'declined': 'declined'}[s.status] }] {s.text}"
                for s in state.suggestions) + "\n</suggestions_so_far>")
        if approved:
            context.append("<author_approved/>")
        last = messages[-1]
        last.content = "\n\n".join(c for c in context if c) + "\n\n" + last.content
    return messages


@dataclass
class AgentReply:
    """What the planner sent back: section edits, decisions, new suggestions, its message, and whether the
    plan is ready. `whole_plan` is the old form (a complete <plan>), still accepted."""
    edits: list[tuple[str, str]] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    message: str = ""
    ready: bool = False
    whole_plan: str | None = None
    agenda: list[str] | None = None  # the decisions still open, most important first (None: not sent)
    ruled_out: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)  # earlier decisions a new one changes (dropped)


def parse_agent_reply(raw: str) -> AgentReply:
    reply = AgentReply()
    for match in re.finditer(r"<edit\s+section\s*=\s*[\"']([^\"']+)[\"']\s*>(.*?)</edit>", raw, re.DOTALL | re.I):
        reply.edits.append((match.group(1).strip(), match.group(2).strip()))
    for replaces, text in re.findall(r"<decision(?:\s+replaces\s*=\s*\"([^\"]*)\")?\s*>(.*?)</decision>", raw,
                                     re.DOTALL | re.I):
        if text.strip():
            reply.decisions.append(text.strip())
        if replaces.strip():
            reply.replaced.append(replaces.strip())
    reply.suggestions = [m.strip() for m in re.findall(r"<suggestion>(.*?)</suggestion>", raw, re.DOTALL | re.I)
                         if m.strip()]
    for key in ("ruled_out", "preferences"):
        tag = "ruled_out" if key == "ruled_out" else "preference"
        setattr(reply, key, [m.strip() for m in re.findall(rf"<{tag}>(.*?)</{tag}>", raw, re.DOTALL | re.I)
                             if m.strip()])
    reply.ready = bool(re.search(r"<ready\s*/?>", raw, re.I))
    agenda = re.search(r"<agenda>(.*?)</agenda>|<agenda\s*/>", raw, re.DOTALL | re.I)
    if agenda:
        lines = [re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip() for line in (agenda.group(1) or "").split("\n")]
        reply.agenda = [line for line in lines if line and not line.startswith("(")]
    plan, message = split_brainstorm(raw)
    reply.whole_plan = plan
    found = re.search(r"<reply>(.*?)(?:</reply>|$)", raw, re.DOTALL | re.I)
    if found:
        reply.message = found.group(1).strip()
    else:  # no tags: whatever isn't one of the other parts is the message
        rest = re.sub(r"<(edit|decision|suggestion|plan|agenda|ruled_out|preference)\b.*?(</\1>|$)", "", raw,
                      flags=re.DOTALL | re.I)
        reply.message = re.sub(r"<ready\s*/?>", "", rest, flags=re.I).strip()
    return reply


def plan_sections(plan: str) -> list[tuple[str, str]]:
    """The plan as (section name, text) in order; text before the first "## " heading has the name ""."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in plan.split("\n"):
        heading = re.match(r"^##\s+(.+?)\s*#*\s*$", line)
        if heading:
            sections.append((heading.group(1).strip(), []))
        else:
            sections[-1][1].append(line)
    return [(name, "\n".join(lines).strip()) for name, lines in sections if name or "\n".join(lines).strip()]


def _same_section(a: str, b: str) -> bool:
    def norm(name: str) -> str:
        return re.sub(r"\(.*?\)|[^a-z ]", "", name.lower()).strip()
    return norm(a) == norm(b) or (len(norm(a)) > 3 and (norm(a).startswith(norm(b)) or norm(b).startswith(norm(a))))


def apply_edits(plan: str, edits: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """The plan with each edited section replaced (or added at the end). Returns (new plan, sections changed)."""
    sections = plan_sections(plan)
    changed = []
    for name, text in edits:
        at = next((i for i, (existing, _) in enumerate(sections) if existing and _same_section(existing, name)), None)
        if at is None:  # a new section goes before "Open questions", which stays last
            last = next((i for i, (existing, _) in enumerate(sections) if _same_section(existing, "Open questions")),
                        len(sections))
            sections.insert(last, (name, text))
            changed.append(name)
        elif sections[at][1].strip() != text.strip():
            sections[at] = (sections[at][0], text)
            changed.append(sections[at][0])
    out = []
    for name, text in sections:
        out.append(f"## {name}\n{text}".rstrip() if name else text)
    return "\n\n".join(part for part in out if part.strip()).strip(), changed


PLOT_SECTIONS = ("logline", "premise", "story arc", "ending", "chapters", "escalation")  # sections that tell the story


def plan_without_plot(plan: str) -> str:
    """The story plan without the sections that tell the story (Logline, Premise, Story arc, Ending): its
    characters, world, voice, rules and length stay. Text before the first heading stays too."""
    parts = re.split(r"(?m)^(?=##\s)", plan)
    kept = [p for p in parts if not re.match(r"##\s+(" + "|".join(PLOT_SECTIONS) + r")\b", p.strip(), re.I)]
    return "".join(kept).strip()


def _context(state: PlannerState, plot: bool = True, notes: bool = True) -> str:
    """What a planning request starts from: the writer's instructions, the approved plan, and the decisions,
    preferences and source notes. No files or pictures: what they hold is in the notes.

    Once the chapter architecture exists it is the story's only plot, so what comes after it (scene lists, the
    guide planned again, the briefs, following what was written, changes asked for in the Reader) gets
    plot=False, notes=False: the plan without its plot sections, and no source notes, so the whole story (or the
    reference it came from) can't pull events into a chapter that the architecture didn't put there."""
    plan = state.plan.strip() if plot else plan_without_plot(state.plan)
    return "\n\n".join(p for p in (writer_instructions(state.root), f"<story_plan>\n{plan}\n</story_plan>",
                                    decisions_block(state, notes=notes)) if p)


def downstream_context(state: PlannerState) -> str:
    """For everything planned from the chapter architecture: no plot sections of the plan, no source notes."""
    return _context(state, plot=False, notes=False)


def architecture_lines(state: PlannerState) -> str:
    lines = []
    for c in state.chapters:
        scenes = f"{c.scene_count} scenes, " if c.scene_count else ""
        lines.append(f"- Chapter {c.number}: {c.title} ({scenes}{c.words:,} words). {c.purpose}".strip())
    return "\n".join(lines)


def scene_count(state: PlannerState, chapter: ChapterPlan) -> int:
    """How many scenes to plan: the architecture's number, else what the chapter's length gives (6 to 10)."""
    if chapter.scene_count:
        return chapter.scene_count
    guess = round(chapter.words / state.scene_words) if chapter.words and state.scene_words else 8
    return min(max(guess, 6), 10)


def guide_messages(state: PlannerState, sees_pictures: bool = False) -> list[ChatMessage]:
    context = _context(state, plot=False, notes=not state.chapters)  # no plot in the guide
    task = ["<task>", "Write the writer's guide for this novel."]
    if state.guide_note.strip():
        task += ["", "The author's note for this try:", state.guide_note.strip()]
    task.append("</task>")
    return [system_message(state.root, GUIDE_JOB, state.scene_words),
            ChatMessage("user", context + "\n\n" + "\n".join(task))]


def chapter_block(chapter: ChapterPlan) -> str:
    beats = "\n".join(f"- {b}" for b in chapter.beats)
    return "\n".join(part for part in (
        f"Chapter {chapter.number}: {chapter.title}",
        f"Target length: about {chapter.words:,} words" if chapter.words else "",
        f"Purpose: {chapter.purpose}" if chapter.purpose else "",
        f"Summary: {chapter.summary}" if chapter.summary else "",
        f"Point of view: {chapter.pov}" if chapter.pov else "",
        f"When and where: {chapter.when_where}" if chapter.when_where else "",
        f"Key beats:\n{beats}" if beats else "",
        f"Ending: {chapter.ending}" if chapter.ending else "",
        f"Arcs: {chapter.arcs}" if chapter.arcs else "",
        f"Sets up: {chapter.sets_up}" if chapter.sets_up else "",
        f"Pays off: {chapter.pays_off}" if chapter.pays_off else "",
        f"Strands: {chapter.strands}" if chapter.strands else "",
    ) if part)


def chapter_frame(chapter: ChapterPlan) -> str:
    """What a scene's brief needs of its chapter, and no more: no summary, key beats or ending (they tell what the
    other scenes do), only the chapter's job, viewpoint, place and time, and the long view: where each arc
    stands, what it plants and what pays off."""
    return "\n".join(part for part in (
        f"Chapter {chapter.number}: {chapter.title}",
        f"Purpose: {chapter.purpose}" if chapter.purpose else "",
        f"Point of view: {chapter.pov}" if chapter.pov else "",
        f"When and where: {chapter.when_where}" if chapter.when_where else "",
        f"Arcs: {chapter.arcs}" if chapter.arcs else "",
        f"Sets up: {chapter.sets_up}" if chapter.sets_up else "",
        f"Pays off: {chapter.pays_off}" if chapter.pays_off else "",
        f"Strands: {chapter.strands}" if chapter.strands else "",
    ) if part)


def architecture_messages(state: PlannerState, sees_pictures: bool = False) -> list[ChatMessage]:
    context = "\n\n".join(p for p in (_context(state), your_plan(state)) if p)
    if state.registry.sources:  # an adaptation: the reference's events, in its order, for the chapters to carry
        events = "\n".join(f"- [{s.id}] {s.text}" + (f" (roles: {s.roles})" if s.roles else "")
                           + (f" ({s.weight})" if s.weight else "") + ("" if s.required else " (optional)")
                           for s in state.registry.sources)
        context += f"\n\n<reference_events>\n{events}\n</reference_events>"
    task = ["<task>", "Design the chapter architecture for the whole novel.",
            "One scene per continuous stretch of time and place; unless the plan's Length says otherwise, a scene "
            f"is usually about {state.scene_words:,} words, an unbroken major sequence far more."]
    if state.architecture_note.strip():
        task += ["", "The author's note for this try:", state.architecture_note.strip()]
    task.append("</task>")
    return [system_message(state.root, ARCHITECTURE_JOB, state.scene_words),
            ChatMessage("user", context + "\n\n" + "\n".join(task))]


def scenes_messages(state: PlannerState, chapter: ChapterPlan, sees_pictures: bool = False) -> list[ChatMessage]:
    context = downstream_context(state)  # from the architecture, not the plan's plot
    parts = [context, f"<chapter_architecture>\n{architecture_lines(state)}\n</chapter_architecture>"]
    before = state.chapter(chapter.number - 1)
    if before and before.scenes:
        titles = "\n".join(f"- Scene {s.number}: {s.title}. {s.purpose}" for s in before.scenes)
        parts.append(f"<previous_chapter_scenes chapter=\"{before.number}\">\n{titles}\n</previous_chapter_scenes>")
    count = scene_count(state, chapter)
    words = chapter.words or count * state.scene_words
    carried = reference_block(state, state.registry.of_chapter(chapter.number))
    if carried:
        parts.append(carried)
    so_far = [s for s in state.registry.summaries if s.chapter < chapter.number][-SCENES_SO_FAR:]
    if so_far:
        parts.append("<scenes_so_far>\n" + "\n".join(f"- ({s.kind or 'scene'}) {s.text}" for s in so_far)
                     + "\n</scenes_so_far>")
    task = ["<task>", "Plan the scenes for this chapter:", "", chapter_block(chapter), "",
            f"Number of scenes: {count}, about {words:,} words in all: share them by what each scene carries."]
    if chapter.note.strip():
        task += ["", "The author's note for this try:", chapter.note.strip()]
    task.append("</task>")
    parts.append("\n".join(task))
    return [system_message(state.root, SCENES_JOB, state.scene_words), ChatMessage("user", "\n\n".join(parts))]


def neighbours(state: PlannerState, chapter: ChapterPlan, scene: ScenePlan) -> tuple:
    """((chapter, scene) before this one, (chapter, scene) after it), across chapters; None at either end."""
    order = [(c, s) for c in state.chapters for s in c.scenes]
    at = next(i for i, (c, s) in enumerate(order) if c.number == chapter.number and s.number == scene.number)
    return (order[at - 1] if at > 0 else None), (order[at + 1] if at + 1 < len(order) else None)


def scene_messages(state: PlannerState, chapter: ChapterPlan, scene: ScenePlan,
                   sees_pictures: bool = False, chapter_from_plan: bool = False) -> list[ChatMessage]:
    """The brief for one scene. The writer will see only this brief and the guide, so the request carries
    what the brief must account for: how things stand coming in (the continuity after the scene before, in
    this chapter or the last), that scene's brief, and what the next scene needs to begin from.
    chapter_from_plan: chapters are being briefed side by side, so a chapter's first scene comes in from the
    ending the architecture gives the chapter before it, not from that chapter's last brief (not written yet).

    A chapter planned with its long view (arcs, set-ups, pay-offs: v0.23 on) gets a need-to-know request: the
    guide, your preferences, the chapter's frame, this scene's own line and how things stand coming in, nothing
    about other scenes or chapters, so there is little to leak or drift into. A chapter planned before keeps the
    fuller request (the plan, the notes, the architecture, the chapter's scenes, the scene before and after)."""
    need_to_know = bool(chapter.arcs or chapter.sets_up or chapter.pays_off)
    if need_to_know:
        parts = [p for p in (writer_instructions(state.root), _listed("preferences", state.preferences),
                             _listed("ruled_out", state.ruled_out)) if p]
        if state.guide.strip():
            parts.append(f"<writers_guide>\n{guide_at(state, chapter)}\n</writers_guide>")
        parts += [f"<chapter>\n{chapter_frame(chapter)}\n</chapter>",
                  f'<this_scene number="{scene.number}" of="{len(chapter.scenes)}">\n{scene.title} (about '
                  f"{scene.words or state.scene_words:,} words). {scene.purpose}\n</this_scene>"]
    else:
        parts = [downstream_context(state)]
        if state.guide.strip():
            parts.append(f"<writers_guide>\n{guide_at(state, chapter)}\n</writers_guide>")
        parts += [f"<chapter_architecture>\n{architecture_lines(state)}\n</chapter_architecture>",
                  f"<chapter>\n{chapter_block(chapter)}\n</chapter>",
                  "<scene_list>\n" + "\n".join(f"- Scene {s.number}: {s.title} (about {s.words:,} words). "
                                               f"{s.purpose}" for s in chapter.scenes) + "\n</scene_list>"]
    carried = reference_block(state, [e for e in state.registry.of_chapter(chapter.number)
                                      if e.scene == scene.number])
    if carried:
        parts.append(carried)
    parts += carried_forward(state, chapter, scene)
    worn = worn_so_far(state, chapter, scene)
    if worn:  # the brief decides what the writer says: it must not hand the writer the book's tics
        parts.append("<worn_phrases>\n" + "; ".join(f'"{p}"' for p in worn) + "\n</worn_phrases>")
    before, after = neighbours(state, chapter, scene)
    if chapter_from_plan and before and before[0].number != chapter.number:
        ending = before[0].ending or before[0].summary or before[0].purpose
        coming_in = (f"(from the chapter architecture: chapter {before[0].number}, {before[0].title}, is planned "
                     f"to end like this) {ending}".strip())
    else:
        noted = before[1].continuity.strip() if before else ""
        if before and before[1].brief and (not need_to_know or not noted):  # need to know: only if not noted
            where = f'chapter="{before[0].number}" scene="{before[1].number}"'
            parts.append(f"<previous_scene_brief {where}>\n{before[1].brief}\n</previous_scene_brief>")
        coming_in = noted or (
            "(the story begins here)" if before is None else "(not noted: work it out from the brief above)")
    parts.append(f"<continuity_so_far>\n{coming_in}\n</continuity_so_far>")
    if after and not need_to_know:
        what = f"Scene {after[1].number}: {after[1].title}. {after[1].purpose}".strip()
        if after[0].number != chapter.number:
            what = f"(Chapter {after[0].number}: {after[0].title}) {what}"
        parts.append(f"<next_scene>\n{what}\n</next_scene>")
    task = ["<task>", f"Write the brief for scene {scene.number} of chapter {chapter.number}: {scene.title}.",
            f"Length of the brief: about {state.brief_target(scene):,} words, and no more: keep to it (the "
            f"scene will be about {scene.words or state.scene_words:,} words)."]
    if scene.staging == FULL:
        task.append("Staging: full. Choreograph it stage by stage, as the instruction says.")
    if scene.note.strip() and scene.earlier.strip():
        parts.append(f"<brief_to_change>\n{scene.earlier.strip()}\n</brief_to_change>")
        task += ["", "Change the brief above as the author's note says. Keep everything the note doesn't touch as "
                 "it is, unless the change makes it contradict.", "", "The author's note:", scene.note.strip()]
    elif scene.note.strip():
        task += ["", "The author's note for this try:", scene.note.strip()]
    task.append("</task>")
    parts.append("\n".join(task))
    return [system_message(state.root, SCENE_JOB, state.scene_words), ChatMessage("user", "\n\n".join(parts))]


SCENES_SO_FAR = 30   # the latest written scenes, in a line each, that a chapter's scene list is planned beside
LIKE_THIS = 12       # the earlier scenes of the same kind a brief sees


def carried_forward(state: PlannerState, chapter: ChapterPlan, scene: ScenePlan) -> list[str]:
    """What the story carries into a scene, for its brief: how each character in it stands now (from the last
    scene they were in, however long ago), the consequences still to be lived with, and the earlier scenes of
    the same kind, so this one can be different."""
    registry = state.registry
    at = (chapter.number, scene.number)  # only what stood before this scene: a scene planned again (after the
    # story has gone on) never learns how it, or anything after it, turned out
    before = exit_text(state, chapter, scene)
    present = [c for c in registry.present(" ".join([scene.title, scene.purpose, before, chapter.pov, chapter.arcs]))
               if (c.chapter, c.scene) < at]
    parts = []
    if present:
        parts.append("<characters_now>\n" + "\n".join(f"- {c.name} (as of chapter {c.chapter}, scene {c.scene}): "
                                                      f"{c.state}" for c in present) + "\n</characters_now>")
    names = {c.name.lower() for c in present} | {c.name.split()[0].lower() for c in present if c.name.strip()}
    open_ones = [c for c in registry.consequences
                 if (c.chapter, c.scene) < at and (not c.absorbed_in or tuple(c.absorbed_in) >= at)
                 and (not names or any(n in c.who.lower() for n in names) or not c.who.strip())]
    if open_ones:
        parts.append("<open_consequences>\n" + "\n".join(f"- ({c.who}) {c.text}" for c in open_ones)
                     + "\n</open_consequences>")
    like = registry.like(scene.kind, (chapter.number, scene.number))[-LIKE_THIS:]
    if like:
        parts.append(f'<earlier_scenes_like_this kind="{attr(scene.kind)}">\n'
                     + "\n".join(f"- Chapter {s.chapter}, scene {s.scene}: {s.text}" for s in like)
                     + "\n</earlier_scenes_like_this>")
    return parts


def worn_so_far(state: PlannerState, chapter: ChapterPlan, scene: ScenePlan) -> list[str]:
    """The phrases the book's prose already uses too often (context/phrases.py), for the brief to avoid."""
    from devils_workshop.context.assemble import worn_phrases
    from devils_workshop.project.project import NotAProjectError, load_project
    try:
        return worn_phrases(load_project(state.root), chapter.number, scene.number)
    except NotAProjectError:
        return []


def exit_text(state: PlannerState, chapter: ChapterPlan, scene: ScenePlan) -> str:
    from devils_workshop.planner.run import exit_before
    return exit_before(state, chapter.number, scene.number)


def guide_at(state: PlannerState, chapter: ChapterPlan) -> str:
    """The writer's guide as it stands at this chapter (what only becomes true later left out, planner/canon.py),
    so a brief is never written from what happens after it."""
    from devils_workshop.planner.canon import scoped_guide
    scope = state.registry.guide_scope
    return (scoped_guide(state.guide, scope, chapter.number) if scope else state.guide).strip()


def reference_block(state: PlannerState, events) -> str:
    """An adaptation: the reference's events that these events carry, with the weight the reference gives each,
    so the plan elaborates what the reference elaborates and passes over what it passes over ("" if none)."""
    lines = []
    for event in events:
        for source_id in event.sources:
            source = state.registry.source(source_id)
            if source is not None:
                weight = f"{source.weight}; " if source.weight else ""
                roles = f" (roles: {source.roles})" if source.roles else ""
                lines.append(f"- [{source.id}] ({weight}carried by {event.id}) {source.text}{roles}")
    if not lines:
        return ""
    changes = "".join(f"\n- {c}" for c in state.registry.changes)
    return ("<reference_events>\n" + "\n".join(dict.fromkeys(lines)) + "\n</reference_events>"
            + (f"\n\n<approved_changes>{changes}\n</approved_changes>" if changes else ""))


# --- reading the replies -----------------------------------------------------------------------------

def split_brainstorm(reply: str) -> tuple[str | None, str]:
    """(the new story plan, or None if the reply has no complete one; the message to show you)."""
    plan_match = re.search(r"<plan>(.*?)</plan>", reply, re.DOTALL | re.IGNORECASE)
    reply_match = re.search(r"<reply>(.*?)(?:</reply>|$)", reply, re.DOTALL | re.IGNORECASE)
    plan = plan_match.group(1).strip() if plan_match else None
    if reply_match:
        message = reply_match.group(1).strip()
    else:  # no tags: whatever isn't the plan is the message
        message = re.sub(r"<plan>.*?(</plan>|$)", "", reply, flags=re.DOTALL | re.IGNORECASE).strip()
    return (plan or None), message


def split_scene_reply(reply: str) -> tuple[str, str]:
    """(the brief, the continuity after the scene). Without the tags, the whole reply is the brief. The planner's
    own check of the author's requirements (<requirements>) is never kept: the writer must not copy it."""
    brief = re.search(r"<brief>(.*?)(?:</brief>|<requirements>|<continuity>|$)", reply, re.DOTALL | re.I)
    continuity = re.search(r"<continuity>(.*?)(?:</continuity>|$)", reply, re.DOTALL | re.I)
    if not brief:
        text = re.sub(r"<(continuity|requirements)>.*?(</\1>|$)", "", reply, flags=re.DOTALL | re.I).strip()
        return text, continuity.group(1).strip() if continuity else ""
    return brief.group(1).strip(), continuity.group(1).strip() if continuity else ""


_ATTRS = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_LABELS = {"purpose": "purpose", "summary": "summary", "point of view": "pov", "pov": "pov",
           "when and where": "when_where", "ending": "ending", "arcs": "arcs", "arc": "arcs",
           "sets up": "sets_up", "pays off": "pays_off", "strands": "strands"}


def parse_tagged(reply: str, key: str) -> list[dict]:
    """The chapters or scenes in the tagged form the instructions ask for (<chapter ...>...</chapter>,
    <scene ...>...</scene>), as the same dicts the JSON form gives; [] when there are none."""
    tag = "chapter" if key == "chapters" else "scene"
    items = []
    for attrs, body in _tagged_blocks(reply, tag):
        item = {name.lower(): value for name, value in _ATTRS.findall(attrs)}
        if tag == "scene":
            item.setdefault("purpose", body.strip())
        else:
            beats, current = [], None
            for line in body.strip().split("\n"):
                label, _, value = line.partition(":")
                name = _LABELS.get(label.strip().lower())
                bullet = line.lstrip().startswith(("-", "*"))
                if name and not bullet:
                    # a field on one line ("Arcs: ...") or a list under it ("Arcs:" then "- Mara: ...")
                    item[name], current = value.strip(), name
                elif label.strip().lower() == "beats" and not bullet:
                    current = "beats"
                elif current == "beats" and bullet:
                    beats.append(line.strip()[1:].strip())
                elif current and current != "beats" and line.strip():
                    item[current] = (item[current] + " " + line.strip()).strip()
            item["beats"] = beats
        items.append(item)
    return items


def _tagged_blocks(reply: str, tag: str) -> list[tuple[str, str]]:
    """(attributes, body) of every <tag ...> block. A block runs to its </tag>, or, when the model left that
    out, to the next <tag ...> (or the end of the reply), so a missing closing tag never merges two chapters
    into one or drops the last one."""
    opening = re.compile(rf"<{tag}\b([^>]*)>", re.I)
    closing = re.compile(rf"</{tag}\s*>", re.I)
    starts = list(opening.finditer(reply))
    blocks = []
    for at, start in enumerate(starts):
        end = starts[at + 1].start() if at + 1 < len(starts) else len(reply)
        body = reply[start.end():end]
        closed = closing.search(body)
        blocks.append((start.group(1), body[:closed.start()] if closed else body))
    return blocks


def parse_list(reply: str, key: str) -> list[dict]:
    """The chapters or scenes from a reply, tagged (asked for) or JSON (older instructions)."""
    return parse_tagged(reply, key) or parse_json_list(reply, key)


def parse_json_list(reply: str, key: str) -> list[dict]:
    """The list under `key` in the model's JSON, tolerating code fences or text around it."""
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"The reply didn't contain the {key} as JSON")
    try:
        data = json.loads(reply[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"The {key} couldn't be read: {exc.msg}") from exc
    items = data.get(key) if isinstance(data, dict) else None
    if not isinstance(items, list) or not any(isinstance(i, dict) for i in items):
        raise ValueError(f"The reply had no {key}")
    return [i for i in items if isinstance(i, dict)]


def _words(value) -> int:
    try:
        return max(0, int(str(value).replace(",", "").split()[0]))
    except (ValueError, IndexError):
        return 0


def _text(value) -> str:
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    return str(value or "").strip()


def _noted(value) -> str:
    """A long-view note ("Sets up: nothing" is no note)."""
    text = _text(value)
    return "" if text.lower().strip(" .()") in ("nothing", "none", "n/a", "-") else text


def chapters_from(items: list[dict]) -> list[ChapterPlan]:
    """The chapters, each with the number the reply gave it (contracts.check_chapters has made sure they run 1 to
    N), in that order; a reply with no numbers at all is numbered in order."""
    from devils_workshop.planner.contracts import number_of, order_chapters
    chapters = []
    for position, raw in enumerate(order_chapters(items), 1):
        number = number_of(raw.get("number")) or position
        beats = raw.get("beats") or []
        chapters.append(ChapterPlan(
            id=new_id(), number=number, title=_text(raw.get("title")) or f"Chapter {number}", purpose=_text(raw.get("purpose")),
            summary=_text(raw.get("summary")), words=_words(raw.get("words")), pov=_text(raw.get("pov")),
            when_where=_text(raw.get("when_where")),
            beats=[_text(b) for b in (beats if isinstance(beats, list) else [beats]) if _text(b)],
            ending=_text(raw.get("ending")), arcs=_noted(raw.get("arcs")), sets_up=_noted(raw.get("sets_up")),
            pays_off=_noted(raw.get("pays_off")), strands=_noted(raw.get("strands")),
            scene_count=min(_words(raw.get("scenes")), 30)))
    return chapters


def scenes_from(items: list[dict], chapter_words: int) -> list[ScenePlan]:
    scenes = [ScenePlan(id=new_id(), number=n, title=_text(raw.get("title")) or f"Scene {n}",
                        purpose=_text(raw.get("purpose")), words=_words(raw.get("words")),
                        kind=_text(raw.get("kind")).lower(),
                        staging=FULL if _text(raw.get("staging")).lower() == FULL else "")
              for n, raw in enumerate(items, 1)]
    missing = [s for s in scenes if not s.words]
    if chapter_words and missing:  # scenes without a budget share what's left of the chapter's
        left = chapter_words - sum(s.words for s in scenes)
        share = left // len(missing) if left > 0 else chapter_words // len(scenes)
        for scene in missing:
            scene.words = share
    return scenes
