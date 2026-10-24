"""One chat per chapter (PLAN.md §6, D29, D30).

A chapter's chat starts fresh from the project files and that chapter's
instructions; nothing from other chapters is carried over. What was actually
sent is saved in sessions/chNN.json, so later scenes and change requests
continue the real conversation. The chapter text itself lives in
chapters/chNN.md; every earlier version is kept in chapters/versions/.

Each action has two steps, so you can review the request in between:
    prepared = chat.prepare_write(project)      # or prepare_change / prepare_continue
    ...review, maybe edit, then send it (runner.py)...
    chat.apply(project, prepared, reply_text)   # saves the result
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from devils_workshop.project.scopes import WRITER, check
from devils_workshop.context.assemble import (
    AssembledRequest,
    AssemblyError,
    ChatMessage,
    Overrides,
    assemble,
    assemble_change,
    assemble_continue,
    assemble_followup,
)
from devils_workshop.context.size import DEFAULT_CHARS_PER_TOKEN, DEFAULT_SIZE_LIMIT
from devils_workshop.history import write_project_file
from devils_workshop.project.chapters import (
    ChapterText,
    chapter_file_name,
    parse_chapter_file,
    render_chapter_file,
    version_file_name,
)
from devils_workshop.project.files import read_text, write_text
from devils_workshop.project.issues import IssueList
from devils_workshop.project.project import Project
from devils_workshop.wordcount import count_words

SESSIONS_DIR = "sessions"

WRITE = "write"        # whole chapter
SCENE = "scene"        # one scene, in scene mode
CHANGE = "change"      # a change request
CONTINUE = "continue"  # carry on a reply that stopped short
JOIN = "join"          # smooth the joins between scenes written at the same time (joins.py)


@dataclass
class Prepared:
    """A request ready for review and sending."""
    kind: str
    chapter: int
    scene: int | None
    request: AssembledRequest
    change_text: str = ""
    restart: bool = False

    @property
    def messages(self) -> list[ChatMessage]:
        return self.request.messages

    @property
    def target_words(self) -> int:
        return self.request.target_words


@dataclass
class Version:
    number: int
    path: str
    words: int
    current: bool


@dataclass
class ChapterChat:
    root: Path
    chapter: int
    mode: str | None = None
    opening: list[ChatMessage] = field(default_factory=list)  # system + first user message, as sent
    turns: list[ChatMessage] = field(default_factory=list)    # everything after the opening (scene mode)
    sent_keys: set[str] = field(default_factory=set)          # files already sent in this chat
    change_requests: list[dict] = field(default_factory=list)
    smooth_pending: bool = False  # scenes written together whose joins haven't been smoothed yet

    # --- loading and saving --------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self.root / SESSIONS_DIR / f"ch{self.chapter:02d}.json"

    @classmethod
    def load(cls, root: Path, chapter: int) -> ChapterChat:
        chat = cls(Path(root), chapter)
        if chat.path.is_file():
            try:
                data = json.loads(chat.path.read_text(encoding="utf-8"))
            except ValueError:
                return chat  # a damaged chat file: start a fresh chat rather than fail
            chat.mode = data.get("mode")
            chat.opening = [ChatMessage(m["role"], m["content"]) for m in data.get("opening", [])]
            chat.turns = [ChatMessage(m["role"], m["content"]) for m in data.get("turns", [])]
            chat.sent_keys = set(data.get("sent_keys", []))
            chat.change_requests = list(data.get("change_requests", []))
            chat.smooth_pending = bool(data.get("smooth_pending", False))
        return chat

    def save(self) -> None:
        data = {
            "chapter": self.chapter, "mode": self.mode, "saved": datetime.now().isoformat(timespec="seconds"),
            "opening": [{"role": m.role, "content": m.content} for m in self.opening],
            "turns": [{"role": m.role, "content": m.content} for m in self.turns],
            "sent_keys": sorted(self.sent_keys),
            "change_requests": self.change_requests,
            "smooth_pending": self.smooth_pending,
        }
        write_text(self.path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    # --- state ------------------------------------------------------------------------------

    def default_mode(self, project: Project) -> str:
        outline = project.outline.chapter(self.chapter)
        if outline is None:
            raise AssemblyError(f"Chapter {self.chapter} isn't in the outline")
        return outline.settings.get("mode") or project.config.mode

    def text(self, project: Project) -> ChapterText | None:
        return project.chapters.get(self.chapter)

    def next_scene(self, project: Project) -> int | None:
        """Scene mode: the first outlined scene not written yet (None if all are written)."""
        outline = project.outline.chapter(self.chapter)
        written = self.text(project)
        done = set(written.scenes) if written else set()
        for scene in outline.scenes if outline else []:
            if scene.number not in done:
                return scene.number
        return None

    def versions(self, project: Project) -> list[Version]:
        result = []
        folder = self.root / "chapters" / "versions"
        pattern = re.compile(rf"^ch{self.chapter:02d}\.v(\d+)\.md$")
        if folder.is_dir():
            for path in folder.iterdir():
                match = pattern.match(path.name)
                if match:
                    text = read_text(path)[0]
                    result.append(Version(int(match.group(1)), f"chapters/versions/{path.name}",
                                          _body_words(text), False))
        current = self.text(project)
        if current:
            result.append(Version(current.version, current.path, current.words, True))
        return sorted(result, key=lambda v: (v.number, v.current))

    # --- preparing requests ----------------------------------------------------------------

    def prepare_write(self, project: Project, mode: str | None = None, notes: str = "",
                      overrides: Overrides | None = None, restart: bool = False,
                      size_limit: int = DEFAULT_SIZE_LIMIT,
                      chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> Prepared:
        """Write the chapter (whole mode) or its next scene (scene mode).

        restart=True starts the chapter again from scene 1 (the current text becomes a version).
        """
        mode = mode or self.default_mode(project)
        sizes = {"size_limit": size_limit, "chars_per_token": chars_per_token}
        if mode == "whole":
            return Prepared(WRITE, self.chapter, None,
                            assemble(project, self.chapter, mode="whole", notes=notes, overrides=overrides, **sizes))
        scene = 1 if restart else self.next_scene(project)
        if scene is None:
            raise AssemblyError(f"All scenes of chapter {self.chapter} are written. Ask for changes, "
                                "or start the chapter again.")
        full_chat = project.config.in_chapter_context == "full"
        if scene > 1 and full_chat and self.opening and self.mode == "scenes":
            request = assemble_followup(project, self.chapter, scene, self.opening + self.turns, self.sent_keys,
                                        notes, overrides, **sizes)
        else:
            request = assemble(project, self.chapter, scene, mode="scenes", notes=notes, overrides=overrides, **sizes)
        return Prepared(SCENE, self.chapter, scene, request, restart=restart)

    def prepare_scene_alone(self, project: Project, scene: int, notes: str = "",
                            size_limit: int = DEFAULT_SIZE_LIMIT,
                            chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> Prepared:
        """A scene written at the same time as the chapter's other scenes: a fresh chat of its own,
        with the scenes before it as beats. Save the results with apply_together()."""
        request = assemble(project, self.chapter, scene, mode="scenes", notes=notes, together=True,
                           size_limit=size_limit, chars_per_token=chars_per_token)
        return Prepared(SCENE, self.chapter, scene, request)

    def prepare_change(self, project: Project, request_text: str, scene: int | None = None,
                       overrides: Overrides | None = None, size_limit: int = DEFAULT_SIZE_LIMIT,
                       chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> Prepared:
        current = self.text(project)
        if current is None:
            raise AssemblyError(f"Chapter {self.chapter} hasn't been written yet")
        if not request_text.strip():
            raise AssemblyError("Describe the change you want first")
        if scene is not None and scene not in current.scenes:
            raise AssemblyError(f"Scene {scene} of chapter {self.chapter} hasn't been written yet")
        opening = self.opening or self._fresh_opening(project, scene)
        mode = "scenes" if scene else "whole"
        earlier = [c["text"] for c in self.change_requests]
        draft = _draft_for_change(current)
        if scene and project.config.in_chapter_context == "independent":  # the scene's own setup and text
            opening, draft = self._fresh_opening(project, scene), current.scenes[scene]
            earlier = [c["text"] for c in self.change_requests if c.get("scene") == scene]
        request = assemble_change(project, self.chapter, mode, opening, draft, request_text,
                                  earlier, self.sent_keys, scene, overrides, size_limit, chars_per_token)
        return Prepared(CHANGE, self.chapter, scene, request, change_text=request_text.strip())

    def prepare_continue(self, project: Project, size_limit: int = DEFAULT_SIZE_LIMIT,
                         chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> Prepared:
        current = self.text(project)
        if current is None:
            raise AssemblyError(f"Chapter {self.chapter} hasn't been written yet")
        if current.scenes and project.config.in_chapter_context == "independent":
            scene = max(current.scenes)  # the scene's own setup, then its text
            history = self._fresh_opening(project, scene) + [ChatMessage("assistant", current.scenes[scene])]
            request = assemble_continue(project, self.chapter, "scenes", history, scene, size_limit, chars_per_token)
        elif current.scenes and self.mode == "scenes" and self.opening:
            scene = max(current.scenes)
            history = self.opening + self.turns
            if not history or history[-1].role != "assistant":
                history = history + [ChatMessage("assistant", current.scenes[scene])]
            request = assemble_continue(project, self.chapter, "scenes", history, scene, size_limit, chars_per_token)
        else:
            scene = None
            opening = self.opening or self._fresh_opening(project, None)
            history = opening + [ChatMessage("assistant", current.text)]
            request = assemble_continue(project, self.chapter, "whole", history, None, size_limit, chars_per_token)
        written = current.scenes.get(scene, "") if scene else current.text
        request.target_words = max(request.target_words - count_words(written), 300)
        return Prepared(CONTINUE, self.chapter, scene, request)

    def _fresh_opening(self, project: Project, scene: int | None) -> list[ChatMessage]:
        """For chapters written outside the app (no saved chat): rebuild the setup."""
        if scene:
            first = scene if project.config.in_chapter_context == "independent" else 1
            return assemble(project, self.chapter, first, mode="scenes").messages[:2]
        return assemble(project, self.chapter, mode="whole").messages[:2]

    # --- saving results ---------------------------------------------------------------------

    def apply(self, project: Project, prepared: Prepared, reply: str) -> ChapterText:
        """Save a successful reply: a new chapter version, a new scene, a change or a continuation."""
        text = reply.strip()
        current = self.text(project)
        included = {s.key for s in prepared.request.included() if s.kind == "entry"}
        action = f"ch{self.chapter:02d}-{prepared.kind}" + (f"-s{prepared.scene}" if prepared.scene else "")

        if prepared.kind == WRITE:
            self.smooth_pending = False
            self._new_version(project, current, text=text, action=action)
            self.mode, self.turns, self.change_requests = "whole", [], []
            self.opening = list(prepared.messages[:2])
            self.sent_keys = included

        elif prepared.kind == SCENE:
            scene = prepared.scene
            if scene == 1 or prepared.restart or current is None or not current.scenes:
                self._new_version(project, current if (current and (current.text or current.scenes)) else None,
                                  scenes={scene: text}, action=action)
                self.opening = list(prepared.messages[:2])
                self.turns = list(prepared.messages[2:]) + [ChatMessage("assistant", text)]
                self.sent_keys = included
                self.change_requests = []
            else:
                scenes = dict(current.scenes)
                scenes[scene] = text
                self._write(project, current.version, current.status, scenes=scenes, action=action)
                if self.opening and prepared.messages[:len(self.opening)] == self.opening:
                    self.turns = list(prepared.messages[len(self.opening):])
                else:  # the chat was rebuilt (beats_only, or written outside the app)
                    self.opening = list(prepared.messages[:2])
                    self.turns = list(prepared.messages[2:])
                self.turns.append(ChatMessage("assistant", text))
                self.sent_keys |= included
            self.mode = "scenes"

        elif prepared.kind == CHANGE:
            if prepared.scene:
                scenes = dict(current.scenes)
                scenes[prepared.scene] = text
                version = self._new_version(project, current, scenes=scenes, action=action)
                self._replace_scene_turn(prepared.scene, text)
            else:
                version = self._new_version(project, current, text=text, action=action)
                self.mode, self.turns, self.smooth_pending = "whole", [], False
            if not self.opening:
                self.opening = list(prepared.messages[:2])
            self.change_requests.append({"text": prepared.change_text, "scene": prepared.scene, "version": version})
            self.sent_keys |= included

        elif prepared.kind == CONTINUE:
            if prepared.scene:
                scenes = dict(current.scenes)
                scenes[prepared.scene] = _join(scenes[prepared.scene], text)
                self._write(project, current.version, current.status, scenes=scenes, action=action)
                self._replace_scene_turn(prepared.scene, scenes[prepared.scene])
            else:
                self._write(project, current.version, current.status, text=_join(current.text, text), action=action)
        self.save()
        return self._reload(project)

    def apply_together(self, project: Project, texts: dict[int, str], restart: bool = False) -> ChapterText:
        """Save scenes written at the same time, all in one go. They join the scenes already written
        (finishing a half-written chapter), or start a new version (a new chapter, or restart=True)."""
        current = self.text(project)
        texts = {number: text.strip() for number, text in texts.items()}
        action = f"ch{self.chapter:02d}-together"
        if current is not None and current.scenes and not restart:
            self._write(project, current.version, current.status, scenes={**current.scenes, **texts}, action=action)
        else:
            self._new_version(project, current if (current and (current.text or current.scenes)) else None,
                              scenes=texts, action=action)
            self.change_requests = []
        # Independent scenes (the planner's) need no smoothing: each brief already says how it begins and ends.
        self.mode, self.smooth_pending = "scenes", project.config.in_chapter_context != "independent"
        self._reload(project)
        self._rebuild_chat(project)
        self.save()
        return self._reload(project)

    def replace_scenes(self, project: Project, texts: dict[int, str], action: str) -> ChapterText:
        """Scenes changed in place, in the chapter's current version: no earlier version is kept (the Reader's
        changes), but History has the chapter as it was, for one Undo. The chapter goes back to draft."""
        current = self.text(project)
        scenes = {**(current.scenes if current else {}), **{n: t.strip() for n, t in texts.items()}}
        self._write(project, current.version if current else 1, "draft", scenes=scenes, action=action)
        self.mode = "scenes"
        self._reload(project)
        self._rebuild_chat(project)
        self.save()
        return self._reload(project)

    def apply_join(self, project: Project, scenes: dict[int, str]) -> ChapterText:
        """Save the scenes with their smoothed openings (same version: it finishes writing them)."""
        current = self.text(project)
        self._write(project, current.version, current.status, scenes=scenes, action=f"ch{self.chapter:02d}-join")
        self.smooth_pending = False
        self._reload(project)
        self._rebuild_chat(project)
        self.save()
        return self._reload(project)

    def _rebuild_chat(self, project: Project) -> None:
        """After scenes written at the same time: the chat as it would be had they been written in order,
        so later scenes, changes and continuations see the chapter as it now stands."""
        current = self.text(project)
        numbers = sorted(current.scenes) if current else []
        self.opening, self.turns, self.sent_keys = [], [], set()
        if not numbers or numbers != list(range(1, numbers[-1] + 1)):
            return  # a scene is missing: requests are rebuilt from the chapter file when needed
        try:
            request = assemble(project, self.chapter, numbers[-1], mode="scenes")
        except AssemblyError:
            return
        messages = request.messages + [ChatMessage("assistant", current.scenes[numbers[-1]])]
        self.opening, self.turns = messages[:2], messages[2:]
        self.sent_keys = {s.key for s in request.included() if s.kind == "entry"}

    def restore_version(self, project: Project, number: int) -> ChapterText:
        """Bring back an earlier version as the newest one (nothing is lost)."""
        path = self.root / version_file_name(self.chapter, number)
        if not path.is_file():
            raise FileNotFoundError(f"Chapter {self.chapter} has no version {number}")
        old = parse_chapter_file(read_text(path)[0], version_file_name(self.chapter, number), self.chapter, IssueList())
        current = self.text(project)
        self._new_version(project, current, text=old.text if not old.scenes else "",
                          scenes=old.scenes or None, action=f"ch{self.chapter:02d}-restore-v{number}")
        self.save()
        return self._reload(project)

    def mark_final(self, project: Project, final: bool = True) -> ChapterText:
        current = self.text(project)
        if current is None:
            raise AssemblyError(f"Chapter {self.chapter} hasn't been written yet")
        self._write(project, current.version, "final" if final else "draft",
                    text=current.text if not current.scenes else "", scenes=current.scenes or None,
                    action=f"ch{self.chapter:02d}-{'final' if final else 'draft'}")
        return self._reload(project)

    # --- helpers ------------------------------------------------------------------------------

    def _title(self, project: Project) -> str:
        outline = project.outline.chapter(self.chapter)
        return outline.title if outline else ""

    def _write(self, project: Project, version: int, status: str, text: str = "",
               scenes: dict[int, str] | None = None, action: str = "") -> None:
        content = render_chapter_file(self.chapter, self._title(project), version, status, text, scenes)
        check(WRITER, chapter_file_name(self.chapter))  # the writer writes the story's text, and nothing else
        write_project_file(self.root, chapter_file_name(self.chapter), content, action)

    def _new_version(self, project: Project, current: ChapterText | None, text: str = "",
                     scenes: dict[int, str] | None = None, action: str = "") -> int:
        number = 1
        if current is not None:
            old = (self.root / chapter_file_name(self.chapter)).read_text(encoding="utf-8")
            check(WRITER, version_file_name(self.chapter, current.version))
            write_project_file(self.root, version_file_name(self.chapter, current.version), old, action)
            number = max([current.version] + [v.number for v in self.versions(project)]) + 1
        self._write(project, number, "draft", text, scenes, action)
        return number

    def _replace_scene_turn(self, scene: int, text: str) -> None:
        """Keep the saved chat in step with the chapter file after a scene changes."""
        assistant_turns = [i for i, m in enumerate(self.turns) if m.role == "assistant"]
        if 0 < scene <= len(assistant_turns):
            self.turns[assistant_turns[scene - 1]] = ChatMessage("assistant", text)

    def _reload(self, project: Project) -> ChapterText:
        path = self.root / chapter_file_name(self.chapter)
        chapter = parse_chapter_file(read_text(path)[0], chapter_file_name(self.chapter), self.chapter, IssueList())
        project.chapters[self.chapter] = chapter
        return chapter


def _draft_for_change(current: ChapterText) -> str:
    """The current draft as the model sees it in a change request (scene headings help it find a scene)."""
    if not current.scenes:
        return current.text
    return "\n\n".join(f"### Scene {n}\n{current.scenes[n]}" for n in sorted(current.scenes))


def _join(before: str, after: str) -> str:
    """Join a continuation onto the text it continues."""
    before, after = before.rstrip(), after.lstrip()
    if not before:
        return after
    if before[-1] in '.!?"”’)…':
        return f"{before}\n\n{after}"
    return f"{before} {after}"


def _body_words(file_text: str) -> int:
    issues = IssueList()
    return parse_chapter_file(file_text, "", 0, issues).words
