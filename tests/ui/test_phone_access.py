"""Phone access through the real app: the sign-in gate, the sign-in page, the network listener, and
the screens a phone gets (with NiceGUI's simulated user, against a fake OpenRouter)."""

import asyncio
import socket
import struct
import sys
import zlib
from pathlib import Path

import httpx
import pytest
from nicegui import core, ui
from nicegui.testing.user_simulation import user_simulation
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from devils_workshop.llm.catalog import Catalog
from devils_workshop.phone import Throttle, hash_password
from devils_workshop.ui import phone_access
from devils_workshop.ui.phone_access import PhoneGate, devices
from devils_workshop.ui.state import STATE

from tests.fakes import TEST_KEY, FakeOpenRouter

MAIN = Path(__file__).parent / "app_main.py"
PASSWORD = "harbour-clock-42"
PHONE = ("192.168.1.20", 52000)
LISTEN_AT_LAUNCH = phone_access._on_startup


async def _no_listener_at_launch() -> None:
    """The tests start the listener themselves (never on the real port 8730)."""


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DW_SETTINGS_DIR", str(tmp_path / "settings"))
    monkeypatch.setenv("OPENROUTER_API_KEY", TEST_KEY)
    monkeypatch.setattr("devils_workshop.ui.home.default_stories_folder", lambda: tmp_path / "stories")
    monkeypatch.setattr(phone_access, "THROTTLE", Throttle())
    monkeypatch.setattr(phone_access, "_on_startup", _no_listener_at_launch)
    fake = FakeOpenRouter()
    STATE.root, STATE.settings, STATE.catalog, STATE.catalog_notes = None, None, Catalog([]), []
    STATE.client_factory = lambda key: fake.client(key)
    yield fake
    STATE.root = None


def phone_on(port: int = 8730) -> None:
    phone = STATE.app_settings().phone
    phone.enabled, phone.port = True, port
    phone.password_hash = hash_password(PASSWORD, iterations=1000)  # quick to check in tests
    STATE.save_settings()


def open_sample(tmp_path) -> Path:
    from devils_workshop.project.newproject import create_project
    root = create_project(tmp_path / "story", sample=True)
    STATE.open_story(root)
    return root


def simulate(steps):
    async def go():
        async with user_simulation(main_file=MAIN) as user:
            await steps(user)
    asyncio.run(go())


def from_phone(cookies: dict | None = None) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(core.app, client=PHONE)
    return httpx.AsyncClient(transport=transport, base_url="http://192.168.1.5:8730", cookies=cookies)


async def sign_in(phone: httpx.AsyncClient, password: str = PASSWORD, remember: bool = True,
                  target: str = "/story") -> httpx.Response:
    form = {"password": password, "next": target} | ({"remember": "on"} if remember else {})
    return await phone.post("/login", data=form)


def phone_cookie(response: httpx.Response) -> str:
    """The sign-in cookie as sent (the tests' app also sets NiceGUI's own session cookie)."""
    found = [c for c in response.headers.get_list("set-cookie") if c.startswith("dw_phone=")]
    return found[0] if found else ""


# --- the gate on its own --------------------------------------------------------------------------------

def gate_app() -> Starlette:
    async def page(request):
        return PlainTextResponse("the page")

    async def socket_endpoint(websocket):
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    return Starlette(routes=[Route("/story", page), Route("/login", page), WebSocketRoute("/ws", socket_endpoint)])


def test_pages_start_in_the_apps_colour_so_nothing_flashes_white(app_env):
    from devils_workshop.ui.main import _window_colour
    from devils_workshop.ui.theme import PAGE_DARK, PAGE_LIGHT, first_paint
    assert PAGE_DARK in first_paint("dark") and "!important" in first_paint("dark")
    assert "prefers-color-scheme: dark" in first_paint("system") and PAGE_DARK not in first_paint("light")
    STATE.app_settings().appearance = "dark"
    STATE.save_settings()
    assert _window_colour() == PAGE_DARK  # the app window, before its first page shows
    phone_on()

    async def go():
        async with user_simulation(main_file=MAIN):
            desktop = httpx.AsyncClient(transport=httpx.ASGITransport(core.app, client=("127.0.0.1", 5000)),
                                        base_url="http://test")
            for path in ("/", "/settings"):  # Settings goes out as a shell first: it still starts dark
                page = (await desktop.get(path)).text
                head = page[:page.find("</head>")]
                assert head.find(PAGE_DARK) != -1 and head.find('content="dark"') < head.find("nicegui-color-scheme")
            sign_in_page = (await from_phone().get("/login")).text
            assert "color-scheme: dark; }" in sign_in_page and PAGE_DARK in sign_in_page[:300]  # even on a light phone
            manifest = await desktop.get("/manifest.webmanifest")
            assert manifest.json()["name"]  # anything that isn't a page goes out as it was

    asyncio.run(go())
    STATE.app_settings().appearance = "light"
    assert "color-scheme: dark; }" not in phone_access.gate_style()  # Light: the phone's own setting
    assert _window_colour() in (PAGE_LIGHT, PAGE_DARK)


def test_gate_sends_other_devices_to_sign_in_and_lets_the_desktop_through(app_env):
    phone_on()
    app = PhoneGate(gate_app())

    async def go():
        phone = httpx.AsyncClient(transport=httpx.ASGITransport(app, client=PHONE), base_url="http://test")
        desktop = httpx.AsyncClient(transport=httpx.ASGITransport(app, client=("127.0.0.1", 5000)),
                                    base_url="http://test")
        answer = await phone.get("/story?chapter=2")
        assert answer.status_code == 303
        assert answer.headers["location"] == "/login?next=%2Fstory%3Fchapter%3D2"
        assert (await phone.post("/story")).status_code == 401
        assert (await phone.get("/login")).text == "the page"  # the sign-in page itself is open
        assert (await desktop.get("/story")).text == "the page"
        token = devices().add("iPhone · Chrome", remember=True)
        assert (await phone.get("/story", cookies={"dw_phone": token})).text == "the page"
        assert (await phone.get("/story", cookies={"dw_phone": "made-up"})).status_code == 303
        devices().forget_all()
        assert (await phone.get("/story", cookies={"dw_phone": token})).status_code == 303

    asyncio.run(go())


def test_gate_refuses_a_websocket_from_a_phone_that_isnt_signed_in(app_env):
    phone_on()
    client = TestClient(PhoneGate(gate_app()))  # its address, "testclient", isn't this PC
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws"):
            pass
    assert refused.value.code == 1008
    token = devices().add("iPhone · Chrome", remember=True)
    client.cookies.set("dw_phone", token)
    with client.websocket_connect("/ws") as connection:
        assert connection.receive_text() == "hello"


def test_with_phone_access_off_no_phone_gets_in(app_env):
    phone_on()
    token = devices().add("iPhone · Chrome", remember=True)
    STATE.app_settings().phone.enabled = False
    app = PhoneGate(gate_app())

    async def go():
        phone = httpx.AsyncClient(transport=httpx.ASGITransport(app, client=PHONE), base_url="http://test")
        assert (await phone.get("/story", cookies={"dw_phone": token})).status_code == 303

    asyncio.run(go())


# --- signing in, in the real app --------------------------------------------------------------------------

def test_phone_signs_in_once_and_is_remembered(app_env, tmp_path):
    open_sample(tmp_path)
    phone_on()

    async def steps(user):
        async with from_phone() as phone:
            answer = await phone.get("/story")
            assert answer.status_code == 303 and answer.headers["location"] == "/login?next=%2Fstory"
            page = await phone.get("/login?next=%2Fstory")
            assert 'type="password"' in page.text and 'name="remember" checked' in page.text
            assert 'href="/manifest.webmanifest"' in page.text
            signed_in = await sign_in(phone)
            assert signed_in.status_code == 303 and signed_in.headers["location"] == "/story"
            cookie = phone_cookie(signed_in).lower()
            assert "dw_phone=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
            assert "path=/" in cookie and "max-age=34560000" in cookie and "secure" not in cookie
            story = await phone.get("/story")  # the cookie is sent back from now on
            assert story.status_code == 200 and "The Saltmarsh Clock" in story.text
            out = await phone.get("/logout")
            assert out.status_code == 303 and out.headers["location"] == "/login"
            assert (await phone.get("/story")).status_code == 303
        await user.open("/story")  # the desktop window never signs in
        await user.should_see("The Saltmarsh Clock")

    simulate(steps)


def test_without_remember_the_cookie_ends_with_the_browser(app_env, tmp_path):
    phone_on()

    async def steps(user):
        async with from_phone() as phone:
            signed_in = await sign_in(phone, remember=False)
            cookie = phone_cookie(signed_in).lower()
            assert signed_in.status_code == 303 and cookie and "max-age" not in cookie

    simulate(steps)


def test_wrong_passwords_are_throttled(app_env):
    phone_on()

    async def steps(user):
        async with from_phone() as phone:
            for _ in range(4):
                answer = await sign_in(phone, "wrong password")
                assert answer.status_code == 401 and "That password isn&#x27;t right." in answer.text
            fifth = await sign_in(phone, "wrong password")
            assert fifth.status_code == 401 and "Try again in 30 seconds" in fifth.text
            locked = await sign_in(phone)  # even the right password waits now
            assert locked.status_code == 429 and "Too many wrong passwords" in locked.text
            assert not phone_cookie(locked)

    simulate(steps)


def test_sign_in_page_ignores_addresses_elsewhere(app_env):
    phone_on()

    async def steps(user):
        async with from_phone() as phone:
            for target in ("https://example.com/", "//example.com/", "/\\example.com"):
                answer = await sign_in(phone, target=target)
                assert answer.headers["location"] == "/"

    simulate(steps)


def test_signing_in_is_refused_while_phone_access_is_off(app_env):
    phone_on()
    STATE.app_settings().phone.enabled = False
    STATE.save_settings()

    async def steps(user):
        async with from_phone() as phone:
            answer = await sign_in(phone)
            assert answer.status_code == 403 and "turned off" in answer.text

    simulate(steps)


def test_add_to_home_screen_needs_no_sign_in(app_env):
    phone_on()

    async def steps(user):
        async with from_phone() as phone:
            manifest = (await phone.get("/manifest.webmanifest")).json()
            assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
            assert {icon["sizes"] for icon in manifest["icons"]} == {"192x192", "512x512"}
            icon = await phone.get("/apple-touch-icon.png")
            assert icon.status_code == 200 and icon.headers["content-type"] == "image/png"
            assert icon.content.startswith(b"\x89PNG\r\n\x1a\n")
            width, height = struct.unpack(">II", icon.content[16:24])
            assert (width, height) == (180, 180)
            assert zlib.decompress(icon.content[icon.content.index(b"IDAT") + 4:-16])  # a real image inside

    simulate(steps)


def test_a_phone_never_gets_the_key_or_the_phone_settings(app_env, tmp_path, credential_store):
    from devils_workshop.settings import set_api_key
    set_api_key("sk-or-v1-stored-secret-key-999")
    open_sample(tmp_path)
    phone_on()

    async def steps(user):
        async with from_phone() as phone:
            await sign_in(phone)
            pages = [await phone.get(path) for path in ("/settings", "/story", "/story/chapter/1", "/story/files")]
            for page in pages:
                assert page.status_code == 200
                assert TEST_KEY not in page.text and "stored-secret-key" not in page.text
                assert "pbkdf2_sha256" not in page.text
            settings = pages[0].text
            assert "Paste a new key" not in settings and "Allow access from my phone" not in settings
            assert "Sign out of this device" in settings
        await user.open("/settings")  # on the PC the card is all there
        await user.should_see("Allow access from my phone")
        await user.should_see("Paste a new key")

    simulate(steps)


# --- the listener ------------------------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_listener_starts_and_stops_while_the_app_runs(app_env, tmp_path, monkeypatch):
    open_sample(tmp_path)
    port = free_port()
    phone_on(port)
    monkeypatch.setattr(phone_access, "_on_startup", LISTEN_AT_LAUNCH)  # phone access is on: listen at launch

    async def steps(user):
        try:
            for _ in range(100):  # NiceGUI runs startup handlers as background tasks
                if phone_access.SERVER.running:
                    break
                await asyncio.sleep(0.05)
            assert phone_access.SERVER.running and phone_access.SERVER.port == port
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as phone:
                # this PC itself, but through the phone listener: it signs in like a phone
                answer = await phone.get("/story")
                assert answer.status_code == 303 and answer.headers["location"] == "/login?next=%2Fstory"
                await sign_in(phone)
                assert "The Saltmarsh Clock" in (await phone.get("/story")).text
            second = phone_access.PhoneServer()
            assert not await second.start(port) and str(port) in second.problem  # the port is taken
            STATE.app_settings().phone.enabled = False  # switched off in Settings
            await phone_access.apply_settings()
        finally:
            await phone_access.SERVER.stop()
        assert not phone_access.SERVER.running
        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):  # Windows retries a refused port a while
            async with httpx.AsyncClient(trust_env=False) as client:
                await client.get(f"http://127.0.0.1:{port}/login", timeout=5)

    simulate(steps)


def test_settings_card_sets_the_password_and_switches_access_on(app_env, monkeypatch):
    started = []

    async def fake_start(port):
        started.append(port)
        return False

    monkeypatch.setattr(phone_access.SERVER, "start", fake_start)

    async def steps(user):
        await user.open("/settings")
        await user.should_see("Set a password below first")
        user.find(marker="phone-password-new").type("too short")
        user.find(marker="phone-password-again").type("too short!")
        user.find(marker="phone-password-save").click()
        await user.should_see("The two passwords don't match.")
        user.find(marker="phone-password-new").clear().type(PASSWORD)
        user.find(marker="phone-password-again").clear().type(PASSWORD)
        user.find(marker="phone-password-save").click()
        await user.should_see("Password set", retries=50)
        await user.should_see("Password (set)", retries=50)  # the card is drawn again (in the background)
        user.find(marker="phone-switch").click()
        await user.should_see("Try again", retries=50)  # the fake listener didn't start

    simulate(steps)
    phone = STATE.app_settings().phone
    assert phone.enabled and started == [8730]
    from devils_workshop.phone import check_password
    from devils_workshop.settings import settings_path
    assert check_password(PASSWORD, phone.password_hash)
    assert PASSWORD not in settings_path().read_text(encoding="utf-8")


# --- the screens on a phone ---------------------------------------------------------------------------------

def test_phone_layout_pieces(app_env, tmp_path):
    """The simulated user has no screen size, so this checks the pieces a phone shows: the menu button,
    the chapter picker and the file picker (CSS hides the desktop ones below 600px)."""
    open_sample(tmp_path)

    async def steps(user):
        await user.open("/story/chapter/1")
        await user.should_see(marker="menu")
        user.find(marker="chapter-picker").click()
        user.find("3 · Low Tide").click()
        await user.should_see("Mara and Tobias climb the tower")
        await user.open("/story/files")
        user.find(marker="file-picker").click()
        user.find("bible/style_guide.md").click()
        await user.should_see("Style guide")

    simulate(steps)


def test_a_chapter_being_written_shows_on_a_page_opened_later(app_env, tmp_path):
    """A phone back from sleep (or a second window) sees the request under way, and can stop it."""
    root = open_sample(tmp_path)
    key = (str(root.resolve()), 3)

    async def steps(user):
        chapter = sys.modules["devils_workshop.ui.chapter"]  # the simulation imports the screens afresh
        chapter.WRITING[key] = chapter.Writing(version=1, text="Half a chapter so far.", status="Writing with GLM")
        try:
            await user.open("/story/chapter/3")
            await user.should_see("Half a chapter so far.")
            await user.should_see("Writing version 1…")
            user.find(kind=ui.button, content="Stop").click()
            assert chapter.WRITING[key].cancel.is_set()
        finally:
            chapter.WRITING.pop(key, None)

    simulate(steps)
