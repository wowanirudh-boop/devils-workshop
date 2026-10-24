"""A loud warning, on every screen where it matters, when two of your files are the same document and both
are sent (e.g. a handoff saved as .md and as .docx): with one click, stop sending the copy."""

from __future__ import annotations

from pathlib import Path


from devils_workshop.project.file_ops import FileOpError, stop_sending
from devils_workshop.project.own_files import OUTLINE
from devils_workshop.project.project import Project
from devils_workshop.ui.components import notice, smooth_reload, toast


def duplicates_notice(project: Project) -> None:
    """Nothing if no file is sent twice."""
    for found in project.duplicates:
        roles = project.config.own_files or {}
        outline = roles.get(found.copy) == OUTLINE == roles.get(found.keep)
        effect = ("so every chapter gets its instructions twice, and written scene by scene, its scenes twice "
                  "(the same scenes again after the first set)." if outline else "so the model gets it twice.")
        text = (f"Two of your files are the same document: {found.copy} repeats {found.keep} "
                f"({found.similarity:.0%} the same). Both are sent, {effect} Stop sending the copy before writing.")

        def fix(copy: str = found.copy) -> None:
            try:
                stop_sending(project.root, copy)
            except FileOpError as exc:
                toast(str(exc), "warning")
                return
            toast(f"{copy} is no longer sent. Undo it in History if you need it back.")
            smooth_reload()

        notice("problem", text, f"Stop sending {Path(found.copy).name}", fix).mark("duplicate-notice")
