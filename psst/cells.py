"""The research grid: H3 cells at resolution 7 (about 5 km² each)."""

import h3

RESEARCH_RESOLUTION = 7
DEMAND_RESOLUTION = 5


def cell_for(lat: float, lon: float, resolution: int = RESEARCH_RESOLUTION) -> str:
    return h3.latlng_to_cell(lat, lon, resolution)


def polygon_wkt(cell: str) -> str:
    """The cell's outline as WKT (longitude first), closed."""
    ring = [(lng, lat) for lat, lng in h3.cell_to_boundary(cell)]
    ring.append(ring[0])
    return "POLYGON((" + ", ".join(f"{x} {y}" for x, y in ring) + "))"


def bounds(cell: str) -> tuple[float, float, float, float]:
    """south, west, north, east."""
    points = h3.cell_to_boundary(cell)
    lats = [p[0] for p in points]
    lngs = [p[1] for p in points]
    return min(lats), min(lngs), max(lats), max(lngs)


def neighbors(cell: str) -> list[str]:
    return [c for c in h3.grid_disk(cell, 1) if c != cell]
