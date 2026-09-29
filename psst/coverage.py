"""The coverage map: every research cell colored by its state, with place and fact counts, and where app
users asked for places. One self-contained HTML page, served to the team at /coverage/."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import h3

REMOTE_DIR = "/www/wwwroot/psst/public/coverage"

COLORS = {"open": "#9e9e9e", "claimed": "#f2a900", "drafted": "#0067b1", "reviewed": "#7a1f5c", "done": "#00783a"}
LABELS = {"open": "Open for research", "claimed": "Being researched now", "drafted": "Researched, waiting for review",
          "reviewed": "Reviewed, waiting to be published", "done": "Finished and published"}

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
const labels = __LABELS__;
const map = L.map("map", { preferCanvas: true });
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, attribution: "&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors"
}).addTo(map);
const cellsLayer = L.geoJSON(data.cells, {
  style: f => ({ color: colors[f.properties.state], weight: 1, fillOpacity: f.properties.places ? 0.45 : 0.18 }),
  onEachFeature: (f, layer) => {
    const p = f.properties;
    layer.bindPopup(`<b>${p.city || "Research cell"}</b><br>${labels[p.state]}<br>` +
      `${p.places} places, ${p.published} stories in the app, ${p.pending} waiting for review` +
      (p.leads ? `<br>${p.leads - p.open_leads} of ${p.leads} leads handled` : "") +
      `<br>${p.passes} full research ${p.passes === 1 ? "pass" : "passes"}` +
      (p.notes ? `<br><i>${p.notes.replace(/</g, "&lt;")}</i>` : "") + `<br><small>Cell ${p.cell}</small>`);
  }
}).addTo(map);
// Heat: how often each empty area was looked at, on a log scale so one busy area doesn't wash out the rest.
const heatColors = ["#fee391", "#fec44f", "#fe9929", "#ec7014", "#cc4c02", "#8c2d04"];
const maxViews = Math.max(1, ...data.demand.features.map(f => f.properties.count));
const heatColor = n => heatColors[Math.min(heatColors.length - 1,
  Math.floor(Math.log(n) / Math.log(maxViews + 1) * heatColors.length))];
const demandLayer = L.geoJSON(data.demand, {
  style: f => ({ color: heatColor(f.properties.count), weight: 1, fillColor: heatColor(f.properties.count), fillOpacity: 0.4 }),
  onEachFeature: (f, layer) => layer.bindPopup(`<b>${f.properties.count} views</b> while empty, last 90 days<br>` +
    `${f.properties.plannedCells ? f.properties.openCells + " of " + f.properties.plannedCells + " research cells open" : "Not planned for research"}` +
    `<br>${f.properties.cell}`)
}).addTo(map);
L.control.layers(null, { "Research cells": cellsLayer, "Empty areas people looked at": demandLayer }).addTo(map);
const legend = L.control({ position: "bottomleft" });
legend.onAdd = () => {
  const div = L.DomUtil.create("div", "panel");
  div.innerHTML = `<h1>Psst coverage</h1><div style="margin-bottom:6px">Each hexagon is a research cell, about 2.5 km across.</div><table>` +
    Object.entries(data.totals).map(([state, n]) =>
      `<tr><td><span class="swatch" style="background:${colors[state]}"></span>${labels[state]}</td><td>${n}</td></tr>`).join("") +
    `</table>` +
    (data.demand.features.length ? `<div style="margin-top:6px">Empty areas people looked at: ` +
      heatColors.map(c => `<span class="swatch" style="background:${c};margin-right:1px"></span>`).join("") +
      ` up to ${maxViews} views</div>` : "") +
    `<div style="margin-top:6px">${data.places} places and ${data.facts} stories in the app. Updated ${data.generatedAt}.</div>` +
    `<div class="cities">` + data.cities.map((c, i) => `<a data-i="${i}">${c.name} (${c.cells})</a>`).join("") +
    (data.demand.features.length ? `<a data-demand="1">Most viewed empty areas</a>` : "") + `</div>`;
  div.querySelectorAll(".cities a[data-i]").forEach(a => a.onclick = () => show(data.cities[a.dataset.i]));
  div.querySelectorAll(".cities a[data-demand]").forEach(a => a.onclick = () => map.fitBounds(demandLayer.getBounds(), { maxZoom: 8 }));
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
        SELECT rc.cell, rc.state, rc.notes, ci.name AS city, rc.passes,
               (SELECT count(*) FROM research_leads l WHERE l.cell = rc.cell) AS leads,
               (SELECT count(*) FROM research_leads l WHERE l.cell = rc.cell AND l.status = 'open') AS open_leads,
               count(DISTINCT p.id) AS places,
               count(f.id) FILTER (WHERE f.state = 'published') AS published,
               count(f.id) FILTER (WHERE f.state IN ('draft', 'reviewed')) AS pending
        FROM research_cells rc LEFT JOIN admin_areas ci ON ci.id = rc.city_id
        LEFT JOIN places p ON p.h3_cell = rc.cell AND p.state = 'active'
        LEFT JOIN facts f ON f.place_id = p.id
        GROUP BY rc.cell, rc.state, rc.notes, ci.name, rc.passes ORDER BY rc.cell""").fetchall()
    demand = conn.execute("""SELECT d.cell, sum(d.count) AS count FROM demand d WHERE d.day > current_date - 90
                             GROUP BY d.cell ORDER BY d.cell""").fetchall()
    planned = {}
    for d in demand:
        if h3.is_valid_cell(d["cell"]):
            children = list(h3.cell_to_children(d["cell"], 7))
            planned[d["cell"]] = conn.execute("""SELECT count(*) AS cells, count(*) FILTER (WHERE state = 'open') AS open
                                                 FROM research_cells WHERE cell = ANY(%s)""", (children,)).fetchone()
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
                         "properties": {k: r[k] for k in ("cell", "state", "city", "places", "published", "pending", "notes",
                                                          "passes", "leads", "open_leads")}})
    data = {
        "cells": {"type": "FeatureCollection", "features": features},
        "demand": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": polygon(d["cell"]),
             "properties": {"cell": d["cell"], "count": int(d["count"]), "plannedCells": planned[d["cell"]]["cells"],
                            "openCells": planned[d["cell"]]["open"]}}
            for d in demand if h3.is_valid_cell(d["cell"])]},
        "cities": sorted(cities.values(), key=lambda c: -c["cells"]),
        "totals": state_totals, "places": totals["places"], "facts": totals["facts"], "generatedAt": totals["at"],
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return PAGE.replace("__DATA__", payload).replace("__COLORS__", json.dumps(COLORS)).replace("__LABELS__", json.dumps(LABELS))


def write(html: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def upload(host: str, path: Path) -> None:
    subprocess.run(["ssh", host, f"mkdir -p {REMOTE_DIR}"], check=True)
    subprocess.run(["scp", "-q", str(path), f"{host}:{REMOTE_DIR}/index.html.tmp"], check=True)
    subprocess.run(["ssh", host, f"mv {REMOTE_DIR}/index.html.tmp {REMOTE_DIR}/index.html"], check=True)

