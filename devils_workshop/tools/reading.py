"""The story as one text for reading: chapters in order, scenes joined by a break.

Used by the Read screen and by "Save as… web page", which writes a single .html
file that opens in any browser (phone or computer) without the app. Only the
current version of each written chapter is used.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from devils_workshop.project.project import Project

WORDS_PER_MINUTE = 250
_BREAK = re.compile(r"^\s*(?:\*\s*\*\s*\*|(?:-\s*){3,}|(?:_\s*){3,}|(?:~\s*){3,}|#\s*#\s*#|⁂|(?:•\s*){3})\s*$")
_STRONG = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_EM = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])|(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])")


@dataclass
class ReadingScene:
    number: int   # 0 for a chapter written whole
    title: str
    text: str


@dataclass
class ReadingChapter:
    number: int
    title: str
    scenes: list[ReadingScene]
    status: str
    version: int
    words: int


def reading_chapters(project: Project) -> list[ReadingChapter]:
    """Every written chapter, in order."""
    result = []
    for number in sorted(project.chapters):
        written = project.chapters[number]
        outline = project.outline.chapter(number)
        if written.scenes:
            titles = {s.number: s.title for s in outline.scenes} if outline else {}
            scenes = [ReadingScene(n, titles.get(n, ""), written.scenes[n]) for n in sorted(written.scenes)]
        elif written.text.strip():
            scenes = [ReadingScene(0, "", written.text)]
        else:
            continue
        title = (outline.title if outline else "") or written.title
        result.append(ReadingChapter(number, title, scenes, written.status, written.version, written.words))
    return result


def _inline(line: str) -> str:
    text = html.escape(line, quote=False)
    text = _STRONG.sub(r"<strong>\1</strong>", text)
    return _EM.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)


def prose_html(text: str) -> str:
    """Paragraphs (blank-line separated, or one per line if the text has no blank lines; a single line break
    inside a paragraph is a space, as in Markdown), with *italics*, **bold** and scene-break lines such as
    "* * *" shown as a break."""
    text = text.strip().replace("\r\n", "\n")
    if not text:
        return ""
    blocks = re.split(r"\n\s*\n", text) if re.search(r"\n\s*\n", text) else text.split("\n")
    out, first = [], True
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if _BREAK.match(block):
            out.append('<p class="dwr-break">⁂</p>')
            first = True
            continue
        if block.startswith("#"):  # a stray heading in the model's reply
            out.append(f'<p class="dwr-aside">{_inline(block.lstrip("#").strip())}</p>')
            first = True
            continue
        lines = " ".join(_inline(line.strip()) for line in block.split("\n"))  # Markdown: a line break is a space
        out.append(f'<p class="dwr-first">{lines}</p>' if first else f"<p>{lines}</p>")
        first = False
    return "\n".join(out)


def _scene_html(scene: ReadingScene, index: int) -> str:
    parts = []
    if index:
        parts.append('<p class="dwr-break">⁂</p>')
    if scene.number:
        label = f"Scene {scene.number}" + (f" · {html.escape(scene.title)}" if scene.title else "")
        parts.append(f'<p class="dwr-scene-label">{label}</p>')
    parts.append(prose_html(scene.text))
    return "\n".join(parts)


def chapter_html(chapter: ReadingChapter) -> str:
    title = f"<h3>{html.escape(chapter.title)}</h3>" if chapter.title else "<h3></h3>"
    body = "\n".join(_scene_html(scene, i) for i, scene in enumerate(chapter.scenes))
    return (f'<section class="dwr-chapter" id="ch{chapter.number}">\n<h2>Chapter {chapter.number}</h2>\n'
            f"{title}\n{body}\n</section>")


def _minutes(words: int) -> str:
    minutes = max(1, round(words / WORDS_PER_MINUTE))
    return f"about {minutes} min" if minutes < 60 else f"about {minutes // 60} h {minutes % 60:02d} min"


def reader_html(project: Project, only: int | None = None, in_app: bool = True) -> str:
    """The reader: the whole story (with contents), or one chapter with links to its neighbours."""
    chapters = reading_chapters(project)
    bar = BAR
    if not chapters:
        return f'{READER_CSS}<div class="dwr-reader">{bar}<div class="dwr-book"><p class="dwr-meta">' \
               "Nothing is written yet.</p></div></div>"

    if only is not None:
        chapter = next((c for c in chapters if c.number == only), None)
        numbers = [c.number for c in chapters]
        if chapter is None:
            body = f'<p class="dwr-meta">Chapter {only} isn\'t written yet.</p>'
        else:
            at = numbers.index(only)
            prev = f'<a href="/story/read?chapter={numbers[at - 1]}">← Chapter {numbers[at - 1]}</a>' if at else "<span></span>"
            nxt = (f'<a href="/story/read?chapter={numbers[at + 1]}">Chapter {numbers[at + 1]} →</a>'
                   if at + 1 < len(numbers) else "<span></span>")
            meta = f'<p class="dwr-meta">{chapter.words:,} words · {_minutes(chapter.words)}</p>'
            body = f'{chapter_html(chapter)}\n{meta}\n<nav class="dwr-nav">{prev}<a href="/story/read">Whole story</a>{nxt}</nav>'
        return f'{READER_CSS}<div class="dwr-reader">{bar}<div class="dwr-book">{body}</div></div>'

    words = sum(c.words for c in chapters)
    toc = []
    for c in chapters:
        name = f"Chapter {c.number}" + (f": {html.escape(c.title)}" if c.title else "")
        state = f' <span class="dwr-state">{c.status} · v{c.version}</span>' if in_app else ""
        toc.append(f'<li><a href="#ch{c.number}">{name}</a>{state}</li>')
    missing = sorted({ch.number for ch in project.outline.chapters} - {c.number for c in chapters})
    note = (f'<p class="dwr-meta">Not written yet: chapter{"s" if len(missing) > 1 else ""} '
            f'{", ".join(map(str, missing))}</p>') if missing and in_app else ""
    head = (f'<h1 class="dwr-title">{html.escape(project.config.title)}</h1>'
            f'<p class="dwr-meta">{len(chapters)} chapter{"s" if len(chapters) != 1 else ""} · {words:,} words · '
            f'{_minutes(words)}</p>{note}<ol class="dwr-toc">{"".join(toc)}</ol>')
    body = "\n".join(chapter_html(c) for c in chapters)
    return f'{READER_CSS}<div class="dwr-reader">{bar}<div class="dwr-book">{head}\n{body}</div></div>'


def standalone_html(project: Project) -> str:
    """One self-contained .html file with the whole story."""
    title = html.escape(project.config.title)
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{title}</title><style>body{{margin:0}}</style><script>{READER_JS}</script></head>"
            f"<body>{reader_html(project, in_app=False)}"
            "<script>dwReader.apply()</script></body></html>\n")


BAR = (
    '<div class="dwr-bar">'
    '<button class="dwr-aa" onclick="dwReader.panel()" title="Text size, spacing, width and colours">Aa</button>'
    '<button class="dwr-full" onclick="dwReader.full()" title="Read on the whole screen">'
    '<span class="dwr-full-on">⤢ Full page</span><span class="dwr-full-off">✕ Exit full page</span></button>'
    '<div class="dwr-panel">'
    '<div class="dwr-row"><span>Text size</span>'
    '<button onclick="dwReader.size(-1)" title="Smaller text">A−</button>'
    '<span class="dwr-size-value"></span>'
    '<button onclick="dwReader.size(1)" title="Larger text">A+</button></div>'
    '<div class="dwr-row"><span>Line spacing</span>'
    '<button data-s="tight" onclick="dwReader.spacing(\'tight\')">Tight</button>'
    '<button data-s="normal" onclick="dwReader.spacing(\'normal\')">Normal</button>'
    '<button data-s="loose" onclick="dwReader.spacing(\'loose\')">Roomy</button></div>'
    '<div class="dwr-row"><span>Width</span>'
    '<button data-w="narrow" onclick="dwReader.width(\'narrow\')">Narrow</button>'
    '<button data-w="wide" onclick="dwReader.width(\'wide\')">Wide</button>'
    '<button data-w="full" onclick="dwReader.width(\'full\')">Full</button></div>'
    '<div class="dwr-row"><span>Page</span>'
    '<button data-t="light" onclick="dwReader.theme(\'light\')">Light</button>'
    '<button data-t="sepia" onclick="dwReader.theme(\'sepia\')">Sepia</button>'
    '<button data-t="dark" onclick="dwReader.theme(\'dark\')">Dark</button></div>'
    '<div class="dwr-row"><span>Scenes</span>'
    '<button data-t="scenes" onclick="dwReader.scenes()" title="Show or hide scene labels">Scene labels</button></div>'
    '</div></div>')


READER_JS = """
(window.dwBeforeReveal = window.dwBeforeReveal || []).push(function () {  // text size and page colour first
  if (window.dwReader) window.dwReader.apply();
});
window.dwReader = (function () {
  var KEY = "devils-workshop-reader";
  function load() { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; } }
  function save(s) { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) {} }
  var state = load(), full = false, open = false;
  function phone() { return window.innerWidth < 640; }
  function size() { return state.size || (phone() ? 17 : 20); }
  function apply() {
    document.querySelectorAll(".dwr-reader").forEach(function (el) {
      el.style.setProperty("--dwr-size", size() + "px");
      el.setAttribute("data-theme", state.theme || (document.body.classList.contains("body--dark") ? "dark" : "light"));
      el.setAttribute("data-spacing", state.spacing || "normal");
      el.setAttribute("data-width", state.width || (phone() ? "full" : "narrow"));
      el.classList.toggle("dwr-show-scenes", !!state.scenes);
      el.classList.toggle("dwr-panel-open", open);
      el.classList.toggle("dwr-fullpage", full);
      el.querySelectorAll(".dwr-size-value").forEach(function (s) { s.textContent = size(); });
    });
    document.documentElement.classList.toggle("dwr-locked", full);
  }
  function set(key, value) { state[key] = value; save(state); apply(); }
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && full) { full = false; apply(); } });
  return {
    apply: apply,
    size: function (d) { set("size", Math.min(34, Math.max(12, size() + d))); },
    theme: function (t) { set("theme", t); },
    spacing: function (s) { set("spacing", s); },
    width: function (w) { set("width", w); },
    scenes: function () { set("scenes", !state.scenes); },
    panel: function () { open = !open; apply(); },
    full: function () {  // keep your place: the paragraph at the top of the screen stays there
      var spot = null;
      document.querySelectorAll(".dwr-book p, .dwr-book h3").forEach(function (p) {
        if (!spot && p.getBoundingClientRect().top > 90) { spot = p; }
      });
      full = !full; open = false; apply();
      if (spot) { spot.scrollIntoView({block: "start"}); var r = spot.closest(".dwr-reader");
        if (r && full) { r.scrollTop -= 70; } else { window.scrollBy(0, -130); } }
    }
  };
})();
"""

READER_CSS = """<style>
.dwr-reader { --dwr-size: 20px; --bg: #fbfaf7; --fg: #1f1d1a; --muted: #8a8378; --rule: #e4dfd6;
  background: var(--bg); color: var(--fg); min-height: 100vh; padding: 0 16px 5rem; box-sizing: border-box; }
.dwr-reader[data-theme="sepia"] { --bg: #f4ecd8; --fg: #4a3b2a; --muted: #8c7a62; --rule: #e0d2b4; }
.dwr-reader[data-theme="dark"] { --bg: #1b1a18; --fg: #ddd7cc; --muted: #948c7f; --rule: #3a3731; }
.dwr-bar { position: sticky; top: 0; z-index: 1; display: flex; flex-wrap: wrap; gap: 6px; justify-content: flex-end;
  padding: 10px 0; background: var(--bg); border-bottom: 1px solid var(--rule);
  font: 14px system-ui, -apple-system, "Segoe UI", sans-serif; }
.dwr-bar button { background: transparent; color: var(--fg); border: 1px solid var(--rule); border-radius: 6px;
  padding: 4px 10px; cursor: pointer; font: inherit; }
.dwr-reader[data-theme="light"] .dwr-bar button[data-t="light"], .dwr-reader[data-theme="sepia"] .dwr-bar button[data-t="sepia"],
.dwr-reader[data-theme="dark"] .dwr-bar button[data-t="dark"], .dwr-show-scenes .dwr-bar button[data-t="scenes"] {
  background: #A3253B; color: #fff; border-color: #A3253B; }
.dwr-book { max-width: 34em; margin: 0 auto; font-size: var(--dwr-size); line-height: 1.65;
  font-family: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif; }
.dwr-title { text-align: center; font-weight: normal; font-size: 2em; margin: 2.5em 0 0.3em; line-height: 1.2; }
.dwr-meta { text-align: center; color: var(--muted); font: 0.7em/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  margin: 0.5em 0; text-indent: 0; }
.dwr-toc { margin: 2.5em auto 0; padding-left: 1.2em; font-size: 0.9em; }
.dwr-toc a, .dwr-nav a { color: inherit; text-decoration: none; border-bottom: 1px solid var(--rule); }
.dwr-state { color: var(--muted); font: 0.7em system-ui, sans-serif; margin-left: 0.5em; }
.dwr-chapter { margin-top: 5em; scroll-margin-top: 4em; }
.dwr-chapter h2 { text-align: center; font-weight: normal; font-size: 0.75em; letter-spacing: 0.25em;
  text-transform: uppercase; color: var(--muted); margin: 0; }
.dwr-chapter h3 { text-align: center; font-weight: normal; font-size: 1.5em; line-height: 1.25; margin: 0.4em 0 1.8em; }
.dwr-book p { margin: 0; text-indent: 1.5em; hyphens: auto; -webkit-hyphens: auto; }
.dwr-book p.dwr-first { text-indent: 0; }
.dwr-book p.dwr-break { text-align: center; text-indent: 0; margin: 1.4em 0; color: var(--muted); }
.dwr-book p.dwr-aside { text-indent: 0; text-align: center; font-style: italic; margin: 1em 0; }
.dwr-book p.dwr-scene-label { display: none; text-indent: 0; text-align: center; color: var(--muted);
  font: 0.65em/1.5 system-ui, sans-serif; letter-spacing: 0.08em; margin: 1.6em 0 0.8em; }
.dwr-show-scenes p.dwr-scene-label { display: block; }
.dwr-show-scenes p.dwr-break { display: none; }
.dwr-nav { display: flex; justify-content: space-between; gap: 1em; margin-top: 3em;
  font: 0.8em system-ui, sans-serif; }
.dwr-title, .dwr-chapter h3, .dwr-toc a { overflow-wrap: anywhere; }
.dwr-reader[data-spacing="tight"] .dwr-book { line-height: 1.45; }
.dwr-reader[data-spacing="loose"] .dwr-book { line-height: 1.85; }
.dwr-reader[data-width="wide"] .dwr-book { max-width: 48em; }
.dwr-reader[data-width="full"] .dwr-book { max-width: none; }
.dwr-bar { align-items: center; }
.dwr-bar .dwr-aa { font-weight: 600; min-width: 2.6em; }
.dwr-panel { display: none; flex-basis: 100%; flex-direction: column; gap: 8px; padding: 6px 0 2px; }
.dwr-panel-open .dwr-panel { display: flex; }
.dwr-panel-open .dwr-bar .dwr-aa { background: var(--rule); }
.dwr-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; justify-content: flex-end; }
.dwr-row > span:first-child { margin-right: auto; color: var(--muted); }
.dwr-size-value { min-width: 2.2em; text-align: center; }
.dwr-reader[data-spacing="tight"] .dwr-bar button[data-s="tight"], .dwr-reader[data-spacing="normal"] .dwr-bar button[data-s="normal"],
.dwr-reader[data-spacing="loose"] .dwr-bar button[data-s="loose"], .dwr-reader[data-width="narrow"] .dwr-bar button[data-w="narrow"],
.dwr-reader[data-width="wide"] .dwr-bar button[data-w="wide"], .dwr-reader[data-width="full"] .dwr-bar button[data-w="full"] {
  background: #A3253B; color: #fff; border-color: #A3253B; }
.dwr-bar button { min-height: 34px; }
.dwr-full-off { display: none; }
.dwr-fullpage .dwr-full-on { display: none; }
.dwr-fullpage .dwr-full-off { display: inline; }
.dwr-reader.dwr-fullpage { position: fixed; inset: 0; z-index: 5000; overflow-y: auto; height: 100vh; height: 100dvh;
  min-height: 0; -webkit-overflow-scrolling: touch; overscroll-behavior: contain; }
html.dwr-locked, html.dwr-locked body { overflow: hidden; }
@media (max-width: 640px) {
  .dwr-reader { padding: 0 12px 4rem; }
  .dwr-reader.dwr-fullpage { padding: 0 12px calc(4rem + env(safe-area-inset-bottom)); }
  .dwr-book p { text-indent: 1.2em; }
  .dwr-title { font-size: 1.6em; margin-top: 1.4em; }
  .dwr-chapter { margin-top: 3em; }
  .dwr-chapter h3 { margin-bottom: 1.2em; }
}
</style>"""
