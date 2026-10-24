"""Shared test helpers. All tests use the invented sample story, The Saltmarsh Clock."""

from __future__ import annotations

from pathlib import Path

import pytest

from devils_workshop.project.issues import IssueList
from devils_workshop.project.newproject import create_project


@pytest.fixture(autouse=True)
def app_folder(tmp_path_factory, monkeypatch) -> Path:
    """Each test gets its own empty app folder (settings, default instructions), never the real one."""
    folder = tmp_path_factory.mktemp("app")
    monkeypatch.setenv("DW_SETTINGS_DIR", str(folder))
    return folder


@pytest.fixture(autouse=True)
def credential_store():
    """Each test gets an empty in-memory credential store, never the real one (Windows Credential Manager)."""
    import keyring
    from keyring.backend import KeyringBackend
    from keyring.errors import PasswordDeleteError

    class MemoryKeyring(KeyringBackend):
        priority = 1

        def __init__(self):
            super().__init__()
            self.passwords: dict[tuple[str, str], str] = {}

        def get_password(self, service, username):
            return self.passwords.get((service, username))

        def set_password(self, service, username, password):
            self.passwords[(service, username)] = password

        def delete_password(self, service, username):
            if self.passwords.pop((service, username), None) is None:
                raise PasswordDeleteError("nothing stored")

    real = keyring.get_keyring()
    store = MemoryKeyring()
    keyring.set_keyring(store)
    yield store
    keyring.set_keyring(real)


@pytest.fixture(autouse=True)
def picture_limits():
    """Picture limits learned from a service's rejection last until the app closes; in tests, until the test ends."""
    from devils_workshop.llm.pictures import forget_limits
    forget_limits()
    yield
    forget_limits()


@pytest.fixture(autouse=True)
def keep_awake_calls(monkeypatch) -> list[int]:
    """What the app asked Windows about sleep, recorded instead of done (tests never change your PC's power)."""
    from devils_workshop import keepawake
    calls: list[int] = []
    monkeypatch.setattr(keepawake, "setter", calls.append)
    monkeypatch.setattr(keepawake, "_holders", 0)
    return calls


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    """A fresh copy of the sample story project."""
    return create_project(tmp_path / "sample", sample=True)


@pytest.fixture
def issues() -> IssueList:
    return IssueList()


def settings(root: Path, **changes) -> None:
    """Change a story's settings (defaults__pov="Mara" for nested ones; None takes one out)."""
    from devils_workshop.project.story_settings import migrate, update_settings
    migrate(root)
    update_settings(root, **changes)


def settings_file(root: Path) -> str:
    return (root / "Story settings.md").read_text(encoding="utf-8")


def write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def messages(issue_list, severity: str | None = None) -> list[str]:
    return [i.message for i in issue_list if severity is None or i.severity == severity]
