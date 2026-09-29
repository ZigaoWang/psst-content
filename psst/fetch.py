"""Reading a source as plain text, for researchers and reviewers. Many sites refuse scripts (Historic England,
parliament.uk, Londonist); when one does, the newest Internet Archive copy of the same page is read instead,
and the output says so. Cite the original URL either way (guide, section 8)."""

from __future__ import annotations

import html
import http.client
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

from . import net

TIMEOUT = 30


@dataclass
class Page:
    url: str          # what was actually read
    status: int
    title: str
    text: str
    via_archive: bool = False
    archived_at: str | None = None


class _Text(HTMLParser):
    """Visible text with paragraph breaks; scripts, styles, navigation, and footers dropped."""
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in joined.split("\n")]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _get(url: str) -> tuple[int, str, bytes]:
    """(status, content type, body). Any failure to connect is status 0, never an exception."""
    request = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read(5_000_000)
    except urllib.error.HTTPError as exc:
        return exc.code, "", b""
    except (OSError, ValueError, http.client.HTTPException):
        return 0, "", b""


def _archive_copy(url: str) -> tuple[str, str] | None:
    """The newest good Internet Archive copy of a page: (raw copy URL, timestamp). Uses the archive's full
    index; its quick "available" lookup misses many copies."""
    query = urllib.parse.urlencode({"url": url, "output": "json", "filter": "statuscode:200", "limit": "-1"})
    try:
        rows = net.fetch_json(f"https://web.archive.org/cdx/search/cdx?{query}", attempts=2, timeout=40)
    except RuntimeError:
        return None
    if len(rows) < 2:
        return None
    stamp, original = rows[-1][1], rows[-1][2]
    # "id_" asks for the page as it was captured, without the archive's toolbar.
    return f"https://web.archive.org/web/{stamp}id_/{original}", stamp


def _page(url: str, status: int, content_type: str, body: bytes, **extra) -> Page:
    if "pdf" in content_type or body[:5] == b"%PDF-":
        return Page(url, status, "", "(This is a PDF. Open it another way; the text can't be read here.)", **extra)
    charset = re.search(r"charset=([\w-]+)", content_type)
    decoded = body.decode(charset.group(1) if charset else "utf-8", errors="replace")
    if "html" not in content_type and not decoded.lstrip().startswith("<"):
        return Page(url, status, "", decoded, **extra)
    parser = _Text()
    parser.feed(decoded)
    return Page(url, status, html.unescape(parser.title).strip(), parser.text(), **extra)


def read(url: str, archive: bool = False) -> Page:
    """Read a page. With `archive`, go straight to the Internet Archive."""
    if not archive:
        status, content_type, body = _get(url)
        if 200 <= status < 400 and body:
            return _page(url, status, content_type, body)
        # Refused, gone, or down: an archived copy of the same page is the next best thing.
        original_status = status
    else:
        original_status = None
    copy = _archive_copy(url)
    if not copy:
        reason = "no archived copy" if archive else f"the site answered {original_status or 'nothing'}, and there's no archived copy"
        return Page(url, original_status or 0, "", f"(Couldn't read this page: {reason}.)")
    copy_url, stamp = copy
    status, content_type, body = _get(copy_url)
    if not (200 <= status < 400 and body):
        return Page(copy_url, status, "", f"(The archived copy answered {status}.)")
    return _page(copy_url, status, content_type, body, via_archive=True,
                 archived_at=f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}")


def render(page: Page, limit: int) -> str:
    head = [f"URL: {page.url}"]
    if page.via_archive:
        head.append(f"Read from the Internet Archive copy of {page.archived_at}; the live page refused or failed.")
    if page.title:
        head.append(f"Title: {page.title}")
    text = page.text if len(page.text) <= limit else page.text[:limit] + f"\n\n[... {len(page.text) - limit} more characters; use --max]"
    return "\n".join(head) + "\n\n" + text
