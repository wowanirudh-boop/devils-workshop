"""Settings: connection, models, privacy and providers, appearance, advanced."""

from __future__ import annotations

from nicegui import app, ui

from devils_workshop import startup
from devils_workshop.llm.experiential import is_explabs_key
from devils_workshop.llm.livecheck import venice_check
from devils_workshop.llm.services import (
    BUILT_IN,
    EXPLABS_NAME,
    EXPLABS_SLUG,
    OPENROUTER,
    service_name,
    service_of,
    slugify,
)
from devils_workshop.settings import (
    PLANNER,
    WRITER,
    JobOptions,
    ServiceSettings,
    api_key_source,
    delete_api_key,
    delete_service_key,
    delete_venice_key,
    explabs_key_source,
    get_service_key,
    get_venice_key,
    key_place,
    recommended,
    set_api_key,
    set_service_key,
    set_venice_key,
    settings_dir,
    settings_path,
    venice_key_source,
)
from devils_workshop.ui.components import (
    card,
    chip,
    model_name,
    money,
    notice,
    page,
    page_header,
    primary,
    quiet,
    secondary,
    toast,
    smooth_reload,
)
from devils_workshop.ui.home import check_connection
from devils_workshop.ui.layout import frame
from devils_workshop.ui.phone_access import from_phone
from devils_workshop.ui.phone_settings import phone_card
from devils_workshop.ui.providers import providers_dialog
from devils_workshop.ui.state import STATE

EFFORTS = {"(not sent)": "Model default", "none": "Off", "minimal": "Minimal", "low": "Low", "medium": "Medium",
           "high": "High", "xhigh": "Extra high", "max": "Maximum"}  # thinking effort


def _split(text) -> list[str]:
    if isinstance(text, list):
        return [str(t).strip() for t in text if str(t).strip()]
    return [part.strip() for part in (text or "").split(",") if part.strip()]


async def settings_page() -> None:
    frame("/settings")
    settings = STATE.app_settings()
    if not STATE.catalog.models:
        await STATE.refresh_catalog()
    on_phone = from_phone()  # the key and phone access can only be changed on the PC

    with page("narrow"):
        with page_header("Settings", "These apply to every story. Your OpenRouter key is never stored in a file."):
            pass

        # --- connection ----------------------------------------------------------------------
        source = api_key_source()
        with card("OpenRouter connection", "Your chapters are written by AI models reached through OpenRouter "
                                           "(openrouter.ai), which you pay per use."):
            with ui.row().classes("items-center gap-2"):
                chip("final" if source != "none" else "problem",
                     "Key saved" if source == "stored" else "Key from the environment" if source == "environment"
                     else "No key yet")
                ui.label({"environment": "Using the OPENROUTER_API_KEY environment variable.",
                          "stored": f"Kept in {key_place()}.",
                          "none": "Paste your key below. Create one at openrouter.ai/keys."}[source]).classes(
                    "dw-small")
            if on_phone:
                ui.label("The key can only be added or changed on the PC.").classes("dw-small")
            else:
                with ui.row().classes("w-full items-end no-wrap gap-2"):
                    key_input = ui.input("Paste a new key", password=True, password_toggle_button=True).classes("grow")

                    def save_key() -> None:
                        value = (key_input.value or "").strip()
                        if not value.startswith("sk-"):
                            toast("That doesn't look like an OpenRouter key (they start with sk-or-)", "warning")
                            return
                        try:
                            set_api_key(value)
                        except Exception as exc:
                            toast(f"Couldn't store the key: {exc}", "negative")
                            return
                        key_input.value = ""
                        toast("Key saved")
                        smooth_reload()

                    primary("Save key", save_key, "key")
            with ui.row().classes("gap-2"):
                secondary("Check the connection", lambda: check_connection(report), "wifi_tethering").tooltip(
                    "One tiny request (well under $0.01) to confirm the key and the models work")
                if source == "stored" and not on_phone:
                    def remove_key() -> None:
                        delete_api_key()
                        toast("Stored key removed")
                        smooth_reload()
                    quiet("Remove the stored key", remove_key)
            report = ui.column().classes("w-full gap-2")

        # --- Venice ------------------------------------------------------------------------------
        venice_card(on_phone)

        # --- Experiential Labs --------------------------------------------------------------------
        experiential_card(on_phone)

        # --- other services ----------------------------------------------------------------------
        services_card(on_phone)

        # --- models: the writer's and the planner's, chosen the same way --------------------------------
        if STATE.catalog_notes:
            notice("warning", " ".join(STATE.catalog_notes) + " Names and prices may be missing; check "
                              "your internet connection and refresh the list.",
                   "Refresh the list", lambda: refresh())
        options = {mid: f"{model_name(mid)}  ·  {mid}" for mid in sorted(STATE.catalog.models)}
        for mid in (settings.primary, settings.fallback, settings.planner_primary, settings.planner_fallback):
            if mid and mid not in options:
                options[mid] = f"{model_name(mid)}  ·  {mid}"
        with ui.element("div").classes("dw-cols halves").mark("model-roles"):
            writer = _role_card(WRITER, options)
            planner = _role_card(PLANNER, options)

        def copy_models(source: dict, target: dict) -> None:
            target["main"].value = source["main"].value
            target["fallback"].value = source["fallback"].value

        with writer["actions"]:
            quiet("Use the planner's models", lambda: copy_models(planner, writer), "content_copy") \
                .props("dense").mark("writer-same-as-planner")
        with planner["actions"]:
            quiet("Use the writer's models", lambda: copy_models(writer, planner), "content_copy") \
                .props("dense").mark("planner-same-as-writer")

        async def refresh() -> None:
            await STATE.refresh_catalog(force=True)
            smooth_reload()

        quiet("Refresh the model list", refresh, "refresh").classes("self-start -ml-2")

        # --- privacy and providers -------------------------------------------------------------------
        with card("Privacy and providers", "OpenRouter sends each request to one of several providers that run "
                                           "the model. Choose them for each model, for the writer and the "
                                           "planner, with Providers… above; these apply to every model."):
            deny = ui.switch("Only use providers that don't store or train on your text",
                             value=settings.providers.data_collection == "deny")
            allow = ui.input_chips("Only use these providers, for every model (leave empty to allow any)",
                                   value=list(settings.providers.allow), new_value_mode="add-unique").classes("w-full")
            block = ui.input_chips("Never use these providers, for any model", value=list(settings.providers.block),
                                   new_value_mode="add-unique").classes("w-full")
            ui.label("Type a provider's name and press Enter. 'Check the connection' shows which provider "
                     "answered.").classes("dw-small")

        # --- phone ---------------------------------------------------------------------------------------
        await phone_card(on_phone)
        if not on_phone and startup.supported():
            startup_card()
        if not on_phone:
            lock_card()

        # --- appearance ---------------------------------------------------------------------------------
        with card("Appearance"):
            def set_appearance(value: str) -> None:
                settings.appearance = value
                STATE.save_settings()
                smooth_reload()

            ui.toggle({"light": "Light", "dark": "Dark", "system": "Match Windows"}, value=settings.appearance,
                      on_change=lambda e: set_appearance(e.value))

        # --- advanced -----------------------------------------------------------------------------------
        with ui.expansion("Advanced", icon="settings_suggest").classes("dw-section-block"):
            with ui.column().classes("gap-3 px-4 pb-4 w-full"):
                ui.label("When a request fails for a passing reason (busy server, dropped connection), it is tried "
                         "again before switching to the fallback model.").classes("dw-small")
                with ui.row().classes("gap-4"):
                    attempts = ui.number("Tries per model", value=settings.retries.attempts_per_model, min=1, max=6,
                                         precision=0).classes("w-44")
                    delay = ui.number("Seconds before the first retry", value=settings.retries.base_delay_s,
                                      min=0.5, max=30).classes("w-64")
                ui.label(f"Settings file: {settings_path()}").classes("dw-small")

        def save() -> None:
            settings.primary = writer["main"].value or settings.primary
            settings.fallback = writer["fallback"].value or None
            settings.planner_primary = planner["main"].value or settings.planner_primary
            settings.planner_fallback = planner["fallback"].value or None
            settings.writer_options = _read_options(writer["options"], settings.writer_options)
            settings.planner_options = _read_options(planner["options"], settings.planner_options)
            settings.providers.allow = _split(allow.value)
            settings.providers.block = _split(block.value)
            settings.providers.data_collection = "deny" if deny.value else "allow"
            settings.retries.attempts_per_model = int(attempts.value or 3)
            settings.retries.base_delay_s = float(delay.value or 2)
            STATE.save_settings()
            toast("Settings saved")
            smooth_reload()

        with ui.element("div").classes("dw-savebar"):
            primary("Save settings", save, "save")


ROLE_CARDS = {
    WRITER: ("edit_note", "Writer", "Writes your chapters and scenes, and changes them when you ask. A story can "
             "choose its own in its Story settings."),
    PLANNER: ("psychology", "Planner", "Brainstorms the story plan with you, then designs the chapters and briefs "
              "every scene. A story can pick its own main model on its Planner screen."),
}


def _role_card(role: str, options: dict) -> dict:
    """The writer's or the planner's models: main and fallback, each with its own providers."""
    icon, title, text = ROLE_CARDS[role]
    main, fallback = STATE.app_settings().models_of(role)
    with ui.card().classes("dw-card gap-3").props("flat").mark(f"models-{role}"):
        with ui.row().classes("items-center no-wrap gap-3"):
            ui.icon(icon).classes("text-2xl flex-none").style("color: var(--dw-accent)")
            with ui.column().classes("gap-0 min-w-0"):
                ui.label(f"{title} models").classes("dw-h2")
                ui.label(text).classes("dw-small")
        main_select = ui.select(options, value=main, label="Main model", with_input=True,
                                new_value_mode="add-unique").classes("w-full").mark(f"{role}-main")
        _facts(main)
        _providers_button(main, role)
        fallback_select = ui.select(options, value=fallback, label="Fallback model", with_input=True,
                                    new_value_mode="add-unique", clearable=True).classes("w-full") \
            .mark(f"{role}-fallback")
        _facts(fallback)
        _providers_button(fallback, role)
        ui.label("The fallback steps in when the main model fails or refuses.").classes("dw-small")
        actions = ui.row().classes("gap-2 -ml-2")
        inputs = _options(role)
    return {"main": main_select, "fallback": fallback_select, "actions": actions, "options": inputs}


def _facts(model_id: str | None) -> None:
    if not model_id:
        return
    info = STATE.catalog.get(model_id)
    if info is None:
        service = service_of(model_id)
        ui.label("Not in Venice's model list (add your Venice key above, or refresh the list)." if service == "venice"
                 else f"Not in {EXPLABS_NAME}' model list (add its key above, or refresh the list)."
                 if service == f"svc-{EXPLABS_SLUG}"
                 else "Not in OpenRouter's model list. Check the name, or refresh the list." if service == OPENROUTER
                 else f"Not in {service_name(model_id, STATE.app_settings().services)}'s model list (is the service "
                      "running? Check it under Other services, or refresh the list).").classes("dw-small").style(
            "color: var(--dw-problem); margin-top: -6px")
        return
    price_in, price_out = info.price_per_million()
    facts = []
    if price_in is not None:
        # a typical request: about 8,000 tokens of story files sent, a 3,000-word chapter (about 4,000 tokens) back
        chapter = (8000 * price_in + 4050 * (price_out or 0)) / 1_000_000
        facts.append(f"roughly {money(chapter)} per 3,000-word chapter")
    else:
        facts.append("price unknown (the service doesn't report one)")
    if info.context_length:
        facts.append(f"reads about {info.context_length * 3 // 4 // 1000:,}k words at once")
    line = ui.label(" · ".join(facts)).classes("dw-small").style("margin-top: -6px")
    if price_in is not None:
        line.tooltip(f"${price_in:.2f} per million tokens sent, ${price_out or 0:.2f} per million written; "
                     "thinking models can cost more")


OPTION_HELP = {
    WRITER: "Recommended for prose with mid-size open models: temperature 0.8 for fresh sentences without "
            "drifting, and a little thinking (low) to hold the scene together without flattening the voice. No "
            "repetition penalty: on long replies it can make a model's text fall apart.",
    PLANNER: "Recommended for planning: temperature 0.5 keeps plans, briefs and reply formats reliable, and "
             "medium thinking checks continuity and structure before answering.",
}
THINKING_TEXT = {
    "levels": "takes a thinking level{levels}",
    "switch": "thinks, but takes no level: Off turns its thinking off; any other choice leaves it on",
    "none": "doesn't think, so Thinking is left out",
    "unknown": "its service doesn't say whether it thinks; Thinking is sent as set",
}


def _options(role: str) -> dict:
    """The job's options: set to the recommended values; change any and Save settings keeps it."""
    current = STATE.app_settings().options_of(role)
    best = recommended(role)
    with ui.expansion("Options: how these models write", icon="tune").classes("w-full dw-section-block") \
            .mark(f"{role}-options"):
        with ui.column().classes("gap-2 px-3 pb-3 w-full"):
            ui.label(OPTION_HELP[role]).classes("dw-small")
            with ui.row().classes("gap-3 w-full"):
                temperature = ui.number("Temperature", value=current.temperature, min=0, max=2, step=0.05) \
                    .classes("w-36").mark(f"{role}-temperature").tooltip("Blank: the model's own default")
                top_p = ui.number("Top-p", value=current.top_p, min=0, max=1, step=0.05).classes("w-28") \
                    .tooltip("Blank: the model's own default")
                repetition = ui.number("Repetition penalty", value=current.repetition, min=-2, max=2, step=0.05) \
                    .classes("w-40").tooltip("Frequency penalty. Usually best left blank: it grows with every "
                                             "repeat, and on long replies can make a model's text fall apart")
                effort = ui.select(EFFORTS, value=current.effort or "(not sent)", label="Thinking") \
                    .classes("w-36").mark(f"{role}-effort")
            with ui.row().classes("gap-3 w-full"):
                max_out = ui.number("Longest reply (tokens)", value=current.max_output_tokens, min=256,
                                    precision=0).classes("w-44").tooltip("Thinking included; only what is used "
                                                                         "is paid for")
                size = ui.number("Warn above (tokens sent)", value=current.size_limit_tokens, min=1000,
                                 precision=0).classes("w-44")
            for slot, model_id in zip(("Main", "Fallback"), STATE.app_settings().models_of(role)):
                if model_id:
                    info = STATE.catalog.get(model_id)
                    kind = info.thinking if info else "unknown"
                    levels = f" ({', '.join(info.effort_levels)})" if info and info.effort_levels else ""
                    ui.label(f"Thinking: {model_name(model_id)} ({slot.lower()}) "
                             + THINKING_TEXT[kind].format(levels=levels) + ".").classes("dw-small") \
                        .mark(f"{role}-thinking-{slot.lower()}")
            changed = ui.label().classes("dw-small")

            def describe() -> None:
                now = _read_options(inputs, current)
                changed.text = "These are the recommended values." if now == best else \
                    "Changed from the recommended values; Save settings keeps them."

            def restore() -> None:
                temperature.value, top_p.value, repetition.value = best.temperature, best.top_p, best.repetition
                effort.value = best.effort or "(not sent)"
                max_out.value, size.value = best.max_output_tokens, best.size_limit_tokens

            quiet("Use the recommended values", restore, "restart_alt").props("dense").classes("self-start -ml-2") \
                .mark(f"{role}-recommended")
    inputs = {"temperature": temperature, "top_p": top_p, "repetition": repetition, "effort": effort,
              "max_out": max_out, "size": size}
    for element in inputs.values():
        element.on_value_change(describe)
    describe()
    return inputs


def _read_options(inputs: dict, current: JobOptions) -> JobOptions:
    def number(key: str):
        value = inputs[key].value
        return None if value is None or value == "" else float(value)

    effort = inputs["effort"].value
    return JobOptions(temperature=number("temperature"), top_p=number("top_p"), repetition=number("repetition"),
                      effort=None if effort in (None, "(not sent)") else effort,
                      max_output_tokens=int(inputs["max_out"].value or current.max_output_tokens),
                      size_limit_tokens=int(inputs["size"].value or current.size_limit_tokens),
                      params=dict(current.params))


def _providers_button(model_id: str | None, role: str = WRITER) -> None:
    """Providers… for an OpenRouter model, with what the writer or the planner has chosen now."""
    if not model_id or service_of(model_id) != OPENROUTER:
        return
    choice = STATE.app_settings().model(model_id).providers.of(role)
    summary = {"auto": "OpenRouter chooses", "price": "cheapest first", "throughput": "fastest first",
               "latency": "quickest to start"}.get(choice.mode) or (
        ", ".join(filter(None, [f"prefer {len(choice.prefer)}" if choice.prefer else "",
                                f"only {len(choice.only)}" if choice.only else ""])) or "your choice")
    if choice.never:
        summary += f", never {len(choice.never)}"
    with ui.row().classes("items-center gap-2").style("margin-top: -4px"):
        quiet("Providers…", lambda: providers_dialog(model_id, role), "dns").props("dense") \
            .mark("providers-open", f"providers-open-{role}")
        ui.label(f"Providers: {summary}").classes("dw-small")


def venice_card(on_phone: bool) -> None:
    settings = STATE.app_settings()
    source = venice_key_source()
    with card("Venice AI", "Venice's own models, beside OpenRouter's: pick them for the writer or the planner. "
                           "You pay Venice for them (venice.ai)."):
        with ui.row().classes("items-center gap-2"):
            chip("final" if source != "none" else "draft",
                 "Key saved" if source == "stored" else "Key from the environment" if source == "environment"
                 else "No Venice key")
            ui.label({"environment": "Using the VENICE_API_KEY environment variable.",
                      "stored": f"Kept in {key_place()}. Venice's models are in the model lists.",
                      "none": "Optional. Add a key to use Venice's models (create one in Venice's API settings)."}
                     [source]).classes("dw-small")
        if on_phone:
            ui.label("The key can only be added or changed on the PC.").classes("dw-small")
        else:
            with ui.row().classes("w-full items-end no-wrap gap-2"):
                key_input = ui.input("Paste a Venice key", password=True, password_toggle_button=True) \
                    .classes("grow").mark("venice-key")

                async def save_key() -> None:
                    value = (key_input.value or "").strip()
                    if len(value) < 16:
                        toast("That doesn't look like a Venice API key", "warning")
                        return
                    try:
                        set_venice_key(value)
                    except Exception as exc:  # noqa: BLE001
                        toast(f"Couldn't store the key: {exc}", "negative")
                        return
                    key_input.value = ""
                    await STATE.refresh_catalog(force=True)  # Venice's models join the lists
                    toast("Venice key saved")
                    smooth_reload()

                primary("Save key", save_key, "key").mark("venice-save")

        def set_prompt(value: bool) -> None:
            settings.venice_system_prompt = value
            STATE.save_settings()

        ui.switch("Let Venice add its own system prompt", value=settings.venice_system_prompt,
                  on_change=lambda e: set_prompt(bool(e.value))).tooltip(
            "Off: only your own instructions are sent (recommended)")
        report = ui.column().classes("w-full gap-2")

        async def check() -> None:
            report.clear()
            with report:
                with ui.row().classes("items-center gap-2"):
                    ui.spinner(size="18px")
                    ui.label("Checking… (one tiny request, well under $0.01)").classes("dw-small")
            venice = STATE.client().venice
            if venice is None:
                report.clear()
                with report:
                    notice("info", "Add your Venice key first.")
                return
            result = await venice_check(venice, settings, settings_dir() / "logs", get_venice_key())
            report.clear()
            with report:
                notice("success" if result.ok else "problem",
                       "Connected. Your Venice key works." if result.ok else
                       "The Venice check didn't pass. The details below say what went wrong.")
                with ui.expansion("Technical details").classes("w-full"):
                    ui.label(result.text()).classes("dw-raw")

        with ui.row().classes("gap-2"):
            secondary("Check the connection", check, "wifi_tethering")
            if source == "stored" and not on_phone:
                async def remove_key() -> None:
                    delete_venice_key()
                    await STATE.refresh_catalog(force=True)
                    toast("Venice key removed")
                    smooth_reload()
                quiet("Remove the stored key", remove_key)


def startup_card() -> None:
    """Start in the background when you sign in to Windows (so the phone can reach the app after a restart), and
    run hidden, with only an icon beside the clock (devils_workshop/tray.py)."""
    settings = STATE.app_settings()
    phone = settings.phone
    with card("Start with Windows and run hidden", "Runs Devil's Workshop in the background, without a window, "
                                                   "so your phone can use it after the PC restarts, and so it "
                                                   "doesn't show on the desktop."):
        def toggled(event) -> None:
            try:
                startup.enable() if event.value else startup.disable()
            except OSError as exc:
                toast(f"Windows didn't allow it: {exc}", "negative")
                return
            toast("It will start in the background when you sign in" if event.value else
                  "It won't start with Windows any more")

        ui.switch("Start in the background when I sign in to Windows", value=startup.enabled(),
                  on_change=toggled).mark("startup-switch")

        def hidden(event) -> None:
            settings.tray = bool(event.value)
            STATE.save_settings()
            if STATE.background:  # this copy is the hidden one: show or remove its icon now
                from devils_workshop.tray import TRAY
                TRAY.start() if settings.tray else TRAY.stop()
            toast("From now on, closing the window leaves it running, with an icon beside the clock (it may be "
                  "under the ^). Takes effect the next time you open it" if settings.tray and not STATE.background
                  else "It now runs hidden, with an icon beside the clock (it may be under the ^)" if settings.tray
                  else "Closing the window will close the app again")

        ui.switch("Run hidden: no window or taskbar button, only an icon beside the clock", value=settings.tray,
                  on_change=hidden).mark("tray-switch")
        ui.label("The icon is called Workshop and Windows usually tucks it under the ^ beside the clock. Click it "
                 "for Open (a window; closing it hides the app again), Lock now and Quit. With a passcode "
                 "(below), the window asks for it each time it opens.").classes("dw-small")
        ui.label("Open Devil's Workshop as usual to see it in a window; closing that window leaves it running. In "
                 "that window, type or paste folder paths (Browse… needs the app's own window).").classes("dw-small")
        if not (phone.enabled and phone.password_hash):
            ui.label("Phone access is off, so nothing can reach it yet: switch it on above.").classes("dw-small") \
                .style("color: var(--dw-problem)")
        ui.label("Windows still sleeps when the PC is idle: set Windows Settings → System → Power → Sleep to Never, "
                 "and after a restart someone has to sign in (see the README for starting before sign-in).") \
            .classes("dw-small")
        if STATE.background:
            def stop() -> None:
                toast("Stopping. Open Devil's Workshop again to start it")
                app.shutdown()
            quiet("Stop the background app", stop, "power_settings_new").classes("self-start") \
                .mark("startup-stop")


def lock_card() -> None:
    """A passcode that closes this PC's screens (devils_workshop/ui/lock.py)."""
    from devils_workshop.settings import LOCK_IDLE_CHOICES
    from devils_workshop.ui import lock
    settings = STATE.app_settings()
    with card("Passcode lock", "A code that keeps this PC's screens private: the app asks for it when it opens, "
                               "after it goes unused for a while, and when you choose Lock now. Your phone keeps "
                               "its own password."):
        with ui.row().classes("items-center gap-2"):
            chip("final" if lock.enabled() else "draft", "Passcode set" if lock.enabled() else "No passcode")
            ui.label("Only a scrambled form of it is saved. It hides the screens; it doesn't encrypt your story "
                     "files.").classes("dw-small")
        with ui.expansion("Change the passcode" if lock.enabled() else "Set a passcode", icon="pin",
                          value=not lock.enabled()).classes("dw-section-block w-full").mark("lock-code"):
            with ui.column().classes("gap-2 px-4 pb-4 w-full"):
                with ui.row().classes("w-full gap-2 no-wrap"):
                    code = ui.input("New passcode", password=True, password_toggle_button=True) \
                        .classes("grow").mark("lock-code-new")
                    again = ui.input("The same again", password=True).classes("grow").mark("lock-code-again")
                ui.label(f"At least {lock.MIN_CODE} characters; digits are fine. Forgot it? See the README.") \
                    .classes("dw-small")

                def save_code() -> None:
                    problem = lock.code_problem(code.value or "", again.value or "")
                    if problem:
                        toast(problem, "warning")
                        return
                    lock.set_code(code.value)
                    toast("Passcode saved. Lock now is at the bottom of the sidebar")
                    smooth_reload()

                primary("Save the passcode", save_code, "lock").classes("self-start").mark("lock-save")

        def idle_changed(event) -> None:
            settings.lock.idle_minutes = int(event.value)
            STATE.save_settings()
            lock.touch()

        idle = {m: "Only when I lock it" if m == 0 else f"After {m} minute{'s' if m != 1 else ''} unused"
                for m in LOCK_IDLE_CHOICES}
        ui.select(idle, value=settings.lock.idle_minutes, label="Lock by itself",
                  on_change=idle_changed).classes("w-72").mark("lock-idle")
        if lock.enabled():
            with ui.row().classes("gap-2"):
                secondary("Lock now", lock.lock_now, "lock").mark("lock-now-settings")

                def remove() -> None:
                    lock.remove_code()
                    toast("Passcode removed")
                    smooth_reload()
                quiet("Remove the passcode", remove, "lock_open").mark("lock-remove")


def experiential_card(on_phone: bool) -> None:
    source = explabs_key_source()
    with card(EXPLABS_NAME, "A gateway to many models, some of which it runs itself. You pay Experiential Labs for "
                            "them (experientiallabs.ai)."):
        with ui.row().classes("items-center gap-2"):
            chip("final" if source != "none" else "draft",
                 "Key saved" if source == "stored" else "Key from the environment" if source == "environment"
                 else "No Experiential Labs key")
            ui.label({"environment": "Using the EXPLABS_API_KEY environment variable.",
                      "stored": f"Kept in {key_place()}. Its models are in the model lists.",
                      "none": "Optional. Add a key to use its models (create one at platform.experientiallabs.ai; "
                              "it starts with xpl_)."}[source]).classes("dw-small")
        if on_phone:
            ui.label("The key can only be added or changed on the PC.").classes("dw-small")
        else:
            with ui.row().classes("w-full items-end no-wrap gap-2"):
                key_input = ui.input("Paste an Experiential Labs key", password=True, password_toggle_button=True) \
                    .classes("grow").mark("explabs-key")

                async def save_key() -> None:
                    value = (key_input.value or "").strip()
                    if not is_explabs_key(value):
                        toast("That doesn't look like an Experiential Labs key (they start with xpl_)", "warning")
                        return
                    try:
                        set_service_key(EXPLABS_SLUG, value)
                    except Exception as exc:  # noqa: BLE001
                        toast(f"Couldn't store the key: {exc}", "negative")
                        return
                    key_input.value = ""
                    await STATE.refresh_catalog(force=True)  # its models join the lists
                    toast("Key saved. Its models are in the lists: choose them under Writer models and Planner "
                          "models")
                    smooth_reload()

                primary("Save key", save_key, "key").mark("explabs-save")
        report = ui.column().classes("w-full gap-2")

        async def check() -> None:
            report.clear()
            with report:
                with ui.row().classes("items-center gap-2"):
                    ui.spinner(size="18px")
                    ui.label(f"Asking {EXPLABS_NAME} for your models…").classes("dw-small")
            client = STATE.client().services.get(EXPLABS_SLUG)
            found, problem = [], None
            if client is not None:
                try:
                    found = await client.list_models()
                except Exception as exc:  # noqa: BLE001 - offline, wrong key
                    problem = exc
            report.clear()
            with report:
                if client is None:
                    notice("info", f"Add your {EXPLABS_NAME} key first.")
                elif problem is None:
                    notice("success" if found else "warning",
                           f"Connected. Your key can call {len(found):,} model{'s' if len(found) != 1 else ''}.")
                else:
                    status = getattr(getattr(problem, "response", None), "status_code", None)
                    notice("problem", f"Couldn't reach {EXPLABS_NAME} ({type(problem).__name__}"
                                      f"{f', {status}' if status else ''})."
                                      + (" Check the key." if status in (401, 403) else
                                         " Check your internet connection."))

        with ui.row().classes("gap-2"):
            secondary("Check the connection", check, "wifi_tethering").mark("explabs-check")
            if source == "stored" and not on_phone:
                async def remove_key() -> None:
                    delete_service_key(EXPLABS_SLUG)
                    await STATE.refresh_catalog(force=True)
                    toast(f"{EXPLABS_NAME} key removed")
                    smooth_reload()
                quiet("Remove the stored key", remove_key)


def services_card(on_phone: bool) -> None:
    """Any other service that speaks OpenAI's chat format: added with a name, its address and an optional key."""
    settings = STATE.app_settings()
    with card("Other services", "Any service that speaks OpenAI's chat format, beside OpenRouter and Venice: for "
                                "example Featherless or NanoGPT, or a model on this PC in LM Studio or Ollama. "
                                "Its models join every model list."):
        if not settings.services:
            ui.label("None added yet.").classes("dw-small")
        for service in settings.services:
            _service_row(service, on_phone)
        if on_phone:
            ui.label("Services can only be added or removed on the PC.").classes("dw-small")
            return
        with ui.expansion("Add a service", icon="add_link").classes("dw-section-block w-full") \
                .props("default-opened" if not settings.services else ""):
            with ui.column().classes("gap-2 px-4 pb-4 w-full"):
                name = ui.input("Name", placeholder="e.g. Featherless").classes("w-full").mark("service-name")
                url = ui.input("Address (base URL)", placeholder="e.g. https://api.featherless.ai/v1").classes(
                    "w-full").mark("service-url")
                ui.label("Featherless: https://api.featherless.ai/v1 · NanoGPT: https://nano-gpt.com/api/v1 · "
                         "LM Studio: http://localhost:1234/v1 · Ollama: http://localhost:11434/v1").classes("dw-small")
                key = ui.input("API key (leave empty if it needs none)", password=True,
                               password_toggle_button=True).classes("w-full").mark("service-key")

                async def add() -> None:
                    label = (name.value or "").strip()
                    slug = slugify(label)
                    address = (url.value or "").strip().rstrip("/")
                    for ending in ("/chat/completions", "/models"):
                        if address.endswith(ending):
                            address = address[: -len(ending)]
                    if not slug:
                        toast("Give the service a name", "warning")
                        return
                    if slug in BUILT_IN:
                        toast(f"{BUILT_IN[slug]} is built in: add its key under {BUILT_IN[slug]} above", "warning")
                        return
                    if any(s.slug == slug for s in settings.services):
                        toast(f"There is already a service called {label}", "warning")
                        return
                    if not address.startswith(("http://", "https://")):
                        toast("The address starts with http:// or https://", "warning")
                        return
                    secret = (key.value or "").strip()
                    if secret:
                        try:
                            set_service_key(slug, secret)
                        except Exception as exc:  # noqa: BLE001
                            toast(f"Couldn't store the key: {exc}", "negative")
                            return
                    settings.services.append(ServiceSettings(name=label, slug=slug, base_url=address))
                    STATE.save_settings()
                    key.value = ""
                    await STATE.refresh_catalog(force=True)  # its models join the lists
                    toast(f"{label} added")
                    smooth_reload()

                primary("Add service", add, "add").classes("self-start").mark("service-add")


def _service_row(service: ServiceSettings, on_phone: bool) -> None:
    settings = STATE.app_settings()
    models = sum(1 for m in STATE.catalog.models if service_of(m) == f"svc-{service.slug}")
    with ui.column().classes("w-full gap-1"):
        with ui.row().classes("items-center gap-2"):
            ui.label(service.name).classes("dw-h3")
            chip("final" if models else "draft", f"{models:,} model{'s' if models != 1 else ''}" if models
                 else "No models listed")
            ui.label((f"Key kept in {key_place()}" if get_service_key(service.slug) else "No key")
                     + f" · {service.base_url}").classes("dw-small")
        report = ui.column().classes("w-full gap-2")

        async def check() -> None:
            report.clear()
            with report:
                with ui.row().classes("items-center gap-2"):
                    ui.spinner(size="18px")
                    ui.label(f"Asking {service.name} for its models…").classes("dw-small")
            client = STATE.client().services.get(service.slug)
            try:
                found = await client.list_models()
                problem = None
            except Exception as exc:  # noqa: BLE001 - not running, wrong address, wrong key
                found, problem = [], exc
            report.clear()
            with report:
                if problem is None:
                    notice("success", f"Connected. {service.name} lists {len(found):,} model"
                                      f"{'s' if len(found) != 1 else ''}.")
                else:
                    status = getattr(getattr(problem, "response", None), "status_code", None)
                    notice("problem", f"Couldn't reach {service.name} ({type(problem).__name__}"
                                      f"{f', {status}' if status else ''}). Check the address"
                                      f"{' and the key' if status in (401, 403) else ''}, and that it is running.")

        with ui.row().classes("gap-2"):
            secondary("Check the connection", check, "wifi_tethering").mark(f"service-check-{service.slug}")
            if not on_phone:
                async def remove() -> None:
                    delete_service_key(service.slug)
                    settings.services = [s for s in settings.services if s.slug != service.slug]
                    STATE.save_settings()
                    await STATE.refresh_catalog(force=True)
                    toast(f"{service.name} removed")
                    smooth_reload()
                quiet("Remove", remove, "delete_outline").mark(f"service-remove-{service.slug}")
