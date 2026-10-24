"""Privacy on this PC: the passcode lock in front of its screens, and running hidden with an icon beside the clock."""

import asyncio
import sys
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from devils_workshop import startup, tray
from devils_workshop.llm.catalog import Catalog
from devils_workshop.phone import Throttle, hash_password
from devils_workshop.settings import AppSettings, LockSettings, load_settings, save_settings
from devils_workshop.ui import lock
from devils_workshop.ui.phone_access import PhoneGate, devices
from devils_workshop.ui.state import STATE

from tests.fakes import TEST_KEY, FakeOpenRouter

CODE = "2468"
DESKTOP = ("127.0.0.1", 5000)
PHONE = ("192.168.1.20", 52000)


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DW_SETTINGS_DIR", str(tmp_path / "settings"))
    monkeypatch.setenv("OPENROUTER_API_KEY", TEST_KEY)
    monkeypatch.setattr("devils_workshop.ui.home.default_stories_folder", lambda: tmp_path / "stories")
    monkeypatch.setattr(lock, "THROTTLE", Throttle())
    fake = FakeOpenRouter()
    STATE.root, STATE.settings, STATE.catalog, STATE.catalog_notes = None, None, Catalog([]), []
    STATE.client_factory = lambda key: fake.client(key)
    yield fake
    lock.unlock()
    STATE.root = None


def with_code(locked: bool = True) -> None:
    lock.set_code(CODE, iterations=1000)  # quick to check in tests
    lock._locked = locked


def gate_app() -> PhoneGate:
    async def page(request):
        return PlainTextResponse("the page")

    async def socket_endpoint(websocket):
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    return PhoneGate(Starlette(routes=[
        Route("/story", page), Route("/unlock", lock.unlock_page, methods=["GET"]),
        Route("/unlock", lock.unlock_submit, methods=["POST"]), Route("/lock", lock.lock_request, methods=["POST"]),
        Route("/lock/active", lock.active_request, methods=["POST"]), WebSocketRoute("/ws", socket_endpoint)]))


def client(app, who=DESKTOP) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app, client=who), base_url="http://test")


def test_without_a_passcode_nothing_is_locked(app_env):
    async def go():
        desktop = client(gate_app())
        assert not lock.enabled() and (await desktop.get("/story")).text == "the page"
    asyncio.run(go())


def test_a_locked_pc_gets_the_unlock_page_and_opens_with_the_code(app_env):
    with_code(locked=True)
    app = gate_app()

    async def go():
        desktop = client(app)
        answer = await desktop.get("/story?chapter=2")
        assert answer.status_code == 303 and answer.headers["location"] == "/unlock?next=%2Fstory%3Fchapter%3D2"
        assert (await desktop.post("/story")).status_code == 401
        page = await desktop.get("/unlock?next=/story")
        assert page.status_code == 200 and "Passcode" in page.text and "Locked" in page.text
        wrong = await desktop.post("/unlock", data={"code": "1357", "next": "/story"})
        assert wrong.status_code == 401 and "That code isn" in wrong.text and lock.is_locked()
        right = await desktop.post("/unlock", data={"code": CODE, "next": "/story"})
        assert right.status_code == 303 and right.headers["location"] == "/story" and not lock.is_locked()
        assert (await desktop.get("/story")).text == "the page"
        assert (await desktop.post("/lock")).status_code == 204 and lock.is_locked()  # the window opening
        assert (await desktop.get("/story")).status_code == 303

    asyncio.run(go())


def test_a_locked_pc_refuses_its_open_pages_connection(app_env):
    with_code(locked=True)
    connection = TestClient(gate_app(), client=DESKTOP)
    with pytest.raises(WebSocketDisconnect) as refused:
        with connection.websocket_connect("/ws"):
            pass
    assert refused.value.code == 1008


def test_wrong_codes_are_throttled(app_env):
    with_code(locked=True)

    async def go():
        desktop = client(gate_app())
        for _ in range(5):
            await desktop.post("/unlock", data={"code": "0000"})
        answer = await desktop.post("/unlock", data={"code": CODE})
        assert answer.status_code == 429 and "Too many wrong codes" in answer.text and lock.is_locked()

    asyncio.run(go())


def test_phones_arent_locked_and_cant_lock_or_open_the_pc(app_env):
    with_code(locked=True)
    phone_settings = STATE.app_settings().phone
    phone_settings.enabled, phone_settings.password_hash = True, hash_password("harbour-clock-42", iterations=1000)
    token = devices().add("iPhone · Chrome", remember=True)

    async def go():
        phone = httpx.AsyncClient(transport=httpx.ASGITransport(gate_app(), client=PHONE), base_url="http://test",
                                  cookies={"dw_phone": token})
        assert (await phone.get("/story")).text == "the page"  # the phone has its own password
        answer = await phone.post("/unlock", data={"code": CODE})
        assert answer.status_code == 403 and lock.is_locked()
        lock.unlock()
        await phone.post("/lock")
        assert not lock.is_locked()  # only this PC locks itself

    asyncio.run(go())


def test_it_locks_after_going_unused(app_env, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(lock, "clock", lambda: now[0])
    with_code(locked=False)
    lock.touch()
    STATE.app_settings().lock.idle_minutes = 5
    now[0] += 4 * 60
    lock.check_idle()
    assert not lock.is_locked()
    lock.touch()  # a page was used
    now[0] += 4 * 60
    lock.check_idle()
    assert not lock.is_locked()
    now[0] += 2 * 60
    lock.check_idle()
    assert lock.is_locked()

    lock.unlock()
    STATE.app_settings().lock.idle_minutes = 0  # only when asked
    now[0] += 24 * 3600
    lock.check_idle()
    assert not lock.is_locked()


def test_a_page_in_use_keeps_it_open(app_env, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(lock, "clock", lambda: now[0])
    with_code(locked=False)

    async def go():
        desktop = client(gate_app())
        now[0] += 10 * 60
        assert (await desktop.post("/lock/active")).status_code == 204
        lock.check_idle()
        assert not lock.is_locked()

    asyncio.run(go())


def test_codes_must_be_long_enough_and_match():
    assert lock.code_problem("12", "12").startswith("Use at least 4")
    assert lock.code_problem("1234", "1243") == "The two codes don't match."
    assert lock.code_problem("1234", "1234") == ""


def test_lock_and_run_hidden_are_saved(tmp_path):
    path = tmp_path / "settings.yaml"
    save_settings(AppSettings(tray=True, lock=LockSettings(code_hash="pbkdf2_sha256$1$a$b", idle_minutes=15)), path)
    loaded = load_settings(path)
    assert loaded.tray and loaded.lock.code_hash == "pbkdf2_sha256$1$a$b" and loaded.lock.idle_minutes == 15
    path.write_text(path.read_text(encoding="utf-8").replace("idle_minutes: 15", "idle_minutes: 7"), encoding="utf-8")
    assert load_settings(path).lock.idle_minutes == 5  # not a choice: the default


# --- running hidden -------------------------------------------------------------------------------------

def test_the_tray_icon_and_its_menu(monkeypatch):
    image = tray.icon_image()
    assert image.size == (64, 64) and tray.TOOLTIP == "Workshop"
    launched, handed = [], []
    monkeypatch.setattr(startup, "launch", lambda *args: launched.append(args))

    class Loop:
        def call_soon_threadsafe(self, action):
            handed.append(action)

    icon = tray.Tray()
    icon.loop = Loop()
    icon.open()
    assert launched == [()]  # a new copy opens a window onto this one
    icon.quit()
    from nicegui import app
    assert handed == [app.shutdown]


def test_another_copy_is_started_on_its_own(monkeypatch):
    started = []
    monkeypatch.setattr(startup.subprocess, "Popen", lambda cmd, **options: started.append((cmd, options)))
    startup.launch("--background")
    cmd, options = started[0]
    assert cmd[-1] == "--background" and options["stdin"] == startup.subprocess.DEVNULL
    if sys.platform == "win32":
        assert options["creationflags"] & startup.subprocess.DETACHED_PROCESS


def test_running_hidden_opens_a_window_onto_a_background_copy(monkeypatch):
    from devils_workshop.ui import main
    opened = []
    monkeypatch.setattr(startup, "running_url", lambda timeout=2.0: None)
    monkeypatch.setattr(main, "_run_hidden", lambda: True)
    monkeypatch.setattr(startup, "start_background", lambda: "http://127.0.0.1:5556")
    monkeypatch.setattr(main, "open_window", lambda url, browser=False: opened.append(url) or 0)
    assert main.main([]) == 0 and opened == ["http://127.0.0.1:5556"]


def test_closing_the_window_with_run_hidden_on_carries_on_in_the_background(monkeypatch):
    from devils_workshop.ui import main
    launched = []
    monkeypatch.setattr(startup, "launch", lambda *args: launched.append(args))
    main._hide_on_close(False)
    assert launched == []  # Run hidden off: closing the window closes the app
    main._hide_on_close(True)
    assert launched == ([("--background", "--after-close")] if sys.platform == "win32" else [])


def test_the_copy_taking_over_waits_for_the_closing_one(monkeypatch):
    from devils_workshop.ui import main
    answers = iter(["http://127.0.0.1:5557", "http://127.0.0.1:5557", None])
    monkeypatch.setattr(startup, "running_url", lambda timeout=2.0: next(answers, None))
    monkeypatch.setattr(startup.time, "sleep", lambda s: None)
    startup.wait_until_stopped()
    assert next(answers, "done") == "done"  # it asked until the closing copy stopped answering

    waited = []
    monkeypatch.setattr(startup, "wait_until_stopped", lambda: waited.append(True))
    monkeypatch.setattr(startup, "running_url", lambda timeout=2.0: "http://127.0.0.1:5557")
    assert main.main(["--background", "--after-close"]) == 0 and waited == [True]


def test_settings_card_sets_the_passcode(app_env):
    from nicegui.testing.user_simulation import user_simulation
    main_file = Path(__file__).parent / "app_main.py"

    async def steps(user):
        await user.open("/settings")
        await user.should_see("Passcode lock")
        await user.should_see("No passcode")
        user.find(marker="lock-code-new").type("12")
        user.find(marker="lock-code-again").type("12")
        user.find(marker="lock-save").click()
        await user.should_see("Use at least 4")
        user.find(marker="lock-code-new").clear().type(CODE)
        user.find(marker="lock-code-again").clear().type(CODE)
        user.find(marker="lock-save").click()
        await user.should_see("Passcode saved")
        await user.open("/settings")
        await user.should_see("Passcode set")
        await user.should_see(marker="lock-now")  # in the sidebar

    async def go():
        async with user_simulation(main_file=main_file) as user:
            await steps(user)
    asyncio.run(go())
    assert lock.enabled() and not lock.is_locked()
