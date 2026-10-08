"""Builds the version 2 app content from the database: only published facts, only places that have at
least one, only tags used on enough places. See docs/DESIGN.md, "App format", and format/v2/.

Output is deterministic: the same database state always produces byte-identical packs with the same
hashes, so unchanged cities are never downloaded again.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from . import guides, rules, tags

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "format" / "v2"
FORMAT_VERSION = 2


class ExportError(RuntimeError):
    pass


@dataclass
class Export:
    directory: Path
    manifest: dict
    places: int
    facts: int
    images: list[str] = field(default_factory=list)  # ids of every photo exported
    guides: list[str] = field(default_factory=list)  # ids of every guide exported


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text())


def _write_pack(directory: Path, name: str, document: dict) -> dict:
    raw = json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    # mtime=0 keeps gzip output identical for identical content.
    data = gzip.compress(raw, compresslevel=9, mtime=0)
    digest = hashlib.sha256(data).hexdigest()
    relative = f"packs/{name}.{digest[:16]}.json.gz"
    (directory / "packs").mkdir(parents=True, exist_ok=True)
    (directory / relative).write_bytes(data)
    return {"file": relative, "sha256": digest, "bytes": len(data)}


def _names(rows, key_name="lang") -> dict[str, str]:
    return {r[key_name]: r["name"] for r in rows}


def build(conn, out_root: Path, include: list[str] = (), include_images: list[str] = (),
          include_guides: list[str] = ()) -> Export:
    """Export everything published, plus the facts in `include`, the images in `include_images`, and the guides
    in `include_guides` (reviewed ones about to be published), into a new directory under out_root. Raises ExportError, and writes nothing usable, if any exported
    fact breaks the writing rules or any pack fails its schema."""
    facts = conn.execute("""
        SELECT f.id, f.place_id, f.category, f.veracity, f.headline, f.short, f.long, f.researched_at,
               f.last_verified_at,
               coalesce((SELECT json_agg(json_build_object('title', s.title, 'publisher', s.publisher, 'url', s.url)
                                         ORDER BY fs.position)
                         FROM fact_sources fs JOIN sources s ON s.id = fs.source_id WHERE fs.fact_id = f.id),
                        '[]') AS sources,
               coalesce((SELECT array_agg(tag_id ORDER BY tag_id) FROM fact_tags WHERE fact_id = f.id), '{}') AS tags
        FROM facts f JOIN places p ON p.id = f.place_id
        WHERE (f.state = 'published' OR (f.state = 'reviewed' AND f.id = ANY(%s))) AND p.state = 'active'
        ORDER BY f.place_id, f.position, f.id""", (list(include),)).fetchall()
    if not facts:
        raise ExportError("Nothing is published.")

    # Last guard: nothing that breaks the writing rules leaves the building.
    report = rules.Report()
    for fact in facts:
        rules.check_fact(report, fact["id"], {**fact, "sources": fact["sources"]})
    if report.errors:
        raise ExportError("Published facts break the rules:\n  " + "\n  ".join(report.errors[:30]))

    place_ids = sorted({f["place_id"] for f in facts})
    places = conn.execute("""
        SELECT p.id, p.kind, p.size, ST_Y(p.geom) AS lat, ST_X(p.geom) AS lon, p.coord_source, p.coord_source_ref,
               p.coord_license, p.country_code, p.district_id, p.neighborhood_id,
               coalesce(p.city_id, p.region_id) AS group_id,
               (SELECT name FROM place_names WHERE place_id = p.id AND role = 'display') AS name,
               (SELECT json_build_object('lang', lang, 'name', name) FROM place_names
                WHERE place_id = p.id AND role = 'local') AS local_name,
               coalesce((SELECT json_object_agg(lang, name ORDER BY lang) FROM place_names
                         WHERE place_id = p.id AND role = 'alt'), '{}') AS names
        FROM places p WHERE p.id = ANY(%s) ORDER BY p.id""", (place_ids,)).fetchall()
    missing_group = [p["id"] for p in places if p["group_id"] is None]
    if missing_group:
        raise ExportError(f"Places without a city or region (run psst hierarchy assign): {missing_group[:10]}")

    # Tags appear only once they connect enough places.
    tag_rows = conn.execute("""
        SELECT t.id, t.canonical_name, t.type, t.wikidata_id, count(DISTINCT f.place_id) AS places,
               coalesce((SELECT json_object_agg(lang, name ORDER BY lang) FROM tag_names WHERE tag_id = t.id), '{}')
                   AS names,
               coalesce((SELECT array_agg(label ORDER BY label) FROM tag_labels
                         WHERE tag_id = t.id AND NOT is_canonical), '{}') AS aliases
        FROM tags t JOIN fact_tags ft ON ft.tag_id = t.id JOIN facts f ON f.id = ft.fact_id
        WHERE f.state = 'published' OR (f.state = 'reviewed' AND f.id = ANY(%s)) GROUP BY t.id
        HAVING count(DISTINCT f.place_id) >= %s ORDER BY t.id""", (list(include), tags.MIN_PLACES_TO_PUBLISH)).fetchall()
    published_tags = {t["id"] for t in tag_rows}

    facts_by_place: dict[str, list[dict]] = {}
    for f in facts:
        facts_by_place.setdefault(f["place_id"], []).append({
            "id": f["id"], "category": f["category"], "veracity": f["veracity"], "headline": f["headline"],
            "short": f["short"], "long": f["long"], "sources": f["sources"],
            "tags": [t for t in f["tags"] if t in published_tags],
            "researchedOn": f["researched_at"].isoformat() if f["researched_at"] else None,
            "lastVerified": f["last_verified_at"].date().isoformat() if f["last_verified_at"] else None,
        })

    images_by_place: dict[str, list[dict]] = {}
    for i in conn.execute("""
            SELECT * FROM images WHERE place_id = ANY(%s)
              AND (state = 'published' OR (state = 'reviewed' AND id = ANY(%s)))
            ORDER BY place_id, position, id""", (place_ids, list(include_images))):
        images_by_place.setdefault(i["place_id"], []).append({
            "id": i["id"], "kind": i["kind"], "year": i["year"], "alt": i["alt_text"],
            "focus": [round(i["focus_x"], 3), round(i["focus_y"], 3)],
            "full": {"file": i["full_file"], "width": i["full_width"], "height": i["full_height"]},
            "thumb": {"file": i["thumb_file"], "width": i["thumb_width"], "height": i["thumb_height"]},
            "credit": {"author": i["author"], "authorUrl": i["author_url"], "license": i["license"],
                       "licenseUrl": i["license_url"], "sourceUrl": i["source_url"], "source": i["source"],
                       "title": i["title"]},
        })

    # One guide per place: a reviewed one about to be published replaces the published one.
    guides_by_place: dict[str, dict] = {}
    for g in conn.execute("""
            SELECT g.id, g.place_id, g.identifier, g.about, g.wikidata_id, g.last_verified_at,
                   coalesce((SELECT json_agg(json_build_object('title', s.title, 'publisher', s.publisher, 'url', s.url)
                                             ORDER BY gs.position)
                             FROM guide_sources gs JOIN sources s ON s.id = gs.source_id WHERE gs.guide_id = g.id),
                            '[]') AS sources,
                   coalesce((SELECT json_agg(json_build_object('property', k.property, 'label', k.label,
                                                               'value', k.value, 'valueId', k.value_id)
                                             ORDER BY k.position)
                             FROM guide_key_facts k WHERE k.guide_id = g.id AND (k.flag IS NULL OR k.flag_confirmed)
                               AND k.property = ANY(%s)),
                            '[]') AS key_facts
            FROM guides g WHERE g.place_id = ANY(%s)
              AND (g.state = 'published' OR (g.state = 'reviewed' AND g.id = ANY(%s)))
            ORDER BY g.place_id, g.state = 'reviewed', g.published_at, g.id""",
                          (list(guides.PROPERTY), place_ids, list(include_guides))):
        guides_by_place[g["place_id"]] = g
    for g in guides_by_place.values():
        rules.check_guide(report, g["id"], g)
    if report.errors:
        raise ExportError("Guides break the rules:\n  " + "\n  ".join(report.errors[:30]))

    def guide_entry(g: dict) -> dict:
        # Several values of one property are one line: "Material: Bronze, Granite".
        key_facts: list[dict] = []
        for k in g["key_facts"]:
            if key_facts and key_facts[-1]["property"] == k["property"]:
                key_facts[-1]["value"] += ", " + k["value"]
                key_facts[-1]["values"].append({"value": k["value"], "id": k["valueId"]})
            else:
                key_facts.append({"property": k["property"], "label": k["label"], "value": k["value"],
                                  "values": [{"value": k["value"], "id": k["valueId"]}]})
        return {"id": g["id"], "identifier": g["identifier"], "about": g["about"], "sources": g["sources"],
                "keyFacts": guides.shown(key_facts), "wikidataId": g["wikidata_id"],
                "lastVerified": g["last_verified_at"].date().isoformat() if g["last_verified_at"] else None}

    by_group: dict[int, list[dict]] = {}
    area_ids: set[int] = set()
    for p in places:
        area_ids.update(a for a in (p["district_id"], p["neighborhood_id"]) if a)
        by_group.setdefault(p["group_id"], []).append({
            "id": p["id"], "name": p["name"], "localName": p["local_name"], "names": p["names"],
            "kind": p["kind"], "size": p["size"], "lat": round(p["lat"], 7), "lon": round(p["lon"], 7),
            "location": {"source": p["coord_source"], "ref": p["coord_source_ref"], "license": p["coord_license"]},
            "countryCode": p["country_code"],
            "districtId": str(p["district_id"]) if p["district_id"] else None,
            "neighborhoodId": str(p["neighborhood_id"]) if p["neighborhood_id"] else None,
            "facts": facts_by_place[p["id"]],
            **({"images": images_by_place[p["id"]]} if p["id"] in images_by_place else {}),
            **({"guide": guide_entry(guides_by_place[p["id"]])} if p["id"] in guides_by_place else {}),
        })

    groups = conn.execute("""
        SELECT a.id, a.name, a.country_code,
               coalesce((SELECT json_object_agg(lang, name ORDER BY lang) FROM admin_area_names
                         WHERE area_id = a.id AND lang <> 'en'), '{}') AS names
        FROM admin_areas a WHERE a.id = ANY(%s)""", (list(by_group),)).fetchall()
    areas = conn.execute("""
        SELECT a.id, a.level, a.name,
               coalesce((SELECT json_object_agg(lang, name ORDER BY lang) FROM admin_area_names
                         WHERE area_id = a.id AND lang <> 'en'), '{}') AS names
        FROM admin_areas a WHERE a.id = ANY(%s) ORDER BY a.id""", (sorted(area_ids),)).fetchall()
    city_of_area: dict[int, int] = {}
    for group_id, group_places in by_group.items():
        for p in group_places:
            for key in ("districtId", "neighborhoodId"):
                if p[key]:
                    city_of_area.setdefault(int(p[key]), group_id)

    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = out_root / version
    directory.mkdir(parents=True, exist_ok=False)

    city_entries, cities = [], []
    for group in sorted(groups, key=lambda g: g["id"]):
        group_places = sorted(by_group[group["id"]], key=lambda p: p["id"])
        city_id = str(group["id"])
        document = {"formatVersion": FORMAT_VERSION, "cityId": city_id, "places": group_places}
        jsonschema.validate(document, _schema("city"))
        pack = _write_pack(directory, f"city-{city_id}", document)
        city_entries.append({**pack, "cityId": city_id})
        lats = [p["lat"] for p in group_places]
        lons = [p["lon"] for p in group_places]
        cities.append({"id": city_id, "name": group["name"], "names": group["names"],
                       "countryCode": (group["country_code"] or group_places[0]["countryCode"] or "").upper(),
                       "bounds": {"south": min(lats), "west": min(lons), "north": max(lats), "east": max(lons)},
                       "placeCount": len(group_places)})

    legacy = {r["legacy_id"]: r["place_id"] for r in conn.execute(
        "SELECT legacy_id, place_id FROM legacy_place_ids ORDER BY legacy_id")}
    common = {
        "formatVersion": FORMAT_VERSION,
        "cities": cities,
        "areas": [{"id": str(a["id"]), "level": a["level"], "name": a["name"], "names": a["names"],
                   "cityId": str(city_of_area[a["id"]])} for a in areas],
        "tags": [{"id": t["id"], "name": t["canonical_name"], "type": t["type"], "wikidataId": t["wikidata_id"],
                  "names": t["names"], "aliases": list(t["aliases"]), "placeCount": t["places"]} for t in tag_rows],
        "legacyIds": legacy,
    }
    jsonschema.validate(common, _schema("common"))
    manifest = {
        "formatVersion": FORMAT_VERSION,
        "contentVersion": version,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "common": _write_pack(directory, "common", common),
        "cities": sorted(city_entries, key=lambda c: c["cityId"]),
        "counts": {"places": len(places), "facts": len(facts)},
    }
    jsonschema.validate(manifest, _schema("manifest"))
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    exported_images = [i["id"] for p in places if p["id"] in images_by_place for i in images_by_place[p["id"]]]
    return Export(directory, manifest, len(places), len(facts), exported_images,
                  sorted(g["id"] for g in guides_by_place.values()))


def load_pack(directory: Path, entry: dict) -> dict:
    """Read a pack back, checking its hash, exactly as the app does."""
    data = (directory / entry["file"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != entry["sha256"] or len(data) != entry["bytes"]:
        raise ExportError(f"{entry['file']} doesn't match its hash")
    return json.loads(gzip.decompress(data))
