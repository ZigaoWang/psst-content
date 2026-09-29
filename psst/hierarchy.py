"""Location hierarchy: country, region, city, district, and neighborhood for every place, from real
boundary data only. See docs/DESIGN.md, "Location hierarchy".

Loading reads a Who's On First SQLite bundle (run it on the VPS, where the bundles are downloaded).
Assignment is pure SQL over the loaded boundaries, so it is the same wherever it runs.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

# Who's On First placetype -> Psst level. When several placetypes share a level, the smallest area wins.
LEVELS = {
    "country": "country",
    "macroregion": "region",
    "region": "region",
    "locality": "city",
    "borough": "district",
    "county": "district",
    "localadmin": "district",
    "macrohood": "neighborhood",
    "neighbourhood": "neighborhood",
}

# ISO 639-3 (as Who's On First writes it) -> BCP 47.
LANGUAGES = {
    "eng": "en", "zho": "zh", "jpn": "ja", "kor": "ko", "fra": "fr", "deu": "de", "spa": "es", "ita": "it",
    "por": "pt", "rus": "ru", "ara": "ar", "hin": "hi", "msa": "ms", "ind": "id", "tha": "th", "vie": "vi",
    "tur": "tr", "nld": "nl", "pol": "pl", "swe": "sv", "tam": "ta", "heb": "he", "ell": "el", "ukr": "uk",
    "fas": "fa", "ben": "bn", "urd": "ur", "cym": "cy", "gle": "ga", "gla": "gd",
}

NEAREST_NEIGHBORHOOD_METERS = 1500


def bcp47(language: str, script: str | None, region: str | None) -> str | None:
    base = LANGUAGES.get(language)
    if not base:
        return None
    if base == "zh":
        if script in ("Hant", "Hans"):
            return f"zh-{script}"
        return "zh-Hant" if region in ("TW", "HK", "MO") else "zh-Hans"
    return base


def read_wof(path: Path, country: str):
    """Yields (id, placetype, level, name, parent_id, geojson geometry, {lang: name}) for current records."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    placetypes = ",".join(f"'{p}'" for p in LEVELS)
    rows = db.execute(f"""
        SELECT s.id, s.placetype, s.name, s.parent_id, json_extract(g.body, '$.geometry')
        FROM spr s JOIN geojson g ON g.id = s.id AND g.is_alt = 0
        WHERE s.placetype IN ({placetypes}) AND s.is_deprecated = 0 AND s.is_current != 0
          AND s.is_superseded = 0 AND s.country = ?""", (country,))
    for area_id, placetype, name, parent_id, geometry in rows:
        names: dict[str, str] = {}
        for language, script, region, name_text in db.execute(
                "SELECT language, script, region, name FROM names WHERE id = ? AND privateuse = 'preferred' "
                "ORDER BY (region = '' OR region IS NULL) DESC", (area_id,)):
            tag = bcp47(language, script or None, region or None)
            if tag and tag not in names:
                names[tag] = name_text
        yield area_id, placetype, LEVELS[placetype], names.get("en") or name, parent_id, geometry, names
    db.close()


def load_wof(conn, path: Path, country: str) -> int:
    """Replace this country's Who's On First boundaries with the bundle's. `conn` is an open transaction."""
    count = 0
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE s_wof (id bigint, placetype text, level text, name text, parent_id bigint, "
                    "geometry text, names jsonb) ON COMMIT DROP")
        with cur.copy("COPY s_wof FROM STDIN") as copy:
            for area_id, placetype, level, name, parent_id, geometry, names in read_wof(path, country):
                copy.write_row((area_id, placetype, level, name, parent_id, geometry, json.dumps(names)))
                count += 1
        # Places keep pointing at areas that still exist; anything this bundle no longer has is dropped.
        cur.execute("""
            INSERT INTO admin_areas (id, source, placetype, level, name, country_code, parent_id, geom, area_km2, license)
            SELECT id, 'wof', placetype, level, name, %(country)s, nullif(parent_id, -1),
                   ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(geometry), 4326)), NULL, 'CC0-1.0'
            FROM s_wof
            ON CONFLICT (id) DO UPDATE SET placetype = EXCLUDED.placetype, level = EXCLUDED.level,
                name = EXCLUDED.name, parent_id = EXCLUDED.parent_id, geom = EXCLUDED.geom""", {"country": country})
        cur.execute("""UPDATE admin_areas SET area_km2 = CASE WHEN is_point THEN 0
                           ELSE ST_Area(geom::geography) / 1e6 END
                       WHERE country_code = %s AND source = 'wof'""", (country,))
        cur.execute("DELETE FROM admin_area_names WHERE area_id IN (SELECT id FROM s_wof)")
        cur.execute("""INSERT INTO admin_area_names (area_id, lang, name)
                       SELECT s.id, n.key, n.value FROM s_wof s, jsonb_each_text(s.names) n""")
    return count


ASSIGN = """
WITH target AS (
    SELECT id, geom FROM places WHERE state = 'active' AND (%(only)s::text[] IS NULL OR id = ANY(%(only)s))
),
polygon AS (
    SELECT t.id AS place_id, lvl.level,
           (SELECT a.id FROM admin_areas a
            WHERE a.level = lvl.level AND NOT a.is_point AND ST_Contains(a.geom, t.geom)
            ORDER BY a.area_km2 ASC LIMIT 1) AS area_id
    FROM target t CROSS JOIN (VALUES ('country'), ('region'), ('city'), ('district'), ('neighborhood')) lvl(level)
),
chosen AS (
    SELECT place_id,
           max(area_id) FILTER (WHERE level = 'country') AS country_id,
           max(area_id) FILTER (WHERE level = 'region') AS region_id,
           max(area_id) FILTER (WHERE level = 'city') AS city_id,
           max(area_id) FILTER (WHERE level = 'district') AS district_id,
           max(area_id) FILTER (WHERE level = 'neighborhood') AS neighborhood_id
    FROM polygon GROUP BY place_id
),
-- No boundary contains the place: take the nearest neighborhood point that belongs to the same city.
nearest AS (
    SELECT c.place_id, n.id AS area_id, n.distance
    FROM chosen c JOIN target t ON t.id = c.place_id
    CROSS JOIN LATERAL (
        SELECT a.id, ST_Distance(a.geom::geography, t.geom::geography) AS distance
        FROM admin_areas a
        WHERE a.level = 'neighborhood' AND a.is_point AND c.city_id IS NOT NULL AND a.parent_id = c.city_id
          AND ST_DWithin(a.geom::geography, t.geom::geography, %(nearest)s)
        ORDER BY a.geom <-> t.geom LIMIT 1
    ) n
    WHERE c.neighborhood_id IS NULL
)
UPDATE places p SET
    country_code = coalesce(upper(ca.country_code), p.country_code),
    region_id = c.region_id,
    city_id = c.city_id,
    -- A district with the same name as its city (Kuala Lumpur in Kuala Lumpur) adds nothing.
    district_id = CASE WHEN da.name = ci.name THEN NULL ELSE c.district_id END,
    neighborhood_id = coalesce(c.neighborhood_id, n.area_id),
    admin_assignment = jsonb_build_object(
        'rule', 'smallest containing boundary per level',
        'neighborhood', CASE WHEN c.neighborhood_id IS NOT NULL THEN 'boundary'
                             WHEN n.area_id IS NOT NULL THEN 'nearest point, ' || round(n.distance) || ' m'
                             ELSE 'none' END,
        'assignedAt', now())
FROM chosen c
LEFT JOIN nearest n ON n.place_id = c.place_id
LEFT JOIN admin_areas ca ON ca.id = c.country_id
LEFT JOIN admin_areas ci ON ci.id = c.city_id
LEFT JOIN admin_areas da ON da.id = c.district_id
WHERE p.id = c.place_id
"""


def assign(conn, only: list[str] | None = None) -> int:
    """Fill in every level for the given places (or all active places). Returns how many were updated."""
    cur = conn.execute(ASSIGN, {"only": only, "nearest": NEAREST_NEIGHBORHOOD_METERS})
    return cur.rowcount
