"""Exact coordinates from Wikidata (P625) and OpenStreetMap. Coordinates are never typed by hand: a draft
names its Wikidata item or OSM element, and these lookups supply the position. Always WGS-84."""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
from dataclasses import dataclass

from . import net, rules

# A Wikidata coordinate coarser than this (in degrees, about 50 m) can't place a pin; use OSM instead.
MAX_WIKIDATA_PRECISION = 0.0005


@dataclass(frozen=True)
class Position:
    lat: float
    lon: float
    source: str       # "wikidata" or "osm"
    ref: str          # "Q123" or "way/456"

    @property
    def license(self) -> str:
        return "CC0-1.0" if self.source == "wikidata" else "ODbL-1.0"


def wikidata(qids: list[str]) -> dict[str, list[tuple[float, float, float | None]]]:
    """Every P625 value per item: (lat, lon, precision). Items without a coordinate map to []."""
    found: dict[str, list[tuple[float, float, float | None]]] = {}
    for start in range(0, len(qids), 50):
        batch = qids[start:start + 50]
        try:
            entities = net.wikidata_entities(batch, props="claims")
            for qid in batch:
                claims = entities.get(qid, {}).get("claims", {}).get("P625", [])
                found[qid] = [(v["latitude"], v["longitude"], v.get("precision"))
                              for v in (c.get("mainsnak", {}).get("datavalue", {}).get("value") for c in claims) if v]
        except RuntimeError:
            # The API rate limits shared addresses hard; the query service is a separate pool.
            found.update(_wikidata_sparql(batch))
    return found


def _wikidata_sparql(batch: list[str]) -> dict[str, list[tuple[float, float, float | None]]]:
    values = " ".join(f"wd:{qid}" for qid in batch)
    query = f"""SELECT ?item ?lat ?lon ?precision WHERE {{
      VALUES ?item {{ {values} }}
      OPTIONAL {{ ?item p:P625/psv:P625 ?node .
                 ?node wikibase:geoLatitude ?lat ; wikibase:geoLongitude ?lon .
                 OPTIONAL {{ ?node wikibase:geoPrecision ?precision }} }}
    }}"""
    payload = net.fetch_json("https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(query))
    found: dict[str, list[tuple[float, float, float | None]]] = {qid: [] for qid in batch}
    for row in payload["results"]["bindings"]:
        qid = row["item"]["value"].rsplit("/", 1)[-1]
        if "lat" in row:
            precision = float(row["precision"]["value"]) if "precision" in row else None
            found.setdefault(qid, []).append((float(row["lat"]["value"]), float(row["lon"]["value"]), precision))
    return found


def osm(refs: list[str]) -> dict[str, tuple[float, float]]:
    """A node's position, or the center of a way's or relation's bounding box (Overpass `out center`)."""
    found: dict[str, tuple[float, float]] = {}
    for start in range(0, len(refs), 100):
        batch = refs[start:start + 100]
        parts = "".join(f"{ref.split('/')[0]}({ref.split('/')[1]});" for ref in batch)
        try:
            payload = net.overpass(f"[out:json][timeout:90];({parts});out center;")
        except RuntimeError:
            for ref in batch:
                coord = _osm_api_center(ref)
                if coord:
                    found[ref] = coord
            continue
        for element in payload.get("elements", []):
            ref = f"{element['type']}/{element['id']}"
            if "lat" in element:
                found[ref] = (element["lat"], element["lon"])
            elif "center" in element:
                found[ref] = (element["center"]["lat"], element["center"]["lon"])
        time.sleep(1)
    return found


def osm_by_wikidata(qids: list[str]) -> dict[str, list[tuple[str, float, float]]]:
    """OpenStreetMap elements tagged with each Wikidata item: {qid: [(ref, lat, lon), ...]}."""
    found: dict[str, list[tuple[str, float, float]]] = {}
    for start in range(0, len(qids), 50):
        batch = qids[start:start + 50]
        pattern = "|".join(batch)
        try:
            payload = net.overpass(f'[out:json][timeout:90];nwr["wikidata"~"^({pattern})$"];out center;')
        except RuntimeError:
            continue
        for element in payload.get("elements", []):
            qid = element.get("tags", {}).get("wikidata")
            point = element if "lat" in element else element.get("center")
            if qid in batch and point:
                found.setdefault(qid, []).append((f"{element['type']}/{element['id']}", point["lat"], point["lon"]))
    return found


def _osm_api_center(ref: str) -> tuple[float, float] | None:
    kind, number = ref.split("/")
    suffix = ".json" if kind == "node" else "/full.json"
    try:
        payload = net.fetch_json(f"https://api.openstreetmap.org/api/0.6/{kind}/{number}{suffix}", attempts=3)
    except (RuntimeError, urllib.error.HTTPError):
        return None
    nodes = [e for e in payload.get("elements", []) if e.get("type") == "node" and "lat" in e]
    if not nodes:
        return None
    if kind == "node":
        return nodes[0]["lat"], nodes[0]["lon"]
    lats, lons = [n["lat"] for n in nodes], [n["lon"] for n in nodes]
    return (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2


def resolve(entries: list[dict], report: rules.Report) -> dict[int, Position]:
    """Positions for draft places that name `wikidata` and/or `osm`, keyed by the entry's index.

    Wikidata is preferred (CC0); OSM is used when the item has no coordinate, several, or an imprecise one.
    Problems go into `report` as errors.
    """
    qids = sorted({e["wikidata"] for e in entries if e.get("wikidata")})
    refs = sorted({e["osm"] for e in entries if e.get("osm")})
    from_wikidata = wikidata(qids) if qids else {}
    from_osm = osm(refs) if refs else {}
    # Items whose Wikidata coordinate can't be used and that came without an OSM element: look for the
    # element tagged with the item, so nobody has to hunt for it by hand.
    unusable = [e["wikidata"] for e in entries if e.get("wikidata") and not e.get("osm")
                and (len(from_wikidata.get(e["wikidata"]) or []) != 1
                     or (from_wikidata[e["wikidata"]][0][2] or 0) > MAX_WIKIDATA_PRECISION)]
    tagged = osm_by_wikidata(sorted(set(unusable))) if unusable else {}
    positions: dict[int, Position] = {}
    for index, entry in enumerate(entries):
        where = f"places[{index}] ({entry.get('name', '?')})"
        qid, ref = entry.get("wikidata"), entry.get("osm")
        if not qid and not ref:
            continue
        reason = None
        if qid:
            values = from_wikidata.get(qid)
            if values is None:
                reason = f"{qid} was not found on Wikidata"
            elif not values:
                reason = f"{qid} has no coordinate on Wikidata"
            elif len(values) > 1:
                reason = f"{qid} has {len(values)} coordinates on Wikidata"
            elif values[0][2] is not None and values[0][2] > MAX_WIKIDATA_PRECISION:
                reason = f"{qid}'s coordinate is only precise to {values[0][2]} degrees"
            else:
                positions[index] = Position(values[0][0], values[0][1], "wikidata", qid)
                continue
        if ref:
            coord = from_osm.get(ref)
            if coord:
                positions[index] = Position(coord[0], coord[1], "osm", ref)
                continue
            report.error(where, f"{ref} was not found on OpenStreetMap" + (f"; also {reason}" if reason else ""))
        elif len(tagged.get(qid, [])) == 1:
            ref, lat, lon = tagged[qid][0]
            positions[index] = Position(lat, lon, "osm", ref)
            report.warn(where, f"{reason}, so the OpenStreetMap element tagged with it ({ref}) was used")
        elif tagged.get(qid):
            options = ", ".join(r for r, _, _ in tagged[qid][:6])
            report.error(where, f"{reason}; several OpenStreetMap elements carry {qid} ({options}): "
                                "add the right one as 'osm'")
        else:
            report.error(where, f"{reason}; add the OpenStreetMap element as 'osm'")
    return positions
