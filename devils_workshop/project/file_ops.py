"""Adding, renaming and deleting a story's files (Story files → Files), and removing a whole story.

Every change to the files takes one snapshot first (history.py) of everything it touches, so one
Undo in History puts it all back: the file and, for a story in your own files, the list in
Story settings.md too. A whole story folder goes to the Windows Recycle Bin instead, so it can be
restored from there.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from devils_workshop.project.story_settings import is_story_folder
from devils_workshop.history import snapshot, write_project_file
from devils_workshop.project.files import read_text, write_text
from devils_workshop.project.own_files import (
    NOT_SENT,
    SCENES_FILE,
    LEGACY_STORY_FILE,
    STORY_FILE,
    binary_problem,
    data_looks_binary,
    extract_text,
    guess_role,
    list_story_files,
    settings_text,
)
from devils_workshop.project.project import BIBLE_FILES, OUTLINE_FILE, SYSTEM_PROMPT_FILE, load_project


class FileOpError(Exception):
    """Why a file can't be added, renamed or deleted, in plain words."""


# What a file added to a story made by the app is used as: key -> (label, folder for a new file, or the
# one file it replaces). Your own files are added as they are instead (see add_own_file).
ADD_AS = {
    "character": ("A character", "characters", None),
    "place": ("A place", "places", None),
    "lore": ("Lore", "lore", None),
    "subplot": ("A subplot", "subplots", None),
    "note": ("A note (never sent)", "notes", None),
    "outline": ("The outline (replaces it)", None, OUTLINE_FILE),
    "system": ("The system prompt (replaces it)", None, SYSTEM_PROMPT_FILE),
    "style_guide": ("The style guide (replaces it)", None, BIBLE_FILES["style_guide"]),
    "story_bible": ("The story bible (replaces it)", None, BIBLE_FILES["story_bible"]),
    "world_rules": ("The world rules (replaces it)", None, BIBLE_FILES["world_rules"]),
}
_NEEDS_NAME = ("characters", "places", "lore", "subplots")  # these files start with a 'name:' block


def safe_name(name: str) -> str:
    """A file name Windows accepts: no folders, no reserved characters."""
    name = Path(name.replace("\\", "/")).name
    return re.sub(r'[<>:"/\\|?*]+', "-", name).strip(" .")


def protected(rel: str) -> str:
    """Why this file can't be renamed or deleted here ("" if it can)."""
    if rel in (STORY_FILE, LEGACY_STORY_FILE):
        return f"{rel} holds the story's settings, so it can't be renamed or deleted."
    if rel == SCENES_FILE:
        return "app-scenes.md holds the scenes you approved, so it can't be renamed or deleted here."
    if re.fullmatch(r"chapters/(ch\d+\.md|ch\d+(\.s\d+)?\.partial\.md|versions/.*)", rel):
        return ("Written chapters are kept with all their versions; change them on the chapter's screen "
                "(⋯ → Restore an earlier version).")
    return ""


def _own_list(root: Path):
    """(the story's config, its list of your own files, or None for a story made by the app)."""
    config = load_project(root).config
    return config, (dict(config.own_files) if config.own_files is not None else None)


def _save_list(root: Path, config, roles: dict[str, str]) -> None:
    existing = read_text(root / STORY_FILE)[0]
    write_text(root / STORY_FILE, settings_text(roles, config.title, config.chapter_words, existing))


def _text_of(name: str, data: bytes) -> tuple[str, str]:
    """(text, problem) of a file's contents, read the way the app reads that kind of file."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ("file" + Path(name).suffix.lower())
        path.write_bytes(data)
        return extract_text(path)


# --- adding -----------------------------------------------------------------------------------------

def add_own_file(root: Path, name: str, data: bytes) -> tuple[str, str]:
    """Copy a file into a story in your own files. Returns (its name, what it is for).

    A new file gets the same guess the setup screen makes (change it under What's sent); a file with the
    same name is replaced and keeps what it was for. Undo removes the new file (or puts the old one back).
    """
    root = Path(root)
    rel = safe_name(name)
    if not rel:
        raise FileOpError(f"{name}: give the file a name")
    if data_looks_binary(Path(rel).suffix, data[:4096]):
        raise FileOpError(f"{name}: {binary_problem(rel)}")
    if rel.lower() in (STORY_FILE.lower(), LEGACY_STORY_FILE, SCENES_FILE):
        raise FileOpError(f"{rel} is the app's own file (this story's settings or scenes), so it wasn't added")
    config, roles = _own_list(root)
    if roles is None:
        raise FileOpError("This story was made by the app; choose what the file is for when you add it")
    snapshot(root, [rel, STORY_FILE], f"add-{rel}")
    (root / rel).write_bytes(data)
    if rel not in roles:
        roles[rel] = NOT_SENT if duplicate_of(root, rel, roles) else guess_role(rel, extract_text(root / rel)[0])
        _save_list(root, config, roles)
    return rel, roles[rel]


def duplicate_of(root: Path, rel: str, roles: dict[str, str]) -> str | None:
    """The sent file that `rel` is the same document as (e.g. its .md, for a .docx), if any."""
    from devils_workshop.project.duplicates import SAME, similarity
    root = Path(root)
    text = extract_text(root / rel)[0]
    for other, role in roles.items():
        if other != rel and role != NOT_SENT and (root / other).is_file() \
                and similarity(text, extract_text(root / other)[0]) >= SAME:
            return other
    return None


def add_story_file(root: Path, name: str, data: bytes, kind: str) -> str:
    """Add a file to a story made by the app, as `kind` (see ADD_AS): any text file (.md, .txt, .json…),
    PDF or Word, saved as Markdown text. A character, place, lore or subplot file gets a 'name:' block if it
    has none. Returns its path."""
    if kind not in ADD_AS:
        raise FileOpError(f"Unknown kind of file: {kind}")
    text, problem = _text_of(name, data)
    if not text.strip():
        raise FileOpError(f"{name}: {problem or 'it has no text'}")
    _, folder, target = ADD_AS[kind]
    title = Path(safe_name(name)).stem.replace("_", " ").replace("-", " ").strip() or "Untitled"
    if target:
        rel = target
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "untitled"
        rel = f"{folder}/{slug}.md"
        if folder in _NEEDS_NAME and not text.lstrip().startswith("---"):
            text = f"---\nname: {title}\n---\n\n{text.strip()}\n"
    write_project_file(root, rel, text, f"add-{rel}")
    return rel


# --- renaming and deleting --------------------------------------------------------------------------

def rename_file(root: Path, rel: str, new_name: str) -> str:
    """Rename a file in its own folder (the extension stays). Returns the new path."""
    root = Path(root)
    reason = protected(rel)
    if reason:
        raise FileOpError(reason)
    old = root / rel
    if not old.is_file():
        raise FileOpError(f"{rel} isn't there any more")
    name = safe_name(new_name)
    if not Path(name).stem:
        raise FileOpError("Give it a name")
    if Path(name).suffix.lower() != old.suffix.lower():
        name += old.suffix
    new_rel = f"{rel.rsplit('/', 1)[0]}/{name}" if "/" in rel else name
    if new_rel == rel:
        return rel
    new = root / new_rel
    if new.exists() and not (sys.platform == "win32" and new_rel.lower() == rel.lower()):
        raise FileOpError(f"There is already a file called {name}")
    config, roles = _own_list(root)
    listed = roles is not None and rel in roles
    snapshot(root, [rel] + ([new_rel] if new_rel.lower() != rel.lower() else []) +
             ([STORY_FILE] if listed else []), f"rename-{rel} → {new_rel}")
    old.rename(new)
    if listed:
        _save_list(root, config, {(new_rel if r == rel else r): role for r, role in roles.items()})
    return new_rel


def delete_file(root: Path, rel: str) -> None:
    """Delete a file (Undo in History brings it back); a story in your own files also stops listing it."""
    root = Path(root)
    reason = protected(rel)
    if reason:
        raise FileOpError(reason)
    path = root / rel
    if not path.is_file():
        raise FileOpError(f"{rel} isn't there any more")
    config, roles = _own_list(root)
    listed = roles is not None and rel in roles
    snapshot(root, [rel] + ([STORY_FILE] if listed else []), f"delete-{rel}")
    path.unlink()
    if listed:
        del roles[rel]
        _save_list(root, config, roles)
    if roles is not None:  # your own folder: don't leave a subfolder behind that the delete emptied
        parent = path.parent
        while parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


# --- removing a whole story -------------------------------------------------------------------------

def story_folder_problem(folder: Path) -> str:
    """Why this folder shouldn't be moved to the Recycle Bin as one story ("" if it can)."""
    folder = Path(folder)
    if not folder.is_dir():
        return "This folder can't be found any more."
    if not is_story_folder(folder):
        return "This folder isn't a story folder (it has no Story settings.md)."
    resolved, home = folder.resolve(), Path.home().resolve()
    if resolved == Path(resolved.anchor) or resolved == home or resolved in home.parents:
        return f"{resolved} is a whole drive or user folder, so the app won't delete it."
    if resolved.parent == home:
        return f"{resolved} is one of your main folders (like Documents), so the app won't delete it."
    if any(p.is_dir() and is_story_folder(p) for p in resolved.iterdir()):
        return "This folder holds other stories too, so the app won't delete it."
    return ""


def own_files_inside(folder: Path) -> list[str]:
    """Your own files in a story folder (what deleting the folder would also remove)."""
    readable, other = list_story_files(Path(folder))
    return readable + other


def trash_folder() -> Path:
    """Where deleted stories go where there is no Recycle Bin (a Linux server): Trash in the app's folder."""
    from devils_workshop.settings import settings_dir
    return settings_dir() / "Trash"


def move_to_recycle_bin(path: Path) -> Path | None:
    """Move a file or folder to the Windows Recycle Bin (where it can be restored from). Elsewhere it moves
    into trash_folder(), named with when it was deleted, and that path is returned."""
    path = Path(path).resolve()
    if sys.platform != "win32":
        import shutil
        import time
        target = trash_folder() / f"{path.name} (deleted {time.strftime('%Y-%m-%d %H.%M.%S')})"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        return target
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    fo_delete = 3
    # Allow undo (= Recycle Bin), no "are you sure" (the app asked already), no progress window, no error
    # pop-ups; but do ask before deleting for good on a drive without a Recycle Bin.
    flags = 0x0040 | 0x0010 | 0x0004 | 0x0400 | 0x4000
    operation = SHFILEOPSTRUCTW(None, fo_delete, str(path) + "\0", None, flags, False, None, None)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted or path.exists():
        raise OSError(f"Windows couldn't move {path} to the Recycle Bin (error {result})")
    return None


def stop_sending(root: Path, rel: str) -> None:
    """Set one of your own files to Not sent (e.g. a second copy of the outline); Undo in History puts it back."""
    root = Path(root)
    config, roles = _own_list(root)
    if roles is None or rel not in roles:
        raise FileOpError(f"{rel} isn't on this story's list")
    roles[rel] = NOT_SENT
    existing = read_text(root / STORY_FILE)[0]
    write_project_file(root, STORY_FILE, settings_text(roles, config.title, config.chapter_words, existing),
                       f"not-sent-{rel}")
