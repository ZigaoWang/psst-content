"""Every `psst` command beyond the basics in cli.py. Each wraps a module that does the actual work."""

from __future__ import annotations

from pathlib import Path

from . import db
from .cli import arg, command


@command("hierarchy load-wof", "Load a Who's On First admin bundle for one country (run on the VPS).",
         arg("bundle", help="path to whosonfirst-data-admin-xx-latest.db"),
         arg("--country", required=True, help="ISO country code, e.g. GB"))
def hierarchy_load_wof(args) -> int:
    from . import hierarchy
    with db.connect(actor="hierarchy") as conn:
        count = hierarchy.load_wof(conn, Path(args.bundle), args.country.upper())
    print(f"Loaded {count} {args.country.upper()} boundaries.")
    return 0


@command("hierarchy load-osm", "Load OpenStreetMap administrative boundaries inside a box (see OSM_LEVELS).",
         arg("--bounds", required=True, help="south,west,north,east"),
         arg("--country", required=True, help="ISO country code, e.g. CN"))
def hierarchy_load_osm(args) -> int:
    from . import hierarchy
    bounds = tuple(float(x) for x in args.bounds.split(","))
    with db.connect(actor="hierarchy") as conn:
        count = hierarchy.load_osm(conn, bounds, args.country.upper())
    print(f"Loaded {count} {args.country.upper()} OpenStreetMap boundaries.")
    return 0


@command("hierarchy names", "Name boundaries in use from their Wikidata labels.")
def hierarchy_names(args) -> int:
    from . import hierarchy
    with db.connect(actor="hierarchy") as conn:
        print(f"Renamed {hierarchy.english_names_from_wikidata(conn)} boundaries from Wikidata.")
    return 0


@command("hierarchy assign", "Assign country, region, city, district, and neighborhood to every place.")
def hierarchy_assign(args) -> int:
    from . import hierarchy
    with db.connect(actor="hierarchy") as conn:
        updated = hierarchy.assign(conn)
        rows = conn.execute("""
            SELECT coalesce(ci.name, '(no city)') AS city, count(*) AS places,
                   count(p.neighborhood_id) AS with_neighborhood, count(p.district_id) AS with_district
            FROM places p LEFT JOIN admin_areas ci ON ci.id = p.city_id
            WHERE p.state = 'active' GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    print(f"Assigned {updated} places.")
    for row in rows:
        print(f"  {row['city']}: {row['places']} places, {row['with_district']} with a district, "
              f"{row['with_neighborhood']} with a neighborhood")
    return 0


@command("names fetch", "Fetch multilingual names from Wikidata and OpenStreetMap for every place.",
         arg("--missing-only", action="store_true", help="only places with no alt names yet"))
def names_fetch(args) -> int:
    from . import names
    with db.connect(actor="names") as conn:
        places = conn.execute("""
            SELECT p.id, p.country_code AS country, p.wikidata_id, p.osm_ref,
                   (SELECT name FROM place_names WHERE place_id = p.id AND role = 'display') AS display,
                   (SELECT name FROM place_names WHERE place_id = p.id AND role = 'local') AS local
            FROM places p WHERE p.state = 'active'
              AND (NOT %s OR NOT EXISTS (SELECT 1 FROM place_names WHERE place_id = p.id AND role = 'alt'))
        """, (args.missing_only,)).fetchall()
        result = names.apply(conn, places)
    print(f"Stored {result['names']} names for {len(places)} places; "
          f"recorded {result['wikidata_ids']} Wikidata ids found through OpenStreetMap.")
    return 0
