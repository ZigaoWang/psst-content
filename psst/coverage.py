"""The coverage map: every research cell colored by its state, with place and fact counts, and where app
users asked for places. One self-contained HTML page, served to the team at /coverage/."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import h3

REMOTE_DIR = "/www/wwwroot/psst/public/coverage"

COLORS = {"open": "#9e9e9e", "claimed": "#f2a900", "drafted": "#0067b1", "reviewed": "#7a1f5c", "done": "#00783a"}

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Psst coverage</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body, #map { height: 100%; margin: 0; font: 14px -apple-system, system-ui, sans-serif; }
  .panel { background: white; padding: 10px 12px; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,.3); }
  .panel h1 { font-size: 15px; margin: 0 0 6px; }
  .swatch { display: inline-block; width: 12px; height: 12px; border-radius: 2px; margin-right: 6px; vertical-align: -1px; }
  table { border-collapse: collapse; } td { padding: 1px 8px 1px 0; }
  .cities a { display: inline-block; margin: 6px 8px 0 0; color: #0067b1; cursor: pointer; text-decoration: none; }
</style>
</head>
<body>
<div id="map"></div>
<script>
const data = __DATA__;
const colors = __COLORS__;
const map = L.map("map", { preferCanvas: true });
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, attribution: "&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors"
}).addTo(map);
const cellsLayer = L.geoJSON(data.cells, {
  style: f => ({ color: colors[f.properties.state], weight: 1, fillOpacity: f.properties.places ? 0.45 : 0.18 }),
  onEachFeature: (f, layer) => {
    const p = f.properties;
    layer.bindPopup(`<b>${p.cell}</b><br>${p.city || ""}<br>State: ${p.state}<br>` +
      `${p.places} places, ${p.published} published facts, ${p.pending} waiting` +
      (p.notes ? `<br><i>${p.notes.replace(/</g, "&lt;")}</i>` : ""));
  }
}).addTo(map);
const demandLayer = L.geoJSON(data.demand, {
  style: f => ({ color: "#d3221b", weight: 2, dashArray: "4 4", fillOpacity: Math.min(0.05 + f.properties.count / 200, 0.4) }),
  onEachFeature: (f, layer) => layer.bindPopup(`${f.properties.count} requests in the last 90 days`)
});
L.control.layers(null, { "Research cells": cellsLayer, "Requested areas": demandLayer }).addTo(map);
const legend = L.control({ position: "bottomleft" });
legend.onAdd = () => {
  const div = L.DomUtil.create("div", "panel");
  div.innerHTML = `<h1>Psst coverage</h1><table>` +
    Object.entries(data.totals).map(([state, n]) =>
      `<tr><td><span class="swatch" style="background:${colors[state]}"></span>${state}</td><td>${n} cells</td></tr>`).join("") +
    `</table><div style="margin-top:6px">${data.places} places, ${data.facts} published facts. Updated ${data.generatedAt}.</div>` +
    `<div class="cities">` + data.cities.map((c, i) => `<a data-i="${i}">${c.name} (${c.cells})</a>`).join("") + `</div>`;
  div.querySelectorAll(".cities a").forEach(a => a.onclick = () => show(data.cities[a.dataset.i]));
  L.DomEvent.disableClickPropagation(div);
  return div;
};
legend.addTo(map);
function show(city) { map.fitBounds([[city.south, city.west], [city.north, city.east]]); }
if (data.cities.length) show(data.cities[0]); else map.setView([30, 0], 2);
</script>
</body>
</html>
"""


def build(conn) -> str:
    rows = conn.execute("""
        SELECT rc.cell, rc.state, rc.notes, ci.name AS city,
               count(DISTINCT p.id) AS places,
               count(f.id) FILTER (WHERE f.state = 'published') AS published,
               count(f.id) FILTER (WHERE f.state IN ('draft', 'reviewed')) AS pending
        FROM research_cells rc LEFT JOIN admin_areas ci ON ci.id = rc.city_id
        LEFT JOIN places p ON p.h3_cell = rc.cell AND p.state = 'active'
        LEFT JOIN facts f ON f.place_id = p.id
        GROUP BY rc.cell, rc.state, rc.notes, ci.name ORDER BY rc.cell""").fetchall()
    demand = conn.execute("""SELECT cell, sum(count) AS count FROM demand WHERE day > current_date - 90
                             GROUP BY cell ORDER BY cell""").fetchall()
    totals = conn.execute("""SELECT (SELECT count(*) FROM places WHERE state = 'active'
                                     AND EXISTS (SELECT 1 FROM facts WHERE place_id = places.id AND state = 'published')) AS places,
                                    (SELECT count(*) FROM facts WHERE state = 'published') AS facts,
                                    to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI "UTC"') AS at""").fetchone()

    def polygon(cell: str) -> dict:
        ring = [[round(lng, 6), round(lat, 6)] for lat, lng in h3.cell_to_boundary(cell)]
        return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}

    state_totals = {state: 0 for state in COLORS}
    features = []
    cities: dict[str, dict] = {}
    for r in rows:
        state_totals[r["state"]] += 1
        lats, lngs = zip(*h3.cell_to_boundary(r["cell"]))
        c = cities.setdefault(r["city"] or "Other", {"name": r["city"] or "Other", "cells": 0, "south": 90, "west": 180,
                                                     "north": -90, "east": -180})
        c["cells"] += 1
        c["south"], c["north"] = min(c["south"], *lats), max(c["north"], *lats)
        c["west"], c["east"] = min(c["west"], *lngs), max(c["east"], *lngs)
        features.append({"type": "Feature", "geometry": polygon(r["cell"]),
                         "properties": {k: r[k] for k in ("cell", "state", "city", "places", "published", "pending", "notes")}})
    data = {
        "cells": {"type": "FeatureCollection", "features": features},
        "demand": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": polygon(d["cell"]), "properties": {"count": int(d["count"])}}
            for d in demand if h3.is_valid_cell(d["cell"])]},
        "cities": sorted(cities.values(), key=lambda c: -c["cells"]),
        "totals": state_totals, "places": totals["places"], "facts": totals["facts"], "generatedAt": totals["at"],
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return PAGE.replace("__DATA__", payload).replace("__COLORS__", json.dumps(COLORS))


def write(html: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def upload(host: str, path: Path) -> None:
    subprocess.run(["ssh", host, f"mkdir -p {REMOTE_DIR}"], check=True)
    subprocess.run(["scp", "-q", str(path), f"{host}:{REMOTE_DIR}/index.html.tmp"], check=True)
    subprocess.run(["ssh", host, f"mv {REMOTE_DIR}/index.html.tmp {REMOTE_DIR}/index.html"], check=True)

