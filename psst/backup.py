"""Plain-text backups: every table as a CSV sorted by its primary key, so git stores only what changed
from one day to the next, and any day can be restored. See docs/RESTORE.md.

Boundaries are large and can be reloaded from Who's On First and OpenStreetMap, so only the boundaries
places and research cells use are included, with their parents, so every foreign key holds on restore.
Countries and regions are kept as their bounding boxes (their full shapes run to megabytes each); reloading
boundaries after a restore puts the real shapes back (docs/RESTORE.md).

Each backup records the migrations it was taken with (SCHEMA_VERSION). A restore recreates exactly that
schema, loads the data, and only then migrates forward.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import psycopg

# Tables in load order (parents before children), with the columns that sort them.
TABLES = [
    ("pipeline_runs", "id"),
    ("admin_areas", "id"),
    ("admin_area_names", "area_id, lang"),
    ("places", "id"),
    ("place_names", "place_id, role, lang"),
    ("legacy_place_ids", "legacy_id"),
    ("sources", "id"),
    ("facts", "id"),
    ("fact_sources", "fact_id, source_id"),
    ("fact_events", "id"),
    ("tags", "id"),
    ("tag_labels", "normalized"),
    ("tag_names", "tag_id, lang"),
    ("fact_tags", "fact_id, tag_id"),
    ("research_cells", "cell"),
    ("reports", "id"),
    ("demand", "cell, day"),
    ("publications", "id"),
]
USED_AREAS = """WITH RECURSIVE used(id) AS (
        SELECT id FROM (SELECT unnest(ARRAY[region_id, city_id, district_id, neighborhood_id]) FROM psst.places
                        UNION SELECT city_id FROM psst.research_cells) seed(id) WHERE id IS NOT NULL
        UNION SELECT a.parent_id FROM psst.admin_areas a JOIN used u ON a.id = u.id WHERE a.parent_id IS NOT NULL)
    SELECT id FROM used"""
SERIALS = ["fact_events", "reports", "publications"]


def _columns(conn, table: str) -> list[str]:
    rows = conn.execute("""SELECT column_name, data_type FROM information_schema.columns
                           WHERE table_schema = 'psst' AND table_name = %s AND is_generated = 'NEVER'
                           ORDER BY ordinal_position""", (table,)).fetchall()
    return [r["column_name"] if isinstance(r, dict) else r[0] for r in rows]


def _select(conn, table: str, order: str) -> str:
    columns = []
    for name in _columns(conn, table):
        # Geometry goes out as EWKT text, which COPY reads straight back into a geometry column.
        if name != "geom":
            columns.append(name)
        elif table == "admin_areas":
            columns.append("ST_AsEWKT(CASE WHEN level IN ('country', 'region') THEN ST_Envelope(geom) ELSE geom END) AS geom")
        else:
            columns.append(f"ST_AsEWKT({name}) AS {name}")
    where = f" WHERE id IN ({USED_AREAS})" if table == "admin_areas" else ""
    if table == "admin_area_names":
        where = f" WHERE area_id IN ({USED_AREAS})"
    return f"SELECT {', '.join(columns)} FROM psst.{table}{where} ORDER BY {order}"


def export(conn, target: Path) -> dict[str, str]:
    """Write every table to target/<table>.csv. Returns each file's SHA-256."""
    target.mkdir(parents=True, exist_ok=True)
    digests = {}
    for table, order in TABLES:
        path = target / f"{table}.csv"
        with path.open("wb") as out, conn.cursor().copy(
                f"COPY ({_select(conn, table, order)}) TO STDOUT WITH (FORMAT csv, HEADER true)") as copy:
            for chunk in copy:
                out.write(chunk)
        digests[table] = hashlib.sha256(path.read_bytes()).hexdigest()
    migrations = conn.execute("SELECT version FROM public.psst_schema_migrations ORDER BY version").fetchall()
    (target / "SCHEMA_VERSION").write_text("\n".join(r["version"] if isinstance(r, dict) else r[0]
                                                      for r in migrations) + "\n")
    return digests


def schema_version(source: Path) -> list[str]:
    """The migrations a backup was taken with."""
    return [line.strip() for line in (source / "SCHEMA_VERSION").read_text().splitlines() if line.strip()]


def restore(conninfo: str, source: Path) -> None:
    """Load a text backup into an empty database that already has the schema (psst db migrate)."""
    with psycopg.connect(conninfo) as conn:
        with conn.transaction():
            # Our triggers would log a fresh event for every fact; the backup already has the real history.
            # Foreign keys stay on, so a restore that succeeds is also a consistent one.
            for table, _ in TABLES:
                conn.execute(f"ALTER TABLE psst.{table} DISABLE TRIGGER USER")
            for table, _ in TABLES:
                path = source / f"{table}.csv"
                header = path.open(encoding="utf-8").readline().strip()
                with path.open("rb") as data, conn.cursor().copy(
                        f"COPY psst.{table} ({header}) FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
                    while chunk := data.read(1 << 20):
                        copy.write(chunk)
            for table in SERIALS:
                conn.execute(f"SELECT setval(pg_get_serial_sequence('psst.{table}', 'id'), "
                             f"coalesce((SELECT max(id) FROM psst.{table}), 0) + 1, false)")
            for table, _ in TABLES:
                conn.execute(f"ALTER TABLE psst.{table} ENABLE TRIGGER USER")


def sudo_postgres(sql: str, database: str = "postgres") -> None:
    subprocess.run(["sudo", "-u", "postgres", "psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", database, "-c", sql],
                   check=True, cwd="/tmp")
