"""The admin page's data: everything the page reads, from the real database, without changing anything."""

from psst import admin


def test_admin_data_covers_everything(scratch, tmp_path):
    data = admin.build(scratch)
    for key in ("totals", "cities", "places", "facts", "images", "reports", "runs", "demand", "publications", "tags"):
        assert key in data
    live = [p for p in data["places"] if p["published"]]
    assert data["totals"]["live_places"] == len(live)
    assert sum(c["live_places"] for c in data["cities"]) == len(live)
    place_ids = {p["id"] for p in data["places"]}
    assert all(f["place_id"] in place_ids for f in data["facts"])
    written = admin.write(scratch, tmp_path / "admin")
    assert {f.name for f in written.iterdir()} == {"index.html", "data.json.gz", "map.html"}
