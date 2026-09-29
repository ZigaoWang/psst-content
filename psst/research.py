"""Research by grid cell: plan a city's cells, claim one, brief the researcher, check and submit drafts.

The pipeline only ever writes drafts. A draft reaches the app after a separate, skeptical review
(review.py) and a publish through staging (publish.py).
"""

from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import h3
import jsonschema

from . import cells, coords, hierarchy, ids, names, net, rules, runs

ROOT = Path(__file__).resolve().parent.parent
DRAFT_SCHEMA = json.loads((ROOT / "format" / "draft.schema.json").read_text())
CLAIM_HOURS = 12
# Places closer than this with a similar name are probably the same thing.
DUPLICATE_METERS = 150
LOCAL_WIKIPEDIA = {"CN": "zh", "HK": "zh", "TW": "zh", "MY": "ms", "JP": "ja", "KR": "ko", "FR": "fr", "DE": "de",
                   "ES": "es", "IT": "it"}


# Planning --------------------------------------------------------------------------------------------

def find_city(conn, name: str, country: str | None = None) -> dict:
    rows = conn.execute("""
        SELECT id, name, country_code, ST_XMin(geom) AS west, ST_YMin(geom) AS south,
               ST_XMax(geom) AS east, ST_YMax(geom) AS north
        FROM admin_areas WHERE level = 'city' AND NOT is_point AND lower(name) = lower(%s)
          AND (%s::text IS NULL OR country_code = %s)
          AND EXISTS (SELECT 1 FROM admin_area_parts WHERE area_id = admin_areas.id)
        ORDER BY area_km2 DESC""", (name, country, country)).fetchall()
    if not rows:
        raise RuntimeError(f"No city called {name!r} is set up. Set it up with: "
                           f"uv run psst city add \"{name}\" --country <ISO code>")
    if len({r["country_code"] for r in rows}) > 1 and not country:
        options = ", ".join(f"{r['name']} ({r['country_code']})" for r in rows)
        raise RuntimeError(f"Several cities are called {name!r}: {options}. Pass --country.")
    return rows[0]


def plan(conn, city: dict) -> dict[str, int]:
    """Create the research cells covering a city. Every cell starts open, including ones that already hold
    places: those get a full pass that tops them up."""
    ring = [(city["south"], city["west"]), (city["south"], city["east"]), (city["north"], city["east"]),
            (city["north"], city["west"])]
    candidates = h3.geo_to_cells(h3.LatLngPoly(ring), cells.RESEARCH_RESOLUTION)
    # Pad by one ring so cells cut by the bounding box edge aren't lost.
    candidates = set(candidates) | {n for c in candidates for n in h3.grid_disk(c, 1)}
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE s_cells (cell text, wkt text) ON COMMIT DROP")
        with cur.copy("COPY s_cells FROM STDIN") as copy:
            for cell in candidates:
                copy.write_row((cell, cells.polygon_wkt(cell)))
        cur.execute("""
            INSERT INTO research_cells (cell, resolution, geom, city_id)
            SELECT s.cell, %s, ST_GeomFromText(s.wkt, 4326), %s FROM s_cells s
            WHERE EXISTS (SELECT 1 FROM admin_area_parts p WHERE p.area_id = %s
                          AND ST_Intersects(p.geom, ST_GeomFromText(s.wkt, 4326)))
            ON CONFLICT (cell) DO UPDATE SET city_id = coalesce(research_cells.city_id, EXCLUDED.city_id)""",
                    (cells.RESEARCH_RESOLUTION, city["id"], city["id"]))
        added = cur.rowcount
        started = cur.execute("""SELECT count(*) AS n FROM research_cells rc WHERE rc.city_id = %s
                                 AND EXISTS (SELECT 1 FROM places p WHERE p.h3_cell = rc.cell AND p.state = 'active')""",
                              (city["id"],)).fetchone()["n"]
    return {"cells": added, "with_places": started}


# Demand ------------------------------------------------------------------------------------------------

DEMAND_DAYS = 90


def wanted(conn, limit: int = 20) -> list[dict]:
    """The areas app users looked at most while they were empty, over the last 90 days, with where they
    are and how much of each is planned for research."""
    rows = conn.execute("""SELECT cell, sum(count)::int AS views, max(day) AS last_seen FROM demand
                           WHERE day > current_date - %s GROUP BY cell ORDER BY views DESC, cell LIMIT %s""",
                        (DEMAND_DAYS, limit)).fetchall()
    result = []
    for row in rows:
        lat, lon = h3.cell_to_latlng(row["cell"])
        places = conn.execute("""
            SELECT a.level, a.name, a.country_code FROM admin_area_parts ap JOIN admin_areas a ON a.id = ap.area_id
            WHERE a.level IN ('country', 'city') AND ST_Intersects(ap.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            ORDER BY a.level = 'city' DESC, a.area_km2""", (lon, lat)).fetchall()
        city = next((p for p in places if p["level"] == "city"), None)
        country = next((p["country_code"] for p in places if p["country_code"]), None)
        children = list(h3.cell_to_children(row["cell"], cells.RESEARCH_RESOLUTION))
        planned = conn.execute("""SELECT count(*) AS cells, count(*) FILTER (WHERE state = 'open') AS open
                                  FROM research_cells WHERE cell = ANY(%s)""", (children,)).fetchone()
        result.append({**row, "lat": round(lat, 3), "lon": round(lon, 3), "city": city["name"] if city else None,
                       "country": country, "plannedCells": planned["cells"], "openCells": planned["open"]})
    return result


# Claiming --------------------------------------------------------------------------------------------

CELL_STATS = """
    SELECT rc.cell, rc.state, rc.claimed_by_run, rc.claimed_until, rc.last_researched_at, rc.notes,
           ci.name AS city, ci.country_code,
           (SELECT count(*) FROM places p WHERE p.h3_cell = rc.cell AND p.state = 'active') AS places,
           (SELECT count(*) FROM facts f JOIN places p ON p.id = f.place_id
            WHERE p.h3_cell = rc.cell AND f.state = 'published') AS published,
           (SELECT count(*) FROM facts f JOIN places p ON p.id = f.place_id
            WHERE p.h3_cell = rc.cell AND f.state IN ('draft', 'reviewed')) AS pending
    FROM research_cells rc LEFT JOIN admin_areas ci ON ci.id = rc.city_id"""


def claim(conn, run_id: str, cell: str | None = None, city_id: int | None = None,
          near: tuple[float, float] | None = None) -> dict:
    """Claim a cell for research. With no cell given, the most wanted open cell: most requested by app
    users first, then the one touching the most finished cells, so coverage grows outward evenly."""
    if cell:
        row = conn.execute("SELECT * FROM research_cells WHERE cell = %s FOR UPDATE", (cell,)).fetchone()
        if not row:
            raise RuntimeError(f"{cell} is not a planned research cell. Plan its city first (psst research plan).")
        if row["state"] == "claimed" and row["claimed_until"] and row["claimed_by_run"] != run_id:
            still = conn.execute("SELECT %s > now() AS still", (row["claimed_until"],)).fetchone()["still"]
            if still:
                raise RuntimeError(f"{cell} is claimed by {row['claimed_by_run']} until {row['claimed_until']}.")
    else:
        open_cells = conn.execute("""
            SELECT rc.cell FROM research_cells rc
            WHERE (rc.state = 'open' OR (rc.state = 'claimed' AND rc.claimed_until < now()))
              AND (%s::bigint IS NULL OR rc.city_id = %s)""", (city_id, city_id)).fetchall()
        if not open_cells:
            raise RuntimeError("No open cells" + (" in that city" if city_id else "") + ". Plan more with "
                               "psst research plan, or name a done cell with --cell to revisit it.")
        demand = {r["cell"]: r["n"] for r in conn.execute(
            "SELECT cell, sum(count) AS n FROM demand WHERE day > current_date - %s GROUP BY cell", (DEMAND_DAYS,))}
        done = {r["cell"] for r in conn.execute("SELECT cell FROM research_cells WHERE state = 'done'")}
        existing = {r["h3_cell"]: r["n"] for r in conn.execute(
            "SELECT h3_cell, count(*) AS n FROM places WHERE state = 'active' GROUP BY h3_cell")}
        passes = {r["cell"]: r["passes"] for r in conn.execute("SELECT cell, passes FROM research_cells")}

        # Where people looked first; then cells with the fewest passes, so every cell gets one before any
        # gets a second; then cells that already show rich content (dense areas only partly covered); then
        # next to finished cells, so coverage grows outward evenly.
        def priority(c: str) -> tuple:
            parent = h3.cell_to_parent(c, cells.DEMAND_RESOLUTION)
            return (-demand.get(parent, 0), passes.get(c, 0), -existing.get(c, 0),
                    -sum(n in done for n in cells.neighbors(c)), c)

        if near:
            # The open cell closest to a spot the person asked for ("focus on the Bund").
            cell = min((r["cell"] for r in open_cells),
                       key=lambda c: h3.great_circle_distance(h3.cell_to_latlng(c), near))
        else:
            cell = min((r["cell"] for r in open_cells), key=priority)
    conn.execute("""UPDATE research_cells SET state = 'claimed', claimed_by_run = %s,
                    claimed_until = now() + make_interval(hours => %s) WHERE cell = %s""",
                 (run_id, CLAIM_HOURS, cell))
    conn.execute("UPDATE pipeline_runs SET cell = %s WHERE id = %s", (cell, run_id))
    return conn.execute(CELL_STATS + " WHERE rc.cell = %s", (cell,)).fetchone()


def release(conn, cell: str) -> None:
    conn.execute("""UPDATE research_cells SET state = CASE WHEN last_researched_at IS NULL THEN 'open' ELSE 'done' END,
                    claimed_by_run = NULL, claimed_until = NULL WHERE cell = %s AND state = 'claimed'""", (cell,))


# Briefing --------------------------------------------------------------------------------------------

def brief(conn, cell: str, sweep: bool = True) -> dict:
    """Everything a researcher needs for one cell: where it is, what's already there, and leads."""
    row = conn.execute(CELL_STATS + " WHERE rc.cell = %s", (cell,)).fetchone()
    if not row:
        raise RuntimeError(f"{cell} is not a planned research cell.")
    south, west, north, east = cells.bounds(cell)
    lat, lon = h3.cell_to_latlng(cell)
    nearby = h3.grid_disk(cell, 1)
    existing = conn.execute("""
        SELECT p.id, dn.name, ln.name AS local_name, p.kind, p.wikidata_id, p.osm_ref,
               round(ST_Y(p.geom)::numeric, 6) AS lat, round(ST_X(p.geom)::numeric, 6) AS lon,
               p.h3_cell = %s AS in_cell, nb.name AS neighborhood,
               coalesce((SELECT json_agg(json_build_object('id', f.id, 'headline', f.headline, 'state', f.state,
                                                           'category', f.category) ORDER BY f.position)
                         FROM facts f WHERE f.place_id = p.id AND f.state <> 'retired'), '[]') AS facts
        FROM places p
        JOIN place_names dn ON dn.place_id = p.id AND dn.role = 'display'
        LEFT JOIN place_names ln ON ln.place_id = p.id AND ln.role = 'local'
        LEFT JOIN admin_areas nb ON nb.id = p.neighborhood_id
        WHERE p.h3_cell = ANY(%s) AND p.state = 'active'
        ORDER BY p.h3_cell <> %s, dn.name""", (cell, list(nearby), cell)).fetchall()
    hoods = [r["name"] for r in conn.execute("""
        SELECT DISTINCT a.name FROM admin_area_parts ap JOIN admin_areas a ON a.id = ap.area_id
        WHERE a.level = 'neighborhood' AND a.id <> ALL(%s) AND ST_Intersects(ap.geom, ST_GeomFromText(%s, 4326))
        ORDER BY a.name""", (list(hierarchy.EXCLUDED_AREAS), cells.polygon_wkt(cell)))]
    result = {
        "cell": cell, "state": row["state"], "city": row["city"], "countryCode": row["country_code"],
        "center": {"lat": round(lat, 6), "lon": round(lon, 6)},
        "bounds": {"south": round(south, 6), "west": round(west, 6), "north": round(north, 6), "east": round(east, 6)},
        "neighborhoods": hoods, "notes": row["notes"],
        "existingPlaces": existing, "candidates": [], "sweepProblems": [],
    }
    if sweep:
        known_q = {p["wikidata_id"] for p in existing if p["wikidata_id"]}
        known_osm = {p["osm_ref"] for p in existing if p["osm_ref"]}
        candidates, problems = _sweep(cell, row["country_code"])
        for c in candidates:
            c["known"] = bool((c.get("wikidata") and c["wikidata"] in known_q) or (c.get("osm") and c["osm"] in known_osm))
        result["candidates"] = sorted(candidates, key=lambda c: (c["known"], not c.get("wikipedia"), c["name"]))
        result["sweepProblems"] = problems
    return result


def _sweep(cell: str, country: str | None) -> tuple[list[dict], list[str]]:
    south, west, north, east = cells.bounds(cell)
    lat, lon = h3.cell_to_latlng(cell)
    found: dict[str, dict] = {}
    problems: list[str] = []

    def add(key: str, entry: dict) -> None:
        if h3.latlng_to_cell(entry["lat"], entry["lon"], cells.RESEARCH_RESOLUTION) != cell:
            return
        merged = found.setdefault(key, {"key": key, "name": entry["name"], "lat": round(entry["lat"], 6),
                                        "lon": round(entry["lon"], 6)})
        for k, v in entry.items():
            if v and not merged.get(k):
                merged[k] = v

    for lang in dict.fromkeys(["en", LOCAL_WIKIPEDIA.get(country or "", "en")]):
        url = (f"https://{lang}.wikipedia.org/w/api.php?action=query&format=json&generator=geosearch"
               f"&ggscoord={lat}|{lon}&ggsradius=2000&ggslimit=500&prop=coordinates|pageprops"
               f"&ppprop=wikibase_item&colimit=500")
        try:
            pages = net.fetch_json(url, attempts=3).get("query", {}).get("pages", {}).values()
        except RuntimeError as exc:
            problems.append(f"{lang}.wikipedia geosearch failed: {exc}")
            continue
        for page in pages:
            point = (page.get("coordinates") or [{}])[0]
            if "lat" not in point:
                continue
            qid = page.get("pageprops", {}).get("wikibase_item")
            add(qid or f"{lang}:{page['title']}", {"name": page["title"], "lat": point["lat"], "lon": point["lon"],
                                                   "wikidata": qid, "wikipedia": f"https://{lang}.wikipedia.org/wiki/"
                                                   + urllib.parse.quote(page["title"].replace(" ", "_"))})
        time.sleep(0.3)

    query = f"""[out:json][timeout:120][bbox:{south},{west},{north},{east}];
(
  nwr[historic]; nwr[heritage]; nwr[memorial]; nwr[tourism~"attraction|museum|artwork|viewpoint|gallery"];
  nwr[railway=station]; nwr[public_transport=station]; nwr[amenity=ferry_terminal]; nwr[amenity=pub];
  nwr[amenity~"bar|cafe|restaurant|theatre|cinema|place_of_worship|marketplace"][wikidata];
  nwr[building][wikidata]; nwr[man_made][wikidata]; nwr[shop][wikidata]; nwr[bridge][name];
);
out center tags;"""
    try:
        elements = net.overpass(query).get("elements", [])
    except RuntimeError as exc:
        problems.append(f"OpenStreetMap sweep failed ({exc}); rely on the Wikipedia leads and search OSM by hand.")
        elements = []
    for element in elements:
        tags = element.get("tags", {})
        name = tags.get("name:en") or tags.get("name")
        point = element if "lat" in element else element.get("center")
        if not name or not point:
            continue
        what = next((f"{k}={tags[k]}" for k in ("historic", "tourism", "railway", "public_transport", "amenity",
                                                 "building", "man_made", "shop", "memorial", "bridge") if k in tags), "")
        ref = f"{element['type']}/{element['id']}"
        # Long features (rail lines, roads) come in many segments with one name; one lead is enough.
        add(tags.get("wikidata") or f"name:{name}", {"name": name, "localName": tags.get("name") if tags.get("name") != name else None,
                                          "lat": point["lat"], "lon": point["lon"], "osm": ref,
                                          "wikidata": tags.get("wikidata"), "what": what})
    return list(found.values()), problems


def store_leads(conn, cell: str, candidates: list[dict], run_id: str) -> dict[str, int]:
    """Record a cell's leads. Leads from earlier passes keep what happened to them."""
    for c in candidates:
        conn.execute("""
            INSERT INTO research_leads (cell, key, name, wikidata, osm, url, what, status, run_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (cell, key) DO UPDATE SET name = EXCLUDED.name, wikidata = EXCLUDED.wikidata, osm = EXCLUDED.osm,
                url = EXCLUDED.url, what = EXCLUDED.what,
                status = CASE WHEN research_leads.status = 'open' THEN EXCLUDED.status ELSE research_leads.status END""",
                     (cell, c["key"], c["name"], c.get("wikidata"), c.get("osm"), c.get("wikipedia"), c.get("what"),
                      "known" if c.get("known") else "open", run_id))
    return {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, count(*) AS n FROM research_leads WHERE cell = %s GROUP BY status", (cell,))}


def open_leads(conn, cell: str) -> list[dict]:
    return conn.execute("SELECT * FROM research_leads WHERE cell = %s AND status = 'open' ORDER BY name",
                        (cell,)).fetchall()


def account_for_leads(conn, draft: dict, positions: dict) -> tuple[dict[str, tuple[str, str | None]], list[dict]]:
    """Which open leads this draft covers, and how (status, reason); and the ones it leaves unaccounted."""
    refs = set()
    for index, place in enumerate(draft["places"]):
        refs.update(r for r in (place.get("wikidata"), place.get("osm")) if r)
        if "place" in place:
            row = conn.execute("SELECT wikidata_id, osm_ref FROM places WHERE id = %s", (place["place"],)).fetchone()
            if row:
                refs.update(r for r in (row["wikidata_id"], row["osm_ref"]) if r)
    nearby = list(h3.grid_disk(draft["cell"], 1))
    known = {r for row in conn.execute("SELECT wikidata_id, osm_ref FROM places WHERE h3_cell = ANY(%s) AND state = 'active'",
                                       (nearby,)) for r in (row["wikidata_id"], row["osm_ref"]) if r}
    skipped: dict[str, str] = {}
    later: set[str] = set()
    for item in draft.get("skipped", []):
        for label in [item.get("name")] + item.get("names", []) + [item.get("wikidata"), item.get("osm")]:
            if label:
                (later.add if item.get("later") else lambda x: skipped.__setitem__(x, item["reason"]))(label.casefold())
    added_names = {p["name"].casefold() for p in draft["places"] if p.get("name")}
    covered: dict[str, tuple[str, str | None]] = {}
    missing = []
    for lead in open_leads(conn, draft["cell"]):
        ids = {r for r in (lead["wikidata"], lead["osm"]) if r}
        if ids & refs or lead["name"].casefold() in added_names:
            covered[lead["key"]] = ("added", None)
        elif ids & known:
            covered[lead["key"]] = ("known", None)
        elif reason := next((skipped[x.casefold()] for x in [lead["name"], lead["key"], *ids] if x.casefold() in skipped), None):
            covered[lead["key"]] = ("skipped", reason)
        elif any(x.casefold() in later for x in [lead["name"], lead["key"], *ids]):
            covered[lead["key"]] = ("later", None)
        else:
            missing.append(lead)
    return covered, missing


def write_brief(data: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "brief.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")
    lines = [f"# Cell {data['cell']}", "",
             f"{data['city'] or 'Unknown city'} ({data['countryCode']}). Neighborhoods: "
             f"{', '.join(data['neighborhoods']) or 'none on record'}.",
             f"Center {data['center']['lat']}, {data['center']['lon']}. Bounds S {data['bounds']['south']} "
             f"W {data['bounds']['west']} N {data['bounds']['north']} E {data['bounds']['east']}.", ""]
    if data.get("notes"):
        lines += [f"Notes: {data['notes']}", ""]
    lines += ["## Already in Psst (this cell, then its neighbors)", ""]
    for p in data["existingPlaces"]:
        refs = " ".join(x for x in (p["wikidata_id"], p["osm_ref"]) if x)
        where = "" if p["in_cell"] else " (neighboring cell)"
        lines.append(f"- {p['id']} {p['name']}{where} [{p['kind']}] {refs}")
        for f in p["facts"]:
            lines.append(f"  - {f['category']}, {f['state']}: {f['headline']}")
    open_keys = {lead["key"] for lead in data.get("openLeads", [])}
    lines += ["", "## Leads to account for", "",
              "Every lead marked TODO must end up in the draft: added as a place, or in `skipped` with a reason "
              "(one reason can cover several with `names`). `psst draft check` lists any that are left. Leads are "
              "only a start: also look for places the sweep can't see (guide, section 5).", ""]
    for c in data["candidates"]:
        refs = " ".join(x for x in (c.get("wikidata"), c.get("osm")) if x)
        if c["known"]:
            flag = "(already in Psst)"
        elif c["key"] in open_keys or not open_keys:
            flag = "TODO"
        else:
            flag = "(handled in an earlier pass)"
        extra = " ".join(x for x in (c.get("what"), c.get("wikipedia")) if x)
        lines.append(f"- {flag} {c['name']} {refs} {extra}".rstrip())
    if data["sweepProblems"]:
        lines += ["", "## Sweep problems", ""] + [f"- {p}" for p in data["sweepProblems"]]
    path = directory / "brief.md"
    path.write_text("\n".join(lines) + "\n")
    return path


# Checking and submitting drafts ------------------------------------------------------------------------

@dataclass
class Checked:
    report: rules.Report
    positions: dict[int, coords.Position] = field(default_factory=dict)


def check(conn, draft: dict, online: bool = True) -> Checked:
    """Every check a draft must pass before it's stored. Errors block; warnings are for the reviewer."""
    report = rules.Report()
    for error in sorted(jsonschema.Draft202012Validator(DRAFT_SCHEMA).iter_errors(draft), key=lambda e: e.path):
        where = "/".join(str(p) for p in error.absolute_path) or "draft"
        report.error(where, error.message)
    if not report.ok:
        return Checked(report)
    cell = draft["cell"]
    if not conn.execute("SELECT 1 FROM research_cells WHERE cell = %s", (cell,)).fetchone():
        report.error("cell", f"{cell} is not a planned research cell")

    tag_ids = {t for p in draft["places"] for f in p["facts"] for t in f["tags"]}
    known_tags = {r["id"] for r in conn.execute("SELECT id FROM tags WHERE id = ANY(%s)", (list(tag_ids),))}
    for missing in sorted(tag_ids - known_tags):
        report.error("tags", f"{missing} is not a tag; find one with psst tags search or add it with psst tags propose")

    existing_ids = [p["place"] for p in draft["places"] if "place" in p]
    known_places = {r["id"] for r in conn.execute(
        "SELECT id FROM places WHERE id = ANY(%s) AND state = 'active'", (existing_ids,))}
    new_places = [p for p in draft["places"] if "place" not in p]
    for index, place in enumerate(draft["places"]):
        where = f"places[{index}] ({place.get('name') or place.get('place')})"
        if "place" in place and place["place"] not in known_places:
            report.error(where, f"{place['place']} is not an active place")
        if "place" not in place:
            ref = [r for r in (place.get("wikidata"), place.get("osm")) if r]
            dup = conn.execute("""SELECT p.id, n.name FROM places p JOIN place_names n ON n.place_id = p.id
                                  AND n.role = 'display' WHERE p.wikidata_id = ANY(%s) OR p.osm_ref = ANY(%s)""",
                               (ref, ref)).fetchone()
            if dup:
                report.error(where, f"already in Psst as {dup['id']} ({dup['name']}); add facts with "
                                    f'{{"place": "{dup["id"]}", "facts": [...]}}')
            if place.get("localName") and place["localName"]["name"] == place["name"]:
                report.warn(where, "localName is the same as name; leave it out")
        for f_index, fact in enumerate(place["facts"]):
            rules.check_fact(report, f"{where}.facts[{f_index}]", fact)

    # The same text or source set twice in one draft is almost always a copy-paste slip.
    headlines = [f["headline"] for p in draft["places"] for f in p["facts"]]
    for repeated in sorted({h for h in headlines if headlines.count(h) > 1}):
        report.error("draft", f"headline used twice: {repeated!r}")

    if report.ok:
        _, missing = account_for_leads(conn, draft, {})
        if missing:
            shown = ", ".join(f"{m['name']} ({m['wikidata'] or m['osm'] or m['key']})" for m in missing[:25])
            report.error("leads", f"{len(missing)} leads from the brief aren't accounted for: {shown}"
                         + (" and more" if len(missing) > 25 else "")
                         + ". Add each as a place, or list it in skipped with a reason (see the guide, section 11).")

    positions: dict[int, coords.Position] = {}
    if online and new_places and report.ok:
        by_new_index = coords.resolve(new_places, report)
        new_indexes = [i for i, p in enumerate(draft["places"]) if "place" not in p]
        positions = {new_indexes[i]: pos for i, pos in by_new_index.items()}
        for index, pos in positions.items():
            place = draft["places"][index]
            where = f"places[{index}] ({place['name']})"
            placed = cells.cell_for(pos.lat, pos.lon)
            if placed != cell and placed not in cells.neighbors(cell):
                report.error(where, f"{pos.ref} is at {pos.lat:.6f}, {pos.lon:.6f}, outside this cell and its neighbors")
            elif placed != cell:
                report.warn(where, f"{pos.ref} falls in the neighboring cell {placed}")
            problem = local_name_problem(conn, place.get("localName"), pos)
            if problem:
                report.error(where, problem)
            near = conn.execute("""
                SELECT p.id, n.name, round(ST_Distance(p.geom::geography, ST_MakePoint(%(lon)s, %(lat)s)::geography)) AS m
                FROM places p JOIN place_names n ON n.place_id = p.id
                WHERE p.state = 'active' AND ST_DWithin(p.geom::geography, ST_MakePoint(%(lon)s, %(lat)s)::geography, %(d)s)
                  AND similarity(lower(n.name), lower(%(name)s)) > 0.4 LIMIT 1""",
                                {"lat": pos.lat, "lon": pos.lon, "d": DUPLICATE_METERS, "name": place["name"]}).fetchone()
            if near and near["id"] in place.get("distinctFrom", []):
                report.warn(where, f"marked as distinct from {near['id']} ({near['name']}), {near['m']} m away; "
                                   "the reviewer should confirm they're separate things")
            elif near:
                report.error(where, f"looks like {near['id']} ({near['name']}), {near['m']} m away. If it's that place, "
                                    f'add facts to it with {{"place": "{near["id"]}"}}. If it really is a separate thing, '
                                    f'add "distinctFrom": ["{near["id"]}"] to this place')
    elif not online and new_places:
        report.warn("draft", "coordinates not checked (offline); submit always checks them")
    return Checked(report, positions)


def local_name_problem(conn, local: dict | None, pos: coords.Position) -> str | None:
    """The local name is the name on the signs, so it must be in the language of the country it's in."""
    if not local:
        return None
    country = conn.execute("""
        SELECT a.country_code FROM admin_area_parts ap JOIN admin_areas a ON a.id = ap.area_id
        WHERE a.level = 'country' AND ST_Intersects(ap.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
        LIMIT 1""", (pos.lon, pos.lat)).fetchone()
    language = names.COUNTRY_LANGUAGE.get(country["country_code"]) if country else None
    if language and local["lang"] != language:
        return (f"localName is the name on the signs, which in {country['country_code']} is in '{language}', not "
                f"'{local['lang']}'. Names in other languages are added automatically.")
    return None


def submit(conn, draft: dict, run: dict, checked: Checked) -> dict[str, int]:
    """Store a checked draft: new places, their names, sources, and facts in the draft state."""
    if not checked.report.ok:
        raise RuntimeError("The draft has errors; run psst draft check and fix them first.")
    if not run["model"]:
        raise RuntimeError("Research runs record the model that did the work; start the run with --model.")
    holder = conn.execute("SELECT claimed_by_run FROM research_cells WHERE cell = %s", (draft["cell"],)).fetchone()
    if not holder or holder["claimed_by_run"] != run["id"]:
        raise RuntimeError(f"Run {run['id']} doesn't hold the claim on {draft['cell']}; claim it first "
                           "(psst research claim --cell ...).")
    today = date.today().isoformat()
    counts = {"places": 0, "facts": 0, "sources": 0}
    new_place_ids: list[str] = []
    fact_tags: dict[str, list[str]] = {}
    for index, entry in enumerate(draft["places"]):
        if "place" in entry:
            place_id = entry["place"]
        else:
            pos = checked.positions[index]
            place_id = ids.new("pl")
            conn.execute("""
                INSERT INTO places (id, kind, size, geom, coord_source, coord_source_ref, coord_license, wikidata_id,
                                    osm_ref, h3_cell, created_by_run)
                VALUES (%s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s, %s, %s, %s, %s)""",
                         (place_id, entry["kind"], entry.get("size", "medium"), pos.lon, pos.lat, pos.source, pos.ref,
                          pos.license, entry.get("wikidata"), entry.get("osm") or (pos.ref if pos.source == "osm" else None),
                          cells.cell_for(pos.lat, pos.lon),
                          run["id"]))
            conn.execute("INSERT INTO place_names (place_id, role, lang, name, source) VALUES (%s, 'display', 'en', %s, 'research')",
                         (place_id, entry["name"]))
            if entry.get("localName"):
                conn.execute("INSERT INTO place_names (place_id, role, lang, name, source) VALUES (%s, 'local', %s, %s, 'research')",
                             (place_id, entry["localName"]["lang"], entry["localName"]["name"]))
            new_place_ids.append(place_id)
            counts["places"] += 1
        position = conn.execute("SELECT coalesce(max(position) + 1, 0) AS n FROM facts WHERE place_id = %s",
                                (place_id,)).fetchone()["n"]
        for fact in entry["facts"]:
            fact_id = ids.new("fa")
            conn.execute("""
                INSERT INTO facts (id, place_id, position, category, veracity, headline, short, long, state,
                                   researched_at, researched_by, research_run)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s)""",
                         (fact_id, place_id, position, fact["category"], fact["veracity"], fact["headline"],
                          fact["short"], fact["long"], today, run["model"], run["id"]))
            position += 1
            counts["facts"] += 1
            for s_index, source in enumerate(fact["sources"]):
                key = rules.normalize_url(source["url"])
                row = conn.execute("""
                    INSERT INTO sources (id, url, url_key, title, publisher) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (url_key) DO NOTHING RETURNING id""",
                                   (ids.new("so"), source["url"], key, source["title"], source["publisher"])).fetchone()
                if row:
                    counts["sources"] += 1
                source_id = row["id"] if row else conn.execute(
                    "SELECT id FROM sources WHERE url_key = %s", (key,)).fetchone()["id"]
                conn.execute("INSERT INTO fact_sources (fact_id, source_id, position) VALUES (%s, %s, %s)",
                             (fact_id, source_id, s_index))
            fact_tags[fact_id] = fact["tags"]
    from . import tags
    tags.assign_many(conn, fact_tags)
    if new_place_ids:
        # Also fills in each new place's country.
        hierarchy.assign(conn, new_place_ids)
        try:
            # Areas a city's first places land in get their usual English names from Wikidata.
            hierarchy.english_names_from_wikidata(conn, new_place_ids)
        except RuntimeError:
            pass  # Wikidata unavailable: the boundary names from the data files stay; nothing depends on it.
    notes = draft.get("notes", "")
    if draft.get("skipped"):
        notes += ("\n" if notes else "") + "Skipped: " + "; ".join(
            f"{', '.join(s.get('names') or [s.get('name') or s.get('wikidata') or s.get('osm')])} ({s['reason']})"
            for s in draft["skipped"])
    covered, _ = account_for_leads(conn, draft, checked.positions)
    for key, (status, reason) in covered.items():
        if status != "later":
            conn.execute("UPDATE research_leads SET status = %s, reason = %s, run_id = %s WHERE cell = %s AND key = %s",
                         (status, reason, run["id"], draft["cell"], key))
    left = sum(1 for status, _ in covered.values() if status == "later")
    if left:
        # A partial pass: its drafts go to review as usual, and the cell is open again for the rest.
        state = "open"
        notes += ("\n" if notes else "") + f"{left} leads left for the next pass."
    else:
        # A cell with nothing worth adding is finished; otherwise it waits for review.
        state = "drafted" if counts["facts"] else "done"
    conn.execute("""UPDATE research_cells SET state = %s, claimed_by_run = NULL, claimed_until = NULL, passes = passes + 1,
                    last_researched_at = now(), notes = nullif(%s, '') WHERE cell = %s""",
                 (state, notes, draft["cell"]))
    counts["leads_left"] = left
    counts["new_place_ids"] = new_place_ids
    return counts


def fetch_names(conn, place_ids: list[str]) -> dict[str, int]:
    """Multilingual names for newly added places (Wikidata labels and OSM name tags)."""
    places = conn.execute("""
        SELECT p.id, p.country_code AS country, p.wikidata_id, p.osm_ref,
               (SELECT name FROM place_names WHERE place_id = p.id AND role = 'display') AS display,
               (SELECT name FROM place_names WHERE place_id = p.id AND role = 'local') AS local
        FROM places p WHERE p.id = ANY(%s)""", (place_ids,)).fetchall()
    return names.apply(conn, places)


def load_draft(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path} is not valid JSON: {exc}") from exc


def require_research_run(conn, run_id: str | None) -> dict:
    return runs.require(conn, run_id, "research")
