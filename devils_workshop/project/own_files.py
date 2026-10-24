"""Stories kept in your own files (any text file: .md, .txt, .json…; PDF and Word too), used as they are.

Story settings.md lists each file with what it is for, under ## Files:

    - World and characters.md: every chapter
    - Plot outline.pdf: chapter outline
    - Chapter 3 notes.txt: chapter 3
    - Voice.md: system prompt
    - Old ideas.md: not sent

- every chapter: sent with every chapter's request.
- chapter outline: split at its "Chapter N" headings; each chapter gets its own part.
  Text before the first chapter heading is sent with every chapter.
- chapter N: this file is the instructions for chapter N only.
- system prompt: sent as the system message.
- not sent: never sent.

"Scene N" headings inside a chapter's instructions make scenes, for scene mode.
Nothing is converted, moved or rewritten. Scenes the app proposes (and you
approve) are kept separately in app-scenes.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


from devils_workshop.project.files import read_text, relative_name
from devils_workshop.project.issues import IssueList
from devils_workshop.project.outline import Outline, OutlineChapter, Scene, parse_outline

SYSTEM = "system prompt"
EVERY = "every chapter"
OUTLINE = "chapter outline"
NOT_SENT = "not sent"
ROLE_HINT = "Use one of: every chapter, chapter outline, chapter 3 (any number), system prompt, not sent."

STORY_FILE = "Story settings.md"  # see story_settings.py
LEGACY_STORY_FILE = "story.yaml"   # before v0.17
SCENES_FILE = "app-scenes.md"
REPORT_FILE = "Manuscript check.md"  # the app's report on the whole manuscript (planner/manuscript.py)
PLANNER_OUTLINE = "Chapter outline.md"  # the planner's outline (planner/output.py): its headings are strictly in order
DOCUMENTS = (".pdf", ".docx")  # read with their own readers; any other file is read as text, if it is text
# Files that are never text, whatever is in their first bytes.
_BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tif", ".tiff", ".heic", ".psd", ".svgz",
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".zip", ".7z", ".rar", ".gz", ".tar", ".exe", ".dll", ".msi", ".bin", ".iso",
    ".doc", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".epub", ".pages",
    ".ttf", ".otf", ".woff", ".woff2", ".sqlite", ".db",
}
_UNICODE_BOMS = (b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")
# Folders and files the app itself writes into the story folder.
APP_FOLDERS = {"logs", "sessions", ".history", ".import-review", "exports", "planner"}
_APP_CHAPTER_FILE = re.compile(r"^chapters/(ch\d+\.md|ch\d+(\.s\d+)?\.partial\.md|versions/.*)$")
_IGNORED_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}


@dataclass
class Document:
    """A file (or the part of one) that is sent with every chapter."""
    key: str     # stable id: the file path, or "path#before-chapters"
    label: str   # what the review screen shows
    text: str


# --- which files are there ------------------------------------------------------------

def list_story_files(root: Path) -> tuple[list[str], list[str]]:
    """(files the app can read, other files), as project-relative paths. The app's own files are left out."""
    readable, other = [], []
    for path in sorted(root.rglob("*"), key=lambda p: relative_name(p, root).lower()):
        if not path.is_file():
            continue
        rel = relative_name(path, root)
        parts = rel.split("/")
        if parts[0] in APP_FOLDERS or any(p.startswith(".") for p in parts) or \
                rel in (STORY_FILE, LEGACY_STORY_FILE, SCENES_FILE, REPORT_FILE):
            continue
        if _APP_CHAPTER_FILE.match(rel) or rel.startswith("prompts/jobs/"):
            continue
        name = parts[-1].lower()
        if name in _IGNORED_NAMES or name.startswith("~$"):
            continue
        (other if looks_binary(path) else readable).append(rel)
    return readable, other


def looks_binary(path: Path) -> bool:
    """True for a picture, archive, program or other file with no text the app can send."""
    suffix = path.suffix.lower()
    if suffix in DOCUMENTS:
        return False
    if suffix in _BINARY_SUFFIXES:
        return True
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
    except OSError:
        return False
    return data_looks_binary(suffix, head)


def data_looks_binary(suffix: str, head: bytes) -> bool:
    """The same test for a file's first bytes (before it is saved anywhere)."""
    suffix = suffix.lower()
    if suffix in DOCUMENTS:
        return False
    if suffix in _BINARY_SUFFIXES:
        return True
    return not head.startswith(_UNICODE_BOMS) and b"\x00" in head[:4096]


def binary_problem(name: str) -> str:
    """Why nothing can be sent from a file that isn't text, and what to do instead."""
    suffix = Path(name).suffix.lower()
    if suffix == ".doc":
        return "Old Word (.doc) files can't be read; open it in Word and save it as .docx"
    if suffix in (".odt", ".pages", ".epub"):
        return "This kind of document can't be read; save or export it as .docx, PDF or plain text"
    if suffix in (".xls", ".xlsx", ".ods"):
        return "Spreadsheets can't be read; save the sheet as CSV (a text file) to use it"
    return "This isn't a text file (it looks like a picture, archive or program), so nothing can be sent from it"


def is_editable(path: Path) -> bool:
    """A text file the app can open in its editor (not PDF, Word or a binary file)."""
    return path.suffix.lower() not in DOCUMENTS and not looks_binary(path)


def text_copy_name(folder: Path, name: str) -> str:
    """A free name for an editable text copy of a PDF or Word file: "Voice.pdf" -> "Voice.md" (or "Voice (text).md")."""
    path = Path(name)
    stem, parent = path.stem, path.parent.as_posix()
    for candidate in [f"{stem}.md"] + [f"{stem} (text{'' if n == 1 else f' {n}'}).md" for n in range(1, 50)]:
        rel = candidate if parent in ("", ".") else f"{parent}/{candidate}"
        if not (folder / rel).exists():
            return rel
    raise ValueError(f"No free name for a text copy of {name}")


def make_text_copy(root: Path, rel: str) -> str:
    """Edit a PDF or Word file as text: its text (as the app reads it) is saved as an editable .md file that
    does the original's job from now on; the original is kept, and no longer sent. Returns the copy's name."""
    from devils_workshop.history import write_project_file
    from devils_workshop.project.project import load_project

    text, problem = extract_text(root / rel)
    if not text.strip():
        raise ValueError(problem or f"No text could be read from {rel}")
    copy = text_copy_name(root, rel)
    write_project_file(root, copy, text.strip() + "\n", "edit-as-text")
    config = load_project(root).config
    if config.own_files is not None:
        roles = dict(config.own_files)
        roles[copy] = roles.get(rel, NOT_SENT)
        if rel in roles:
            roles[rel] = NOT_SENT
        existing = read_text(root / STORY_FILE)[0]
        write_project_file(root, STORY_FILE, settings_text(roles, config.title, config.chapter_words, existing),
                           "your-files")
    return copy


# --- reading ----------------------------------------------------------------------------

_PDF_CACHE: dict[tuple[str, int, int], tuple[str, str]] = {}


def extract_text(path: Path) -> tuple[str, str]:
    """(text, problem). PDFs are read with pypdf, Word files with the standard library; problem is ""
    when all went well."""
    if path.suffix.lower() == ".docx":
        return _read_docx(path)
    if path.suffix.lower() != ".pdf":
        if looks_binary(path):
            return "", binary_problem(path.name)
        raw = path.read_bytes()
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):  # Notepad's "Unicode" (UTF-16)
            return raw.decode("utf-16", errors="replace"), ""
        text, encoding = read_text(path)
        problem = "" if encoding == "utf-8" else "Not saved as UTF-8; read as Windows-1252"
        return text, problem
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    if key not in _PDF_CACHE:
        _PDF_CACHE[key] = _read_pdf(path)
    return _PDF_CACHE[key]


def _read_docx(path: Path) -> tuple[str, str]:
    """The text of a Word file: one line per paragraph, headings included (formatting is dropped)."""
    import zipfile
    from xml.etree import ElementTree

    word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return "", "This Word file couldn't be read (it may be damaged, or not really a Word file)"
    paragraphs = []
    for para in root.iter(f"{word}p"):
        pieces = []
        for node in para.iter():
            if node.tag == f"{word}t" and node.text:
                pieces.append(node.text)
            elif node.tag in (f"{word}tab",):
                pieces.append("\t")
            elif node.tag in (f"{word}br", f"{word}cr"):
                pieces.append("\n")
        paragraphs.append("".join(pieces))
    text = "\n\n".join(p for p in paragraphs if p.strip())
    return (text, "") if text else ("", "No text found in this Word file")


def _read_pdf(path: Path) -> tuple[str, str]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception:  # a damaged or encrypted PDF
        return "", "This PDF couldn't be read (it may be damaged or password-protected)"
    empty = sum(1 for p in pages if not p)
    text = "\n\n".join(p for p in pages if p).replace("\r\n", "\n")
    if not text:
        return "", "No text found in this PDF (it may be scanned images, which can't be read)"
    if empty:
        return text, f"{empty} of {len(pages)} pages have no text (scanned?) and were skipped"
    return text, ""


# --- chapter and scene headings ---------------------------------------------------------------

_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
          "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_WORD_NUMBER = (rf"(?:{'|'.join(_TENS)})(?:[\s-]*(?:{'|'.join(_UNITS[1:10])}))?"
                rf"|{'|'.join(sorted(_UNITS[1:], key=len, reverse=True))}")
_NUMBER = rf"\d{{1,3}}|{_WORD_NUMBER}|[ivxlc]+"
_ROMAN = re.compile(r"^c{0,3}(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")
_HEADING = {
    # "Ch" only as "Ch. 3", "Ch 3" or a line that is just "ch03" (so "CH4-rich air" isn't a heading).
    "chapter": re.compile(rf"^(?:chapter\s*({_NUMBER})|ch(?:\.\s*|\s+)(\d{{1,3}})|ch(\d{{1,3}})(?=\s*$))\b(.*)$",
                          re.IGNORECASE),
    "scene": re.compile(rf"^scene\s*({_NUMBER})()()\b(.*)$", re.IGNORECASE),
}
_DECORATION = re.compile(r"^(?:#{1,6}\s*|[-*•]\s+|\d{1,3}[.)]\s+)")
_SEPARATOR = re.compile(r"^\s*[:.\-–—)|]+\s*")
_RANGE = re.compile(r"^\s*(?:[-–—]|to|through|thru|and|&)\s*\d", re.IGNORECASE)  # "–16", " to 5", " & 4"
MAX_PLAIN_HEADING = 60    # "Chapter 3 The Flood" without ":" or "-": longer plain lines are sentences
MAX_TITLE = 80            # a longer "title" is the start of the chapter's instructions


def number_value(raw: str) -> int | None:
    raw = raw.lower().strip()
    if raw.isdigit():
        return int(raw)
    if _ROMAN.match(raw) and raw:
        values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
        total = 0
        for i, char in enumerate(raw):
            value = values[char]
            total += -value if i + 1 < len(raw) and values[raw[i + 1]] > value else value
        return total
    words = re.split(r"[\s-]+", raw)
    if len(words) == 1 and words[0] in _UNITS:
        return _UNITS.index(words[0])
    if words and words[0] in _TENS:
        tens = (_TENS.index(words[0]) + 2) * 10
        if len(words) == 1:
            return tens
        if len(words) == 2 and words[1] in _UNITS[1:10]:
            return tens + _UNITS.index(words[1])
    return None


def match_heading(line: str, kind: str = "chapter") -> tuple[int, str] | None:
    """(number, title) if the line is a "Chapter N" (or "Scene N") heading."""
    stripped = line.strip()
    is_markdown = stripped.startswith("#")
    text = _DECORATION.sub("", stripped)
    text = text.strip("*_ ").rstrip("#").strip("*_ ")
    match = _HEADING[kind].match(text)
    if not match:
        return None
    number = number_value(match.group(1) or match.group(2) or match.group(3))
    if number is None or number < 1:
        return None
    rest = match.group(4)
    if _RANGE.match(rest):
        return None   # "Chapter 01–16: Oren stays aboard…": a note about several chapters, not a heading
    separated = _SEPARATOR.match(rest)
    if separated:
        title = rest[separated.end():]
    elif not rest.strip():
        title = ""
    elif is_markdown or (len(stripped) <= MAX_PLAIN_HEADING and
                         not stripped.rstrip("*_ ").endswith((".", "!", "?", ","))):
        title = rest  # "Chapter 3 The Flood"
    else:
        return None   # a sentence that starts with "Chapter 3 ..."
    return number, title.strip().strip("*_").strip()


@dataclass
class Part:
    number: int
    title: str
    text: str
    line: int


def split_at_headings(text: str, kind: str = "chapter", strict: bool = False,
                      rejected: list[tuple[int, int]] | None = None) -> tuple[str, list[Part]]:
    """(text before the first heading, one part per heading).

    strict (the planner's Chapter outline.md, whose headings the app writes in order): a heading line whose
    number isn't the next one (a repeat, or one going back) is text of the part it is in, never a part of its
    own, so it can't take that text to another chapter or scene. Each one is added to `rejected` as
    (its line, its number)."""
    before: list[str] = []
    parts: list[Part] = []
    for number, line in enumerate(text.split("\n"), 1):
        found = match_heading(line, kind)
        if found and strict and parts and found[0] != parts[-1].number + 1:
            if rejected is not None:
                rejected.append((number, found[0]))
            found = None
        if found:
            title = found[1]
            body = []
            if len(title) > MAX_TITLE:
                title, body = "", [found[1]]
            parts.append(Part(found[0], title, "", number))
            parts[-1].text = "\n".join(body)
            continue
        if parts:
            parts[-1].text += ("\n" if parts[-1].text else "") + line
        else:
            before.append(line)
    for part in parts:
        part.text = part.text.strip()
    return "\n".join(before).strip(), parts


def split_scenes(text: str, strict: bool = False, rejected: list[tuple[int, int]] | None = None,
                 renumbered: list[tuple[int, int]] | None = None) -> tuple[str, list[Scene]]:
    """A chapter's instructions: (text before the first scene, scenes numbered 1, 2, 3... in order). strict (the
    planner's outline): a scene heading out of order is text of the scene it is in. A heading whose own number
    isn't its place (your own outline: "Scene 1", "Scene 3") is added to `renumbered` as (its number, its place)."""
    intro, parts = split_at_headings(text, "scene", strict, rejected)
    if renumbered is not None:
        renumbered += [(p.number, n) for n, p in enumerate(parts, 1) if p.number != n]
    return intro, [Scene(n, p.title, {}, p.text, p.line) for n, p in enumerate(parts, 1)]


# --- guessing what a file is for (the setup screen) -------------------------------------------------

_SYSTEM_NAME = re.compile(r"system[\s_-]*prompt", re.IGNORECASE)
# Names that usually mean "not for the model": old ideas, scraps, backups.
_SET_ASIDE = re.compile(r"\b(old|ideas?|unused|scraps?|backup|archive|deleted|discarded)\b", re.IGNORECASE)
# "Chapter 4 notes", "notes_ch07", "ch3": a chapter number in a file name ("Beach 2" doesn't count).
_NAME_CHAPTER = re.compile(r"(?<![a-z])(?:chapter|ch)[\s._-]*(\d{1,3})(?!\d)", re.IGNORECASE)


def guess_role(rel: str, text: str) -> str:
    stem = Path(rel).stem
    if _SYSTEM_NAME.search(stem):
        return SYSTEM
    named = match_heading(stem.replace("_", " "))
    if named:
        return f"chapter {named[0]}"
    loose = _NAME_CHAPTER.search(stem)
    if loose and int(loose.group(1)) > 0:
        return f"chapter {int(loose.group(1))}"
    if len(split_at_headings(text)[1]) >= 2:
        return OUTLINE
    if Path(rel).name.lower() in ("readme.md", "readme.txt") or _SET_ASIDE.search(stem):
        return NOT_SENT
    return EVERY


def parse_role(value) -> str | None:
    """The role in its standard spelling, or None if it isn't one."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    if text in (SYSTEM, EVERY, OUTLINE, NOT_SENT):
        return text
    if text in ("never", "no", "skip", "ignore"):
        return NOT_SENT
    if text in ("always", "every chapter (always)", "all chapters"):
        return EVERY
    if text == "outline":
        return OUTLINE
    match = re.fullmatch(r"(?:chapter|ch\.?)\s*(\d{1,3})(?: only)?", text)
    if match and int(match.group(1)) > 0:
        return f"chapter {int(match.group(1))}"
    return None


def chapter_of(role: str) -> int | None:
    match = re.fullmatch(r"chapter (\d+)", role)
    return int(match.group(1)) if match else None


# --- saving the list (Story settings.md) ------------------------------------------------------------

def settings_text(roles: dict[str, str], title: str, chapter_words: int, existing: str = "",
                  set_defaults: dict | None = None) -> str:
    """Story settings.md with this list of files (and any `set_defaults`). Other settings already in it (models,
    genre...) are kept; `existing` may be Story settings.md or an older story.yaml."""
    from devils_workshop.project.story_settings import parse_settings, settings_markdown
    try:
        data = parse_settings(existing)
    except ValueError:
        data = {}
    data = {k: v for k, v in data.items() if k != "files"}
    data["title"] = title
    defaults = data.get("defaults") if isinstance(data.get("defaults"), dict) else {}
    data["defaults"] = {**defaults, **(set_defaults or {}), "chapter_words": int(chapter_words)}
    data["files"] = dict(roles)
    return settings_markdown(data)


# --- loading -----------------------------------------------------------------------------------------

def load_own_files(root: Path, roles: dict[str, str], issues: IssueList,
                   duplicates: list | None = None) -> tuple[str | None, list[Document], Outline]:
    """(system prompt, documents sent with every chapter, the chapters) from the listed files. Two sent files
    that are the same document are a warning, and are added to `duplicates` if given."""
    from devils_workshop.project.duplicates import find_duplicates

    sent: dict[str, str] = {}
    system: list[str] = []
    documents: list[Document] = []
    parts: list[tuple[str, Part]] = []
    strict_files: set[str] = set()
    for rel, role in roles.items():
        path = root / rel
        if rel in (STORY_FILE, LEGACY_STORY_FILE, SCENES_FILE):  # the app's own files are never sent as yours
            issues.info(STORY_FILE, None, f"'{rel}' is the app's own file, so it isn't sent",
                        "Save on What's sent takes it off the list.")
            continue
        if not path.is_file():
            issues.warning(STORY_FILE, None, f"'{rel}' is listed in Story settings but isn't in the folder",
                           "It was renamed, moved or deleted. Open 'Your files' to update the list.")
            continue
        if role == NOT_SENT:
            continue
        text, problem = extract_text(path)
        if problem:
            issues.warning(rel, None, problem)
        if not text.strip():
            continue
        sent[rel] = text
        if role == SYSTEM:
            system.append(text.strip())
        elif role == EVERY:
            documents.append(Document(rel, rel, text))
        elif role == OUTLINE:
            strict = rel == PLANNER_OUTLINE
            rejected: list[tuple[int, int]] = []
            before, found = split_at_headings(text, strict=strict, rejected=rejected)
            for line, number in rejected:
                issues.error(rel, line, f"A 'Chapter {number}' heading out of order is kept as text of the chapter "
                                        "it is in", "The planner writes this file: plan the chapter again, or fix the "
                                                    "heading by hand.")
            if not found:
                issues.warning(rel, None, "No chapter headings found, so it is sent with every chapter instead",
                               "Chapter headings are lines like 'Chapter 3', 'Chapter 3: Title' or '# Chapter Three'.")
                documents.append(Document(rel, rel, text))
                continue
            if before:
                documents.append(Document(f"{rel}#before-chapters", f"{rel} (before the chapters)", before))
            parts += [(rel, part) for part in found]
            if strict:
                strict_files.add(rel)
        else:
            parts.append((rel, _chapter_file_part(rel, chapter_of(role), text)))

    for found in find_duplicates(sent):
        twice = (" Every chapter gets its instructions twice, and in scene mode its scenes twice (the same "
                 "scenes again after the first set)." if roles.get(found.copy) == OUTLINE == roles.get(found.keep)
                 else "")
        issues.warning(found.copy, None, f"Same document as '{found.keep}' ({found.similarity:.0%} the same), and "
                       f"both are sent, so the model gets it twice.{twice}",
                       f"Set '{found.copy}' to Not sent under What's sent (or press Stop sending the copy).")
        if duplicates is not None:
            duplicates.append(found)

    readable, other = list_story_files(root)
    for rel in readable:
        if rel not in roles:
            issues.warning(rel, None, "New file, not in your list yet, so it isn't sent",
                           "Open 'Your files' and choose what it is for.")
    for rel in other:
        issues.info(rel, None, binary_problem(rel) + "; it is ignored")

    chapters = _merge(parts, issues, strict_files)
    _add_app_scenes(root, chapters, issues)
    outline = Outline([chapters[n] for n in sorted(chapters)], "")
    return ("\n\n".join(system) if system else None), documents, outline


def _chapter_file_part(rel: str, number: int, text: str) -> Part:
    """A whole file for one chapter. Its title comes from a matching first heading, or the file name."""
    lines = text.strip().split("\n")
    first = match_heading(lines[0]) if lines and lines[0].strip() else None
    if first and first[0] == number:
        return Part(number, first[1], "\n".join(lines[1:]).strip(), 1)
    named = match_heading(Path(rel).stem.replace("_", " "))
    if named:
        title = named[1] if named[0] == number else ""
    else:
        title = "" if _NAME_CHAPTER.search(Path(rel).stem) else Path(rel).stem
    return Part(number, title, text.strip(), 1)


def _merge(parts: list[tuple[str, Part]], issues: IssueList,
           strict_files: set[str] = frozenset()) -> dict[int, OutlineChapter]:
    by_number: dict[int, list[tuple[str, Part]]] = {}
    for rel, part in parts:
        by_number.setdefault(part.number, []).append((rel, part))
    chapters = {}
    for number, found in by_number.items():
        if len(found) > 1:
            where = ", ".join(sorted({rel for rel, _ in found}))
            issues.info(found[0][0], found[1][1].line,
                        f"Chapter {number} has instructions in more than one place ({where}); all are used, in order")
        title = next((p.title for _, p in found if p.title), "")
        text = "\n\n".join(p.text for _, p in found if p.text)
        settings, text = _leading_words(text)
        strict = all(rel in strict_files for rel, _ in found)
        rejected: list[tuple[int, int]] = []
        renumbered: list[tuple[int, int]] = []
        intro, scenes = split_scenes(text, strict, rejected, renumbered)
        for given, place in renumbered:
            issues.warning(found[0][0], found[0][1].line, f"Chapter {number}: the heading 'Scene {given}' is scene "
                                                           f"{place} in order, so it is written as scene {place}")
        for _, scene_number in rejected:
            issues.error(found[0][0], found[0][1].line, f"Chapter {number}: a 'Scene {scene_number}' heading out of "
                                                         "order is kept as text of the scene it is in")
        if "words" not in settings:
            settings.update(stated_words(intro, lines=4, kind="chapter"))
        for scene in scenes:
            scene.settings, scene.text = _leading_words(scene.text)
            if "words" not in scene.settings:
                scene.settings.update(stated_words(scene.text, lines=2, kind="scene"))
        chapters[number] = OutlineChapter(number, title, settings, intro, found[0][1].line, scenes,
                                          source=found[0][0])
    return chapters


_WORDS_LINE = re.compile(r"^\s*words\s*:\s*([\d,]+)\s*$", re.IGNORECASE)
# How outlines state a length near the top of a chapter or scene: "Target 4,700 words", "Target length:
# about 5,000 words", "· 900 words" at the end of a short line, "(1,200 words)".
_TARGET = re.compile(r"\btarget(?:\s+length)?\s*[:\-–—]?\s*(?:of\s+)?(?:about\s+|around\s+|approx\.?\s*|~\s*)?"
                     r"(\d{1,2}(?:,\d{3})|\d{3,5})\s*words?\b", re.IGNORECASE)
_SHORT_COUNT = re.compile(r"(?:^|[·•|,;:–—\-(]\s*)(?:about\s+|around\s+|~\s*)?(\d{1,2},\d{3}|\d{3,5})\s*words?\b\W*$",
                          re.IGNORECASE)
MIN_STATED, MAX_STATED = 100, 30_000


def stated_words(text: str, lines: int, kind: str) -> dict:
    """{"words": N} if one of the first `lines` lines states the length, as outlines usually do; else {}.
    Chapters: "Target N words" anywhere in a line. Scenes also: a short line ending in "N words"."""
    found = [line.strip() for line in text.split("\n") if line.strip()][:lines]
    for line in found:
        plain = line.strip("*_ ")
        match = _TARGET.search(plain) or (_SHORT_COUNT.search(plain) if kind == "scene" and len(plain) <= 160
                                          else None)
        if match:
            words = int(match.group(1).replace(",", ""))
            if MIN_STATED <= words <= MAX_STATED:
                return {"words": words}
    return {}


def _leading_words(text: str) -> tuple[dict, str]:
    """A "words: 4500" line right under a chapter or scene heading sets its length (the planner writes one)."""
    lines = text.strip().split("\n")
    match = _WORDS_LINE.match(lines[0]) if lines else None
    if not match:
        return {}, text
    words = int(match.group(1).replace(",", ""))
    return ({"words": words} if words > 0 else {}), "\n".join(lines[1:]).strip()


def _add_app_scenes(root: Path, chapters: dict[int, OutlineChapter], issues: IssueList) -> None:
    path = root / SCENES_FILE
    if not path.is_file():
        return
    parsed = parse_outline(read_text(path)[0], SCENES_FILE, issues)
    for chapter in parsed.chapters:
        target = chapters.get(chapter.number)
        if target is None:
            issues.warning(SCENES_FILE, chapter.line, f"Chapter {chapter.number} isn't in your files any more")
        elif target.scenes:
            issues.info(SCENES_FILE, chapter.line, f"Chapter {chapter.number} has scenes in your own files, "
                        "so the ones here are ignored")
        elif chapter.scenes:
            target.scenes = chapter.scenes
