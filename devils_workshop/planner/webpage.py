"""A reference story from a web page, read with the author's own extractor (Clearpage), on this PC.

The extractor (vendor/clearpage/clearpage.cjs, built by tools/build_clearpage.py from
https://github.com/wowanirudh-boop/webscraper) reads a page's main text, joins an article's numbered pages in
order, leaves out ads, comments, navigation and opening boilerplate, and gives the text as Markdown. It runs
with the copy of Node inside the app (or Node on the PATH, when the app runs from source). The page is fetched
from this PC: no AI service sees the address. What it returns is kept as one of the planner's files, like a
file you attach (planner/inputs.py), so the planner takes its notes on it as on any other.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "vendor" / "clearpage" / "clearpage.cjs"
TIMEOUT_S = 90  # the extractor gives up on its own after 60 s for all of an article's pages
PROXY = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")


class WebPageError(Exception):
    """Why the page couldn't be read, in words for the author."""


@dataclass
class WebPage:
    title: str
    url: str
    markdown: str
    words: int
    pages: int = 1
    site: str = ""
    author: str = ""
    notice: str = ""


def node() -> str | None:
    """The Node that runs the extractor: the app's own copy (inside the exe), else one on the PATH."""
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", "")) / "node" / ("node.exe" if sys.platform == "win32" else "node")
        if bundled.is_file():
            return str(bundled)
    return shutil.which("node")


def read_page(url: str, saved: Path | None = None) -> WebPage:
    """The story at url, all its pages joined (saved: a page already on disk, read as if from url). This waits
    for the page, so the screens call it in a thread."""
    runtime = node()
    if runtime is None:
        raise WebPageError("Reading a web page needs Node.js, which this copy of the app doesn't have. Install "
                           "Node.js, or save the page as a file and attach it instead.")
    if not SCRIPT.is_file():
        raise WebPageError("The web page reader is missing from this copy of the app.")
    args = [runtime] + (["--use-env-proxy"] if any(os.environ.get(k) for k in PROXY) else []) + [str(SCRIPT)]
    args += ["--file", str(saved), url] if saved is not None else [url.strip()]
    try:
        done = subprocess.run(args, capture_output=True, timeout=TIMEOUT_S,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # no console flashing up
    except subprocess.TimeoutExpired:
        raise WebPageError(f"The page took longer than {TIMEOUT_S} seconds to read. Try again, or a direct link "
                           "to the story.") from None
    except OSError as exc:
        raise WebPageError(f"The web page reader couldn't start: {exc}") from None
    try:
        reply = json.loads(done.stdout.decode("utf-8"))
    except ValueError:
        last = (done.stderr.decode("utf-8", "replace").strip().splitlines() or ["no answer"])[-1]
        raise WebPageError(f"The web page reader stopped without an answer ({last[:200]}).") from None
    if not reply.get("ok"):
        raise WebPageError(reply.get("error") or "The page couldn't be read.")
    return WebPage(title=reply.get("title") or "", url=reply.get("url") or url, markdown=reply.get("markdown") or "",
                   words=int(reply.get("words") or 0), pages=int(reply.get("pages") or 1),
                   site=reply.get("site") or "", author=reply.get("author") or "", notice=reply.get("notice") or "")


def document(page: WebPage) -> tuple[str, bytes]:
    """The page as a file for the planner: its name (from the title), and Markdown that says where it came from."""
    title = " ".join(page.title.split()) or page.site or "Web page"
    name = title.replace("/", "-")[:90].strip(" .-") + ".md"
    joined = f", {page.pages} pages joined" if page.pages > 1 else ""
    header = f"# {title}\n\nFrom {page.url}{joined}." + (f" {page.notice}" if page.notice else "")
    return name, (header + "\n\n" + page.markdown.strip() + "\n").encode("utf-8")
