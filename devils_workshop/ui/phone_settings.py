"""Settings, "Phone and other devices": the password, the switch, the addresses and QR codes.

Shown in full only in the desktop window; a phone sees a note and "Sign out".
"""

from __future__ import annotations

import asyncio

import segno
from nicegui import run, ui

from devils_workshop.phone import MIN_PASSWORD, Addresses, find_addresses, hash_password, password_problem
from devils_workshop.ui.components import card, chip, notice, plural, primary, quiet, secondary, toast
from devils_workshop.ui.phone_access import SERVER, apply_settings, devices, reload_phones
from devils_workshop.ui.state import STATE

TAILSCALE_STEPS = [
    "On this PC: install Tailscale from tailscale.com/download and sign in (a free personal account is enough).",
    "On the iPhone: install Tailscale from the App Store and sign in with the same account. Leave it switched on.",
    "Open the Tailscale address below in Chrome on the iPhone. It works at home too, so it's the one to keep.",
]


async def phone_card(on_phone: bool) -> None:
    if on_phone:
        with card("Phone and other devices"):
            ui.label("You're using Devil's Workshop from another device. Phone access, its password and your "
                     "OpenRouter key are managed on the PC.").classes("dw-body")
            secondary("Sign out of this device", lambda: ui.navigate.to("/logout"), "logout").mark("sign-out")
        return
    with card("Phone and other devices", "Use Devil's Workshop from your phone's browser (Chrome on an iPhone "
                                         "works). This PC still does the work: keep it on with the app open."):
        body = ui.refreshable(_phone_body)  # one per page, so redrawing it never touches another window
        await body(lambda: body.refresh())


async def _phone_body(redraw) -> None:
    phone = STATE.app_settings().phone
    has_password = bool(phone.password_hash)

    switch = ui.switch("Allow access from my phone", value=phone.enabled and has_password).mark("phone-switch")
    if not has_password:
        switch.disable()
        ui.label("Set a password below first. A phone asks for it once; this PC never does.").classes("dw-small")

    async def toggled(event) -> None:
        phone.enabled = bool(event.value)
        STATE.save_settings()
        await apply_settings()
        redraw()

    switch.on_value_change(toggled)

    if phone.enabled and has_password:
        await _status_and_addresses(phone.port, redraw)

    with ui.expansion("Password" + (" (set)" if has_password else ""), icon="password",
                      value=not has_password).classes("dw-section-block").mark("phone-password"):
        with ui.column().classes("gap-2 px-4 pb-4 w-full"):
            ui.label(f"At least {MIN_PASSWORD} characters. Only a scrambled form of it is saved. "
                     + ("Changing it signs out every phone." if has_password else "")).classes("dw-small")
            with ui.row().classes("w-full gap-2 items-end"):
                first = ui.input("New password", password=True, password_toggle_button=True) \
                    .props('autocomplete="new-password"').classes("grow").mark("phone-password-new")
                again = ui.input("The same again", password=True) \
                    .props('autocomplete="new-password"').classes("grow").mark("phone-password-again")

            async def save_password() -> None:
                problem = password_problem(first.value or "", again.value or "")
                if problem:
                    toast(problem, "warning")
                    return
                phone.password_hash = await asyncio.to_thread(hash_password, first.value)
                first.value = again.value = ""
                if has_password:
                    devices().forget_all()
                    reload_phones()
                STATE.save_settings()
                toast("Password changed; phones sign in again" if has_password else "Password set")
                redraw()

            primary("Change the password" if has_password else "Set the password", save_password, "key") \
                .classes("self-start").mark("phone-password-save")

    with ui.expansion("Port and signed-in phones", icon="tune").classes("dw-section-block"):
        with ui.column().classes("gap-3 px-4 pb-4 w-full"):
            with ui.row().classes("items-end gap-2"):
                port = ui.number("Port", value=phone.port, min=1024, max=65535, precision=0).classes("w-32")

                async def save_port() -> None:
                    value = int(port.value or 0)
                    if not 1024 <= value <= 65535:
                        toast("Choose a port from 1024 to 65535", "warning")
                        return
                    phone.port = value
                    STATE.save_settings()
                    await apply_settings()
                    redraw()

                secondary("Use this port", save_port)
            ui.label("The number after the colon in the address. Change it only if another program uses "
                     f"{phone.port}.").classes("dw-small")
            count = devices().count()
            with ui.row().classes("items-center gap-3"):
                ui.label(plural(count, "phone") + " signed in" if count else "No phones signed in").classes("dw-body")
                if count:
                    def sign_out_all() -> None:
                        devices().forget_all()
                        reload_phones()
                        toast("Every phone is signed out")
                        redraw()
                    quiet("Sign out every phone", sign_out_all, "logout").classes("dw-danger")


async def _status_and_addresses(port: int, redraw) -> None:
    if not SERVER.running:
        async def retry() -> None:
            await apply_settings()
            redraw()
        notice("problem", SERVER.problem or f"Not listening on port {port} yet.", "Try again", retry)
        return
    with ui.row().classes("items-center gap-2"):
        chip("final", "On")
        ui.label(f"Listening on your network, port {port}").classes("dw-small")
    found: Addresses = await run.io_bound(find_addresses)
    lan = [f"http://{ip}:{port}" for ip in found.lan]
    tail = ([f"http://{found.tailscale_name}:{port}"] if found.tailscale_name else []) + \
        [f"http://{ip}:{port}" for ip in found.tailscale]

    with ui.tabs().classes("w-full").props("align=left dense") as tabs:
        home = ui.tab("At home (Wi-Fi)")
        away = ui.tab("Anywhere (Tailscale)")
    with ui.tab_panels(tabs, value=away if tail else home).classes("w-full").style("background: transparent"):
        with ui.tab_panel(home).classes("px-0"):
            if lan:
                _address_block(lan[0], lan[1:], "Your phone must be on the same Wi-Fi as this PC.")
            else:
                notice("warning", "This PC doesn't seem to be on a home network. Connect it to your Wi-Fi.")
        with ui.tab_panel(away).classes("px-0"):
            if tail:
                _address_block(tail[0], tail[1:], "Works wherever the iPhone has internet, with Tailscale switched "
                                                  "on there. Nothing is opened to the internet.")
            else:
                ui.label("Tailscale isn't on this PC yet. It's a free app that links your own devices privately, "
                         "so the phone can reach this PC from anywhere without opening it to the internet."
                         ).classes("dw-body")
            with ui.column().classes("gap-1 mt-2"):
                for number, step in enumerate(TAILSCALE_STEPS, 1):
                    ui.label(f"{number}. {step}").classes("dw-small")
    with ui.expansion("Opening it on the iPhone", icon="smartphone").classes("dw-section-block"):
        with ui.column().classes("gap-1 px-4 pb-4"):
            for step in ("Scan the code with the Camera app, or in Chrome: touch and hold the Chrome icon, then "
                         "Scan QR Code. You can also type the address.",
                         "Enter the password once and leave 'Remember this phone' ticked.",
                         "For an app icon: in Chrome, tap the Share button, then Add to Home Screen. Sign in once "
                         "more there; the icon then opens Devil's Workshop full screen.",
                         "If Windows asks whether Devil's Workshop may use your network, allow Private networks. "
                         "If the phone can't connect, check that Windows sees your Wi-Fi as a private network."):
                ui.label("· " + step).classes("dw-small")


def _address_block(url: str, others: list[str], note: str) -> None:
    with ui.row().classes("w-full items-center gap-4"):
        qr = segno.make(url, error="m").svg_inline(scale=4, border=2, dark="#1F1D1A", light="#FFFFFF")
        ui.html(f'<div class="dw-qr">{qr}</div>', sanitize=False).mark("phone-qr")
        with ui.column().classes("gap-1 min-w-0"):
            ui.label(url).classes("dw-mono").style("font-size: 15px; overflow-wrap: anywhere").mark("phone-address")
            ui.label(note).classes("dw-small")
            quiet("Copy the address", lambda: (ui.clipboard.write(url), toast("Address copied")), "content_copy") \
                .classes("self-start -ml-2")
            if others:
                ui.label("If that doesn't open, try: " + ", ".join(others)).classes("dw-small")

