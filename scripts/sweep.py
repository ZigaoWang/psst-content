#!/usr/bin/env python3
"""Collect candidate spots for an area from OpenStreetMap and Wikipedia, before any research.

Usage:
    python3 scripts/sweep.py --bounds 51.468,-0.025,51.490,0.010 --name london-greenwich
    python3 scripts/sweep.py --area london-greenwich                  # reuse an existing area's bounds
    python3 scripts/sweep.py --bounds ... --name shanghai-jingan --lang zh

Writes candidates/<name>.json and candidates/<name>.md (the folder is not committed) and prints a
summary. Each candidate carries its Wikidata id and OSM element where known, and is marked when it's
already in an area file. A sweep is a long list of leads, not a list of spots: most candidates will be
cut during research (see CONTENT_GUIDE.md, sections 4 and 5).
"""

import argparse
import json
import math
import sys
import time
import urllib.parse
from pathlib import Path

import validate

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "candidates"

OVERPASS_QUERY = """[out:json][timeout:180][bbox:{s},{w},{n},{e}];
(
  nwr[historic]; nwr[heritage]; nwr[memorial];
  nwr[tourism~"attraction|museum|artwork|viewpoint|hotel|gallery"];
  nwr[railway=station]; nwr[public_transport=station]; nwr[amenity=ferry_terminal];
  nwr[amenity~"pub|bar|cafe|restaurant|theatre|cinema|place_of_worship|marketplace"][wikidata];
  nwr[amenity=pub];
  nwr[shop][wikidata]; nwr[building][wikidata]; nwr[man_made][wikidata];
  nwr[natural][wikidata]; nwr[bridge][name];
);
out center tags;"""


def overpass(bounds: tuple[float, float, float, float]) -> list[dict]:
    s, w, n, e = bounds
    query = OVERPASS_QUERY.format(s=s, w=w, n=n, e=e)
    data = urllib.parse.urlencode({"data": query}).encode()
    for endpoint in validate.OVERPASS_ENDPOINTS:
        try:
            payload = validate.fetch_json(endpoint, data=data, attempts=3)
            break
        except RuntimeError as exc:
            print(f"  {endpoint} failed: {exc}", file=sys.stderr)
    else:
        return []
    found = []
    for element in payload.get("elements", []):
        tags = element.get("tags", {})
        name = tags.get("name:en") or tags.get("name")
        if not name:
            continue
        lat = element.get("lat", element.get("center", {}).get("lat"))
        lon = element.get("lon", element.get("center", {}).get("lon"))
        if lat is None:
            continue
        what = next((f"{k}={tags[k]}" for k in ("historic", "tourism", "railway", "public_transport", "amenity",
                                                  "building", "man_made", "natural", "shop", "memorial", "bridge")
                     if k in tags), "")
        found.append({
            "name": name,
            "localName": tags.get("name") if tags.get("name") != name else None,
            "lat": lat, "lon": lon,
            "osm": f"{element['type']}/{element['id']}",
            "wikidata": tags.get("wikidata"),
            "what": what,
            "from": "osm",
        })
    return found


def wikipedia(bounds: tuple[float, float, float, float], lang: str) -> list[dict]:
    """Geosearch a grid of points so nothing falls between the circles."""
    s, w, n, e = bounds
    step_km = 2.0
    radius = int(step_km * 1000 * 0.75)
    mid = math.radians((s + n) / 2)
    lat_step = step_km / 111.0
    lon_step = step_km / (111.0 * max(math.cos(mid), 0.1))
    points = []
    lat = s + lat_step / 2
    while lat < n + lat_step / 2:
        lon = w + lon_step / 2
        while lon < e + lon_step / 2:
            points.append((min(lat, n), min(lon, e)))
            lon += lon_step
        lat += lat_step
    found: dict[str, dict] = {}
    for plat, plon in points:
        url = (f"https://{lang}.wikipedia.org/w/api.php?action=query&format=json&generator=geosearch"
               f"&ggscoord={plat}|{plon}&ggsradius={radius}&ggslimit=500"
               f"&prop=coordinates|pageprops&ppprop=wikibase_item&colimit=500")
        try:
            payload = validate.fetch_json(url, attempts=3)
        except RuntimeError as exc:
            print(f"  wikipedia {lang} failed near {plat:.4f},{plon:.4f}: {exc}", file=sys.stderr)
            continue
        for page in payload.get("query", {}).get("pages", {}).values():
            coords = (page.get("coordinates") or [{}])[0]
            if "lat" not in coords or not (s <= coords["lat"] <= n and w <= coords["lon"] <= e):
                continue
            found[page["title"]] = {
                "name": page["title"],
                "localName": None,
                "lat": coords["lat"], "lon": coords["lon"],
                "osm": None,
                "wikidata": page.get("pageprops", {}).get("wikibase_item"),
                "what": f"{lang}.wikipedia",
                "from": f"wikipedia-{lang}",
            }
        time.sleep(0.3)
    return list(found.values())


def merge(candidates: list[dict]) -> list[dict]:
    """One row per real thing: join rows that share a Wikidata id."""
    by_key: dict[str, dict] = {}
    for c in candidates:
        key = c["wikidata"] or c["osm"] or f"{c['name']}@{c['lat']:.4f},{c['lon']:.4f}"
        if key in by_key:
            existing = by_key[key]
            existing["osm"] = existing["osm"] or c["osm"]
            existing["localName"] = existing["localName"] or c["localName"]
            existing["from"] = ",".join(sorted(set(existing["from"].split(",")) | {c["from"]}))
            if c["from"] == "osm" and c["what"]:
                existing["what"] = c["what"]
        else:
            by_key[key] = dict(c)
    return sorted(by_key.values(), key=lambda c: (c["name"].lower()))


def existing_spots() -> tuple[set[str], list[tuple[float, float, str]]]:
    refs: set[str] = set()
    points = []
    for path in sorted(validate.AREAS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for spot in data.get("spots", []):
            source = spot.get("coordinateSource", {})
            refs.add(source.get("id", ""))
            coord = spot.get("coordinate", {})
            points.append((coord.get("latitude", 0), coord.get("longitude", 0), f"{data['id']}/{spot['id']}"))
    return refs, points


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bounds", help="south,west,north,east")
    parser.add_argument("--area", help="use the bounds of an existing area file")
    parser.add_argument("--name", help="name for the output files (defaults to --area)")
    parser.add_argument("--lang", action="append", default=[], help="extra Wikipedia language, e.g. zh (repeatable)")
    args = parser.parse_args()

    if args.area:
        data = json.loads((validate.AREAS_DIR / f"{args.area}.json").read_text(encoding="utf-8"))
        b = data["bounds"]
        bounds = (b["south"], b["west"], b["north"], b["east"])
    elif args.bounds:
        bounds = tuple(float(x) for x in args.bounds.split(","))
        if len(bounds) != 4:
            parser.error("--bounds needs four numbers: south,west,north,east")
    else:
        parser.error("give --bounds or --area")
    name = args.name or args.area
    if not name:
        parser.error("give --name")

    print("Sweeping OpenStreetMap...", file=sys.stderr)
    candidates = overpass(bounds)
    for lang in ["en", *args.lang]:
        print(f"Sweeping {lang}.wikipedia...", file=sys.stderr)
        candidates += wikipedia(bounds, lang)
    candidates = merge(candidates)

    refs, points = existing_spots()
    for c in candidates:
        taken = (c["wikidata"] and c["wikidata"] in refs) or (c["osm"] and c["osm"] in refs)
        if not taken:
            for lat, lon, where in points:
                if abs(lat - c["lat"]) < 0.0005 and abs(lon - c["lon"]) < 0.0008 and \
                        validate.haversine_meters(lat, lon, c["lat"], c["lon"]) < 25:
                    taken = where
                    break
        c["alreadyIn"] = taken if isinstance(taken, str) else ("yes" if taken else None)

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / f"{name}.json").write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    lines = [f"# Candidates for {name}", "", f"Bounds: {bounds}", f"{len(candidates)} candidates", "",
             "| name | local name | what | wikidata | osm | already in |", "| --- | --- | --- | --- | --- | --- |"]
    for c in candidates:
        lines.append(f"| {c['name']} | {c['localName'] or ''} | {c['what']} | {c['wikidata'] or ''} | "
                     f"{c['osm'] or ''} | {c['alreadyIn'] or ''} |")
    (OUT_DIR / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    new = sum(1 for c in candidates if not c["alreadyIn"])
    print(f"{len(candidates)} candidates ({new} not in any area yet) -> candidates/{name}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
