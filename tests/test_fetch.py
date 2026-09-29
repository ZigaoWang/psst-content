from psst import fetch


def test_pages_become_readable_text():
    body = b"""<html><head><title>The Salisbury</title><style>p{}</style></head><body><nav>Menu</nav>
    <h1>The Salisbury</h1><p>Built in 1892 &amp; refitted in 1898.</p><script>x()</script><p>Grade II.</p>
    <footer>Cookies</footer></body></html>"""
    page = fetch._page("https://example.org", 200, "text/html; charset=utf-8", body)
    assert page.title == "The Salisbury"
    assert "Built in 1892 & refitted in 1898." in page.text and "Grade II." in page.text
    assert "Menu" not in page.text and "Cookies" not in page.text and "x()" not in page.text


def test_pdfs_are_flagged():
    assert "PDF" in fetch._page("https://example.org/a.pdf", 200, "application/pdf", b"%PDF-1.7").text


def test_compressed_captures_are_read():
    import gzip
    body = gzip.compress(b"<html><body><p>Captured compressed.</p></body></html>")
    page = fetch._page("https://example.org", 200, "text/html", fetch._decompress(body, ""))
    assert "Captured compressed." in page.text
