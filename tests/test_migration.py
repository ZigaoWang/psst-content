"""Proves the move from area files to the database lost and changed nothing: every place, fact, source,
coordinate, and old place id is in the database and in what production serves, with identical text."""

from __future__ import annotations

import pytest

from psst import ids, legacy, names, rules


@pytest.fixture(scope="module")
def spots(legacy_repo):
    return legacy.read_areas(legacy_repo)


@pytest.fixture(scope="module")
def stored(database, spots):
    legacy_ids = [s.legacy_id for s in spots]
    with database.transaction(force_rollback=True):
        database.execute("SET LOCAL search_path = psst, public")
        places = {r["legacy_id"]: r for r in database.execute("""
            SELECT l.legacy_id, p.id, p.state, p.kind, p.coord_source, p.coord_source_ref,
                   ST_Y(p.geom) AS lat, ST_X(p.geom) AS lon,
                   (SELECT name FROM place_names WHERE place_id = p.id AND role = 'display') AS name,
                   (SELECT name FROM place_names WHERE place_id = p.id AND role = 'local') AS local_name
            FROM legacy_place_ids l JOIN places p ON p.id = l.place_id WHERE l.legacy_id = ANY(%s)""",
                                                              (legacy_ids,))}
        facts = {r["legacy_id"]: r for r in database.execute("""
            SELECT f.legacy_id, f.id, f.place_id, f.state, f.category, f.veracity, f.headline, f.short, f.long,
                   (SELECT coalesce(json_agg(json_build_object('url', s.url, 'key', s.url_key, 'title', s.title,
                                                               'publisher', s.publisher) ORDER BY fs.position), '[]')
                    FROM fact_sources fs JOIN sources s ON s.id = fs.source_id WHERE fs.fact_id = f.id) AS sources
            FROM facts f WHERE f.legacy_id IS NOT NULL""")}
    return places, facts


def test_every_legacy_place_is_stored(spots, stored):
    places, _ = stored
    missing = [s.legacy_id for s in spots if s.legacy_id not in places]
    assert not missing, f"{len(missing)} places missing, e.g. {missing[:5]}"
    assert all(places[s.legacy_id]["state"] == "active" for s in spots)


def test_places_keep_name_kind_and_exact_coordinates(spots, stored, database):
    places, _ = stored
    place_country = {s.legacy_id: s.area["countryCode"] for s in spots}
    alt_languages: dict[str, set] = {}
    with database.transaction(force_rollback=True):
        for r in database.execute("SELECT place_id, lang FROM psst.place_names WHERE role = 'alt'"):
            alt_languages.setdefault(r["place_id"], set()).add(r["lang"])
    for spot in spots:
        row, data = places[spot.legacy_id], spot.spot
        assert row["name"] == data["name"], spot.legacy_id
        if data.get("localName") and row["local_name"] != data["localName"]:
            # The one deliberate change: a local name not in the country's language (Chinese names in
            # Kuala Lumpur) was replaced by the real one, and the place keeps a name in that language.
            lang = names.language_of_local(data["localName"], place_country[spot.legacy_id])
            assert lang != names.COUNTRY_LANGUAGE.get(place_country[spot.legacy_id]), spot.legacy_id
            assert lang in alt_languages.get(places[spot.legacy_id]["id"], set()), spot.legacy_id
        assert row["kind"] == data["kind"], spot.legacy_id
        assert (row["coord_source"], row["coord_source_ref"]) == (data["coordinateSource"]["type"],
                                                                  data["coordinateSource"]["id"]), spot.legacy_id
        assert abs(row["lat"] - data["coordinate"]["latitude"]) < 1e-9, spot.legacy_id
        assert abs(row["lon"] - data["coordinate"]["longitude"]) < 1e-9, spot.legacy_id


def test_place_ids_are_derived_from_legacy_ids(spots, stored):
    places, _ = stored
    for spot in spots:
        assert places[spot.legacy_id]["id"] == ids.derived("pl", spot.legacy_id)


def test_every_fact_is_stored_with_identical_text(spots, stored):
    places, facts = stored
    count = 0
    for spot in spots:
        for fact in spot.facts:
            row = facts.get(fact.legacy_id)
            assert row, f"missing {fact.legacy_id}"
            data = fact.data
            assert row["place_id"] == places[spot.legacy_id]["id"]
            assert row["state"] in ("published", "reviewed"), fact.legacy_id
            for key in ("category", "headline", "short", "long"):
                assert row[key] == data[key], f"{fact.legacy_id}: {key} changed"
            assert row["veracity"] == data["status"], fact.legacy_id
            count += 1
    assert count == len(facts), "the database has legacy facts the area files don't"


def test_every_source_is_linked_in_order(spots, stored):
    _, facts = stored
    citations: dict[str, set] = {}
    for spot in spots:
        for fact in spot.facts:
            for source in fact.data["sources"]:
                citations.setdefault(rules.normalize_url(source["url"]), set()).add((source["title"], source["publisher"]))
    for spot in spots:
        for fact in spot.facts:
            row = facts[fact.legacy_id]
            expected = list(dict.fromkeys(rules.normalize_url(s["url"]) for s in fact.data["sources"]))
            assert [s["key"] for s in row["sources"]] == expected, fact.legacy_id
            for source in row["sources"]:
                # A page cited twice is stored once, under one of the titles it was cited with.
                assert (source["title"], source["publisher"]) in citations[source["key"]], fact.legacy_id


def test_production_serves_every_legacy_place_and_fact(spots, stored, production):
    places, facts = stored
    _, common, cities = production
    served_places = {p["id"]: p for pack in cities.values() for p in pack["places"]}
    served_facts = {f["id"]: f for p in served_places.values() for f in p["facts"]}
    for spot in spots:
        assert common["legacyIds"].get(spot.legacy_id) == places[spot.legacy_id]["id"], spot.legacy_id
        assert places[spot.legacy_id]["id"] in served_places, spot.legacy_id
        for fact in spot.facts:
            if facts[fact.legacy_id]["state"] != "published":
                continue
            served = served_facts.get(facts[fact.legacy_id]["id"])
            assert served, f"{fact.legacy_id} is published but not served"
            for key in ("headline", "short", "long", "category"):
                assert served[key] == fact.data[key], f"{fact.legacy_id}: {key} differs in production"
            assert [rules.normalize_url(s["url"]) for s in served["sources"]] == \
                list(dict.fromkeys(rules.normalize_url(s["url"]) for s in fact.data["sources"]))
