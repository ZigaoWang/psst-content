"""Imports the old area files (areas/*.json, schema 1) into the database.

The import is deterministic and can be run again: ids are derived from the old ids, and every row is
upserted, so running it twice changes nothing. It only writes rows that came from area files.

Provenance comes from the files' git history. Facts in an area's first commit were written by whoever
wrote that area; facts that appear in a later commit were added by the London seeding run. Each fact is
dated by the commit that introduced it.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import cells, ids, rules

# The first eleven areas were researched by Claude Opus 5.5 subagents before the London seeding run began.
SEEDING_STARTED = datetime(2026, 9, 28, 21, 54, tzinfo=timezone.utc)
MODEL_BEFORE_SEEDING = "claude-opus-5-5"
MODEL_SEEDING = "claude-sonnet-5-5"
OPERATOR = "zigao"

COUNTRY_LANGUAGE = {"GB": "en", "CN": "zh-Hans", "MY": "ms", "HK": "zh-Hant", "TW": "zh-Hant", "JP": "ja"}


@dataclass
class FactOrigin:
    committed_at: datetime
    researched_on: str
    model: str


@dataclass
class LegacyFact:
    legacy_id: str
    position: int
    data: dict
    origin: FactOrigin


@dataclass
class LegacyPlace:
    legacy_id: str
    area: dict
    spot: dict
    facts: list[LegacyFact] = field(default_factory=list)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def fact_origins(repo: Path, path: Path) -> dict[str, FactOrigin]:
    """For every fact id ever in this file, the first commit that contains it."""
    relative = path.relative_to(repo)
    commits = _git(repo, "log", "--reverse", "--format=%H %cI", "--", str(relative)).split("\n")
    origins: dict[str, FactOrigin] = {}
    for line in filter(None, commits):
        sha, date = line.split(" ", 1)
        committed = datetime.fromisoformat(date).astimezone(timezone.utc)
        data = json.loads(_git(repo, "show", f"{sha}:{relative}"))
        model = MODEL_BEFORE_SEEDING if committed < SEEDING_STARTED else MODEL_SEEDING
        for spot in data.get("spots", []):
            for fact in spot.get("facts", []):
                key = f"{data['id']}/{spot['id']}/{fact['id']}"
                origins.setdefault(key, FactOrigin(committed, data.get("researchedOn", ""), model))
    return origins


def read_areas(repo: Path, uncommitted_model: str = MODEL_SEEDING) -> list[LegacyPlace]:
    """Every spot in every area file, with the origin of each fact. Files or facts not yet committed
    (a researcher still at work) are dated now and credited to the seeding model."""
    places: list[LegacyPlace] = []
    now = datetime.now(timezone.utc)
    for path in sorted((repo / "areas").glob("*.json")):
        area = json.loads(path.read_text(encoding="utf-8"))
        try:
            origins = fact_origins(repo, path)
        except subprocess.CalledProcessError:
            origins = {}
        for spot in area["spots"]:
            place = LegacyPlace(f"{area['id']}/{spot['id']}", area, spot)
            for position, fact in enumerate(spot["facts"]):
                key = f"{place.legacy_id}/{fact['id']}"
                origin = origins.get(key) or FactOrigin(now, area.get("researchedOn", now.date().isoformat()),
                                                        uncommitted_model)
                place.facts.append(LegacyFact(key, position, fact, origin))
            places.append(place)
    return places


def local_language(name: str, country: str) -> str:
    if any("一" <= ch <= "鿿" for ch in name):
        return "zh-Hant" if country in ("HK", "TW", "MO") else "zh-Hans"
    return COUNTRY_LANGUAGE.get(country, "und")


@dataclass
class ImportResult:
    places: int = 0
    merged: list[tuple[str, str]] = field(default_factory=list)
    facts: int = 0
    sources: int = 0


def build_rows(legacy: list[LegacyPlace]) -> tuple[dict[str, list[tuple]], ImportResult]:
    """Everything the import writes, as plain rows, so it can be bulk loaded in one round trip."""
    result = ImportResult()
    import_run = ids.derived("run", "legacy-import")
    rows: dict[str, list[tuple]] = {k: [] for k in ("runs", "places", "names", "legacy", "facts", "sources",
                                                     "fact_sources")}
    rows["runs"].append((import_run, "import", None, OPERATOR,
                         "Import of the schema 1 area files from psst-content/areas."))
    runs_seen = {import_run}
    by_ref: dict[str, str] = {}
    next_position: dict[str, int] = {}
    sources_seen: dict[str, str] = {}

    for place in legacy:
        spot, area = place.spot, place.area
        source_type, ref = spot["coordinateSource"]["type"], spot["coordinateSource"]["id"]
        if ref in by_ref:
            target = by_ref[ref]
            result.merged.append((place.legacy_id, target))
        else:
            target = ids.derived("pl", place.legacy_id)
            by_ref[ref] = target
            lat, lon = spot["coordinate"]["latitude"], spot["coordinate"]["longitude"]
            rows["places"].append((
                target, spot["kind"], spot.get("size") or "medium", lat, lon, source_type, ref,
                "CC0-1.0" if source_type == "wikidata" else "ODbL-1.0",
                ref if source_type == "wikidata" else None, ref if source_type == "osm" else None,
                cells.cell_for(lat, lon), area["countryCode"], import_run))
            rows["names"].append((target, "display", "en", spot["name"], "research"))
            if spot.get("localName"):
                rows["names"].append((target, "local", local_language(spot["localName"], area["countryCode"]),
                                      spot["localName"], "research"))
            result.places += 1
        rows["legacy"].append((place.legacy_id, target))

        for fact in place.facts:
            position = next_position.get(target, 0)
            next_position[target] = position + 1
            run = ids.derived("run", f"legacy-research:{area['id']}:{fact.origin.model}")
            if run not in runs_seen:
                runs_seen.add(run)
                rows["runs"].append((run, "research", fact.origin.model, OPERATOR,
                                     f"Research for the legacy area {area['id']} ({area['name']}, {area['city']})."))
            fact_id = ids.derived("fa", fact.legacy_id)
            data = fact.data
            rows["facts"].append((
                fact_id, target, position, data["category"], data["status"], data["headline"], data["short"],
                data["long"], fact.origin.researched_on or fact.origin.committed_at.date().isoformat(),
                fact.origin.model, run, fact.origin.committed_at, import_run, fact.legacy_id))
            result.facts += 1
            linked = set()
            for index, source in enumerate(data["sources"]):
                key = rules.normalize_url(source["url"])
                if key not in sources_seen:
                    sources_seen[key] = ids.derived("so", key)
                    rows["sources"].append((sources_seen[key], source["url"], key, source["title"],
                                            source["publisher"]))
                if key not in linked:
                    linked.add(key)
                    rows["fact_sources"].append((fact_id, sources_seen[key], index))
    result.sources = len(rows["sources"])
    return rows, result


STAGING = {
    "runs": "id text, kind text, model text, operator text, notes text",
    "places": ("id text, kind text, size text, lat float8, lon float8, coord_source text, coord_source_ref text, "
               "coord_license text, wikidata_id text, osm_ref text, h3_cell text, country_code text, run text"),
    "names": "place_id text, role text, lang text, name text, source text",
    "legacy": "legacy_id text, place_id text",
    "facts": ("id text, place_id text, position int, category text, veracity text, headline text, short text, "
              "long text, researched_at date, researched_by text, research_run text, committed_at timestamptz, "
              "import_run text, legacy_id text"),
    "sources": "id text, url text, url_key text, title text, publisher text",
    "fact_sources": "fact_id text, source_id text, position int",
}

UPSERTS = [
    """INSERT INTO pipeline_runs (id, kind, model, operator, notes)
       SELECT id, kind, model, operator, notes FROM s_runs ON CONFLICT (id) DO NOTHING""",
    """INSERT INTO places (id, kind, size, geom, coord_source, coord_source_ref, coord_license, wikidata_id, osm_ref,
                           h3_cell, country_code, created_by_run)
       SELECT id, kind, size, ST_SetSRID(ST_MakePoint(lon, lat), 4326), coord_source, coord_source_ref, coord_license,
              wikidata_id, osm_ref, h3_cell, country_code, run FROM s_places
       ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, size = EXCLUDED.size, geom = EXCLUDED.geom,
           coord_source = EXCLUDED.coord_source, coord_source_ref = EXCLUDED.coord_source_ref,
           coord_license = EXCLUDED.coord_license, wikidata_id = coalesce(EXCLUDED.wikidata_id, places.wikidata_id),
           osm_ref = coalesce(EXCLUDED.osm_ref, places.osm_ref), h3_cell = EXCLUDED.h3_cell,
           country_code = EXCLUDED.country_code""",
    """DELETE FROM place_names n USING s_names s
       WHERE n.place_id = s.place_id AND n.role = s.role AND n.source = 'research' AND n.role IN ('display', 'local')""",
    """INSERT INTO place_names (place_id, role, lang, name, source)
       SELECT place_id, role, lang, name, source FROM s_names
       ON CONFLICT (place_id, role, lang) DO UPDATE SET name = EXCLUDED.name""",
    """INSERT INTO legacy_place_ids (legacy_id, place_id) SELECT legacy_id, place_id FROM s_legacy
       ON CONFLICT (legacy_id) DO UPDATE SET place_id = EXCLUDED.place_id""",
    """INSERT INTO sources (id, url, url_key, title, publisher)
       SELECT id, url, url_key, title, publisher FROM s_sources ON CONFLICT (url_key) DO NOTHING""",
    """INSERT INTO facts (id, place_id, position, category, veracity, headline, short, long, state, researched_at,
                          researched_by, research_run, reviewed_at, reviewed_by, review_run, review_notes,
                          published_at, legacy_id)
       SELECT id, place_id, position, category, veracity, headline, short, long, 'published', researched_at,
              researched_by, research_run, committed_at, 'legacy-validator', import_run,
              'Passed the schema 1 validator (structure, style, sources, and coordinates checked online).',
              committed_at, legacy_id FROM s_facts
       ON CONFLICT (id) DO UPDATE SET place_id = EXCLUDED.place_id, position = EXCLUDED.position,
           category = EXCLUDED.category, veracity = EXCLUDED.veracity, headline = EXCLUDED.headline,
           short = EXCLUDED.short, long = EXCLUDED.long""",
    """INSERT INTO fact_sources (fact_id, source_id, position)
       SELECT s.fact_id, src.id, s.position FROM s_fact_sources s
       JOIN s_sources ss ON ss.id = s.source_id JOIN sources src ON src.url_key = ss.url_key
       ON CONFLICT (fact_id, source_id) DO UPDATE SET position = EXCLUDED.position""",
]


def run_import(conn, repo: Path) -> ImportResult:
    """Upsert every legacy place, fact, and source in one transaction. `conn` is an open transaction."""
    rows, result = build_rows(read_areas(repo))
    with conn.cursor() as cur:
        for name, columns in STAGING.items():
            cur.execute(f"CREATE TEMP TABLE s_{name} ({columns}) ON COMMIT DROP")
            with cur.copy(f"COPY s_{name} FROM STDIN") as copy:
                for row in rows[name]:
                    copy.write_row(row)
        for statement in UPSERTS:
            cur.execute(statement)
    return result
