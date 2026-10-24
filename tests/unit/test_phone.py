"""Phone access: the password, wrong-password limits, remembered phones, who counts as the desktop."""

import json

import pytest

from devils_workshop.phone import (
    Devices,
    Throttle,
    check_password,
    cookie_from,
    device_name,
    hash_password,
    is_desktop,
    is_tailscale,
    password_problem,
)
from devils_workshop.settings import AppSettings, load_settings, save_settings, settings_path


def test_password_is_hashed_salted_and_checked():
    stored = hash_password("correct horse", iterations=1000)
    assert "correct horse" not in stored and stored.startswith("pbkdf2_sha256$1000$")
    assert check_password("correct horse", stored)
    assert not check_password("correct hors", stored)
    assert not check_password("", stored)
    assert hash_password("correct horse", iterations=1000) != stored  # a new salt every time


@pytest.mark.parametrize("stored", ["", "plain text", "md5$1$a$b", "pbkdf2_sha256$x$y$z", "pbkdf2_sha256$10$$"])
def test_a_damaged_hash_never_lets_anyone_in(stored):
    assert not check_password("anything", stored)
    assert not check_password("", stored)


def test_the_default_hash_is_slow_enough():
    assert hash_password("x").split("$")[1] == "600000"


def test_new_password_rules():
    assert password_problem("short", "short").startswith("Use at least 8")
    assert password_problem("long enough", "long enougH") == "The two passwords don't match."
    assert password_problem("long enough", "long enough") == ""


def test_settings_keep_only_the_hash(app_folder):
    settings = AppSettings()
    settings.phone.enabled, settings.phone.port = True, 8731
    settings.phone.password_hash = hash_password("my phone password", iterations=1000)
    save_settings(settings)
    text = settings_path().read_text(encoding="utf-8")
    assert "my phone password" not in text and "pbkdf2_sha256$1000$" in text
    loaded = load_settings()
    assert loaded.phone.enabled and loaded.phone.port == 8731
    assert check_password("my phone password", loaded.phone.password_hash)


def test_phone_settings_default_to_off(app_folder):
    settings_path().parent.mkdir(parents=True, exist_ok=True)
    settings_path().write_text("primary: z-ai/glm-5.3\nphone: {port: 80}\n", encoding="utf-8")
    loaded = load_settings()
    assert not loaded.phone.enabled and loaded.phone.password_hash == ""
    assert loaded.phone.port == 8730  # below 1024 isn't allowed


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_wrong_passwords_are_slowed_down():
    clock = Clock()
    throttle = Throttle(free=3, overall=100, first_wait_s=30, longest_wait_s=120, clock=clock)
    for _ in range(2):
        throttle.failed("phone")
        assert throttle.wait("phone") == 0
    throttle.failed("phone")  # the third: now it waits
    assert throttle.wait("phone") == 30
    assert throttle.wait("someone else") == 0
    clock.now += 31
    throttle.failed("phone")
    assert throttle.wait("phone") == 60  # doubles
    clock.now += 61
    throttle.failed("phone")
    clock.now += 0
    assert throttle.wait("phone") == 120
    clock.now += 121
    throttle.failed("phone")
    assert throttle.wait("phone") == 120  # never longer than the longest wait


def test_a_right_password_clears_the_count_and_old_tries_are_forgotten():
    clock = Clock()
    throttle = Throttle(free=2, overall=100, window_s=60, clock=clock)
    throttle.failed("phone")
    throttle.succeeded("phone")
    throttle.failed("phone")
    assert throttle.wait("phone") == 0
    clock.now += 61  # outside the window
    throttle.failed("phone")
    assert throttle.wait("phone") == 0


def test_changing_address_doesnt_give_endless_tries():
    throttle = Throttle(free=5, overall=4, clock=Clock())
    for n in range(4):
        throttle.failed(f"10.0.0.{n}")
    assert throttle.wait("10.0.0.99") > 0


def test_remembered_phones_keep_only_a_hash_of_the_token(tmp_path):
    devices = Devices(tmp_path / "phones.json")
    token = devices.add("iPhone · Chrome", remember=True, now=100.0)
    assert devices.known(token, now=200.0)
    assert not devices.known(token + "x", now=200.0) and not devices.known(None) and not devices.known("")
    stored = (tmp_path / "phones.json").read_text(encoding="utf-8")
    assert token not in stored and json.loads(stored)["devices"][0]["name"] == "iPhone · Chrome"
    again = Devices(tmp_path / "phones.json")  # after a restart
    assert again.known(token, now=300.0) and again.count(now=300.0) == 1
    assert not again.known(token, now=100.0 + 401 * 86400)  # the cookie's life is over


def test_a_phone_that_isnt_remembered_is_signed_in_for_hours(tmp_path):
    devices = Devices(tmp_path / "phones.json")
    token = devices.add("iPhone · Chrome", remember=False, now=0.0)
    assert devices.known(token, now=11 * 3600)
    assert not devices.known(token, now=13 * 3600)


def test_signing_out(tmp_path):
    devices = Devices(tmp_path / "phones.json")
    first, second = devices.add("a", True), devices.add("b", True)
    devices.forget(first)
    assert not devices.known(first) and devices.known(second)
    devices.forget_all()
    assert not devices.known(second) and devices.count() == 0


def scope(client, port=8790, headers=()):
    return {"type": "http", "client": (client, 50000), "server": ("127.0.0.1", port), "headers": list(headers)}


def test_only_the_desktop_window_is_exempt():
    assert is_desktop(scope("127.0.0.1"), phone_port=8730)
    assert is_desktop(scope("::1"), phone_port=8730)
    assert is_desktop(scope("::ffff:127.0.0.1"), phone_port=None)
    assert not is_desktop(scope("192.168.1.20"), phone_port=8730)
    assert not is_desktop(scope("100.101.102.103"), phone_port=8730)
    assert not is_desktop(scope("testclient"), phone_port=8730)
    assert not is_desktop({"type": "http"}, phone_port=8730)
    # through the phone listener, or passed on by a proxy on this PC: not the desktop window
    assert not is_desktop(scope("127.0.0.1", port=8730), phone_port=8730)
    assert not is_desktop(scope("127.0.0.1", headers=[(b"x-forwarded-for", b"100.64.1.2")]), phone_port=8730)


def test_reading_the_cookie():
    headers = [(b"cookie", b"other=1; dw_phone=abc-123_x; more=2")]
    assert cookie_from({"headers": headers}) == "abc-123_x"
    assert cookie_from({"headers": [(b"cookie", b"dw_phonex=1")]}) is None
    assert cookie_from({}) is None


def test_names_and_addresses():
    agent = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
             "CriOS/131.0 Mobile/15E148 Safari/604.1")
    assert device_name(agent) == "iPhone · Chrome"
    assert device_name("") == "Device · browser"
    assert is_tailscale("100.101.102.103") and not is_tailscale("192.168.1.2") and not is_tailscale("fd7a::1")
