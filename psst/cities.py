"""Setting up a city for research, in one step: its country's boundaries, its districts and neighborhoods,
and the research cells that cover it. Safe to run again; anything already done is skipped."""

from __future__ import annotations

import shlex
import subprocess

from . import hierarchy, research

SERVER_ROOT = "/www/wwwroot/psst"
WOF_URL = "https://data.geocode.earth/wof/dist/sqlite/whosonfirst-data-admin-{cc}-latest.db.bz2"


def _server(host: str, script: str) -> None:
    """Run a shell script on the server as its pipeline user would, streaming its output."""
    command = f"set -e; cd {SERVER_ROOT}/app; export PSST_CONFIG={SERVER_ROOT}/.env LC_ALL=C.UTF-8; {script}"
    subprocess.run(["ssh", host, command], check=True)


def psst_on_server(args: str) -> str:
    return f"/root/.local/bin/uv run --no-dev psst {args}"


def boundaries_loaded(conn, country: str) -> bool:
    return conn.execute("SELECT EXISTS (SELECT 1 FROM admin_areas WHERE country_code = %s AND source = 'wof') AS ok",
                        (country,)).fetchone()["ok"]


def load_country(host: str, country: str) -> None:
    """Download the country's Who's On First boundaries on the server and load them (large countries take a
    while: China is a 2.7 GB file)."""
    cc = country.lower()
    bundle = f"{SERVER_ROOT}/wof/whosonfirst-data-admin-{cc}-latest.db"
    _server(host, f"""
        mkdir -p {SERVER_ROOT}/wof
        if [ ! -s {bundle} ]; then
          echo "Downloading {country} boundaries..."
          curl -sfL {shlex.quote(WOF_URL.format(cc=cc))} -o {bundle}.bz2
          bunzip2 -f {bundle}.bz2
        fi
        echo "Loading {country} boundaries..."
        {psst_on_server(f"hierarchy load-wof {bundle} --country {country}")}
    """)


def similar_cities(conn, name: str, country: str | None, limit: int = 8) -> list[str]:
    rows = conn.execute("""
        SELECT DISTINCT ON (lower(name)) name, country_code, similarity(lower(name), lower(%s)) AS score
        FROM admin_areas WHERE level = 'city' AND NOT is_point AND (%s::text IS NULL OR country_code = %s)
          AND lower(name) %% lower(%s)
        ORDER BY lower(name), score DESC""", (name, country, country, name)).fetchall()
    rows.sort(key=lambda r: -r["score"])
    return [f"{r['name']} ({r['country_code']})" for r in rows[:limit]]


def add(conn_factory, host: str, name: str, country: str, reload: bool = False) -> dict:
    """Everything needed before `psst research claim --city NAME` works. Returns the planned city."""
    country = country.upper()
    with conn_factory() as conn:
        loaded = boundaries_loaded(conn, country)
    if reload or not loaded:
        load_country(host, country)
    with conn_factory() as conn:
        try:
            city = research.find_city(conn, name, country)
        except RuntimeError:
            suggestions = similar_cities(conn, name, country)
            raise RuntimeError(f"No city boundary called {name!r} in {country}."
                               + (f" Close matches: {', '.join(suggestions)}." if suggestions else
                                  " Check the spelling, or the country code.")) from None
    if country in hierarchy.OSM_LEVELS:
        bounds = f"{city['south']},{city['west']},{city['north']},{city['east']}"
        print(f"Loading OpenStreetMap districts and neighborhoods for {city['name']}...", flush=True)
        _server(host, psst_on_server(f"hierarchy load-osm --bounds {bounds} --country {country}"))
    if country in hierarchy.OSM_NEIGHBORHOOD_POINTS:
        bounds = f"{city['south']},{city['west']},{city['north']},{city['east']}"
        print(f"Loading OpenStreetMap neighborhoods for {city['name']}...", flush=True)
        _server(host, psst_on_server(f"hierarchy load-osm-points --bounds {bounds} --country {country}"))
    with conn_factory() as conn:
        result = research.plan(conn, city)
    return {"city": city, **result}
