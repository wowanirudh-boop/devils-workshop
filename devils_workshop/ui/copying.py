"""Copying text: Copy buttons, and a right-click menu in the app window.

Copying runs in the page itself, in the click, rather than through the server:
- the clipboard API (navigator.clipboard) only exists on https or localhost, and the phone version is
  plain http on your network, so there it falls back to the older copy command;
- that older command, and Safari, only allow copying during the click itself.

The app window (pywebview) turns off the browser's own right-click menu, so the window gets a small one:
Copy, Cut, Select all, and a reminder that Ctrl+V pastes. In a browser or on a phone the browser's own
menu is left alone. (Selecting text in the window needs its text_select option: see main.py.)
"""

from __future__ import annotations

import json

from nicegui import ui

COPY_JS = r"""<script>
(function () {
  function said(ok) {
    Quasar.Notify.create({message: ok ? "Copied" : "Couldn't copy here: select the text and press Ctrl+C",
                          type: ok ? "positive" : "warning", position: "top", timeout: ok ? 1800 : 6000, group: false});
  }
  function oldWay(text) {
    var area = document.createElement("textarea");
    area.value = text; area.setAttribute("readonly", "");
    area.style.cssText = "position:fixed;top:0;left:0;opacity:0;";
    document.body.appendChild(area);
    area.focus(); area.select(); area.setSelectionRange(0, text.length);
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    area.remove();
    return ok;
  }
  // Put text on the clipboard, and say so.
  window.dwCopy = function (text) {
    text = text == null ? "" : String(text);
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { said(true); }, function () { said(oldWay(text)); });
    } else {
      said(oldWay(text));
    }
  };
  // The text of an element: an editor's whole document, a text field's value, or what it shows.
  window.dwTextOf = function (id) {
    var vm = typeof getElement === "function" ? getElement(id) : null;
    if (vm && vm.editor && vm.editor.state && vm.editor.state.doc) return vm.editor.state.doc.toString();
    var el = typeof getHtmlElement === "function" ? getHtmlElement(id) : null;
    if (!el) return "";
    var field = el.matches("textarea, input") ? el : el.querySelector("textarea, input");
    return field ? field.value : el.innerText;
  };

  // --- the app window's right-click menu ---
  var menu = null;
  function closeMenu() { if (menu) { menu.remove(); menu = null; } }
  function editable(el) {
    return el && (el.matches("textarea, input:not([type=checkbox]):not([type=radio])") || el.isContentEditable);
  }
  function selectedText() {
    var el = document.activeElement;
    if (el && el.matches("textarea, input") && el.selectionStart !== el.selectionEnd)
      return el.value.substring(el.selectionStart, el.selectionEnd);
    return String(window.getSelection() || "");
  }
  function item(label, enabled, action) {
    var row = document.createElement("div");
    row.className = "dw-menu-item" + (enabled ? "" : " off");
    row.textContent = label;
    row.addEventListener("mousedown", function (e) { e.preventDefault(); });  // keep the selection
    if (enabled) row.addEventListener("click", function () { action(); closeMenu(); });
    return row;
  }
  document.addEventListener("contextmenu", function (event) {
    if (!window.pywebview) return;  // a browser or phone: its own menu
    event.preventDefault();
    closeMenu();
    var target = event.target, field = editable(target) ? target : null, text = selectedText();
    menu = document.createElement("div");
    menu.className = "dw-menu";
    menu.appendChild(item("Copy", !!text, function () {
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      if (ok) said(true); else window.dwCopy(text);
    }));
    if (field) menu.appendChild(item("Cut", !!text, function () { document.execCommand("cut"); }));
    menu.appendChild(item("Select all", true, function () {
      if (field && field.select) { field.focus(); field.select(); return; }
      if (field) { field.focus(); document.execCommand("selectAll"); return; }
      var box = target.closest(".q-dialog .q-card, .dw-card, .q-card, .dw-page") || document.body;
      var range = document.createRange(); range.selectNodeContents(box);
      var selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    }));
    if (field) menu.appendChild(item("Paste: Ctrl+V", false));
    document.body.appendChild(menu);
    var x = Math.min(event.clientX, window.innerWidth - menu.offsetWidth - 8);
    var y = Math.min(event.clientY, window.innerHeight - menu.offsetHeight - 8);
    menu.style.left = Math.max(4, x) + "px"; menu.style.top = Math.max(4, y) + "px";
  });
  ["mousedown", "scroll", "resize", "blur"].forEach(function (kind) {
    window.addEventListener(kind, function (e) {
      if (!menu || (e.target instanceof Node && menu.contains(e.target))) return;
      closeMenu();
    }, true);
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeMenu(); });
})();
</script>"""

COPY_CSS = r"""
.dw-menu { position: fixed; z-index: 10000; min-width: 160px; padding: 4px; border-radius: 8px;
  background: var(--dw-surface); border: 1px solid var(--dw-border-strong); box-shadow: 0 6px 24px rgba(0,0,0,.18);
  font: 14px var(--dw-ui); color: var(--dw-text); }
.dw-menu-item { padding: 6px 12px; border-radius: 6px; cursor: default; user-select: none; }
.dw-menu-item:not(.off):hover { background: var(--dw-accent-soft); }
.dw-menu-item.off { color: var(--dw-faint); }
.dw-copy { opacity: .75; }
.dw-copy:hover { opacity: 1; }
"""


def head_html() -> None:
    """On every page (see theme.apply_theme)."""
    ui.add_head_html(COPY_JS)
    ui.add_css(COPY_CSS)


# For a clickable row: a click opens it, but letting go after selecting some of its text doesn't.
UNLESS_SELECTING = "() => { if (!String(window.getSelection() || '')) emit(); }"


def copy_handler(text: str | None = None, source: ui.element | None = None) -> str:
    """The click handler (JavaScript) that copies `text`, or the current text of `source`."""
    what = f"dwTextOf({source.id})" if source is not None else json.dumps(text or "")
    return f"() => dwCopy({what})"


def copy_button(text: str | None = None, source: ui.element | None = None, label: str = "",
                tip: str = "Copy") -> ui.button:
    """A small Copy button: copies `text`, or what `source` holds when clicked (an editor, a text box, a label)."""
    button = ui.button(label, icon="content_copy", color=None).props(
        "flat dense no-caps" + ("" if label else " round size=sm")).classes("dw-quiet dw-copy").tooltip(tip)
    button.on("click", js_handler=copy_handler(text, source))
    return button
