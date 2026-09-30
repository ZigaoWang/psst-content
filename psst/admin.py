"""The admin page at /admin/: everything in the database, read-only, for the team. One static page reads one
data file, regenerated on the server every 10 minutes and after each publish, so the web never touches the
database. Changes still go through `psst` commands, so each keeps its run and history.
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
from pathlib import Path

from . import coverage

REMOTE_DIR = "/www/wwwroot/psst/public/admin"
PAGE = Path(__file__).with_name("admin.html")
BACKUP_STATUS = Path("/www/wwwroot/psst/backup/psst-db-backup/STATUS")


def _rows(conn, sql: str, params=None) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params)]


def build(conn) -> dict:
    places = _rows(conn, """
        SELECT p.id, p.kind, p.size, round(ST_Y(p.geom)::numeric, 6)::float AS lat,
               round(ST_X(p.geom)::numeric, 6)::float AS lon, p.wikidata_id AS wikidata, p.osm_ref AS osm,
               p.coord_source, p.h3_cell AS cell, p.country_code AS country, p.state,
               ci.name AS city, di.name AS district, ne.name AS neighborhood,
               (SELECT name FROM place_names WHERE place_id = p.id AND role = 'display') AS name,
               (SELECT name FROM place_names WHERE place_id = p.id AND role = 'local') AS local_name,
               to_char(p.created_at, 'YYYY-MM-DD') AS created
        FROM places p
        LEFT JOIN admin_areas ci ON ci.id = coalesce(p.city_id, p.region_id)
        LEFT JOIN admin_areas di ON di.id = p.district_id
        LEFT JOIN admin_areas ne ON ne.id = p.neighborhood_id
        WHERE p.state = 'active' ORDER BY p.id""")
    facts = _rows(conn, """
        SELECT f.id, f.place_id, f.position, f.category, f.veracity, f.headline, f.short, f.long, f.state,
               f.needs_review, f.researched_by, f.research_run, f.reviewed_by, f.review_notes, f.retire_reason,
               f.legacy_id IS NOT NULL AS migrated, f.last_verified_at IS NOT NULL AS verified,
               to_char(f.researched_at, 'YYYY-MM-DD') AS researched, to_char(f.reviewed_at, 'YYYY-MM-DD') AS reviewed,
               to_char(f.published_at, 'YYYY-MM-DD') AS published,
               coalesce((SELECT json_agg(json_build_object('title', s.title, 'publisher', s.publisher, 'url', s.url,
                                                           'status', s.last_check_status, 'failed', s.failed_checks)
                                         ORDER BY fs.position)
                         FROM fact_sources fs JOIN sources s ON s.id = fs.source_id WHERE fs.fact_id = f.id), '[]') AS sources,
               coalesce((SELECT json_agg(t.canonical_name ORDER BY t.canonical_name) FROM fact_tags ft
                         JOIN tags t ON t.id = ft.tag_id WHERE ft.fact_id = f.id), '[]') AS tags,
               coalesce((SELECT json_agg(json_build_object('at', to_char(e.at, 'YYYY-MM-DD HH24:MI'), 'to', e.to_state,
                                                           'changes', e.changes, 'actor', e.actor, 'note', e.note)
                                         ORDER BY e.id)
                         FROM fact_events e WHERE e.fact_id = f.id), '[]') AS history
        FROM facts f JOIN places p ON p.id = f.place_id AND p.state = 'active'
        ORDER BY f.place_id, f.position, f.id""")
    images = _rows(conn, """
        SELECT i.id, i.place_id, i.position, i.kind, i.year, i.state, i.needs_review, i.source, i.source_url, i.title,
               i.author, i.author_url, i.license, i.license_url, i.alt_text AS alt, i.focus_x, i.focus_y,
               i.thumb_file AS thumb, i.full_file AS full, i.added_by, i.reviewed_by, i.review_notes, i.retire_reason,
               to_char(i.created_at, 'YYYY-MM-DD') AS added
        FROM images i ORDER BY i.place_id, i.position, i.id""")
    reports = _rows(conn, """
        SELECT r.id, r.fact_id, r.reason, r.message, r.app_version, r.state, r.resolution,
               to_char(r.created_at, 'YYYY-MM-DD HH24:MI') AS at, f.headline, f.place_id
        FROM reports r JOIN facts f ON f.id = r.fact_id ORDER BY (r.state = 'open') DESC, r.created_at DESC""")
    runs = _rows(conn, """
        SELECT r.id, r.kind, r.model, r.operator, r.cell, r.notes,
               to_char(r.started_at, 'YYYY-MM-DD HH24:MI') AS started, to_char(r.finished_at, 'YYYY-MM-DD HH24:MI') AS finished,
               (SELECT count(*) FROM facts WHERE research_run = r.id) AS facts_written,
               (SELECT count(*) FROM facts WHERE review_run = r.id) AS facts_reviewed,
               (SELECT count(*) FROM places WHERE created_by_run = r.id) AS places_added,
               (SELECT count(*) FROM images WHERE added_run = r.id) AS photos_added,
               (SELECT count(*) FROM images WHERE review_run = r.id) AS photos_reviewed
        FROM pipeline_runs r WHERE r.kind <> 'import' ORDER BY r.started_at DESC LIMIT 300""")
    cities = _rows(conn, """
        SELECT ci.name AS city,
               count(*) AS cells,
               count(*) FILTER (WHERE rc.state = 'done') AS done,
               count(*) FILTER (WHERE rc.state = 'open' AND rc.passes > 0) AS partial,
               count(*) FILTER (WHERE rc.state = 'open' AND rc.passes = 0) AS untouched,
               count(*) FILTER (WHERE rc.state IN ('claimed', 'drafted', 'reviewed')) AS in_progress,
               (SELECT count(*) FROM research_leads l JOIN research_cells c2 ON c2.cell = l.cell
                WHERE c2.city_id = rc.city_id AND l.status = 'open') AS open_leads
        FROM research_cells rc JOIN admin_areas ci ON ci.id = rc.city_id
        GROUP BY ci.name, rc.city_id ORDER BY count(*) DESC""")
    demand = _rows(conn, """
        SELECT d.cell, sum(d.count)::int AS views, max(d.day)::text AS last_day
        FROM demand d WHERE d.day > current_date - 90 GROUP BY d.cell ORDER BY sum(d.count) DESC LIMIT 100""")
    publications = _rows(conn, """
        SELECT content_version AS version, places, facts, notes, to_char(created_at, 'YYYY-MM-DD HH24:MI') AS at
        FROM publications WHERE channel = 'production' ORDER BY created_at DESC LIMIT 30""")
    tags = _rows(conn, """
        SELECT t.canonical_name AS name, t.type, count(DISTINCT f.place_id) AS places
        FROM tags t JOIN fact_tags ft ON ft.tag_id = t.id JOIN facts f ON f.id = ft.fact_id AND f.state = 'published'
        GROUP BY t.id ORDER BY count(DISTINCT f.place_id) DESC LIMIT 200""")

    # Per-place summaries, and per-city totals from them.
    by_place: dict[str, dict] = {p["id"]: p for p in places}
    for p in places:
        p.update(published=0, drafts=0, reviewed=0, retired=0, flagged=0, photos=0, photo_drafts=0)
    for f in facts:
        p = by_place.get(f["place_id"])
        if p:
            key = {"published": "published", "draft": "drafts", "reviewed": "reviewed", "retired": "retired"}[f["state"]]
            p[key] += 1
            p["flagged"] += bool(f["needs_review"] and f["state"] != "retired")
    for i in images:
        p = by_place.get(i["place_id"])
        if p:
            p["photos"] += i["state"] == "published"
            p["photo_drafts"] += i["state"] in ("draft", "reviewed")
    city_totals: dict[str, dict] = {}
    for p in places:
        c = city_totals.setdefault(p["city"] or "Other", {"live_places": 0, "published": 0, "drafts": 0,
                                                           "with_photos": 0, "verified": 0})
        live = p["published"] > 0
        c["live_places"] += live
        c["published"] += p["published"]
        c["drafts"] += p["drafts"] + p["reviewed"]
        c["with_photos"] += live and p["photos"] > 0
    for f in facts:
        p = by_place.get(f["place_id"])
        if p and f["state"] == "published" and f["verified"]:
            city_totals[p["city"] or "Other"]["verified"] += 1
    empty = {"live_places": 0, "published": 0, "drafts": 0, "with_photos": 0, "verified": 0}
    for c in cities:
        c.update({**empty, **city_totals.get(c["city"], {})})
    # Places outside every research city (a town on the edge of one) still get a row.
    researched = {c["city"] for c in cities}
    for name, totals in sorted(city_totals.items(), key=lambda kv: -kv[1]["live_places"]):
        if name not in researched and totals["live_places"]:
            cities.append({"city": name, "cells": 0, "done": 0, "partial": 0, "untouched": 0, "in_progress": 0,
                           "open_leads": 0, **totals})

    live_places = [p for p in places if p["published"]]
    status = {}
    if BACKUP_STATUS.exists():
        status = dict(line.split("=", 1) for line in BACKUP_STATUS.read_text().splitlines() if "=" in line)
    totals = {
        "live_places": len(live_places),
        "places_with_photos": sum(1 for p in live_places if p["photos"]),
        "facts": {s: sum(1 for f in facts if f["state"] == s) for s in ("published", "reviewed", "draft", "retired")},
        "flagged": sum(1 for f in facts if f["needs_review"] and f["state"] != "retired"),
        "photos": {s: sum(1 for i in images if i["state"] == s) for s in ("published", "reviewed", "draft", "retired")},
        "open_reports": sum(1 for r in reports if r["state"] == "open"),
        "tags": len(tags),
        "migrated_verified": sum(1 for f in facts if f["migrated"] and f["state"] == "published" and f["verified"]),
        "migrated_published": sum(1 for f in facts if f["migrated"] and f["state"] == "published"),
    }
    generated = conn.execute("SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI \"UTC\"') AS at").fetchone()["at"]
    return {"generatedAt": generated, "totals": totals, "cities": cities, "places": places, "facts": facts,
            "images": images, "reports": reports, "runs": runs, "demand": demand, "publications": publications,
            "tags": tags, "backup": status}


def write(conn, directory: Path) -> Path:
    """Write the page, its data, and the coverage map into `directory`, replacing each file atomically."""
    directory.mkdir(parents=True, exist_ok=True)
    data = json.dumps(build(conn), ensure_ascii=False, separators=(",", ":"), default=str).encode()
    files = {"index.html": PAGE.read_bytes(), "data.json.gz": gzip.compress(data, 9),
             "map.html": coverage.build(conn).encode()}
    for name, body in files.items():
        tmp = directory / f".{name}.tmp"
        tmp.write_bytes(body)
        os.chmod(tmp, 0o644)
        tmp.replace(directory / name)
    return directory


def upload(host: str, directory: Path) -> None:
    subprocess.run(["rsync", "-rt", "--no-owner", "--no-group", "--chmod=D755,F644", "--delay-updates",
                    f"{directory}/", f"{host}:{REMOTE_DIR}/"], check=True)
