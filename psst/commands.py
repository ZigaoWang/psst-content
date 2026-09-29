"""Every `psst` command beyond the basics in cli.py. Each wraps a module that does the actual work."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import db
from .cli import ROOT, arg, command, print_json


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


# Runs ------------------------------------------------------------------------------------------------

RUN_ARG = arg("--run", default=os.environ.get("PSST_RUN"), help="the run doing this work (default: $PSST_RUN)")


@command("run start", "Start a pipeline run and print its id. Every change is recorded against a run.",
         arg("--kind", required=True, help="research, review, tagging, verify, publish, or manual"),
         arg("--model", default=os.environ.get("PSST_MODEL"),
             help="the model doing the work, e.g. claude-sonnet-5-5 (default: $PSST_MODEL; omit for a person)"),
         arg("--cell", help="the research cell, for research and review runs"),
         arg("--notes", help="anything worth knowing about this run"))
def run_start(args) -> int:
    from . import runs
    with db.connect() as conn:
        print(runs.start(conn, args.kind, args.model, args.cell, args.notes))
    return 0


@command("run finish", "Mark a run finished.", arg("run_id"))
def run_finish(args) -> int:
    from . import runs
    with db.connect() as conn:
        runs.finish(conn, args.run_id)
    return 0


# Tags ------------------------------------------------------------------------------------------------

@command("tags search", "Find tags by name or alias.", arg("text"), arg("--json", action="store_true"))
def tags_search(args) -> int:
    from . import tags
    with db.connect() as conn:
        found = tags.find(conn, args.text)
    if args.json:
        print_json(found)
    for t in found if not args.json else []:
        print(f"{t['id']}  {t['canonical_name']}  ({t['type']}, {t['places']} places, "
              f"{t['wikidata_id'] or 'no Wikidata'}, match {t['score']:.2f})")
    return 0


@command("tags list", "List every tag with how many places use it.", arg("--json", action="store_true"))
def tags_list(args) -> int:
    with db.connect() as conn:
        rows = conn.execute("""
            SELECT t.id, t.canonical_name, t.type, t.wikidata_id,
                   (SELECT string_agg(label, ', ' ORDER BY label) FROM tag_labels l
                    WHERE l.tag_id = t.id AND NOT l.is_canonical) AS aliases,
                   (SELECT count(DISTINCT f.place_id) FROM fact_tags ft JOIN facts f ON f.id = ft.fact_id
                    WHERE ft.tag_id = t.id AND f.state <> 'retired') AS places
            FROM tags t ORDER BY places DESC, canonical_name""").fetchall()
    if args.json:
        print_json(rows)
        return 0
    for t in rows:
        aliases = f"  aka {t['aliases']}" if t["aliases"] else ""
        print(f"{t['id']}  {t['places']:>4}  {t['type']:<15} {t['canonical_name']}{aliases}")
    return 0


@command("tags wikidata", "Look up Wikidata items for a would-be tag.", arg("text"))
def tags_wikidata(args) -> int:
    import urllib.parse
    from . import net
    payload = net.fetch_json("https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&language=en"
                             "&limit=7&search=" + urllib.parse.quote(args.text))
    for item in payload.get("search", []):
        print(f"{item['id']}  {item.get('label', '')}: {item.get('description', '')}")
    return 0


@command("tags propose", "Add a tag, or get the existing one it duplicates. Prints the tag id.",
         arg("name", help="the canonical name, e.g. 'The Beatles'"),
         arg("--type", required=True, help="person_or_group, event, era, theme, or movement"),
         arg("--wikidata", help="the Wikidata item, e.g. Q1299 (use psst tags wikidata to find it)"),
         arg("--alias", action="append", default=[], help="another way people write it (repeatable)"),
         arg("--description", help="one line saying what the tag covers"),
         arg("--distinct-from", action="append", default=[],
             help="a similar existing tag id you checked is a different thing (repeatable)"),
         RUN_ARG)
def tags_propose(args) -> int:
    from . import net, names as place_names, runs, tags
    with db.connect(actor="tags") as conn:
        runs.require(conn, args.run)
        labels = {}
        if args.wikidata:
            entity = net.wikidata_entities([args.wikidata], props="labels").get(args.wikidata, {})
            for lang, codes in place_names.WIKIDATA_LANGUAGES.items():
                for code in codes:
                    if code in entity.get("labels", {}) and lang != "en":
                        labels[lang] = entity["labels"][code]["value"]
                        break
        result = tags.propose(conn, args.name, args.type, args.wikidata, tuple(args.alias), args.description,
                              tuple(args.distinct_from), args.run, labels)
    if result.status == "similar":
        print("Not created: similar tags already exist. Use one of these, or pass --distinct-from for each "
              "one that is truly a different thing:")
        for t in result.similar:
            print(f"  {t['id']}  {t['canonical_name']}  ({t['type']}, {t['wikidata_id'] or 'no Wikidata'}, "
                  f"match {t['score']:.2f})")
        return 2
    print(f"{result.tag['id']}  {result.tag['canonical_name']}  ({result.status})")
    return 0


@command("tags merge", "Fold one tag into another.", arg("source"), arg("target"), RUN_ARG)
def tags_merge(args) -> int:
    from . import runs, tags
    with db.connect(actor="tags") as conn:
        runs.require(conn, args.run)
        tags.merge(conn, args.source, args.target)
    print(f"Merged {args.source} into {args.target}.")
    return 0


@command("tags audit", "List pairs of tags that look like duplicates.")
def tags_audit(args) -> int:
    from . import tags
    with db.connect() as conn:
        pairs = tags.audit(conn)
    for p in pairs:
        print(f"{p['score']:.2f}  {p['a']} {p['a_name']}  <->  {p['b']} {p['b_name']}")
    print(f"{len(pairs)} pairs to look at.")
    return 0


@command("tags work", "Write facts that need tags to a file for a tagging agent.",
         arg("--out", required=True), arg("--city", help="only this city (English name)"),
         arg("--district", help="only this district (English name)"),
         arg("--untagged", action="store_true", help="only facts with no tags yet"))
def tags_work(args) -> int:
    import json
    with db.connect() as conn:
        rows = conn.execute("""
            SELECT f.id, pn.name AS place, ci.name AS city, di.name AS district, f.category, f.veracity,
                   f.headline, f.short, f.long,
                   coalesce((SELECT array_agg(tag_id) FROM fact_tags WHERE fact_id = f.id), '{}') AS tags
            FROM facts f JOIN places p ON p.id = f.place_id
            JOIN place_names pn ON pn.place_id = p.id AND pn.role = 'display'
            LEFT JOIN admin_areas ci ON ci.id = p.city_id LEFT JOIN admin_areas di ON di.id = p.district_id
            WHERE f.state <> 'retired' AND (%(city)s::text IS NULL OR ci.name = %(city)s)
              AND (%(district)s::text IS NULL OR di.name = %(district)s)
              AND (NOT %(untagged)s OR NOT EXISTS (SELECT 1 FROM fact_tags WHERE fact_id = f.id))
            ORDER BY ci.name, di.name, pn.name, f.position""",
                            {"city": args.city, "district": args.district, "untagged": args.untagged}).fetchall()
    with open(args.out, "w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Wrote {len(rows)} facts to {args.out}.")
    return 0


@command("tags apply", "Set tags on facts from a JSON file: {\"fa_...\": [\"tg_...\", ...], ...}.",
         arg("file"), RUN_ARG)
def tags_apply(args) -> int:
    import json
    from . import runs, tags
    assignments = json.loads(Path(args.file).read_text(encoding="utf-8"))
    with db.connect(actor="tags", run=args.run) as conn:
        runs.require(conn, args.run, "tagging")
        tags.assign_many(conn, {fact: list(tag_ids) for fact, tag_ids in assignments.items()})
    print(f"Tagged {len(assignments)} facts.")
    return 0


# Backups ---------------------------------------------------------------------------------------------

@command("backup export", "Write the database as sorted CSV files (the nightly GitHub backup).",
         arg("--to", required=True, help="directory to write into"))
def backup_export(args) -> int:
    from . import backup
    with db.connect() as conn:
        digests = backup.export(conn, Path(args.to))
    print(f"Exported {len(digests)} tables to {args.to}.")
    return 0


@command("backup restore", "Restore a text backup into a new, empty database (run on the VPS as root).",
         arg("--from", dest="source", required=True, help="a backup directory (a psst-db-backup checkout)"),
         arg("--database", required=True, help="name of the database to create, e.g. psst_restored"))
def backup_restore(args) -> int:
    from . import backup
    _create_database(args.database)
    backup.restore(_database_url(args.database), Path(args.source))
    print(f"Restored into {args.database}. See docs/RESTORE.md for switching over.")
    return 0


@command("backup test", "Restore the latest backup into a scratch database and prove it matches (VPS only).",
         arg("--from", dest="source", default="/www/wwwroot/psst/backup/psst-db-backup",
             help="the backup to test (default: the nightly GitHub backup checkout)"))
def backup_test(args) -> int:
    import hashlib
    import tempfile
    from . import backup
    name = "psst_restore_test"
    source = Path(args.source)
    expected = {t: hashlib.sha256((source / f"{t}.csv").read_bytes()).hexdigest() for t, _ in backup.TABLES}
    backup.sudo_postgres(f"DROP DATABASE IF EXISTS {name}")
    try:
        _create_database(name)
        backup.restore(_database_url(name), source)
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(_database_url(name), row_factory=dict_row) as conn, \
                tempfile.TemporaryDirectory() as scratch:
            actual = backup.export(conn, Path(scratch))
            counts = {t: conn.execute(f"SELECT count(*) AS n FROM psst.{t}").fetchone()["n"] for t, _ in backup.TABLES}
    finally:
        backup.sudo_postgres(f"DROP DATABASE IF EXISTS {name}")
    mismatched = [t for t in expected if expected[t] != actual.get(t)]
    for table, _ in backup.TABLES:
        print(f"  {table:<18} {counts[table]:>8} rows  {'ok' if table not in mismatched else 'MISMATCH'}")
    if mismatched:
        print(f"Restore test FAILED: {', '.join(mismatched)} differ after restoring.")
        return 1
    print("Restore test passed: every table restored byte for byte.")
    return 0


def _database_url(name: str) -> str:
    import urllib.parse
    base = db.conninfo()
    if base.startswith("postgresql://"):
        parsed = urllib.parse.urlsplit(base)
        return urllib.parse.urlunsplit(parsed._replace(path=f"/{name}"))
    return base.replace("dbname=psst", f"dbname={name}")


def _create_database(name: str) -> None:
    from . import backup
    backup.sudo_postgres(f"CREATE DATABASE {name} OWNER psst ENCODING 'UTF8' TEMPLATE template0")
    backup.sudo_postgres("CREATE EXTENSION postgis; CREATE EXTENSION pg_trgm; CREATE EXTENSION unaccent;", name)
    backup.sudo_postgres(f"REVOKE ALL ON DATABASE {name} FROM PUBLIC; GRANT CONNECT ON DATABASE {name} TO psst, psst_api;")
    db.migrate(_database_url(name))


# Publishing ------------------------------------------------------------------------------------------

@command("publish", "Publish reviewed facts: export, stage, check staging, promote to production.",
         arg("--allow-shrink", metavar="REASON", help="allow places or facts to drop by more than 2 percent"),
         arg("--only-staging", action="store_true", help="stop after checking staging"),
         arg("--no-new-facts", action="store_true", help="re-export what's published without publishing reviewed facts"))
def publish_command(args) -> int:
    from . import export, publish, runs
    config = publish.settings()
    with db.connect(actor="publish") as conn:
        run = runs.start(conn, "publish", None, notes=args.allow_shrink and f"Allowed to shrink: {args.allow_shrink}")
    with db.connect(actor="publish", run=run) as conn:
        reviewed = [] if args.no_new_facts else [r["id"] for r in conn.execute(
            "SELECT id FROM facts WHERE state = 'reviewed' AND NOT needs_review ORDER BY id")]
        result = export.build(conn, ROOT / "export", include=reviewed)
    print(f"Exported {result.places} places and {result.facts} facts ({len(reviewed)} newly published) "
          f"as {result.manifest['contentVersion']}.")
    publish.upload_staging(config["host"], result.directory)
    print(f"Uploaded to staging. Checking {config['url']}/content/staging/ ...")
    problems = publish.check_staging(config["url"], args.allow_shrink)
    with db.connect(actor="publish", run=run) as conn:
        conn.execute("INSERT INTO publications (channel, content_version, manifest, places, facts, run_id, notes) "
                     "VALUES ('staging', %s, %s, %s, %s, %s, %s)",
                     (result.manifest["contentVersion"], json.dumps(result.manifest), result.places, result.facts,
                      run, "; ".join(problems) or "Checks passed."))
    if problems:
        print("Staging check FAILED. Production is unchanged.")
        for problem in problems[:40]:
            print(f"  {problem}")
        return 1
    print("Staging check passed.")
    if args.only_staging:
        return 0
    manifest = publish.promote(config["host"])
    with db.connect(actor="publish", run=run, note="Published through staging.") as conn:
        conn.execute("UPDATE facts SET state = 'published', published_at = now() "
                     "WHERE id = ANY(%s) AND state = 'reviewed'", (reviewed,))
        conn.execute("INSERT INTO publications (channel, content_version, manifest, places, facts, run_id) "
                     "VALUES ('production', %s, %s, %s, %s, %s)",
                     (manifest["contentVersion"], json.dumps(manifest), result.places, result.facts, run))
        runs.finish(conn, run)
    print(f"Promoted {manifest['contentVersion']} to production.")
    return 0


@command("rollback", "Point production back at an earlier version.",
         arg("--to", dest="version", help="a content version (default: the one before the current)"))
def rollback_command(args) -> int:
    from . import publish
    version = publish.rollback(publish.settings()["host"], args.version)
    print(f"Production now serves {version}.")
    return 0


@command("prune", "Delete pack files no manifest still needs (keeps the last 10 versions).")
def publish_prune(args) -> int:
    from . import publish
    print(f"Removed {publish.prune(publish.settings()['host'])} old pack files.")
    return 0


@command("bundle", "Copy production's content into the app as the snapshot it ships with.",
         arg("--app", default=os.environ.get("PSST_APP_DIR", str(ROOT.parent / "psst-map")),
             help="the psst-map repository (default: ../psst-map)"))
def bundle_command(args) -> int:
    from . import publish
    manifest = publish.bundle(publish.settings()["url"], Path(args.app))
    print(f"Bundled {manifest['contentVersion']} ({manifest['counts']['places']} places, "
          f"{manifest['counts']['facts']} facts) into {args.app}/Content/v2. Rebuild the app.")
    return 0
