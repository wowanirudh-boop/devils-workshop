"""The look of the app: colours, type and component styles (docs/ui-redesign.md, "Design tokens").

Colours are CSS variables with a light and a dark set; Quasar's own dark mode
(body.body--dark) switches between them. Component defaults (sentence-case
buttons, outlined inputs) are set once, before any screen is built.
"""

from __future__ import annotations

from nicegui import ui

from devils_workshop.ui import copying
from devils_workshop.ui.phone_access import head_html

ACCENT = "#A3253B"


def set_component_defaults() -> None:
    ui.button.default_props("no-caps unelevated")
    ui.input.default_props("outlined dense")
    ui.number.default_props("outlined dense")
    ui.textarea.default_props("outlined")
    ui.select.default_props("outlined dense options-dense")
    ui.toggle.default_props("no-caps unelevated toggle-color=grey-3 toggle-text-color=grey-10 text-color=grey-7")
    ui.input_chips.default_props("outlined dense")
    ui.checkbox.default_props("dense")
    ui.switch.default_props("dense")


PAGE_LIGHT, PAGE_DARK = "#F7F6F3", "#151413"  # --dw-bg, light and dark (and Quasar's dark page, below)


def page_colour(appearance: str) -> str:
    """The page's own background: dark for Dark, light for Light (and for Match Windows, which can't be known
    before the page loads)."""
    return PAGE_DARK if appearance == "dark" else PAGE_LIGHT


def first_paint(appearance: str) -> str:
    """The page's background in its first lines, before the scripts that turn dark mode on: without it, every
    page opened in Dark (or Match Windows, with Windows dark) flashes white while it loads."""
    # !important: the app's own CSS (later in the page) gives body the light colour until dark mode is on. The
    # Reader's light and sepia pages colour their own panel, not the body, so they are unaffected.
    if appearance == "dark":
        return (f'<meta name="color-scheme" content="dark"><style>html, body {{ background: {PAGE_DARK} '
                "!important; }</style>")
    if appearance == "system":
        return (f'<meta name="color-scheme" content="light dark"><style>html, body {{ background: {PAGE_LIGHT}; }}'
                f" @media (prefers-color-scheme: dark) {{ html, body {{ background: {PAGE_DARK} !important; }} }}"
                "</style>")
    return f"<style>html, body {{ background: {PAGE_LIGHT}; }}</style>"


class FirstPaint:
    """Puts first_paint() at the top of every page the app sends, as it is sent. A screen that is still being
    built when it is sent (Settings waits for the model list, for instance) goes out as an empty shell that is
    filled in afterwards, so the colour can't wait for the screen's own code; a page from the phone sign-in or
    the lock gets it too. (NiceGUI compresses pages after this, so the page is still plain text here.)"""

    def __init__(self, asgi_app) -> None:
        self.app = asgi_app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("method") != "GET" or str(scope.get("path")).startswith("/_nicegui"):
            await self.app(scope, receive, send)
            return
        held: dict = {}
        body: list[bytes] = []

        async def paint(message) -> None:
            if message["type"] == "http.response.start":
                kind = dict(message.get("headers") or []).get(b"content-type", b"")
                if not kind.startswith(b"text/html"):
                    held["pass"] = True
                    await send(message)
                else:
                    held["start"] = message
                return
            if held.get("pass") or message["type"] != "http.response.body" or "start" not in held:
                await send(message)
                return
            body.append(message.get("body", b""))
            if message.get("more_body"):
                return
            from devils_workshop.ui.state import STATE
            appearance = STATE.app_settings().appearance
            page = b"".join(body).replace(b"<head>", b"<head>" + first_paint(appearance).encode("utf-8"), 1)
            if appearance == "dark":  # dark from the first paint, not once the scripts turn it on
                page = page.replace(b"<body>", b'<body class="body--dark">', 1)
            start = held["start"]
            headers = [(k, v) for k, v in start.get("headers") or [] if k.lower() != b"content-length"]
            await send({**start, "headers": headers + [(b"content-length", str(len(page)).encode())]})
            await send({"type": "http.response.body", "body": page})

        await self.app(scope, receive, paint)


def apply_theme(appearance: str = "light") -> None:
    """Call at the start of every page."""
    ui.colors(primary=ACCENT, secondary="#3F3B36", accent=ACCENT, positive="#2E7D4F", negative="#B42318",
              warning="#B26B00", info="#2F5BD3", dark="#1E1D1B", dark_page="#151413")
    ui.dark_mode({"dark": True, "system": None}.get(appearance, False))
    ui.add_css(CSS)
    ui.add_head_html(SMOOTH_JS)  # pages arrive whole, without a flash (see SMOOTH_JS)
    head_html()  # Add to Home Screen, reconnecting after the phone sleeps
    copying.head_html()  # Copy buttons, and the app window's right-click menu


CSS = r"""
:root {
  --dw-bg: #F7F6F3; --dw-surface: #FFFFFF; --dw-sunken: #F1EFEB; --dw-sidebar: #FBFAF8;
  --dw-border: #E7E4DF; --dw-border-strong: #D6D2CB;
  --dw-text: #1F1D1A; --dw-muted: #6F6A63; --dw-faint: #9C968D;
  --dw-accent: #A3253B; --dw-accent-soft: #F8E9EC; --dw-accent-text: #8A1C31;
  --dw-draft: #9A5B00; --dw-draft-bg: #FDF3E1; --dw-final: #2E7D4F; --dw-final-bg: #E6F4EC;
  --dw-writing: #2F5BD3; --dw-writing-bg: #E8EEFC; --dw-problem: #B42318; --dw-problem-bg: #FDECEA;
  --dw-idle: #8A847B; --dw-idle-bg: #EFEDE9;
  --dw-ui: "Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", Inter, system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif;
  --dw-serif: Georgia, Cambria, Caladea, "Bitstream Charter", "Palatino Linotype", "Book Antiqua", serif;
  --dw-mono: "Cascadia Mono", Consolas, "SFMono-Regular", Menlo, monospace;
  --dw-radius: 12px; --dw-radius-sm: 8px;
}
body.body--dark {
  --dw-bg: #151413; --dw-surface: #1E1D1B; --dw-sunken: #191817; --dw-sidebar: #1A1918;
  --dw-border: #34312D; --dw-border-strong: #45413B;
  --dw-text: #ECE8E1; --dw-muted: #A39D94; --dw-faint: #7C766D;
  --dw-accent: #E0576F; --dw-accent-soft: #3A1E24; --dw-accent-text: #F08A9B;
  --dw-draft: #E9A23B; --dw-draft-bg: #36290F; --dw-final: #5CC28A; --dw-final-bg: #173023;
  --dw-writing: #7E9CF0; --dw-writing-bg: #1C2541; --dw-problem: #F07167; --dw-problem-bg: #3B1B18;
  --dw-idle: #A39D94; --dw-idle-bg: #2A2826;
}

body { background: var(--dw-bg); color: var(--dw-text); font-family: var(--dw-ui); font-size: 14px;
  -webkit-font-smoothing: antialiased; }
.nicegui-content { padding: 0; gap: 0; }
.q-page { background: var(--dw-bg); }

/* --- type --------------------------------------------------------------------------- */
.dw-serif { font-family: var(--dw-serif); }
.dw-title, .dw-subtitle, .dw-story-title, .dw-serif, .dw-h2, .dw-h3 { overflow-wrap: anywhere; min-width: 0; }
.dw-reader-box { border: 1px solid var(--dw-border); border-radius: 14px; overflow: clip; }
.dw-eyebrow { font-size: 12px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--dw-muted); }
.dw-title { font-family: var(--dw-serif); font-size: 30px; line-height: 1.2; font-weight: 400; color: var(--dw-text); }
.dw-subtitle { font-size: 14px; color: var(--dw-muted); line-height: 1.5; }
.dw-h2 { font-size: 17px; font-weight: 600; color: var(--dw-text); }
.dw-h3 { font-size: 14px; font-weight: 600; color: var(--dw-text); }
.dw-body { font-size: 14px; line-height: 1.55; color: var(--dw-text); }
.dw-muted { color: var(--dw-muted); }
.dw-small { font-size: 12.5px; color: var(--dw-muted); line-height: 1.45; }
.dw-mono { font-family: var(--dw-mono); font-size: 12.5px; }
.dw-pre { white-space: pre-wrap; overflow-wrap: anywhere; }

/* --- page layout ---------------------------------------------------------------------- */
.dw-page { width: 100%; margin: 0; padding: 32px 40px 64px; display: flex; flex-direction: column; gap: 20px; }
.dw-page.narrow { max-width: 820px; } .dw-page.medium { max-width: 1080px; } .dw-page.wide { max-width: 1320px; }
.dw-page.full { max-width: none; }
.dw-page-header { gap: 16px; margin-bottom: 4px; }

/* --- sidebar -------------------------------------------------------------------------- */
/* NiceGUI puts the drawer's classes on its content element (.q-drawer__content.dw-sidebar). */
.q-drawer--left { background: var(--dw-sidebar) !important; border-right: 1px solid var(--dw-border); }
.q-drawer__content.dw-sidebar { display: flex; flex-direction: column; padding: 18px 12px 14px; gap: 2px;
  background: var(--dw-sidebar); }
.dw-brand { display: flex; align-items: center; gap: 10px; padding: 2px 10px 16px; cursor: pointer; }
.dw-brand-mark { width: 30px; height: 30px; border-radius: 8px; background: var(--dw-accent); color: #fff;
  display: grid; place-items: center; font-family: var(--dw-serif); font-size: 17px; font-weight: 700; }
.dw-brand-name { font-family: var(--dw-serif); font-size: 17px; color: var(--dw-text); }
.dw-story-block { align-self: stretch; margin: 2px 4px 14px; padding: 12px 12px 14px; border: 1px solid var(--dw-border); border-radius: 10px;
  background: var(--dw-surface); }
.dw-story-title { font-family: var(--dw-serif); font-size: 15.5px; line-height: 1.3; color: var(--dw-text); }
.dw-nav-group { font-size: 11px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase;
  color: var(--dw-faint); padding: 14px 12px 6px; }
.q-btn.dw-nav { width: 100%; justify-content: flex-start; padding: 6px 10px; min-height: 36px; border-radius: 8px;
  color: var(--dw-muted); font-weight: 500; font-size: 14px; }
.q-btn.dw-nav .q-icon { font-size: 20px; margin-right: 12px; color: var(--dw-faint); }
.q-btn.dw-nav:hover { background: var(--dw-sunken); color: var(--dw-text); }
.q-btn.dw-nav.active { background: var(--dw-accent-soft); color: var(--dw-accent-text); }
.q-btn.dw-nav.active .q-icon { color: var(--dw-accent); }
.q-btn.dw-nav .q-btn__content { flex-wrap: nowrap; }
.dw-running-pill { margin: 6px 4px 0; padding: 8px 12px; border-radius: 10px; background: var(--dw-accent-soft);
  color: var(--dw-accent-text); font-size: 12.5px; cursor: pointer; display: flex; align-items: center; gap: 8px; }

/* --- surfaces ---------------------------------------------------------------------------- */
.q-card { background: var(--dw-surface); color: var(--dw-text); box-shadow: none; border: 1px solid var(--dw-border);
  border-radius: var(--dw-radius); }
.dw-card { padding: 20px; gap: 12px; width: 100%; }
.dw-card.tight { padding: 14px 16px; }
.dw-card.sunken { background: var(--dw-sunken); }
.dw-card.accent { border: 1.5px solid var(--dw-accent); }
.dw-card.clickable { cursor: pointer; transition: border-color .15s, box-shadow .15s, transform .15s; }
.dw-card.clickable:hover { border-color: var(--dw-border-strong); box-shadow: 0 6px 18px rgba(0,0,0,.06); }
.q-dialog .q-card { box-shadow: 0 24px 64px rgba(0,0,0,.22); border: none; }
.q-dialog__backdrop { background: rgba(20, 18, 16, .45); }
.q-menu { border-radius: 10px; box-shadow: 0 12px 32px rgba(0,0,0,.16); background: var(--dw-surface); color: var(--dw-text); }
.q-separator { background: var(--dw-border); }

/* --- buttons ------------------------------------------------------------------------------ */
.q-btn { border-radius: var(--dw-radius-sm); font-weight: 500; letter-spacing: 0; }
.q-btn.dw-secondary { background: var(--dw-surface); color: var(--dw-text); }
.q-btn.dw-secondary:before { border: 1px solid var(--dw-border-strong); }
.q-btn.dw-secondary .q-icon { color: var(--dw-muted); }
.q-btn.dw-quiet { color: var(--dw-muted); }
.q-btn.dw-quiet:hover { color: var(--dw-text); }
.q-btn.dw-quiet .q-icon { color: var(--dw-faint); }
.q-btn.dw-danger, .q-btn.dw-danger .q-icon { color: var(--dw-problem); }
.q-btn-group { box-shadow: none; }

/* --- inputs ---------------------------------------------------------------------------------- */
.q-field--outlined .q-field__control { border-radius: var(--dw-radius-sm); background: var(--dw-surface); }
.q-field--outlined .q-field__control:before { border-color: var(--dw-border-strong); }
.q-field__label, .q-field__native, .q-field__input { color: var(--dw-text); }
.q-toggle__label, .q-checkbox__label { color: var(--dw-text); }
.q-btn-toggle { border: 1px solid var(--dw-border-strong); border-radius: var(--dw-radius-sm); overflow: hidden;
  background: var(--dw-surface); }
.q-btn-toggle .q-btn { border-radius: 0; padding: 4px 14px; min-height: 34px; }

/* --- chips, notices, stats --------------------------------------------------------------------- */
.dw-chip { display: inline-flex; align-items: center; gap: 6px; padding: 2px 10px; border-radius: 999px;
  font-size: 12px; font-weight: 600; line-height: 20px; white-space: nowrap; }
.dw-chip:before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.dw-chip.idle { color: var(--dw-idle); background: var(--dw-idle-bg); }
.dw-chip.draft { color: var(--dw-draft); background: var(--dw-draft-bg); }
.dw-chip.final { color: var(--dw-final); background: var(--dw-final-bg); }
.dw-chip.writing { color: var(--dw-writing); background: var(--dw-writing-bg); }
.dw-chip.problem { color: var(--dw-problem); background: var(--dw-problem-bg); }
.dw-dot { width: 9px; height: 9px; border-radius: 50%; flex: none; }
.dw-dot.idle { background: transparent; border: 1.5px solid var(--dw-border-strong); }
.dw-dot.draft { background: var(--dw-draft); } .dw-dot.final { background: var(--dw-final); }
.dw-dot.writing { background: var(--dw-writing); } .dw-dot.problem { background: var(--dw-problem); }
.dw-notice { display: flex; gap: 12px; align-items: flex-start; padding: 12px 16px; border-radius: 10px; width: 100%;
  font-size: 13.5px; line-height: 1.5; border: 1px solid transparent; }
.dw-notice .q-icon { font-size: 20px; margin-top: 1px; }
.dw-notice.info { background: var(--dw-writing-bg); color: var(--dw-text); } .dw-notice.info .q-icon { color: var(--dw-writing); }
.dw-notice.warning { background: var(--dw-draft-bg); color: var(--dw-text); } .dw-notice.warning .q-icon { color: var(--dw-draft); }
.dw-notice.problem { background: var(--dw-problem-bg); color: var(--dw-text); } .dw-notice.problem .q-icon { color: var(--dw-problem); }
.dw-notice.success { background: var(--dw-final-bg); color: var(--dw-text); } .dw-notice.success .q-icon { color: var(--dw-final); }
.dw-stat-value { font-size: 24px; font-weight: 600; line-height: 1.15; color: var(--dw-text); font-variant-numeric: tabular-nums; }
.dw-stat-label { font-size: 12.5px; color: var(--dw-muted); }
.dw-bar { height: 6px; border-radius: 99px; background: var(--dw-sunken); overflow: hidden; width: 100%; }
.dw-bar > div { height: 100%; border-radius: 99px; background: var(--dw-accent); }
.dw-bar.final > div { background: var(--dw-final); } .dw-bar.draft > div { background: var(--dw-draft); }

/* --- lists -------------------------------------------------------------------------------------- */
.dw-list { width: 100%; border: 1px solid var(--dw-border); border-radius: var(--dw-radius); background: var(--dw-surface);
  overflow: hidden; }
.dw-row { display: flex; align-items: center; gap: 14px; padding: 12px 16px; border-top: 1px solid var(--dw-border);
  width: 100%; }
.dw-row:first-child { border-top: none; }
.dw-row.clickable { cursor: pointer; } .dw-row.clickable:hover { background: var(--dw-sunken); }
.dw-row.selected { background: var(--dw-accent-soft); }
.q-table__card, .q-table__container { box-shadow: none; border: 1px solid var(--dw-border); border-radius: var(--dw-radius);
  background: var(--dw-surface); color: var(--dw-text); }
.q-table th { font-size: 12px; font-weight: 600; color: var(--dw-muted); }
.q-table tbody td { font-size: 13.5px; }
.q-table--flat.q-table--bordered, .q-table__container.q-table--flat { border: 1px solid var(--dw-border); }

/* --- chapter workspace --------------------------------------------------------------------------- */
.dw-workspace { width: 100%; min-height: 100vh; min-height: 100dvh; align-items: stretch; flex-wrap: nowrap; gap: 0; }
.dw-chapter-list { width: 268px; flex: none; border-right: 1px solid var(--dw-border); background: var(--dw-surface);
  padding: 20px 10px; position: sticky; top: 0; height: 100vh; height: 100dvh; overflow-y: auto; gap: 2px; }
.dw-chapter-item { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 8px; cursor: pointer;
  width: 100%; color: var(--dw-text); }
.dw-chapter-item:hover { background: var(--dw-sunken); }
.dw-chapter-item.active { background: var(--dw-accent-soft); }
.dw-chapter-num { font-size: 12px; color: var(--dw-faint); width: 18px; text-align: right; flex: none; font-variant-numeric: tabular-nums; }
.dw-chapter-name { font-size: 13.5px; line-height: 1.3; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dw-chapter-words { font-size: 11.5px; color: var(--dw-faint); font-variant-numeric: tabular-nums; }
.dw-chapter-pane { flex: 1; min-width: 0; }
.dw-prose { font-family: var(--dw-serif); font-size: 18px; line-height: 1.7; color: var(--dw-text); max-width: 40em; }
.dw-prose p { margin: 0 0 .9em; }
.dw-prose .dw-scene-head { font-family: var(--dw-ui); font-size: 11.5px; font-weight: 600; letter-spacing: .1em;
  text-transform: uppercase; color: var(--dw-faint); margin: 2em 0 1em; }
.dw-prose .dw-scene-head:first-child { margin-top: 0; }
.dw-composer { position: sticky; bottom: 0; z-index: 5; background: linear-gradient(to bottom, transparent, var(--dw-bg) 22px);
  padding-top: 24px; width: 100%; }
.dw-savebar { position: sticky; bottom: 0; z-index: 5; background: var(--dw-bg); border-top: 1px solid var(--dw-border);
  padding: 12px 0; margin-top: 8px; width: 100%; display: flex; justify-content: flex-end; gap: 8px; }
.dw-table-row { display: grid; grid-template-columns: 140px minmax(0, 1fr) 150px 190px 64px 72px; gap: 12px;
  align-items: center; padding: 11px 16px; border-top: 1px solid var(--dw-border); }
.dw-table-row:first-child { border-top: none; }
.dw-table-row.head { background: var(--dw-sunken); font-size: 12px; font-weight: 600; color: var(--dw-muted); }
.dw-table-row.clickable { cursor: pointer; } .dw-table-row.clickable:hover { background: var(--dw-sunken); }
.dw-library-row { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr) minmax(0, 1.4fr) 130px 80px 64px 100px 36px;
  gap: 12px; align-items: center; padding: 9px 16px; border-top: 1px solid var(--dw-border); }
.dw-library-row:first-child { border-top: none; }
.dw-library-row.head { background: var(--dw-sunken); font-size: 12px; font-weight: 600; color: var(--dw-muted); }
.dw-library-row.clickable { cursor: pointer; } .dw-library-row.clickable:hover { background: var(--dw-sunken); }
.dw-brief { font-size: 14px; line-height: 1.6; color: var(--dw-text); }
.dw-brief-scene { border-left: 2px solid var(--dw-border-strong); padding: 2px 0 2px 12px; margin: 8px 0; }

/* --- review dialog -------------------------------------------------------------------------------- */
.dw-dialog-head { padding: 16px 24px; border-bottom: 1px solid var(--dw-border); background: var(--dw-surface); }
.dw-section-block { border: 1px solid var(--dw-border); border-radius: 10px; background: var(--dw-surface); width: 100%; }
.dw-section-block .q-expansion-item__container > .q-item { min-height: 42px; }
.dw-raw { font-family: var(--dw-mono); font-size: 12.5px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere;
  background: var(--dw-sunken); border-radius: 10px; padding: 16px; color: var(--dw-text); width: 100%; }
.q-expansion-item .q-item__label { font-weight: 500; }

/* --- file editor ---------------------------------------------------------------------------------- */
.dw-editor .cm-editor { border: 1px solid var(--dw-border); border-radius: 10px; overflow: hidden; }
.dw-editor .cm-content { font-family: var(--dw-ui); font-size: 14.5px; line-height: 1.65; }
.dw-editor .cm-gutters { background: var(--dw-sunken); border-right: 1px solid var(--dw-border); color: var(--dw-faint); }
.dw-editor .cm-editor.cm-focused { outline: 2px solid var(--dw-accent-soft); }
.q-tree__node-header { border-radius: 6px; padding: 3px 6px; }
.q-tree__node--selected { background: var(--dw-accent-soft); color: var(--dw-accent-text); }

/* --- misc ------------------------------------------------------------------------------------------ */
.q-tab { text-transform: none; font-weight: 500; }
.q-tabs { color: var(--dw-muted); }
.q-tab--active { color: var(--dw-accent-text); }
.q-notification { border-radius: 10px; }
a.dw-link { color: var(--dw-accent-text); text-decoration: none; } a.dw-link:hover { text-decoration: underline; }
.q-tooltip { font-size: 12px; background: #2A2724; }

.dw-qr { background: #fff; border-radius: 10px; padding: 4px; line-height: 0; flex: none; }
.dw-qr svg { width: 148px; height: 148px; }

/* --- side by side on wide screens, stacked on phones ------------------------------------------------ */
.dw-cols { display: grid; gap: 16px; width: 100%; align-items: start; }
.dw-cols.main-side { grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr); gap: 20px; }
.dw-cols.halves { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
.dw-cols.tree { grid-template-columns: 260px minmax(0, 1fr); }
.dw-topbar, .dw-phone-only { display: none !important; }

/* --- narrow windows: the sidebar becomes an icon rail ----------------------------------------- */
@media (min-width: 600px) and (max-width: 1199.98px) {
  .q-drawer--left { width: 68px !important; }
  .q-layout .q-page-container { padding-left: 68px !important; }
  .q-drawer__content.dw-sidebar { padding: 14px 8px; }
  .dw-sidebar .dw-brand-name, .dw-sidebar .dw-story-block, .dw-sidebar .dw-nav-group,
  .dw-sidebar .dw-running-pill .q-item__label, .dw-sidebar .dw-running-pill > div:last-child { display: none; }
  .dw-sidebar .dw-brand { padding: 2px 8px 14px; }
  .q-btn.dw-nav { justify-content: center; padding: 6px 0; }
  .q-btn.dw-nav .q-btn__content { font-size: 0; }
  .q-btn.dw-nav .q-icon { margin-right: 0; }
  .dw-chapter-list { width: 220px; }
  .dw-chapter-words { display: none; }
  .dw-page { padding: 24px 24px 56px; }
}
@media (min-width: 600px) and (max-width: 899.98px) {
  .dw-cols.main-side, .dw-cols.halves { grid-template-columns: minmax(0, 1fr); }
}

/* --- phones (Chrome and Safari on an iPhone are about 390px wide) ------------------------------------
   The sidebar opens over the page from the top bar's menu button; content stacks in one column;
   inputs are 16px (smaller makes iOS zoom in when one is tapped); tap targets are at least 44px;
   heights use dvh (the visible height between the browser's toolbars); safe areas keep content
   clear of the notch and the home indicator when opened full screen from the Home Screen. */
@media (max-width: 599.98px) {
  body { font-size: 15px; }
  .dw-topbar { display: flex !important; position: sticky; top: 0; z-index: 1000; align-items: center; gap: 4px;
    width: 100%; align-self: stretch;
    min-height: 56px; background: var(--dw-bg); border-bottom: 1px solid var(--dw-border);
    padding: calc(4px + env(safe-area-inset-top)) max(12px, env(safe-area-inset-right)) 4px
      max(4px, env(safe-area-inset-left)); }
  .dw-topbar-story { font-size: 12px; color: var(--dw-muted); line-height: 1.2; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; }
  .dw-topbar-title { font-family: var(--dw-serif); font-size: 18px; line-height: 1.25; color: var(--dw-text);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dw-phone-only { display: flex !important; }
  .dw-desktop-only { display: none !important; }
  .q-layout .q-page-container { padding-left: 0 !important; }
  .q-drawer__content.dw-sidebar { padding: calc(14px + env(safe-area-inset-top)) 10px
    calc(14px + env(safe-area-inset-bottom)) max(10px, env(safe-area-inset-left)); overflow-y: auto; }
  .q-btn.dw-nav { min-height: 46px; font-size: 15px; }

  .dw-page { padding: 16px max(16px, env(safe-area-inset-right)) calc(40px + env(safe-area-inset-bottom))
    max(16px, env(safe-area-inset-left)); gap: 16px; }
  .dw-title { font-size: 25px; }
  .dw-page-header { flex-wrap: wrap !important; align-items: flex-start !important; gap: 10px; }
  .dw-reader-box { border: none; border-radius: 0; margin: 0 calc(-1 * max(16px, env(safe-area-inset-right)));
    width: auto !important; }  /* the reader goes edge to edge */
  .dw-reader-box .dwr-reader:not(.dwr-fullpage) .dwr-bar { top: calc(56px + env(safe-area-inset-top)); }  /* below the top bar */
  .dw-page-header > .q-space { display: none; }
  .dw-page-header > :last-child { flex-wrap: wrap !important; flex: 1 1 100% !important; }
  .dw-card { padding: 16px; }
  .dw-cols, .dw-cols.main-side, .dw-cols.halves, .dw-cols.tree { grid-template-columns: minmax(0, 1fr); }
  .dw-wrap-phone { flex-wrap: wrap !important; }
  .dw-full-phone { width: 100% !important; max-width: 100% !important; }

  input, textarea, select, .q-field__native, .q-field__input, .q-select__filter { font-size: 16px !important; }
  .dw-editor .cm-content { font-size: 16px; }
  .q-field--dense .q-field__control, .q-field--dense .q-field__marginal { min-height: 44px; }
  .q-btn { min-height: 44px; }
  .q-btn.q-btn--round { min-width: 44px; }
  .q-btn--dense { min-height: 40px; }
  .q-item, .q-tab, .q-expansion-item .q-item { min-height: 48px; }
  .q-btn-toggle .q-btn { min-height: 42px; padding: 4px 12px; }
  .q-checkbox, .q-toggle { min-height: 44px; }
  .q-tree__node-header { min-height: 40px; align-items: center; }

  .q-dialog__inner--minimized { padding: max(12px, env(safe-area-inset-top)) 12px
    max(12px, env(safe-area-inset-bottom)); }
  .q-dialog__inner--minimized > .q-card { width: 100% !important; max-width: 100% !important; }
  .q-dialog__inner--maximized > .q-card { padding-bottom: env(safe-area-inset-bottom) !important; }
  .dw-dialog-head { padding: calc(8px + env(safe-area-inset-top)) 16px 8px; flex-wrap: wrap !important; row-gap: 4px; }
  .dw-dialog-head > .q-space { display: none; }
  .dw-review .px-6 { padding-left: 16px !important; padding-right: 16px !important; }
  .dw-review .gap-8 { gap: 12px 24px !important; }
  .q-notifications__list--top { top: env(safe-area-inset-top); }

  .dw-workspace { min-height: 0; }
  .dw-chapter-list { display: none; }
  .dw-prose { font-size: 17.5px; line-height: 1.65; }
  /* the change box comes after the text: kept on screen it would cover half of it */
  .dw-composer { position: static; background: none; padding: 8px 0 env(safe-area-inset-bottom); }
  .dw-composer .q-space { display: none; }
  .dw-savebar { padding-bottom: calc(12px + env(safe-area-inset-bottom)); }
  .dw-table-row { grid-template-columns: minmax(0, 1fr) auto; row-gap: 2px; }
  .dw-table-row.head { display: none; }
  .dw-library-row { grid-template-columns: minmax(0, 1fr) auto; row-gap: 2px; }
  .dw-library-row.head { display: none; }
  .dw-qr svg { width: 172px; height: 172px; }
  .dw-chat-scroll { height: 44dvh !important; }
  .dw-plan-editor { min-height: 40dvh !important; }
}
"""

# Pages arrive smoothly. Each screen is a page of its own, built in the browser once its scripts have run, and
# the layout settles a frame or two after that (the sidebar, the top bar's height, the reader's text size). Shown
# as it happens, that is a cut to a blank page, then everything at once, then a jump. Instead the page before
# fades out (a cross-document view transition, where the browser has them) into the page's own colour, and the
# new page is held back until it is built and settled, then fades in. Anything that must change the page before
# it is seen goes in window.dwBeforeReveal (the reader's text size). Reloads replace the page
# (location.replace), so they fade too, and messages shown just before go with them (components.smooth_reload).
# With reduced motion there are no fades: the page simply appears once it is ready.
CSS += r"""
@view-transition { navigation: auto; }
::view-transition-old(root), ::view-transition-new(root) { animation-duration: 160ms; }
html:not(.dw-ready) #app { opacity: 0; }
/* While a page is built, nothing animates: Quasar's dark styles arrive a moment after the elements, and their
   colour transitions would fade every border and label from light to dark as the page appears. */
html:not(.dw-ready) *, html:not(.dw-ready) *::before, html:not(.dw-ready) *::after { transition: none !important; }
/* "Connection lost": only once it has lasted a moment. A phone waking up reconnects within a second or two, and
   the banner would flash on and off each time. */
#popup.nicegui-error-popup[aria-hidden="false"] { transition-delay: 2s; }
#app { transition: opacity 150ms ease-out; }
@media (prefers-reduced-motion: reduce) {
  #app { transition: none; }
  ::view-transition-group(*), ::view-transition-old(*), ::view-transition-new(*) { animation: none !important; }
}
"""

SMOOTH_JS = """<script>
(function () {
  var root = document.documentElement, mounted = false, back = null;
  window.dwBeforeReveal = window.dwBeforeReveal || [];
  try { back = JSON.parse(sessionStorage.getItem('dw-scroll') || 'null'); sessionStorage.removeItem('dw-scroll'); }
  catch (e) {}
  function restore() {  // shown again after a change: back where you were on it
    if (back && back.at === location.pathname + location.search) window.scrollTo(0, back.y);
    back = null;
  }
  function ready() {
    if (root.classList.contains('dw-ready')) return;
    restore(); root.classList.add('dw-ready'); carried();
  }
  function carried() {
    var raw = null;
    try { raw = sessionStorage.getItem('dw-toast'); sessionStorage.removeItem('dw-toast'); } catch (e) {}
    if (!raw) return;
    var list; try { list = JSON.parse(raw) || []; } catch (e) { return; }
    var tries = 0;
    (function show() {
      if (!(window.Quasar && Quasar.Notify)) { if (tries++ < 60) setTimeout(show, 50); return; }
      list.forEach(function (t) {
        Quasar.Notify.create({message: t.message, type: t.kind, position: 'top', group: false,
                              timeout: t.kind === 'positive' ? 3500 : 7000, multiLine: t.message.length > 70});
      });
    })();
  }
  function built() {
    if (mounted) return; mounted = true;
    window.dwBeforeReveal.forEach(function (f) { try { f(); } catch (e) {} });
    requestAnimationFrame(function () { restore(); requestAnimationFrame(ready); });  // once the layout settles
  }
  function watch() {
    var app = document.getElementById('app');
    if (!app) { setTimeout(watch, 10); return; }
    if (app.children.length) { built(); return; }
    new MutationObserver(function (m, o) { if (app.children.length) { o.disconnect(); built(); } })
      .observe(app, {childList: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watch); else watch();
  setTimeout(ready, 1500);  // never leave a page hidden (a page in the background has no frames to wait for)
  window.dwReload = function (toasts) {
    try {
      if (toasts && toasts.length) sessionStorage.setItem('dw-toast', JSON.stringify(toasts));
      sessionStorage.setItem('dw-scroll', JSON.stringify({at: location.pathname + location.search, y: window.scrollY}));
    } catch (e) {}
    location.replace(location.href);
  };
})();
</script>"""
