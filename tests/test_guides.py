"""Guide information: the writing rules, key facts from Wikidata and their sanity checks, and the lighter
review, end to end inside a transaction that is always rolled back."""

from __future__ import annotations

from datetime import date

import pytest

from psst import export, guides, rules, runs

ABOUT = ("A bronze statue of a naval officer on a granite column, put up by public subscription in the 1840s. "
         "It stands at the center of the square, which was laid out at the same time to mark a victory at sea.")


def guide_report(**fields) -> rules.Report:
    report = rules.Report()
    guide = {"identifier": "Bronze statue, 1843, by Edward Baily", "about": ABOUT,
             "sources": [{"url": "https://example.org/statue", "title": "Statue", "publisher": "Example Society"}]}
    rules.check_guide(report, "guide", {**guide, **fields})
    return report


def test_plain_guide_text_passes():
    report = guide_report()
    assert report.ok, report.errors


@pytest.mark.parametrize("about, problem", [
    (ABOUT.replace("A bronze statue", "A famous bronze statue"), "judgment"),
    (ABOUT.replace("put up by", "the tallest of its kind, put up by"), "superlative"),
    (ABOUT.replace("A bronze statue", "One of the bronze statues"), "filler"),
    ("A bronze statue of a naval officer on a granite column, put up by public subscription in the 1840s and still there.", "two or three"),
    (ABOUT.replace("1840s.", "1840s!"), "exclamation"),
    (ABOUT.replace("center", "centre"), "British"),
])
def test_guide_text_stays_neutral(about, problem):
    assert any(problem in e for e in guide_report(about=about).errors)


def test_names_with_hype_words_are_fine():
    about = ABOUT.replace("the square", "Most Holy Redeemer Square, beside the Grand Union Canal,")
    assert guide_report(about=about).ok


def test_identifier_is_a_short_label():
    assert any("period" in e for e in guide_report(identifier="Bronze statue, 1843.").errors)
    assert any("characters" in e for e in guide_report(identifier="A" * 71).errors)
    assert guide_report(identifier="Wikipedia ok", sources=[{"url": "https://en.wikipedia.org/wiki/X",
                                                             "title": "X", "publisher": "Wikipedia"}]).ok


def test_dates_keep_their_precision():
    assert guides.format_time({"time": "+1843-11-03T00:00:00Z", "precision": 11}) == "November 3, 1843"
    assert guides.format_time({"time": "+1843-11-00T00:00:00Z", "precision": 10}) == "November 1843"
    assert guides.format_time({"time": "+1843-00-00T00:00:00Z", "precision": 9}, circa=True) == "about 1843"
    assert guides.format_time({"time": "+1840-00-00T00:00:00Z", "precision": 8}) == "1840s"
    assert guides.format_time({"time": "+1801-00-00T00:00:00Z", "precision": 7}) == "19th century"
    assert guides.format_time({"time": "-0450-00-00T00:00:00Z", "precision": 7}) == "5th century BC"


def claim(prop, value, rank="normal", qualifiers=None):
    kind = "wikibase-entityid" if "id" in value else "other"
    return {"rank": rank, "mainsnak": {"snaktype": "value", "property": prop, "datavalue": {"type": kind, "value": value}},
            **({"qualifiers": qualifiers} if qualifiers else {})}


def year(y):
    return {"time": f"+{y:04d}-00-00T00:00:00Z", "precision": 9}


def item(entity_claims):
    """An item with one claim per value; a list gives several values for one property."""
    return {"claims": {p: [claim(p, v) for v in (values if isinstance(values, list) else [values])]
                       for p, values in entity_claims.items()}}


def person(label, born, died=None):
    claims = {"P31": [claim("P31", {"id": "Q5"})], "P569": [claim("P569", year(born))]}
    if died:
        claims["P570"] = [claim("P570", year(died))]
    return {"labels": {"en": {"value": label}}, "claims": claims}


def test_key_facts_come_with_their_property():
    entity = item({"P170": {"id": "Q1"}, "P571": year(1843), "P186": [{"id": "Q2"}, {"id": "Q3"}],
                   "P2048": {"amount": "+5.5", "unit": "http://www.wikidata.org/entity/Q11573"},
                   "P1435": {"id": "Q4"}})
    values = {"Q1": person("Edward Hodges Baily", 1788, 1867), "Q2": {"labels": {"en": {"value": "bronze"}}},
              "Q3": {"labels": {"en": {"value": "granite"}}}, "Q4": {"labels": {"en": {"value": "Grade I listed building"}}}}
    facts = [k.as_dict() for k in guides.key_facts(entity, values, "memorial", "small", date(2026, 10, 1))]
    assert facts == [
        {"property": "P170", "label": "Creator", "value": "Edward Hodges Baily", "valueId": "Q1"},
        {"property": "P571", "label": "Made", "value": "1843"},
        {"property": "P186", "label": "Material", "value": "Bronze", "valueId": "Q2"},
        {"property": "P186", "label": "Material", "value": "Granite", "valueId": "Q3"},
        {"property": "P2048", "label": "Height", "value": "5.5 m"},
        {"property": "P1435", "label": "Heritage status", "value": "Grade I listed building", "valueId": "Q4"},
    ]


def test_ended_deprecated_and_unknown_values_are_left_out():
    entity = {"claims": {
        "P88": [claim("P88", {"id": "Q1"}, qualifiers={"P582": [{"datavalue": {"value": year(1990)}}]}),
                claim("P88", {"id": "Q2"})],
        "P571": [claim("P571", year(1700), rank="deprecated"), claim("P571", year(1843))],
        "P2048": [claim("P2048", {"amount": "+12", "unit": "http://www.wikidata.org/entity/Q99999"})],
        "P84": [claim("P84", {"id": "Q3"})]}}
    values = {"Q1": {"labels": {"en": {"value": "Old Company"}}}, "Q2": {"labels": {"en": {"value": "New Company"}}},
              "Q3": {"labels": {"zh": {"value": "某人"}}}}
    facts = {k.property: k.value for k in guides.key_facts(entity, values, "building", "medium", date(2026, 10, 1))}
    assert facts == {"P88": "New Company", "P571": "1843"}


def test_implausible_values_are_flagged():
    entity = item({"P84": {"id": "Q1"}, "P571": year(1843), "P1619": year(1830), "P149": {"id": "Q2"},
                   "P2048": {"amount": "+1.6e57", "unit": "http://www.wikidata.org/entity/Q11573"},
                   "P1101": {"amount": "+5440000000", "unit": "1"}})
    values = {"Q1": person("Someone Later", 1870), "Q2": {"labels": {"en": {"value": "Nazi"}},
                                                          "claims": {"P31": [claim("P31", {"id": "Q7278"})]}}}
    flags = {k.property: k.flag for k in guides.key_facts(entity, values, "building", "medium", date(2026, 10, 1))}
    assert "born in 1870" in flags["P84"]
    assert "before it was built" in flags["P1619"]
    assert "style" in flags["P149"]
    assert "implausible" in flags["P2048"] and "implausible" in flags["P1101"]
    assert flags["P571"] is None
    values["Q2"] = {"labels": {"en": {"value": "Italian Baroque"}}, "claims": {"P279": [claim("P279", {"id": "Q32880"})]}}
    assert guides.key_facts(item({"P149": {"id": "Q2"}}), values, "worship", "medium", date(2026, 10, 1))[0].flag is None


def test_conflicting_dates_are_flagged():
    entity = item({"P571": [year(1843), year(1845)]})
    facts = guides.key_facts(entity, {}, "memorial", "small", date(2026, 10, 1))
    assert len(facts) == 1 and "several values: 1843, 1845" in facts[0].flag
    future = guides.key_facts(item({"P1619": year(2350)}), {}, "building", "medium", date(2026, 10, 1))
    assert "future" in future[0].flag


# End to end --------------------------------------------------------------------------------------------------

def start(conn, kind, model):
    return runs.require(conn, runs.start(conn, kind, model), kind)


def read(conn, run_id, url):
    conn.execute("INSERT INTO source_reads (run_id, url_key, ok) VALUES (%s, %s, true) ON CONFLICT DO NOTHING",
                 (run_id, rules.read_key(url)))


@pytest.fixture
def place(scratch):
    row = scratch.execute("""
        SELECT p.id FROM places p WHERE p.state = 'active'
          AND EXISTS (SELECT 1 FROM facts f WHERE f.place_id = p.id AND f.state = 'published')
          AND NOT EXISTS (SELECT 1 FROM guides g WHERE g.place_id = p.id AND g.state <> 'retired')
          AND NOT EXISTS (SELECT 1 FROM guide_claims c WHERE c.place_id = p.id AND c.claimed_until > now())
        ORDER BY p.id LIMIT 1""").fetchone()
    if not row:
        pytest.skip("every place has a guide")
    return row["id"]


SOURCE = {"url": "https://example.org/statue-guide", "title": "Statue", "publisher": "Example Society"}


def entry(place_id, **fields):
    return {"place": place_id, "identifier": "Bronze statue, 1843", "about": ABOUT, "sources": [SOURCE],
            "keyFacts": [], **fields}


def write_guide(scratch, place_id, flagged=False, replacing=False):
    writer = start(scratch, "research", "test-writer")
    # A place with a live guide isn't offered for a new one; a replacement is written for it by id.
    assert [p["id"] for p in guides.claim(scratch, writer["id"], 5, places=[place_id])] == ([] if replacing else [place_id])
    report, _ = guides.check(scratch, [entry(place_id)], writer["id"], online=False)
    assert report.ok, report.errors
    with pytest.raises(RuntimeError, match="Open every source"):
        guides.submit(scratch, [entry(place_id)], writer, {})
    read(scratch, writer["id"], SOURCE["url"])
    fresh = {place_id: [guides.KeyFact("P571", "Built", "1843"),
                        guides.KeyFact("P2048", "Height", "900 m", flag="900 m is implausible for one place")]
             if flagged else [guides.KeyFact("P571", "Built", "1843")]}
    [guide_id] = guides.submit(scratch, [entry(place_id)], writer, fresh)
    return writer, guide_id


def test_a_guide_goes_from_draft_to_export(scratch, place, tmp_path):
    writer, guide_id = write_guide(scratch, place, flagged=True)
    assert not scratch.execute("SELECT 1 FROM guide_claims WHERE run_id = %s", (writer["id"],)).fetchone()
    # A second guide can't wait for review beside the first.
    assert any("waiting" in e for e in guides.check(scratch, [entry(place)], None, online=False)[0].errors)

    assert guide_id in [g["id"] for g in guides.queue(scratch, 100000)]
    assert guide_id not in [g["id"] for g in guides.queue(scratch, 100000, writer["id"])]
    decision = {"guide": guide_id, "decision": "approve",
                "notes": "Example Society page gives the 1843 date and the column."}
    assert any("own research" in e for e in guides.check_decisions(scratch, [decision], writer).errors)
    reviewer = start(scratch, "review", "test-reviewer")
    errors = guides.check_decisions(scratch, [decision], reviewer).errors
    assert any("open at least one" in e for e in errors) and any("P2048 is flagged" in e for e in errors)
    read(scratch, reviewer["id"], SOURCE["url"])
    decision["dropKeyFacts"] = ["P2048"]
    assert guides.check_decisions(scratch, [decision], reviewer).ok
    assert not guides.check_decisions(scratch, [{**decision, "notes": "ok"}], reviewer).ok
    guides.apply_decisions(scratch, [decision], reviewer)
    guide = scratch.execute("SELECT * FROM guides WHERE id = %s", (guide_id,)).fetchone()
    assert guide["state"] == "reviewed" and guide["reviewed_by"] == "test-reviewer"
    assert [k["property"] for k in scratch.execute("SELECT property FROM guide_key_facts WHERE guide_id = %s",
                                                   (guide_id,))] == ["P571"]

    def exported(result):
        for city in result.manifest["cities"]:
            for p in export.load_pack(result.directory, city)["places"]:
                if p["id"] == place:
                    return p.get("guide")
    without = export.build(scratch, tmp_path / "a")
    assert exported(without) is None
    shipped = exported(export.build(scratch, tmp_path / "b", include_guides=[guide_id]))
    assert shipped["identifier"] == "Bronze statue, 1843" and shipped["about"] == ABOUT
    assert shipped["keyFacts"] == [{"property": "P571", "label": "Built", "value": "1843",
                                    "values": [{"value": "1843", "id": None}]}]


def test_a_flagged_value_can_be_confirmed(scratch, place):
    _, guide_id = write_guide(scratch, place, flagged=True)
    reviewer = start(scratch, "review", "test-reviewer")
    read(scratch, reviewer["id"], SOURCE["url"])
    decision = {"guide": guide_id, "decision": "edit", "confirmKeyFacts": ["P2048"],
                "notes": "Example Society page gives the height; the column really is that tall in this test.",
                "changes": {"identifier": "Granite column, 1843"}}
    assert guides.check_decisions(scratch, [decision], reviewer).ok
    guides.apply_decisions(scratch, [decision], reviewer)
    row = scratch.execute("SELECT identifier FROM guides WHERE id = %s", (guide_id,)).fetchone()
    assert row["identifier"] == "Granite column, 1843"
    assert scratch.execute("SELECT flag_confirmed FROM guide_key_facts WHERE guide_id = %s AND property = 'P2048'",
                           (guide_id,)).fetchone()["flag_confirmed"]
    events = scratch.execute("SELECT changes FROM guide_events WHERE guide_id = %s ORDER BY id", (guide_id,)).fetchall()
    assert "identifier" in events[-1]["changes"]


def test_a_rejected_guide_is_retired(scratch, place):
    _, guide_id = write_guide(scratch, place)
    reviewer = start(scratch, "review", "test-reviewer")
    decision = {"guide": guide_id, "decision": "reject", "reason": "Describes the building next door.",
                "notes": "Example Society page is about the neighboring church."}
    assert guides.check_decisions(scratch, [decision], reviewer).ok
    guides.apply_decisions(scratch, [decision], reviewer)
    assert scratch.execute("SELECT state FROM guides WHERE id = %s", (guide_id,)).fetchone()["state"] == "retired"


def test_publishing_a_new_guide_retires_the_old_one(scratch, place):
    _, first = write_guide(scratch, place)
    scratch.execute("""UPDATE guides SET state = 'published', reviewed_at = now(), published_at = now()
                       WHERE id = %s""", (first,))
    _, second = write_guide(scratch, place, replacing=True)
    scratch.execute("""UPDATE guides SET state = 'published', reviewed_at = now(), published_at = now()
                       WHERE id = %s""", (second,))
    assert guides.retire_replaced(scratch, [second]) == 1
    old = scratch.execute("SELECT state, retire_reason FROM guides WHERE id = %s", (first,)).fetchone()
    assert old["state"] == "retired" and second in old["retire_reason"]


def test_another_runs_claim_is_respected(scratch, place):
    first = start(scratch, "research", "test-writer")
    guides.claim(scratch, first["id"], 1, places=[place])
    second = start(scratch, "research", "test-writer")
    assert guides.claim(scratch, second["id"], 5, places=[place]) == []
    assert any("another run" in e for e in guides.check(scratch, [entry(place)], second["id"], online=False)[0].errors)


def test_identifiers_keep_one_shape():
    assert any("heritage" in e for e in guide_report(identifier="Grade II listed Victorian pub").errors)
    assert any("street" in e for e in guide_report(identifier="Victorian pub on Chalcot Road, 1868").errors)
    assert any("range" in w for w in guide_report(identifier="Doric column, 1671 to 1677, by Christopher Wren").warnings)
    assert guide_report(identifier="Gothic Revival parish church, 1844, by Scott and Moffatt").ok


def test_suggested_identifier_and_echoes():
    entity = item({"P31": {"id": "Q9"}, "P149": {"id": "Q2"}, "P571": year(1701), "P84": {"id": "Q1"},
                   "P138": {"id": "Q3"}})
    values = {"Q9": {"labels": {"en": {"value": "synagogue"}}},
              "Q2": {"labels": {"en": {"value": "Neoclassical architecture"}}, "claims": {"P31": [claim("P31", {"id": "Q32880"})]}},
              "Q1": person("Joseph Avis", 1650), "Q3": {"labels": {"en": {"value": "Bevis Marks"}}}}
    found = guides.drop_echoes(guides.key_facts(entity, values, "worship", "medium", date(2026, 10, 1)),
                               "Bevis Marks Synagogue")
    assert "P138" not in [k.property for k in found]
    assert guides.suggest_identifier(entity, values, found) == "Neoclassical synagogue, 1701, by Joseph Avis"


def test_the_info_box_shows_the_most_useful_facts_in_order():
    lines = [{"property": p, "label": p, "value": "x"} for p in
             ["P138", "P170", "P571", "P140", "P2044", "P149", "P1435", "P2048", "P186", "P1101"]]
    assert [k["property"] for k in guides.shown(lines)] == ["P170", "P571", "P149", "P186", "P2048", "P1435"]


def test_a_city_gets_its_guides_all_at_once(scratch, place):
    # Almost every live place has no guide yet, so a single reviewed guide waits for the rest of its city.
    _, guide_id = write_guide(scratch, place)
    scratch.execute("UPDATE guides SET state = 'reviewed', reviewed_at = now() WHERE id = %s", (guide_id,))
    kept, held = guides.publishable(scratch, [guide_id])
    assert kept == [] and held


def test_a_review_run_can_be_spot_checked(scratch, place):
    _, guide_id = write_guide(scratch, place)
    reviewer = start(scratch, "review", "test-reviewer")
    read(scratch, reviewer["id"], SOURCE["url"])
    guides.apply_decisions(scratch, [{"guide": guide_id, "decision": "approve",
                                      "notes": "Example Society page confirms the 1840s column."}], reviewer)
    assert [g["id"] for g in guides.sample(scratch, reviewer["id"], 10)] == [guide_id]
    assert [r["live"] + r["approved"] + r["in_review"] + r["missing"] >= 0 for r in guides.progress(scratch)]


def test_a_careless_guide_review_can_be_reopened(scratch, place):
    _, guide_id = write_guide(scratch, place)
    reviewer = start(scratch, "review", "test-reviewer")
    read(scratch, reviewer["id"], SOURCE["url"])
    guides.apply_decisions(scratch, [{"guide": guide_id, "decision": "approve",
                                      "notes": "Example Society page confirms the 1840s column."}], reviewer)
    assert guides.reopen(scratch, reviewer["id"]) == {"back_to_review": 1, "live_flagged": 0}
    row = scratch.execute("SELECT state, review_run FROM guides WHERE id = %s", (guide_id,)).fetchone()
    assert row["state"] == "draft" and row["review_run"] is None
    assert guide_id in [g["id"] for g in guides.queue(scratch, 100000)]
