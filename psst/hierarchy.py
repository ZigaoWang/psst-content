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

# Where Who's On First is too thin below city level, OpenStreetMap's administrative boundaries are used
# instead. China: WOF has few district boundaries and romanized neighborhood names, while OSM maps every
# district (admin level 6) and subdistrict (街道, level 8) with English names.
OSM_LEVELS = {"CN": {6: "district", 8: "neighborhood"}}
PREFERRED_SOURCE = {"CN": {"district": "osm", "neighborhood": "osm"}}


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
          AND s.is_superseded = 0 AND s.country = ?
          AND json_extract(g.body, '$.geometry.type') IS NOT NULL""", (country,))
    for area_id, placetype, name, parent_id, geometry in rows:
        concordance = db.execute("SELECT other_id FROM concordances WHERE id = ? AND other_source = 'wd:id'",
                                 (area_id,)).fetchone()
        names: dict[str, str] = {}
        for language, script, region, name_text in db.execute(
                "SELECT language, script, region, name FROM names WHERE id = ? AND privateuse = 'preferred' "
                "ORDER BY (region = '' OR region IS NULL) DESC", (area_id,)):
            tag = bcp47(language, script or None, region or None)
            if tag and tag not in names:
                names[tag] = name_text
        qid = concordance[0] if concordance and str(concordance[0]).startswith("Q") else None
        yield area_id, placetype, LEVELS[placetype], names.get("en") or name, parent_id, geometry, names, qid
    db.close()


def load_wof(conn, path: Path, country: str) -> int:
    """Replace this country's Who's On First boundaries with the bundle's. `conn` is an open transaction."""
    count = 0
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE s_wof (id bigint, placetype text, level text, name text, parent_id bigint, "
                    "geometry text, names jsonb, wikidata_id text) ON COMMIT DROP")
        with cur.copy("COPY s_wof FROM STDIN") as copy:
            for area_id, placetype, level, name, parent_id, geometry, names, qid in read_wof(path, country):
                copy.write_row((area_id, placetype, level, name, parent_id, geometry, json.dumps(names), qid))
                count += 1
        # Places keep pointing at areas that still exist; anything this bundle no longer has is dropped.
        cur.execute("""
            INSERT INTO admin_areas (id, source, placetype, level, name, country_code, parent_id, geom, area_km2,
                                     license, wikidata_id)
            SELECT id, 'wof', placetype, level, name, %(country)s, nullif(parent_id, -1), geom, NULL, 'CC0-1.0',
                   wikidata_id
            FROM (
                SELECT *, CASE WHEN GeometryType(raw) = 'POINT' THEN raw
                               ELSE ST_Multi(ST_CollectionExtract(ST_MakeValid(raw), 3)) END AS geom
                FROM (SELECT *, ST_SetSRID(ST_GeomFromGeoJSON(geometry), 4326) AS raw FROM s_wof) parsed
            ) repaired
            -- A few boundaries are degenerate slivers that repair to nothing; they can't contain anything.
            WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom)
            ON CONFLICT (id) DO UPDATE SET placetype = EXCLUDED.placetype, level = EXCLUDED.level,
                name = EXCLUDED.name, parent_id = EXCLUDED.parent_id, geom = EXCLUDED.geom,
                wikidata_id = EXCLUDED.wikidata_id""", {"country": country})
        cur.execute("""UPDATE admin_areas SET area_km2 = CASE WHEN is_point THEN 0
                           ELSE ST_Area(geom::geography) / 1e6 END
                       WHERE country_code = %s AND source = 'wof'""", (country,))
        cur.execute("DELETE FROM admin_area_names WHERE area_id IN (SELECT id FROM s_wof)")
        cur.execute("""INSERT INTO admin_area_names (area_id, lang, name)
                       SELECT s.id, n.key, n.value FROM s_wof s JOIN admin_areas a ON a.id = s.id,
                              jsonb_each_text(s.names) n""")
    return count


def load_osm(conn, bounds: tuple[float, float, float, float], country: str) -> int:
    """Load OpenStreetMap administrative boundaries inside bounds (south, west, north, east) for the
    levels configured in OSM_LEVELS. `conn` is an open transaction."""
    from . import net
    levels = OSM_LEVELS.get(country)
    if not levels:
        raise RuntimeError(f"No OpenStreetMap levels configured for {country}")
    south, west, north, east = bounds
    pattern = "|".join(str(level) for level in levels)
    payload = net.overpass(f'[out:json][timeout:300];relation["boundary"="administrative"]'
                           f'["admin_level"~"^({pattern})$"]({south},{west},{north},{east});out geom tags;')
    count = 0
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE s_osm_lines (id bigint, wkt text) ON COMMIT DROP")
        cur.execute("CREATE TEMP TABLE s_osm (id bigint, level text, admin_level int, name text, names jsonb, "
                    "wikidata_id text) ON COMMIT DROP")
        for relation in payload.get("elements", []):
            tags = relation.get("tags", {})
            admin_level = int(tags.get("admin_level", 0))
            if admin_level not in levels:
                continue
            area_id = -int(relation["id"])
            names = {}
            for key, lang in (("name:en", "en"), ("name:zh-Hans", "zh-Hans"), ("name:zh", "zh-Hans"),
                              ("name:zh-Hant", "zh-Hant"), ("name:ja", "ja"), ("name:ko", "ko")):
                if tags.get(key) and lang not in names:
                    names[lang] = tags[key]
            if "zh-Hans" not in names and tags.get("name"):
                names["zh-Hans" if country == "CN" else "und"] = tags["name"]
            name = names.get("en") or tags.get("name")
            if not name:
                continue
            cur.execute("INSERT INTO s_osm VALUES (%s, %s, %s, %s, %s, %s)",
                        (area_id, levels[admin_level], admin_level, name, json.dumps(names), tags.get("wikidata")))
            for member in relation.get("members", []):
                points = member.get("geometry") or []
                if member.get("type") == "way" and len(points) >= 2:
                    wkt = "LINESTRING(" + ", ".join(f"{p['lon']} {p['lat']}" for p in points) + ")"
                    cur.execute("INSERT INTO s_osm_lines VALUES (%s, %s)", (area_id, wkt))
            count += 1
        # Stitch each relation's ways into polygons. Relations that don't close are skipped.
        cur.execute("""
            INSERT INTO admin_areas (id, source, placetype, level, name, country_code, geom, area_km2, license,
                                     wikidata_id, osm_admin_level)
            SELECT m.id, 'osm', 'admin_level_' || m.admin_level, m.level, m.name, %(country)s, g.geom,
                   ST_Area(g.geom::geography) / 1e6, 'ODbL-1.0', m.wikidata_id, m.admin_level
            FROM s_osm m JOIN LATERAL (
                SELECT ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_BuildArea(ST_Union(
                           ST_SetSRID(ST_GeomFromText(l.wkt), 4326)))), 3)) AS geom
                FROM s_osm_lines l WHERE l.id = m.id
            ) g ON g.geom IS NOT NULL AND NOT ST_IsEmpty(g.geom)
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, level = EXCLUDED.level, geom = EXCLUDED.geom,
                area_km2 = EXCLUDED.area_km2, wikidata_id = EXCLUDED.wikidata_id""", {"country": country})
        cur.execute("DELETE FROM admin_area_names WHERE area_id IN (SELECT id FROM s_osm)")
        cur.execute("""INSERT INTO admin_area_names (area_id, lang, name)
                       SELECT s.id, n.key, n.value FROM s_osm s JOIN admin_areas a ON a.id = s.id,
                              jsonb_each_text(s.names) n WHERE n.key <> 'und'""")
    return count


def english_names_from_wikidata(conn) -> int:
    """Areas that places use and that link to Wikidata take its English label and other labels, which
    are usually the names people know (Lujiazui rather than Lu Jia Zui)."""
    from . import names as place_names
    from . import net
    areas = conn.execute("""
        SELECT a.id, a.wikidata_id FROM admin_areas a
        WHERE a.wikidata_id IS NOT NULL AND a.id IN (
            SELECT unnest(ARRAY[region_id, city_id, district_id, neighborhood_id]) FROM places WHERE state = 'active')
    """).fetchall()
    entities = net.wikidata_entities(sorted({a["wikidata_id"] for a in areas}), props="labels")
    updated = 0
    for area in areas:
        labels = entities.get(area["wikidata_id"], {}).get("labels", {})
        if "en" in labels:
            conn.execute("UPDATE admin_areas SET name = %s WHERE id = %s", (labels["en"]["value"], area["id"]))
            updated += 1
        for lang, codes in place_names.WIKIDATA_LANGUAGES.items():
            for code in codes:
                if code in labels:
                    conn.execute("""INSERT INTO admin_area_names (area_id, lang, name) VALUES (%s, %s, %s)
                                    ON CONFLICT (area_id, lang) DO UPDATE SET name = EXCLUDED.name""",
                                 (area["id"], lang, labels[code]["value"]))
                    break
    return updated


ASSIGN = """
WITH target AS (
    SELECT id, geom, country_code FROM places WHERE state = 'active' AND (%(only)s::text[] IS NULL OR id = ANY(%(only)s))
),
polygon AS (
    SELECT t.id AS place_id, lvl.level,
           (SELECT a.id FROM admin_areas a
            WHERE a.level = lvl.level AND NOT a.is_point AND ST_Contains(a.geom, t.geom)
            ORDER BY (a.source = coalesce(%(preferred)s::jsonb -> t.country_code ->> lvl.level, 'wof')) DESC,
                     a.area_km2 ASC LIMIT 1) AS area_id
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
          AND a.geom && ST_Expand(t.geom, 0.03)
          AND ST_DWithin(a.geom::geography, t.geom::geography, %(nearest)s)
        ORDER BY a.geom <-> t.geom LIMIT 1
    ) n
    WHERE c.neighborhood_id IS NULL
)
UPDATE places p SET
    country_code = coalesce(upper(ca.country_code), p.country_code),
    region_id = c.region_id,
    city_id = c.city_id,
    -- A district named like its city (Kuala Lumpur in Kuala Lumpur), or covering nearly all of it, adds nothing.
    district_id = CASE WHEN da.name = ci.name OR da.area_km2 >= 0.9 * ci.area_km2 THEN NULL
                       ELSE c.district_id END,
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
    cur = conn.execute(ASSIGN, {"only": only, "nearest": NEAREST_NEIGHBORHOOD_METERS,
                                "preferred": json.dumps(PREFERRED_SOURCE)})
    return cur.rowcount
