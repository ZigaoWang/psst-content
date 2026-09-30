"""Reading a source as plain text, for researchers and reviewers. Many sites refuse scripts (Historic England,
parliament.uk, Londonist); when one does, the newest Internet Archive copy of the same page is read instead,
and the output says so. Cite the original URL either way (guide, section 8)."""

from __future__ import annotations

import gzip
import html
import http.client
import io
import re
import zlib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

import pypdf

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
    links: list[tuple[str, str]] = field(default_factory=list)  # (text, absolute URL), in page order
    note: str | None = None


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
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((re.sub(r"\s+", " ", "".join(self._link_text)).strip(), self._href))
            self._href = None
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._href is not None:
            self._link_text.append(data)
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
            body = response.read(5_000_000)
            encoding = (response.headers.get("Content-Encoding") or "").lower()
            return response.status, response.headers.get("Content-Type", ""), _decompress(body, encoding)
    except urllib.error.HTTPError as exc:
        return exc.code, "", b""
    except (OSError, ValueError, http.client.HTTPException):
        return 0, "", b""


def _decompress(body: bytes, encoding: str) -> bytes:
    """Archived copies come back exactly as captured, sometimes still compressed."""
    try:
        if body[:2] == b"\x1f\x8b" or "gzip" in encoding:
            return gzip.decompress(body)
        if "deflate" in encoding:
            return zlib.decompress(body)
    except (OSError, zlib.error):
        pass
    return body


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
        return _pdf(url, status, body, **extra)
    charset = re.search(r"charset=([\w-]+)", content_type)
    decoded = body.decode(charset.group(1) if charset else "utf-8", errors="replace")
    if "html" not in content_type and not decoded.lstrip().startswith("<"):
        return Page(url, status, "", decoded, **extra)
    parser = _Text()
    parser.feed(decoded)
    links, seen = [], set()
    for text, href in parser.links:
        absolute = urllib.parse.urljoin(url, href)
        if absolute.startswith("http") and absolute not in seen:
            seen.add(absolute)
            links.append((text, absolute))
    return Page(url, status, html.unescape(parser.title).strip(), parser.text(), links=links, **extra)


def _pdf(url: str, status: int, body: bytes, **extra) -> Page:
    """A PDF's text, page by page. Scanned PDFs have no text layer and come back empty."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        pages = [f"[Page {n}]\n{(page.extract_text() or '').strip()}" for n, page in enumerate(reader.pages, 1)]
        title = str((reader.metadata or {}).get("/Title") or "")
    except Exception:  # pypdf raises many kinds of errors on damaged files
        return Page(url, status, "", "(This PDF couldn't be read.)", **extra)
    text = "\n\n".join(pages).strip()
    if not re.search(r"\w{3}", text.replace("[Page", "")):
        text = "(This PDF has no text to read; it's probably scanned images.)"
    return Page(url, status, title, text, **extra)


HISTORIC_ENGLAND = re.compile(r"historicengland\.org\.uk/listing/the-list/list-entry/(\d+)", re.I)


def read(url: str, archive: bool = False) -> Page:
    """Read a page. With `archive`, go straight to the Internet Archive. A Historic England listing whose
    text doesn't come through (it's loaded by script) is read from British Listed Buildings, which
    republishes the same official list entry."""
    page = _read(url, archive)
    entry = HISTORIC_ENGLAND.search(url)
    # Every listing's own text includes its grid reference ("Listing NGR"); a page without it is only the
    # site's frame, with the listing still to be loaded by script.
    if entry and "listing ngr" not in page.text.lower():
        mirror = _read(f"https://britishlistedbuildings.co.uk/10{entry.group(1)}", False)
        if len(mirror.text) > len(page.text):
            mirror.note = ("Historic England's page didn't include the listing text, so this is the same list "
                           "entry from British Listed Buildings. Cite the Historic England URL.")
            return mirror
    return page


WAYBACK = re.compile(r"^(https?://web\.archive\.org/web/\d+)(?!id_)(/.+)$", re.I)


def _read(url: str, archive: bool = False) -> Page:
    # An archive link without "id_" returns the archive's own page around the capture; ask for the capture.
    url = WAYBACK.sub(r"\1id_\2", url)
    if not archive and url.startswith("http://"):
        # Most sites now answer on https, which is also the only kind of source URL allowed.
        secure = _read("https://" + url[len("http://"):])
        if secure.text and not secure.text.startswith("(Couldn't"):
            return secure
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


STOPWORDS = {"the", "and", "for", "was", "were", "that", "this", "with", "from", "into", "its", "his", "her",
             "their", "they", "been", "has", "had", "are", "but", "not", "who", "which", "when", "where", "one"}


def passages(text: str, find: str, limit: int = 4000, most: int = 6) -> str:
    """Only the paragraphs that mention the claim's key words (and the one before each, for context), in page
    order. Checking a claim this way costs a fraction of reading the whole page."""
    terms = {t for t in re.findall(r"\w+", find.lower()) if (len(t) > 2 or t.isdigit()) and t not in STOPWORDS}
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    if not terms or not paragraphs:
        return text[:limit]
    scored = []
    for index, paragraph in enumerate(paragraphs):
        words = set(re.findall(r"\w+", paragraph.lower()))
        hits = len(terms & words) + sum(1 for t in terms if len(t) > 5 and t not in words and t in paragraph.lower())
        if hits:
            scored.append((hits, index))
    if not scored:
        return (f"(None of the words {', '.join(sorted(terms))} appear on this page. Its opening, to confirm it's the "
                f"right page:)\n\n" + text[:1500])
    best = sorted(scored, key=lambda s: (-s[0], s[1]))[:most]
    keep = sorted({i for _, i in best} | {i - 1 for _, i in best if i > 0})
    out, used, previous = [], 0, None
    for i in keep:
        if previous is not None and i != previous + 1:
            out.append("[...]")
        piece = paragraphs[i]
        if used + len(piece) > limit:
            out.append(piece[:max(0, limit - used)] + " [...]")
            break
        out.append(piece)
        used += len(piece)
        previous = i
    return (f"(Only the passages mentioning: {', '.join(sorted(terms))}. Use --max without --find for the whole page.)"
            f"\n\n" + "\n\n".join(out))


def render(page: Page, limit: int, links: bool = False, find: str | None = None) -> str:
    head = [f"URL: {page.url}"]
    if page.via_archive:
        head.append(f"Read from the Internet Archive copy of {page.archived_at}; the live page refused or failed.")
    if page.note:
        head.append(page.note)
    if page.title:
        head.append(f"Title: {page.title}")
    if find and not page.text.startswith("("):
        text = passages(page.text, find, min(limit, 4000))
    else:
        text = page.text if len(page.text) <= limit else page.text[:limit] + f"\n\n[... {len(page.text) - limit} more characters; use --max]"
    out = "\n".join(head) + "\n\n" + text
    if links and page.links:
        out += "\n\nLinks:\n" + "\n".join(f"- {t or '(no text)'}: {u}" for t, u in page.links)
    return out
