"""Places get the city, district, and neighborhood people would name, from the boundary data alone."""

import pytest

from psst import cells, hierarchy, ids, runs


def areas_at(scratch, lat, lon):
    run = runs.start(scratch, "manual", None)
    place = ids.new("pl")
    scratch.execute("""INSERT INTO places (id, kind, geom, coord_source, coord_source_ref, coord_license, wikidata_id,
                                           h3_cell, created_by_run)
                       VALUES (%s, 'building', ST_SetSRID(ST_MakePoint(%s, %s), 4326), 'wikidata', 'Q999999998',
                               'CC0-1.0', 'Q999999998', %s, %s)""", (place, lon, lat, cells.cell_for(lat, lon), run))
    hierarchy.assign(scratch, [place])
    return scratch.execute("""
        SELECT p.country_code, ci.name AS city, di.name AS district, nb.name AS neighborhood FROM places p
        LEFT JOIN admin_areas ci ON ci.id = p.city_id LEFT JOIN admin_areas di ON di.id = p.district_id
        LEFT JOIN admin_areas nb ON nb.id = p.neighborhood_id WHERE p.id = %s""", (place,)).fetchone()


@pytest.mark.parametrize("lat, lon, country, city, district, neighborhood", [
    (22.2819, 114.1583, "HK", "Hong Kong", "Central and Western", "Central"),
    (51.5055, -0.0754, "GB", "London", None, None),
    (31.2411, 121.4851, "CN", "Shanghai", "Huangpu District", "Waitan"),
    (3.1579, 101.7116, "MY", "Kuala Lumpur", None, None),
])
def test_places_get_the_areas_people_use(scratch, lat, lon, country, city, district, neighborhood):
    got = areas_at(scratch, lat, lon)
    assert got["country_code"] == country and got["city"] == city
    if district:
        assert got["district"] == district
    if neighborhood:
        assert got["neighborhood"] == neighborhood
    assert got["neighborhood"], "every place in a set-up city gets a neighborhood"
