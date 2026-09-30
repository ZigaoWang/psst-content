from psst import fetch


def test_pages_become_readable_text():
    body = b"""<html><head><title>The Salisbury</title><style>p{}</style></head><body><nav>Menu</nav>
    <h1>The Salisbury</h1><p>Built in 1892 &amp; refitted in 1898.</p><script>x()</script><p>Grade II.</p>
    <footer>Cookies</footer></body></html>"""
    page = fetch._page("https://example.org", 200, "text/html; charset=utf-8", body)
    assert page.title == "The Salisbury"
    assert "Built in 1892 & refitted in 1898." in page.text and "Grade II." in page.text
    assert "Menu" not in page.text and "Cookies" not in page.text and "x()" not in page.text


def test_pdfs_are_read():
    import io
    import pypdf
    writer = pypdf.PdfWriter()
    writer.add_blank_page(200, 200)
    buffer = io.BytesIO()
    writer.write(buffer)
    blank = fetch._page("https://example.org/a.pdf", 200, "application/pdf", buffer.getvalue())
    assert "no text" in blank.text
    assert "couldn't be read" in fetch._page("https://example.org/b.pdf", 200, "application/pdf", b"%PDF-1.7 broken").text


def test_compressed_captures_are_read():
    import gzip
    body = gzip.compress(b"<html><body><p>Captured compressed.</p></body></html>")
    page = fetch._page("https://example.org", 200, "text/html", fetch._decompress(body, ""))
    assert "Captured compressed." in page.text


def test_find_shows_only_the_passages_about_the_claim():
    from psst import fetch
    text = "\n".join(["Intro about the city.", "Unrelated history of trams."] * 30
                     + ["The lion was bought at Harrods in 1969 by Rendall.", "It later went to Kenya."]
                     + ["More unrelated text."] * 30)
    out = fetch.passages(text, "Harrods 1969 lion Kenya")
    assert "bought at Harrods in 1969" in out and len(out) < 600
    assert "None of the words" in fetch.passages(text, "zeppelin")


def test_pages_decode_in_their_declared_encoding():
    from psst import fetch
    page = '<html><head><meta http-equiv="Content-Type" content="text/html; charset=gb2312"></head><body>武康大楼</body></html>'
    assert "武康大楼" in fetch._decode(page.encode("gb18030"), "text/html")
    assert "武康大楼" in fetch._decode("<p>武康大楼</p>".encode("gb18030"), "")  # nothing declared
    assert fetch._decode("café".encode(), "text/html; charset=utf-8") == "café"


def test_the_server_fetcher_only_reads_public_addresses():
    from psst import tunnel
    for private in ("http://127.0.0.1:5432/", "http://localhost/", "http://10.0.0.1/", "file:///etc/passwd",
                    "http://[::1]/"):
        assert not tunnel._public(private), private
