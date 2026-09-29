"""The research pipeline end to end, inside a transaction that is always rolled back: claim a cell,
submit a draft, review it, and export it. Nothing here touches production or leaves rows behind."""

from __future__ import annotations

import copy
from datetime import date

import pytest

from psst import coords, export, research, review, runs, tags

LONG = ("The bench faces the wrong way on purpose. When the square was laid out in 1911 the council placed "
        "it toward the church, but the family who paid for it asked for a view of the river, where their son "
        "had worked as a lighterman. The council agreed, the plaque was cut, and nobody has moved it since, "
        "although it now looks straight at a car park wall that went up in 1968.")


@pytest.fixture
def cell(scratch):
    row = scratch.execute("SELECT cell FROM research_cells WHERE state = 'open' ORDER BY cell LIMIT 1").fetchone()
    if not row:
        pytest.skip("no open research cells")
    return row["cell"]


def start(conn, kind, model):
    run_id = runs.start(conn, kind, model)
    return runs.require(conn, run_id, kind)


def read_sources(conn, run_id, fact_id):
    """What `psst fetch <url> --run <id>` records for each of the fact's sources."""
    conn.execute("""INSERT INTO source_reads (run_id, url_key, ok)
                    SELECT %s, s.url_key, true FROM fact_sources fs JOIN sources s ON s.id = fs.source_id
                    WHERE fs.fact_id = %s ON CONFLICT DO NOTHING""", (run_id, fact_id))


def draft_for(cell, tag_id):
    return {"cell": cell, "notes": "Test draft.", "places": [{
        "name": "Test Bench", "kind": "memorial", "size": "small", "wikidata": "Q999999999",
        "facts": [{"category": "quirk", "veracity": "fact", "headline": "The bench faces the car park on purpose",
                   "short": "A family asked for a river view in 1911, and nobody has turned it since.", "long": LONG,
                   "sources": [{"url": "https://example.org/test-bench", "title": "Bench", "publisher": "Example Society"}],
                   "tags": [tag_id]}]}],
        "skipped": [{"name": "A shop", "reason": "Nothing surprising in the sources."}]}


def position_in(cell):
    import h3
    lat, lon = h3.cell_to_latlng(cell)
    return coords.Position(lat, lon, "wikidata", "Q999999999")


@pytest.fixture
def tag_id(scratch):
    run = start(scratch, "tagging", "test")
    return tags.propose(scratch, "Test Tag For Pipeline", "theme", None, (), None, (), run["id"], {}).tag["id"]


def submit(scratch, cell, tag_id):
    researcher = start(scratch, "research", "test-researcher")
    research.claim(scratch, researcher["id"], cell=cell)
    draft = draft_for(cell, tag_id)
    checked = research.check(scratch, draft, online=False)
    assert checked.report.ok, checked.report.errors
    checked.positions = {0: position_in(cell)}
    counts = research.submit(scratch, draft, researcher, checked)
    return researcher, counts


def test_submit_writes_only_drafts(scratch, cell, tag_id):
    researcher, counts = submit(scratch, cell, tag_id)
    assert counts["places"] == 1 and counts["facts"] == 1
    place = scratch.execute("SELECT * FROM places WHERE id = %s", (counts["new_place_ids"][0],)).fetchone()
    assert place["coord_source"] == "wikidata" and place["coord_license"] == "CC0-1.0"
    assert place["h3_cell"] == cell and place["city_id"] is not None
    fact = scratch.execute("SELECT * FROM facts WHERE place_id = %s", (place["id"],)).fetchone()
    assert fact["state"] == "draft"
    assert (fact["researched_by"], fact["research_run"]) == ("test-researcher", researcher["id"])
    assert str(fact["researched_at"]) == date.today().isoformat()
    state = scratch.execute("SELECT state, notes FROM research_cells WHERE cell = %s", (cell,)).fetchone()
    assert state["state"] == "drafted" and "A shop" in state["notes"]


def test_submit_needs_the_claim(scratch, cell, tag_id):
    researcher = start(scratch, "research", "test-researcher")
    checked = research.check(scratch, draft_for(cell, tag_id), online=False)
    checked.positions = {0: position_in(cell)}
    with pytest.raises(RuntimeError, match="claim"):
        research.submit(scratch, draft_for(cell, tag_id), researcher, checked)


def test_check_catches_problems(scratch, cell, tag_id):
    bad = draft_for(cell, tag_id)
    bad["places"][0]["facts"][0]["short"] = "A family asked for a river view — nobody turned it."
    bad["places"][0]["facts"][0]["tags"] = ["tg_00000000"]
    report = research.check(scratch, bad, online=False).report
    assert any("dash" in e for e in report.errors)
    assert any("tg_00000000" in e for e in report.errors)
    existing = scratch.execute("SELECT wikidata_id FROM places WHERE wikidata_id IS NOT NULL LIMIT 1").fetchone()
    dup = draft_for(cell, tag_id)
    dup["places"][0]["wikidata"] = existing["wikidata_id"]
    assert any("already in Psst" in e for e in research.check(scratch, dup, online=False).report.errors)


def test_review_then_export(scratch, cell, tag_id, tmp_path):
    researcher, counts = submit(scratch, cell, tag_id)
    fact_id = scratch.execute("SELECT id FROM facts WHERE place_id = %s", (counts["new_place_ids"][0],)).fetchone()["id"]

    queue = review.queue(scratch, 500, cell=cell)
    assert fact_id in [r["id"] for r in queue]
    assert fact_id not in [r["id"] for r in review.queue(scratch, 500, cell=cell, reviewer_run=researcher["id"])]

    decision = {"fact": fact_id, "decision": "edit", "notes": "Checked the date and the plaque against the source.",
                "changes": {"headline": "The bench looks at a car park on purpose"}}
    assert any("own research" in e for e in review.check(scratch, [decision], researcher).errors)
    reviewer = start(scratch, "review", "test-reviewer")
    assert any("open every source" in e for e in review.check(scratch, [decision], reviewer).errors)
    read_sources(scratch, reviewer["id"], fact_id)
    assert review.check(scratch, [decision], reviewer).ok
    assert not review.check(scratch, [{**decision, "notes": "ok"}], reviewer).ok
    review.apply(scratch, [decision], reviewer)

    fact = scratch.execute("SELECT * FROM facts WHERE id = %s", (fact_id,)).fetchone()
    assert fact["state"] == "reviewed" and fact["reviewed_by"] == "test-reviewer"
    assert fact["headline"] == "The bench looks at a car park on purpose"
    events = [e["to_state"] for e in scratch.execute("SELECT to_state FROM fact_events WHERE fact_id = %s ORDER BY id",
                                                     (fact_id,))]
    assert events[-1] == "reviewed"
    assert scratch.execute("SELECT state FROM research_cells WHERE cell = %s", (cell,)).fetchone()["state"] == "reviewed"

    # Reviewed facts are exported only when a publish includes them.
    without = export.build(scratch, tmp_path / "a")
    with_fact = export.build(scratch, tmp_path / "b", include=[fact_id])
    assert with_fact.facts == without.facts + 1


def test_rejected_drafts_never_publish(scratch, cell, tag_id, tmp_path):
    _, counts = submit(scratch, cell, tag_id)
    fact_id = scratch.execute("SELECT id FROM facts WHERE place_id = %s", (counts["new_place_ids"][0],)).fetchone()["id"]
    reviewer = start(scratch, "review", "test-reviewer")
    decision = {"fact": fact_id, "decision": "reject", "reason": "The source doesn't support the date.",
                "notes": "Read the source twice; the 1911 date appears nowhere in it."}
    review.apply(scratch, [copy.deepcopy(decision)], reviewer)
    assert scratch.execute("SELECT state FROM facts WHERE id = %s", (fact_id,)).fetchone()["state"] == "retired"
    assert export.build(scratch, tmp_path / "c", include=[fact_id]).facts == export.build(scratch, tmp_path / "d").facts


def test_reports_flag_published_facts_for_review(scratch):
    fact = scratch.execute("SELECT id FROM facts WHERE state = 'published' LIMIT 1").fetchone()
    scratch.execute("SELECT psst.submit_report(%s, 'wrong', 'The date is off.', 'test')", (fact["id"],))
    queue = review.queue(scratch, 5)
    assert queue[0]["id"] == fact["id"] and queue[0]["reports"][0]["reason"] == "wrong"
    reviewer = start(scratch, "review", "test-reviewer")
    read_sources(scratch, reviewer["id"], fact["id"])
    review.apply(scratch, [{"fact": fact["id"], "decision": "approve",
                            "notes": "Checked the date against two sources; it is right."}], reviewer)
    row = scratch.execute("SELECT state, needs_review, last_verified_at FROM facts WHERE id = %s", (fact["id"],)).fetchone()
    assert row["state"] == "published" and not row["needs_review"] and row["last_verified_at"]
    assert scratch.execute("SELECT state FROM reports WHERE fact_id = %s ORDER BY id DESC LIMIT 1",
                           (fact["id"],)).fetchone()["state"] == "resolved"


def test_edits_and_flags_are_kept_in_history(scratch):
    fact = scratch.execute("SELECT id FROM facts WHERE state = 'published' LIMIT 1").fetchone()["id"]
    scratch.execute("UPDATE facts SET needs_review = true WHERE id = %s", (fact,))
    reviewer = start(scratch, "review", "test-reviewer")
    review.apply(scratch, [{"fact": fact, "decision": "edit", "notes": "Tightened the headline after checking it.",
                            "changes": {"headline": "A test headline that is short"}}], reviewer)
    events = scratch.execute("SELECT to_state, changes FROM fact_events WHERE fact_id = %s ORDER BY id DESC LIMIT 2",
                             (fact,)).fetchall()
    assert events[0]["changes"] == ["headline", "unflagged"] and events[0]["to_state"] == "published"
    assert events[1]["changes"] == ["flagged"]


def test_an_empty_cell_is_finished_at_once(scratch, cell):
    researcher = start(scratch, "research", "test-researcher")
    research.claim(scratch, researcher["id"], cell=cell)
    draft = {"cell": cell, "notes": "Only houses and a car park; nothing held up.", "places": []}
    checked = research.check(scratch, draft, online=False)
    assert checked.report.ok
    research.submit(scratch, draft, researcher, checked)
    row = scratch.execute("SELECT state, notes FROM research_cells WHERE cell = %s", (cell,)).fetchone()
    assert row["state"] == "done" and "car park" in row["notes"]


def test_claim_goes_where_people_looked(scratch):
    import h3
    open_cells = [r["cell"] for r in scratch.execute("SELECT cell FROM research_cells WHERE state = 'open'")]
    if len(open_cells) < 2:
        pytest.skip("not enough open cells")
    target = open_cells[len(open_cells) // 2]
    area = h3.cell_to_parent(target, 5)
    scratch.execute("INSERT INTO demand (cell, day, count) VALUES (%s, current_date, 1000)", (area,))
    researcher = start(scratch, "research", "test-researcher")
    claimed = research.claim(scratch, researcher["id"])
    assert h3.cell_to_parent(claimed["cell"], 5) == area
    top = research.wanted(scratch, 1)[0]
    assert top["cell"] == area and top["views"] == 1000 and top["plannedCells"] > 0


def test_migrated_facts_can_be_verified(scratch):
    fact = scratch.execute("""SELECT f.id, ci.name AS city FROM facts f JOIN places p ON p.id = f.place_id
                              JOIN admin_areas ci ON ci.id = p.city_id
                              WHERE f.state = 'published' AND f.last_verified_at IS NULL LIMIT 1""").fetchone()
    reviewer = start(scratch, "review", "any-model")
    read_sources(scratch, reviewer["id"], fact["id"])
    assert fact["id"] in [r["id"] for r in review.queue(scratch, 5000, reviewer_run=reviewer["id"], city=fact["city"],
                                                         verify=True)]
    decision = {"fact": fact["id"], "decision": "approve", "notes": "Opened both sources; every detail matches."}
    assert review.check(scratch, [decision], reviewer).ok
    review.apply(scratch, [decision], reviewer)
    row = scratch.execute("SELECT state, last_verified_at, reviewed_by FROM facts WHERE id = %s", (fact["id"],)).fetchone()
    assert row["state"] == "published" and row["last_verified_at"] and row["reviewed_by"] == "any-model"
    assert fact["id"] not in [r["id"] for r in review.queue(scratch, 5000, reviewer_run=reviewer["id"], verify=True)]


def test_local_names_must_be_in_the_countrys_language(scratch):
    petronas = coords.Position(3.1579, 101.7116, "wikidata", "Q83063")
    assert research.local_name_problem(scratch, {"lang": "zh-Hans", "name": "双峰塔"}, petronas)
    assert research.local_name_problem(scratch, {"lang": "ms", "name": "Menara Berkembar Petronas"}, petronas) is None
    peace_hotel = coords.Position(31.2405, 121.4903, "wikidata", "Q377875")
    assert research.local_name_problem(scratch, {"lang": "zh-Hans", "name": "和平饭店"}, peace_hotel) is None


def test_samples_are_random(scratch):
    reviewer = start(scratch, "review", "some-other-model")
    first = [r["id"] for r in review.queue(scratch, 30, reviewer_run=reviewer["id"], verify=True, sample=True)]
    second = [r["id"] for r in review.queue(scratch, 30, reviewer_run=reviewer["id"], verify=True, sample=True)]
    assert len(first) == 30 and first != second


def test_every_lead_must_be_accounted_for(scratch, cell, tag_id):
    researcher = start(scratch, "research", "test-researcher")
    research.claim(scratch, researcher["id"], cell=cell)
    leads = [{"key": "Q999999999", "name": "Test Bench", "wikidata": "Q999999999", "known": False},
             {"key": "name:Corner Shop", "name": "Corner Shop", "osm": "node/999999991", "known": False},
             {"key": "Q999999997", "name": "Old Ward", "wikidata": "Q999999997", "known": False},
             {"key": "Q999999996", "name": "Bus Garage", "wikidata": "Q999999996", "known": False}]
    research.store_leads(scratch, cell, leads, researcher["id"])

    draft = draft_for(cell, tag_id)  # adds Test Bench, skips "A shop"
    report = research.check(scratch, draft, online=False).report
    assert any("aren't accounted for" in e and "Corner Shop" in e and "Old Ward" in e for e in report.errors)

    draft["skipped"] = [{"name": "Corner Shop", "reason": "Nothing surprising in the sources."},
                        {"names": ["Old Ward", "Bus Garage"], "reason": "Not physical places, or nothing to say."}]
    checked = research.check(scratch, draft, online=False)
    assert checked.report.ok, checked.report.errors
    checked.positions = {0: position_in(cell)}
    research.submit(scratch, draft, researcher, checked)
    statuses = {r["key"]: r["status"] for r in scratch.execute("SELECT key, status FROM research_leads WHERE cell = %s", (cell,))}
    assert statuses == {"Q999999999": "added", "name:Corner Shop": "skipped", "Q999999997": "skipped",
                        "Q999999996": "skipped"}
    assert scratch.execute("SELECT passes FROM research_cells WHERE cell = %s", (cell,)).fetchone()["passes"] == 1


def test_a_second_pass_keeps_earlier_decisions(scratch, cell):
    researcher = start(scratch, "research", "test-researcher")
    lead = {"key": "Q999999995", "name": "Old Pump", "wikidata": "Q999999995", "known": False}
    research.store_leads(scratch, cell, [lead], researcher["id"])
    scratch.execute("UPDATE research_leads SET status = 'skipped', reason = 'Nothing holds up.' WHERE key = 'Q999999995'")
    research.store_leads(scratch, cell, [lead], researcher["id"])
    assert research.open_leads(scratch, cell) == []


def test_leads_left_for_later_keep_the_cell_open(scratch, cell, tag_id):
    researcher = start(scratch, "research", "test-researcher")
    research.claim(scratch, researcher["id"], cell=cell)
    research.store_leads(scratch, cell, [{"key": "Q999999999", "name": "Test Bench", "wikidata": "Q999999999", "known": False},
                                         {"key": "Q999999994", "name": "Big Tower", "wikidata": "Q999999994", "known": False}],
                         researcher["id"])
    draft = draft_for(cell, tag_id)
    draft["skipped"] = [{"name": "Big Tower", "reason": "Not reached in this pass.", "later": True}]
    checked = research.check(scratch, draft, online=False)
    assert checked.report.ok, checked.report.errors
    checked.positions = {0: position_in(cell)}
    counts = research.submit(scratch, draft, researcher, checked)
    assert counts["leads_left"] == 1
    assert scratch.execute("SELECT state FROM research_cells WHERE cell = %s", (cell,)).fetchone()["state"] == "open"
    assert [l["name"] for l in research.open_leads(scratch, cell)] == ["Big Tower"]


def test_claim_near_a_spot(scratch):
    import h3
    target = scratch.execute("SELECT cell FROM research_cells WHERE state = 'open' ORDER BY cell DESC LIMIT 1").fetchone()["cell"]
    researcher = start(scratch, "research", "test-researcher")
    assert research.claim(scratch, researcher["id"], near=h3.cell_to_latlng(target))["cell"] == target
