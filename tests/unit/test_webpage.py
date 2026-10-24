"""A reference story read from a web page with the author's extractor (planner/webpage.py)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from devils_workshop.planner import webpage
from devils_workshop.planner.inputs import add_input, list_inputs
from devils_workshop.planner.webpage import WebPage, WebPageError, document, read_page

STORY = """<html><head><title>The Lamp Keeper - Tales Online</title>
<meta property="og:site_name" content="Tales Online"></head><body>
<nav>Home | Browse | Login</nav>
<div class="ad-slot">BUY-MARKER Cheap lamps!</div>
<article><h1>The Lamp Keeper</h1>
<p>Disclaimer: all characters are fictional.</p>
<p>STORY-MARKER Mara climbed the forty-one iron steps of the lighthouse at dusk, counting under her breath as
her father had taught her, and found the great lens dark for the first time in thirty years.</p>
<p>She lit the wick with the last of the matches and watched the beam swing out over the reef, where a ship's
light answered, once, and was gone into the rain before she could be sure she had seen it at all.</p>
</article>
<div class="comments">COMMENT-MARKER Great story!!</div>
<footer>Copyright Tales Online</footer></body></html>"""

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js isn't installed here")


@needs_node
def test_the_authors_extractor_keeps_the_story_and_leaves_out_the_clutter(tmp_path):
    saved = tmp_path / "page.html"
    saved.write_text(STORY, encoding="utf-8")
    page = read_page("https://tales.example.com/the-lamp-keeper", saved=saved)  # the bundled extractor, no network
    assert page.title.startswith("The Lamp Keeper") and "STORY-MARKER" in page.markdown and page.words > 40
    for clutter in ("BUY-MARKER", "COMMENT-MARKER", "Login", "Copyright", "Disclaimer"):
        assert clutter not in page.markdown, clutter
    name, data = document(page)
    assert name == f"{page.title}.md"
    text = data.decode("utf-8")
    assert text.startswith(f"# {page.title}\n\nFrom https://tales.example.com/the-lamp-keeper.")
    kept = add_input(tmp_path / "story", name, data)  # kept as a planner file, like one you attach
    assert kept in list_inputs(tmp_path / "story")


@needs_node
def test_a_story_over_several_pages_is_read_whole_and_in_order_from_any_of_them(monkeypatch):
    fake_site = Path(__file__).with_name("fake_site.cjs")  # three numbered pages, no network
    monkeypatch.setenv("NODE_OPTIONS", f'--require "{fake_site.as_posix()}"')
    page = read_page("https://tales.example.com/story?page=2")  # given the middle page
    assert page.pages == 3
    assert re.findall(r"PAGE-(\d)-PARA-\d", page.markdown) == ["1"] * 3 + ["2"] * 3 + ["3"] * 3
    assert "3 pages joined" in document(page)[1].decode("utf-8")


@needs_node
def test_a_page_that_cant_be_read_says_why_in_the_extractors_words():
    with pytest.raises(WebPageError, match="Local and private addresses"):
        read_page("http://localhost/story")


def test_no_node_no_answer_and_a_page_of_many_parts(monkeypatch):
    monkeypatch.setattr(webpage, "node", lambda: None)
    with pytest.raises(WebPageError, match="needs Node.js"):
        read_page("https://example.com/a")
    monkeypatch.setattr(webpage, "node", lambda: "node")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"boom\nCRASH"))
    with pytest.raises(WebPageError, match="stopped without an answer \\(CRASH\\)"):
        read_page("https://example.com/a")
    reply = {"ok": True, "title": "Part / One", "url": "https://example.com/a", "markdown": "Text.", "words": 1,
             "pages": 3, "site": "example.com", "notice": "This page may be paywalled."}
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(reply).encode(), b""))
    name, data = document(read_page("https://example.com/a"))
    assert name == "Part - One.md"
    assert "From https://example.com/a, 3 pages joined. This page may be paywalled." in data.decode("utf-8")


def test_the_document_names_a_page_without_a_title_by_its_site():
    name, _ = document(WebPage("", "https://example.com/x", "Some text.", 2, site="example.com"))
    assert name == "example.com.md"
