from psst import ids, rules

LONG = ("The building went up in 1898 for a shipping firm, and its owners wanted clients to know it. "
        "The carved heads over the door are the firm's partners, cut from photographs, and the ship on the "
        "weather vane is a model of the first steamer they owned. When the firm failed in 1931 the new owners "
        "kept every carving, because removing them would have cost more than the building was worth.")


def fact(**changes):
    base = {"category": "design", "veracity": "fact", "headline": "The faces over the door are the owners",
            "short": "The carved heads are the firm's partners, copied from photographs.", "long": LONG,
            "sources": [{"url": "https://historicengland.org.uk/listing/1", "title": "Listing", "publisher": "Historic England"}]}
    base.update(changes)
    return base


def errors(**changes):
    report = rules.Report()
    rules.check_fact(report, "f", fact(**changes))
    return report.errors


def test_a_good_fact_passes():
    assert errors() == []


def test_em_dashes_are_rejected():
    assert errors(short="The heads are the partners — copied from photographs.")


def test_british_spelling_is_rejected():
    assert errors(short="The colour of the stone was chosen by the partners.")


def test_wikipedia_alone_is_not_enough():
    assert errors(sources=[{"url": "https://en.wikipedia.org/wiki/X", "title": "X", "publisher": "Wikipedia"}])


def test_the_same_source_twice_is_rejected():
    source = {"url": "https://example.org/a", "title": "A", "publisher": "P"}
    same = {"url": "https://www.example.org/a/?utm_source=x", "title": "A", "publisher": "P"}
    assert errors(sources=[source, same])


def test_length_limits():
    assert errors(headline="x" * 61)
    assert errors(long="Too short.")


def test_unknown_veracity_is_rejected():
    assert errors(veracity="rumor")


def test_url_keys_ignore_noise():
    assert rules.normalize_url("https://www.Example.org/a/?utm_medium=x&b=1") == "example.org/a?b=1"
    assert rules.normalize_url("https://en.m.wikipedia.org/wiki/X") == "en.wikipedia.org/wiki/X"


def test_ids():
    assert rules.PLACE_ID_RE.match(ids.new("pl"))
    assert ids.derived("pl", "london-soho/x") == ids.derived("pl", "london-soho/x")
    assert ids.derived("pl", "london-soho/x") != ids.derived("pl", "london-soho/y")


def test_an_archive_copy_reads_as_its_original():
    original = "https://www.example.org/history/page"
    assert rules.read_key("https://web.archive.org/web/20200101000000/http://www.example.org/history/page") == rules.read_key(original)
    assert rules.read_key("https://web.archive.org/web/20200101000000id_/https://example.org/history/page") == rules.read_key(original)
    assert rules.read_key("http://example.org/history/page") == rules.read_key(original)


def test_initials_and_short_forms_dont_end_sentences():
    from psst import rules
    assert rules.count_sentences("Designed by C. W. Stephens in 1901. It stands at No. 5 St. James's Street.") == 2
    assert rules.count_sentences("Built in 1901. Rebuilt in 1950. Listed in 1970. Closed in 2001.") == 4
