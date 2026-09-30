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
# Where OpenStreetMap has neighborhoods only as named points (place=suburb, quarter, neighbourhood), and
# they're better than Who's On First's. Places get the nearest one, like any point neighborhood.
OSM_NEIGHBORHOOD_POINTS = {"HK"}
PREFERRED_SOURCE = {"CN": {"district": "osm", "neighborhood": "osm"}, "HK": {"neighborhood": "osm"}}
# Who's On First placetypes that mean something else in a particular country. In Hong Kong, "region" is
# one of the 18 districts people use, and localities are towns and neighborhoods inside the one city.
LEVEL_OVERRIDES = {"HK": {"region": "district", "locality_point": None}}
# OSM node ids and relation ids overlap, so points are stored below this offset.
OSM_NODE_OFFSET = 10**13

# Known errors in the boundary data, never used for assignment. Each needs a reason.
EXCLUDED_AREAS = {
    1158894067: "Who's On First files the River Thames as a London neighbourhood.",
    85792207: "Who's On First labels a patch of Kensal Town in West London as West Tilbury, a village in Essex.",
}

# Suffixes that only say what kind of division an area is, dropped from English names.
NAME_SUFFIXES = (" Subdistrict", " subdistrict", " Sub-district", " sub-district", " Residential District")


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
    overrides = LEVEL_OVERRIDES.get(country, {})
    for area_id, placetype, name, parent_id, geometry in rows:
        level = LEVELS[placetype]
        if placetype in overrides:
            level = overrides[placetype]
        if placetype == "locality" and "locality_point" in overrides and json.loads(geometry).get("type") == "Point":
            if overrides["locality_point"] is None:
                continue
            level = overrides["locality_point"]
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
        yield area_id, placetype, level, names.get("en") or name, parent_id, geometry, names, qid
    db.close()


# Boundaries are cut into pieces of at most this many points; testing a point against a few hundred
# vertices instead of a whole country's outline is what makes assigning places fast.
PART_POINTS = 256


def index_parts(conn, area_ids: list[int] | None = None) -> int:
    """Rebuild the lookup pieces (admin_area_parts) for these boundaries, or for all of them."""
    where = "id = ANY(%(ids)s)" if area_ids is not None else "true"
    conn.execute(f"DELETE FROM admin_area_parts WHERE {'area_id = ANY(%(ids)s)' if area_ids is not None else 'true'}",
                 {"ids": area_ids})
    cur = conn.execute(f"""
        INSERT INTO admin_area_parts (area_id, geom)
        SELECT id, (ST_Dump(ST_Subdivide(ST_CollectionExtract(geom, 3), %(points)s))).geom
        FROM admin_areas WHERE NOT is_point AND {where}""", {"ids": area_ids, "points": PART_POINTS})
    return conn.execute(f"SELECT count(DISTINCT area_id) AS n FROM admin_area_parts WHERE "
                        f"{'area_id = ANY(%(ids)s)' if area_ids is not None else 'true'}", {"ids": area_ids}).fetchone()["n"]


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
        cur.execute("""
            WITH stale AS (
                SELECT a.id FROM admin_areas a
                WHERE a.country_code = %(country)s AND a.source = 'wof' AND a.id NOT IN (SELECT id FROM s_wof)
                  AND NOT EXISTS (SELECT 1 FROM places p WHERE a.id IN (p.region_id, p.city_id, p.district_id, p.neighborhood_id))
                  AND NOT EXISTS (SELECT 1 FROM research_cells rc WHERE rc.city_id = a.id)
                  AND NOT EXISTS (SELECT 1 FROM admin_areas c WHERE c.parent_id = a.id)
            ), names AS (DELETE FROM admin_area_names WHERE area_id IN (SELECT id FROM stale))
            DELETE FROM admin_areas WHERE id IN (SELECT id FROM stale)""", {"country": country})
        cur.execute("""UPDATE admin_areas SET area_km2 = CASE WHEN is_point THEN 0
                           ELSE ST_Area(geom::geography) / 1e6 END
                       WHERE country_code = %s AND source = 'wof'""", (country,))
        index_parts(conn, [r["id"] for r in cur.execute(
            "SELECT id FROM s_wof WHERE id IN (SELECT id FROM admin_areas)").fetchall()])
        cur.execute("DELETE FROM admin_area_names WHERE area_id IN (SELECT id FROM s_wof)")
        cur.execute("""INSERT INTO admin_area_names (area_id, lang, name)
                       SELECT s.id, n.key, n.value FROM s_wof s JOIN admin_areas a ON a.id = s.id,
                              jsonb_each_text(s.names) n""")
    return count


def load_osm_neighborhood_points(conn, bounds: tuple[float, float, float, float], country: str) -> int:
    """Load OpenStreetMap's named neighborhood points (place=suburb, quarter, neighbourhood) inside bounds."""
    from . import net
    south, west, north, east = bounds
    payload = net.overpass(f'[out:json][timeout:180];node["place"~"^(suburb|quarter|neighbourhood)$"]["name"]'
                           f'({south},{west},{north},{east});out;')
    count = 0
    for node in payload.get("elements", []):
        tags = node.get("tags", {})
        names = {lang: tags[key] for key, lang in (("name:en", "en"), ("name:zh-Hant", "zh-Hant"),
                                                    ("name:zh-Hans", "zh-Hans"), ("name:zh", "zh-Hant"))
                 if tags.get(key)}
        name = names.get("en") or tags["name"]
        area_id = -(OSM_NODE_OFFSET + int(node["id"]))
        conn.execute("""
            INSERT INTO admin_areas (id, source, placetype, level, name, country_code, geom, area_km2, license, wikidata_id)
            VALUES (%s, 'osm', %s, 'neighborhood', %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), 0, 'ODbL-1.0', %s)
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, geom = EXCLUDED.geom, wikidata_id = EXCLUDED.wikidata_id""",
                     (area_id, "place_" + tags["place"], name, country, node["lon"], node["lat"], tags.get("wikidata")))
        conn.execute("DELETE FROM admin_area_names WHERE area_id = %s", (area_id,))
        for lang, text in names.items():
            conn.execute("INSERT INTO admin_area_names (area_id, lang, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                         (area_id, lang, text))
        count += 1
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
                           f'["admin_level"~"^({pattern})$"]({south},{west},{north},{east});out geom;')
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
        stored = cur.rowcount
        if count and not stored:
            raise RuntimeError(f"None of the {count} OpenStreetMap boundaries formed a polygon")
        index_parts(conn, [r["id"] for r in cur.execute(
            "SELECT id FROM s_osm WHERE id IN (SELECT id FROM admin_areas)").fetchall()])
        cur.execute("DELETE FROM admin_area_names WHERE area_id IN (SELECT id FROM s_osm)")
        cur.execute("""INSERT INTO admin_area_names (area_id, lang, name)
                       SELECT s.id, n.key, n.value FROM s_osm s JOIN admin_areas a ON a.id = s.id,
                              jsonb_each_text(s.names) n WHERE n.key <> 'und'""")
    return stored


def clean_name(name: str) -> str:
    for suffix in NAME_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name


def romanize(name: str) -> str:
    """Hanyu Pinyin without tones, written as one word the way place names are (静安寺街道 -> Jing'ansi).
    Transliteration, not translation: used only when no source has an English name."""
    from pypinyin import lazy_pinyin
    for suffix in ("街道", "镇", "乡"):
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
    syllables = [s for s in lazy_pinyin(name) if s.strip()]
    word = ""
    for index, syllable in enumerate(syllables):
        if index and syllable[0] in "aoe":
            word += "'"
        word += syllable
    return word[:1].upper() + word[1:]


def english_names_from_wikidata(conn, place_ids: list[str] | None = None) -> int:
    """Name the boundaries places use from Wikidata, found through the boundary's own link or through
    Wikidata's OpenStreetMap relation id (P402). Those are usually the names people know (Lujiazui
    rather than Lu Jia Zui). Division words are dropped, and Chinese-only names are romanized."""
    from . import names as place_names
    from . import net
    areas = conn.execute("""
        SELECT a.id, a.name, a.source, a.wikidata_id,
               (SELECT name FROM admin_area_names WHERE area_id = a.id AND lang = 'zh-Hans') AS chinese
        FROM admin_areas a
        WHERE a.id IN (SELECT unnest(ARRAY[region_id, city_id, district_id, neighborhood_id])
                       FROM places WHERE state = 'active' AND (%s::text[] IS NULL OR id = ANY(%s)))
    """, (place_ids, place_ids)).fetchall()
    missing = [a for a in areas if not a["wikidata_id"] and a["source"] == "osm"]
    if missing:
        values = " ".join(f'"{-a["id"]}"' for a in missing)
        query = f"SELECT ?item ?rel WHERE {{ VALUES ?rel {{ {values} }} ?item wdt:P402 ?rel . }}"
        payload = net.fetch_json("https://query.wikidata.org/sparql?format=json&query="
                                 + __import__("urllib.parse").parse.quote(query))
        for row in payload["results"]["bindings"]:
            qid = row["item"]["value"].rsplit("/", 1)[-1]
            area_id = -int(row["rel"]["value"])
            conn.execute("UPDATE admin_areas SET wikidata_id = %s WHERE id = %s", (qid, area_id))
            for a in areas:
                if a["id"] == area_id:
                    a["wikidata_id"] = qid
    entities = net.wikidata_entities(sorted({a["wikidata_id"] for a in areas if a["wikidata_id"]}), props="labels")
    updated = 0
    for area in areas:
        labels = entities.get(area["wikidata_id"] or "", {}).get("labels", {})
        name = labels.get("en", {}).get("value") or area["name"]
        if place_names.HAN.search(name):
            name = romanize(name)
        name = clean_name(name)
        if name != area["name"]:
            conn.execute("UPDATE admin_areas SET name = %s WHERE id = %s", (name, area["id"]))
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
    -- A new place has no country yet; the country boundary it's in decides which sources to prefer.
    SELECT p.id, p.geom, coalesce(p.country_code, (
               SELECT upper(a.country_code) FROM admin_area_parts ap JOIN admin_areas a ON a.id = ap.area_id
               WHERE a.level = 'country' AND ap.geom && p.geom AND ST_Intersects(ap.geom, p.geom) LIMIT 1)) AS country_code
    FROM places p WHERE p.state = 'active' AND (%(only)s::text[] IS NULL OR p.id = ANY(%(only)s))
),
-- Every boundary piece under each place, found through the spatial index first.
hits AS MATERIALIZED (
    SELECT t.id AS place_id, t.country_code, a.id AS area_id, a.level, a.source, a.area_km2
    FROM target t
    JOIN admin_area_parts ap ON ap.geom && t.geom AND ST_Intersects(ap.geom, t.geom)
    JOIN admin_areas a ON a.id = ap.area_id
    WHERE a.id <> ALL(%(excluded)s)
),
-- Per level: the preferred source first, then the smallest area.
ranked AS (
    SELECT DISTINCT ON (place_id, level) place_id, level, area_id
    FROM hits
    ORDER BY place_id, level,
             (source = coalesce(%(preferred)s::jsonb -> country_code ->> level, 'wof')) DESC, area_km2 ASC
),
chosen AS (
    SELECT t.id AS place_id,
           max(r.area_id) FILTER (WHERE r.level = 'country') AS country_id,
           max(r.area_id) FILTER (WHERE r.level = 'region') AS region_id,
           max(r.area_id) FILTER (WHERE r.level = 'city') AS city_id,
           max(r.area_id) FILTER (WHERE r.level = 'district') AS district_id,
           max(r.area_id) FILTER (WHERE r.level = 'neighborhood') AS neighborhood_id
    FROM target t LEFT JOIN ranked r ON r.place_id = t.id
    GROUP BY t.id
),
-- No neighborhood contains the place (a bridge, a riverbank, or a neighborhood known only as a point):
-- take the nearest neighborhood in the same city within reach, measured to its edge or point.
nearest AS (
    SELECT c.place_id, n.id AS area_id, n.distance
    FROM chosen c JOIN target t ON t.id = c.place_id JOIN admin_areas ci ON ci.id = c.city_id
    CROSS JOIN LATERAL (
        SELECT a.id, ST_Distance(a.geom::geography, t.geom::geography) AS distance
        FROM admin_areas a
        WHERE a.level = 'neighborhood' AND a.id <> ALL(%(excluded)s)
          AND a.geom && ST_Expand(t.geom, 0.03)
          AND (a.parent_id = c.city_id OR ST_Intersects(ST_PointOnSurface(a.geom), ci.geom))
          AND ST_DWithin(a.geom::geography, t.geom::geography, %(nearest)s)
        ORDER BY (a.source = coalesce(%(preferred)s::jsonb -> t.country_code ->> 'neighborhood', a.source)) DESC,
                 ST_Distance(a.geom, t.geom) LIMIT 1
    ) n
    WHERE c.neighborhood_id IS NULL
)
UPDATE places p SET
    country_code = coalesce(upper(ca.country_code), p.country_code),
    region_id = c.region_id,
    city_id = c.city_id,
    -- A district named like its city (Kuala Lumpur in Kuala Lumpur), or one that is really the whole city
    -- (at least 90 percent of its area and containing its center), adds nothing.
    district_id = CASE WHEN da.name = ci.name
                            OR (da.area_km2 >= 0.9 * ci.area_km2
                                AND ST_Covers(da.geom, ST_PointOnSurface(ci.geom))) THEN NULL
                       ELSE c.district_id END,
    neighborhood_id = coalesce(c.neighborhood_id, n.area_id),
    admin_assignment = jsonb_build_object(
        'rule', 'smallest containing boundary per level',
        'neighborhood', CASE WHEN c.neighborhood_id IS NOT NULL THEN 'boundary'
                             WHEN n.area_id IS NOT NULL THEN 'nearest, ' || round(n.distance) || ' m away'
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
                                "preferred": json.dumps(PREFERRED_SOURCE),
                                "excluded": list(EXCLUDED_AREAS)})
    return cur.rowcount
