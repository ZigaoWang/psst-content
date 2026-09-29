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
