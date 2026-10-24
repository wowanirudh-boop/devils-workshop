"""Loading a whole story project folder."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from devils_workshop.project.story_settings import SETTINGS_FILE, is_story_folder, migrate, settings_path
from devils_workshop.project.chapters import CHAPTER_FILE_RE, ChapterText, parse_chapter_file
from devils_workshop.project.entries import ENTRY_FOLDERS, Entry, parse_entry
from devils_workshop.project.files import read_text, relative_name
from devils_workshop.project.issues import IssueList
from devils_workshop.project.markdown import is_blank
from devils_workshop.project.outline import Outline, parse_outline
from devils_workshop.project.own_files import Document, load_own_files
from devils_workshop.project.story_config import StoryConfig, parse_story_config
from devils_workshop.project.subplots import Subplot, parse_subplot

STORY_FILE = SETTINGS_FILE  # Story settings.md (story.yaml before v0.17: turned into it on first open)
SYSTEM_PROMPT_FILE = "system_prompt.md"
OUTLINE_FILE = "outline.md"
BIBLE_FILES = {
    "style_guide": "bible/style_guide.md",
    "story_bible": "bible/story_bible.md",
    "world_rules": "bible/world_rules.md",
}


class NotAProjectError(Exception):
    """The folder doesn't exist or has no story.yaml."""


@dataclass
class Project:
    root: Path
    config: StoryConfig
    system_prompt: str | None          # None if system_prompt.md is missing
    bible: dict[str, str | None]       # key -> text (None if the file is missing)
    entries: list[Entry]
    subplots: list[Subplot]
    outline: Outline
    chapters: dict[int, ChapterText]
    issues: IssueList = field(default_factory=IssueList)
    documents: list[Document] = field(default_factory=list)  # your own files sent with every chapter
    default_system: list[tuple[str, str]] = field(default_factory=list)  # (file, text): default instructions
    duplicates: list = field(default_factory=list)  # your own files sent twice (project/duplicates.py)

    def system_texts(self) -> list[str]:
        """The start of the system message: your default instructions, then this story's system prompt."""
        texts = [text for _, text in self.default_system if not is_blank(text)]
        if self.system_prompt and not is_blank(self.system_prompt):
            texts.append(self.system_prompt)
        return texts

    @property
    def own_files(self) -> bool:
        """True when the story is kept in your own files (story.yaml has 'files:')."""
        return self.config.own_files is not None

    def find_entry(self, name: str) -> Entry | None:
        """Find an entry by its name or any alias (not case-sensitive)."""
        wanted = name.strip().lower()
        for entry in self.entries:
            if any(n.lower() == wanted for n in entry.all_names()):
                return entry
        return None


def load_project(root: Path) -> Project:
    root = Path(root)
    if not root.is_dir():
        raise NotAProjectError(f"Folder not found: {root}")
    if not is_story_folder(root):
        raise NotAProjectError(f"{root} has no {STORY_FILE}, so it isn't a story project folder")
    migrate(root)
    story_path = settings_path(root)

    issues = IssueList()
    config = parse_story_config(_read(story_path, root, issues), STORY_FILE, issues)
    if config.own_files is not None:
        duplicates: list = []
        system_prompt, documents, outline = load_own_files(root, config.own_files, issues, duplicates)
        chapters = _load_chapters(root, issues, quiet=True)
        return _add_defaults(Project(root, config, system_prompt, {key: None for key in BIBLE_FILES}, [], [],
                                     outline, chapters, issues, documents, duplicates=duplicates))

    system_prompt_path = root / SYSTEM_PROMPT_FILE
    system_prompt = _read(system_prompt_path, root, issues) if system_prompt_path.is_file() else None

    bible = {}
    for key, name in BIBLE_FILES.items():
        path = root / name
        bible[key] = _read(path, root, issues) if path.is_file() else None

    entries: list[Entry] = []
    for folder in ENTRY_FOLDERS:
        for path in _markdown_files(root / folder, root, issues):
            rel = relative_name(path, root)
            entries.append(parse_entry(_read(path, root, issues), rel, folder, issues))

    subplots = [
        parse_subplot(_read(path, root, issues), relative_name(path, root), issues)
        for path in _markdown_files(root / "subplots", root, issues)
    ]

    outline_path = root / OUTLINE_FILE
    if outline_path.is_file():
        outline = parse_outline(_read(outline_path, root, issues), OUTLINE_FILE, issues)
    else:
        outline = Outline([], "")

    chapters = _load_chapters(root, issues)
    return _add_defaults(Project(root, config, system_prompt, bible, entries, subplots, outline, chapters, issues))


def _add_defaults(project: Project) -> Project:
    """Your default instructions (unless the story switches them off). A file whose text the story already
    has (its own copy) isn't added again."""
    from devils_workshop.project.default_instructions import KEY_PREFIX, WRITER_ROLES, load_defaults
    from devils_workshop.project.own_files import SYSTEM

    if not project.config.default_instructions:
        return project
    have = {_same(d.text) for d in project.documents} | {_same(t) for t in project.bible.values() if t}
    have.add(_same(project.system_prompt or ""))
    documents = []
    for default in load_defaults():
        if not default.text.strip() or _same(default.text) in have:
            continue
        if default.role == SYSTEM:
            project.default_system.append((default.name, default.text))
        elif default.role in WRITER_ROLES:
            documents.append(Document(KEY_PREFIX + default.name, default.name, default.text))
    project.documents = documents + project.documents
    return project


def _same(text: str) -> str:
    return " ".join(text.split())


def _read(path: Path, root: Path, issues: IssueList) -> str:
    text, encoding = read_text(path)
    if encoding != "utf-8":
        issues.info(relative_name(path, root), None,
                    "This file isn't saved as UTF-8; it was read as Windows-1252",
                    "Saving it as UTF-8 avoids garbled special characters.")
    return text


def _markdown_files(folder: Path, root: Path, issues: IssueList) -> list[Path]:
    """Story files in a folder, sorted. Names starting with '_' or '.' and README files are skipped."""
    if not folder.is_dir():
        return []
    result = []
    for path in sorted(folder.iterdir()):
        if path.name.startswith(("_", ".")) or path.name.lower() == "readme.md" or path.is_dir():
            continue
        if path.suffix.lower() != ".md":
            issues.info(relative_name(path, root), None, "Not a .md file, so it is ignored")
            continue
        result.append(path)
    return result


def _load_chapters(root: Path, issues: IssueList, quiet: bool = False) -> dict[int, ChapterText]:
    """The written chapters. quiet: don't mention other files in chapters/ (they may be your own)."""
    folder = root / "chapters"
    chapters: dict[int, ChapterText] = {}
    if not folder.is_dir():
        return chapters
    for path in sorted(folder.iterdir()):
        if path.is_dir() or path.name.startswith((".", "_")):
            continue
        rel = relative_name(path, root)
        match = CHAPTER_FILE_RE.match(path.name)
        if not match:
            if not quiet and not path.name.endswith(".partial.md"):
                issues.info(rel, None, "Not named like ch01.md, so it isn't treated as a chapter")
            continue
        number = int(match.group(1))
        if number in chapters:
            issues.error(rel, None, f"Two files for chapter {number} ({chapters[number].path} and {rel})")
            continue
        chapters[number] = parse_chapter_file(_read(path, root, issues), rel, number, issues)
    return chapters
