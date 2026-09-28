#!/usr/bin/env python3
"""Look up exact coordinates for Wikidata items and OpenStreetMap elements.

Usage:
    python3 scripts/coords.py Q935104 way/40778038 node/123456

Prints each result as the JSON to paste into a spot, and warns when a Wikidata coordinate is too
imprecise to use (fewer than 4 decimal places), in which case find the OSM element instead.
Uses the same lookups, fallbacks, and rules as the validator, so what this prints will pass --online.
"""

import json
import sys
from decimal import Decimal

import validate


def main() -> int:
    refs = sys.argv[1:]
    if not refs:
        print(__doc__)
        return 1
    qids = [r for r in refs if validate.QID_RE.match(r)]
    osm_refs = [r for r in refs if validate.OSM_RE.match(r)]
    unknown = [r for r in refs if r not in qids and r not in osm_refs]
    for ref in unknown:
        print(f"{ref}: not a Wikidata id (Q123) or OSM element (node/123, way/123, relation/123)", file=sys.stderr)

    wikidata = validate.wikidata_coordinates(qids) if qids else {}
    osm = validate.osm_coordinates(osm_refs) if osm_refs else {}
    status = 1 if unknown else 0

    for qid in qids:
        coords = wikidata.get(qid)
        if not coords:
            print(f"{qid}: no coordinate on Wikidata; use an OSM element instead", file=sys.stderr)
            status = 1
            continue
        lat, lon, precision = coords[0]
        if len(coords) > 1:
            print(f"{qid}: has {len(coords)} coordinates; using the first, check it's the right one", file=sys.stderr)
        if precision is not None and precision > 0.0005:
            print(f"{qid}: precision is only {precision} degrees; use an OSM element instead", file=sys.stderr)
            status = 1
            continue
        emit(lat, lon, "wikidata", qid)

    for ref in osm_refs:
        coord = osm.get(ref)
        if not coord:
            print(f"{ref}: not found on OpenStreetMap", file=sys.stderr)
            status = 1
            continue
        emit(coord[0], coord[1], "osm", ref)
    return status


def emit(lat: float, lon: float, kind: str, ref: str) -> None:
    snippet = {
        "coordinate": {"latitude": Decimal(repr(lat)), "longitude": Decimal(repr(lon))},
        "coordinateSource": {"type": kind, "id": ref},
    }
    text = json.dumps(snippet, ensure_ascii=False, default=lambda d: float(d))
    print(text[1:-1])


if __name__ == "__main__":
    sys.exit(main())
