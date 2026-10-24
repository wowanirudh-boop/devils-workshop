"""On a Linux server (docs/cloud-setup.md): keys without a credential store, deleting without a Recycle Bin."""

import json
import os
import stat
import sys

import keyring
import pytest
from keyring.backends import fail

from devils_workshop.project import file_ops
from devils_workshop.settings import (
    delete_api_key,
    delete_service_key,
    get_api_key,
    get_explabs_key,
    get_service_key,
    get_venice_key,
    key_place,
    set_api_key,
    set_service_key,
    set_venice_key,
    settings_dir,
)


@pytest.fixture
def no_credential_store():
    real = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    yield
    keyring.set_keyring(real)


def test_without_a_credential_store_keys_go_in_a_file_only_you_can_read(no_credential_store):
    assert get_api_key() is None and key_place() == "a file in the app's folder that only you can read"
    set_api_key(" sk-or-v1-server ")
    set_venice_key("venice-server-key-0123456789")
    set_service_key("explabs", "xpl_server_0123456789abcdef")
    assert get_api_key() == "sk-or-v1-server" and get_venice_key() == "venice-server-key-0123456789"
    assert get_explabs_key() == "xpl_server_0123456789abcdef"
    path = settings_dir() / "keys.json"
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {"openrouter", "venice", "service:explabs"}
    if sys.platform != "win32":  # Windows has no such permission bits
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    delete_api_key()
    delete_service_key("explabs")
    delete_service_key("never-added")
    assert get_api_key() is None and get_service_key("explabs") is None and get_venice_key()


def test_with_a_credential_store_no_file_is_written(credential_store):
    set_api_key("sk-or-v1-desktop")
    assert get_api_key() == "sk-or-v1-desktop" and not (settings_dir() / "keys.json").exists()
    assert credential_store.passwords[("DevilsWorkshop", "openrouter")] == "sk-or-v1-desktop"


def test_a_broken_keys_file_means_no_keys(no_credential_store):
    (settings_dir() / "keys.json").write_text("{not json", encoding="utf-8")
    assert get_api_key() is None
    set_api_key("sk-or-v1-again")
    assert get_api_key() == "sk-or-v1-again"


def test_without_a_recycle_bin_a_deleted_story_moves_to_the_apps_trash(tmp_path, monkeypatch):
    monkeypatch.setattr(file_ops.sys, "platform", "linux")
    story = tmp_path / "My Story"
    (story / "chapters").mkdir(parents=True)
    (story / "chapters" / "01.md").write_text("The harbour clock.", encoding="utf-8")
    moved = file_ops.move_to_recycle_bin(story)
    assert not story.exists() and moved.parent == file_ops.trash_folder()
    assert moved.name.startswith("My Story (deleted ") and (moved / "chapters" / "01.md").is_file()
