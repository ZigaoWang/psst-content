from psst import coords, rules


def resolve(monkeypatch, wikidata, tagged):
    monkeypatch.setattr(coords, "wikidata", lambda qids: wikidata)
    monkeypatch.setattr(coords, "osm", lambda refs: {})
    monkeypatch.setattr(coords, "osm_by_wikidata", lambda qids: tagged)
    report = rules.Report()
    positions = coords.resolve([{"name": "Test", "wikidata": "Q1"}], report)
    return positions, report


def test_a_precise_wikidata_coordinate_is_used(monkeypatch):
    positions, report = resolve(monkeypatch, {"Q1": [(51.5, -0.12, 0.00001)]}, {})
    assert positions[0].source == "wikidata" and report.ok


def test_an_unusable_coordinate_falls_back_to_the_tagged_osm_element(monkeypatch):
    positions, report = resolve(monkeypatch, {"Q1": [(51.5, -0.12, 0.00001), (51.6, -0.1, 0.00001)]},
                                {"Q1": [("way/7", 51.50067, -0.12457)]})
    assert positions[0] == coords.Position(51.50067, -0.12457, "osm", "way/7") and report.ok and report.warnings


def test_several_tagged_elements_need_a_choice(monkeypatch):
    positions, report = resolve(monkeypatch, {"Q1": [(51.5, -0.1, 0.1)]},
                                {"Q1": [("way/7", 51.5, -0.1), ("node/8", 51.5, -0.1)]})
    assert not positions and any("way/7" in e and "node/8" in e for e in report.errors)
