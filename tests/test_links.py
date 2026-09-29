from psst import links


def test_classify():
    assert links.classify(200) == "ok" and links.classify(301) == "ok"
    assert links.classify(404) == "failed" and links.classify(410) == "failed" and links.classify(503) == "failed"
    assert links.classify(403) == "blocked" and links.classify(429) == "blocked"
    assert links.classify("dns") == "failed" and links.classify("timeout") == "failed"


def test_only_two_failures_in_a_row_flag_a_fact(scratch):
    row = scratch.execute("""SELECT f.id AS fact, s.id AS source, s.url FROM facts f
                             JOIN fact_sources fs ON fs.fact_id = f.id JOIN sources s ON s.id = fs.source_id
                             WHERE f.state = 'published' AND NOT f.needs_review LIMIT 1""").fetchone()
    source = [{"id": row["source"], "url": row["url"]}]
    dead = lambda url: 404
    alive = lambda url: 200

    assert links.record(scratch, links.check(source, dead))["flagged"] == 0
    assert links.record(scratch, links.check(source, alive))["flagged"] == 0, "a pass resets the count"
    links.record(scratch, links.check(source, dead))
    counts = links.record(scratch, links.check(source, dead))
    assert counts["flagged"] >= 1
    fact = scratch.execute("SELECT needs_review, state FROM facts WHERE id = %s", (row["fact"],)).fetchone()
    assert fact["needs_review"] and fact["state"] == "published", "flagged, but still live until reviewed"
    note = scratch.execute("""SELECT note FROM fact_events WHERE fact_id = %s AND 'flagged' = ANY(changes)
                              ORDER BY id DESC LIMIT 1""", (row["fact"],)).fetchone()["note"]
    assert row["url"] in note


def test_blocked_sites_are_never_flagged(scratch):
    row = scratch.execute("""SELECT s.id, s.url FROM sources s JOIN fact_sources fs ON fs.source_id = s.id
                             JOIN facts f ON f.id = fs.fact_id WHERE f.state = 'published' LIMIT 1""").fetchone()
    for _ in range(3):
        links.record(scratch, links.check([{"id": row["id"], "url": row["url"]}], lambda url: 403))
    assert scratch.execute("SELECT failed_checks FROM sources WHERE id = %s", (row["id"],)).fetchone()["failed_checks"] == 0
