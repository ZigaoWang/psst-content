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


@command("hierarchy load-osm-points", "Load OpenStreetMap's named neighborhood points inside a box.",
         arg("--bounds", required=True, help="south,west,north,east"), arg("--country", required=True))
def hierarchy_load_osm_points(args) -> int:
    from . import hierarchy
    bounds = tuple(float(x) for x in args.bounds.split(","))
    with db.connect(actor="hierarchy") as conn:
        count = hierarchy.load_osm_neighborhood_points(conn, bounds, args.country.upper())
    print(f"Loaded {count} OpenStreetMap neighborhood points.")
    return 0


@command("hierarchy index", "Split every boundary into small pieces for fast lookups (after loading).")
def hierarchy_index(args) -> int:
    from . import hierarchy
    with db.connect(actor="hierarchy") as conn:
        print(f"Indexed {hierarchy.index_parts(conn)} boundaries.")
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


@command("names fix-local", "Move local names that aren't in their country's language to alternative names, "
         "then look up the real local name.")
def names_fix_local(args) -> int:
    from . import names
    with db.connect(actor="names") as conn:
        wrong = names.misplaced_local_names(conn)
        for r in wrong:
            print(f"  {r['place_id']}  {r['name']} ({r['lang']}, in {r['country']})")
        fixed = names.fix_local_names(conn, wrong)
    print(f"Replaced {len(wrong)} local names in the wrong language (kept as alternative names); "
          f"found the real local name for {fixed}.")
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


@command("tags rename", "Change a tag's canonical name; the old name stays as an alias.",
         arg("tag"), arg("name"), RUN_ARG)
def tags_rename(args) -> int:
    from . import runs, tags
    with db.connect(actor="tags") as conn:
        runs.require(conn, args.run, "tagging")
        tags.rename(conn, args.tag, args.name)
    print(f"{args.tag} is now {args.name!r}.")
    return 0


@command("tags retype", "Change a tag's type.", arg("tag"),
         arg("type", help="person_or_group, event, era, theme, or movement"), RUN_ARG)
def tags_retype(args) -> int:
    from . import runs, tags
    if args.type not in tags.TYPES:
        raise RuntimeError(f"type must be one of {', '.join(tags.TYPES)}")
    with db.connect(actor="tags") as conn:
        runs.require(conn, args.run, "tagging")
        if not conn.execute("UPDATE tags SET type = %s WHERE id = %s RETURNING id", (args.type, args.tag)).fetchone():
            raise RuntimeError(f"No tag {args.tag}")
    print(f"{args.tag} is now a {args.type} tag.")
    return 0


@command("tags link", "Set or correct the Wikidata item a tag stands for.", arg("tag"), arg("qid"), RUN_ARG)
def tags_link(args) -> int:
    import re
    from . import runs
    if not re.fullmatch(r"Q[1-9][0-9]*", args.qid):
        raise RuntimeError("the Wikidata id looks like Q42")
    with db.connect(actor="tags") as conn:
        runs.require(conn, args.run, "tagging")
        other = conn.execute("SELECT id, canonical_name FROM tags WHERE wikidata_id = %s AND id <> %s",
                             (args.qid, args.tag)).fetchone()
        if other:
            raise RuntimeError(f"{args.qid} already belongs to {other['id']} ({other['canonical_name']}); merge the tags instead")
        if not conn.execute("UPDATE tags SET wikidata_id = %s WHERE id = %s RETURNING id", (args.qid, args.tag)).fetchone():
            raise RuntimeError(f"No tag {args.tag}")
    print(f"{args.tag} now stands for {args.qid}.")
    return 0


@command("tags alias", "Add or remove another way of writing a tag.",
         arg("tag"), arg("alias"), arg("--remove", action="store_true"), RUN_ARG)
def tags_alias(args) -> int:
    from . import runs, tags
    with db.connect(actor="tags") as conn:
        runs.require(conn, args.run, "tagging")
        (tags.remove_alias if args.remove else tags.add_alias)(conn, args.tag, args.alias)
    print(f"{'Removed' if args.remove else 'Added'} {args.alias!r} {'from' if args.remove else 'to'} {args.tag}.")
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


# Overview --------------------------------------------------------------------------------------------

@command("status", "What's in the database: facts by state, cells by state, open reports, tags.")
def status_command(args) -> int:
    with db.connect() as conn:
        facts = conn.execute("SELECT state, count(*) AS n FROM facts GROUP BY state ORDER BY state").fetchall()
        flagged = conn.execute("SELECT count(*) AS n FROM facts WHERE needs_review AND state <> 'retired'").fetchone()
        cells = conn.execute("""SELECT coalesce(ci.name, '(no city)') AS city, rc.state, count(*) AS n
                                FROM research_cells rc LEFT JOIN admin_areas ci ON ci.id = rc.city_id
                                GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
        other = conn.execute("""SELECT (SELECT count(*) FROM places WHERE state = 'active') AS places,
                                       (SELECT count(*) FROM reports WHERE state = 'open') AS reports,
                                       (SELECT count(*) FROM tags) AS tags,
                                       (SELECT content_version FROM publications WHERE channel = 'production'
                                        ORDER BY id DESC LIMIT 1) AS live""").fetchone()
    print(f"Places: {other['places']}. Facts: " + ", ".join(f"{r['n']} {r['state']}" for r in facts)
          + f". Flagged for review: {flagged['n']}. Open reports: {other['reports']}. Tags: {other['tags']}.")
    print(f"Production serves {other['live'] or 'nothing yet'}.")
    with db.connect() as conn:
        from . import research
        top = research.wanted(conn, 3)
    if top:
        names = [f"{r['city'] or 'near %s, %s' % (r['lat'], r['lon'])} ({r['views']} views)" for r in top]
        print("Most viewed empty areas: " + "; ".join(names) + ". See psst research wanted.")
    by_city: dict[str, list[str]] = {}
    for r in cells:
        by_city.setdefault(r["city"], []).append(f"{r['n']} {r['state']}")
    for city, parts in by_city.items():
        print(f"  {city}: " + ", ".join(parts))
    return 0


@command("places show", "Everything about one place: its names, pin, areas, and every story with its state.",
         arg("place"))
def places_show(args) -> int:
    with db.connect() as conn:
        place = conn.execute("""
            SELECT p.id, p.kind, p.coord_source_ref, round(ST_Y(p.geom)::numeric, 6) AS lat,
                   round(ST_X(p.geom)::numeric, 6) AS lon, p.h3_cell, ci.name AS city, nb.name AS neighborhood,
                   (SELECT string_agg(role || ' ' || lang || ': ' || name, '; ' ORDER BY role, lang)
                    FROM place_names WHERE place_id = p.id AND role <> 'alt') AS names
            FROM places p LEFT JOIN admin_areas ci ON ci.id = p.city_id LEFT JOIN admin_areas nb ON nb.id = p.neighborhood_id
            WHERE p.id = %s""", (args.place,)).fetchone()
        if not place:
            raise RuntimeError(f"No place {args.place}")
        facts = conn.execute("""SELECT id, state, category, veracity, headline, short FROM facts WHERE place_id = %s
                                ORDER BY position""", (args.place,)).fetchall()
    print(f"{place['id']}  {place['names']}\n{place['kind']} at {place['lat']}, {place['lon']} ({place['coord_source_ref']}), "
          f"{place['neighborhood'] or '-'}, {place['city'] or '-'}, cell {place['h3_cell']}\n")
    for f in facts:
        print(f"{f['id']}  [{f['state']}] {f['category']}, {f['veracity']}: {f['headline']}\n    {f['short']}")
    return 0


@command("places search", "Find places by name in any language, to check something isn't already in Psst.",
         arg("text"), arg("--json", action="store_true"))
def places_search(args) -> int:
    with db.connect() as conn:
        rows = conn.execute("""
            SELECT DISTINCT ON (p.id) p.id, dn.name, n.name AS matched, p.wikidata_id, p.osm_ref, p.h3_cell AS cell,
                   ci.name AS city, similarity(lower(n.name), lower(%(q)s)) AS score
            FROM place_names n JOIN places p ON p.id = n.place_id AND p.state = 'active'
            JOIN place_names dn ON dn.place_id = p.id AND dn.role = 'display'
            LEFT JOIN admin_areas ci ON ci.id = p.city_id
            WHERE lower(n.name) %% lower(%(q)s) OR n.name ILIKE '%%' || %(q)s || '%%'
               OR p.wikidata_id = %(q)s OR p.osm_ref = %(q)s
            ORDER BY p.id, score DESC""", {"q": args.text}).fetchall()
    rows.sort(key=lambda r: -r["score"])
    if args.json:
        print_json(rows[:20])
        return 0
    for r in rows[:20]:
        also = f" (as {r['matched']})" if r["matched"] != r["name"] else ""
        refs = " ".join(x for x in (r["wikidata_id"], r["osm_ref"]) if x)
        print(f"{r['id']}  {r['name']}{also}  {r['city'] or ''}  {refs}  cell {r['cell']}")
    if not rows:
        print("Nothing found.")
    return 0


# Cities ------------------------------------------------------------------------------------------------

@command("city add", "Set up a city for research: boundaries, districts, neighborhoods, and research cells.",
         arg("name", help="the city's English name, e.g. 'Hong Kong'"),
         arg("--country", required=True, help="ISO country code, e.g. HK"),
         arg("--reload", action="store_true", help="load the country's boundaries again (after changing how they're read)"))
def city_add(args) -> int:
    import subprocess
    from . import cities, publish
    # The heavy lifting runs on the server, so it gets this code first.
    subprocess.run(["sh", "server/deploy.sh"], cwd=ROOT, check=True, capture_output=True)
    result = cities.add(db.connect, publish.settings()["host"], args.name, args.country, args.reload)
    city = result["city"]
    print(f"{city['name']} ({city['country_code']}) is ready: {result['cells']} research cells, "
          f"{result['with_places']} with places already.")
    print(f'Research it with: uv run psst research claim --city "{city["name"]}"')
    return 0


@command("city list", "Every city set up for research, with its progress.")
def city_list(args) -> int:
    with db.connect() as conn:
        rows = conn.execute("""
            SELECT ci.name, ci.country_code, count(*) AS cells,
                   count(*) FILTER (WHERE rc.state = 'done') AS done,
                   count(*) FILTER (WHERE rc.state IN ('claimed', 'drafted', 'reviewed')) AS in_progress,
                   (SELECT count(*) FROM places p WHERE p.city_id = ci.id AND p.state = 'active') AS places
            FROM research_cells rc JOIN admin_areas ci ON ci.id = rc.city_id
            GROUP BY ci.id, ci.name, ci.country_code ORDER BY places DESC""").fetchall()
    for r in rows:
        print(f"{r['name']} ({r['country_code']}): {r['places']} places. {r['done']} of {r['cells']} cells done, "
              f"{r['in_progress']} in progress.")
    return 0


# Research ----------------------------------------------------------------------------------------------

WORK = ROOT / "work"


@command("research plan", "Create the research cells (H3 resolution 7) covering a city.",
         arg("city", help="the city's English name, e.g. London"), arg("--country", help="ISO code, if ambiguous"))
def research_plan(args) -> int:
    from . import research
    with db.connect(actor="research") as conn:
        city = research.find_city(conn, args.city, args.country and args.country.upper())
        result = research.plan(conn, city)
    print(f"{city['name']} ({city['country_code']}): {result['cells']} cells planned, "
          f"{result['with_places']} with places already.")
    return 0


@command("research cells", "List research cells and their state.",
         arg("--city", help="only this city (English name)"), arg("--state", help="only cells in this state"),
         arg("--json", action="store_true"))
def research_cells_command(args) -> int:
    from . import research
    with db.connect() as conn:
        rows = conn.execute(research.CELL_STATS + """
            WHERE (%s::text IS NULL OR ci.name = %s) AND (%s::text IS NULL OR rc.state = %s)
            ORDER BY ci.name, rc.state, rc.cell""", (args.city, args.city, args.state, args.state)).fetchall()
    if args.json:
        print_json(rows)
        return 0
    for r in rows:
        print(f"{r['cell']}  {r['state']:<8} {r['city'] or '':<16} {r['places']:>3} places  "
              f"{r['published']:>3} published  {r['pending']:>3} waiting")
    print(f"{len(rows)} cells.")
    return 0


@command("research wanted", "The areas app users looked at most while they were empty (last 90 days).",
         arg("--limit", type=int, default=20), arg("--json", action="store_true"))
def research_wanted(args) -> int:
    from . import research
    with db.connect() as conn:
        rows = research.wanted(conn, args.limit)
    if args.json:
        print_json(rows)
        return 0
    for r in rows:
        where = r["city"] or (f"no city boundary loaded ({r['country'] or 'unknown country'})")
        if r["plannedCells"]:
            plan = f"{r['openCells']} of {r['plannedCells']} cells open"
        elif r["city"]:
            plan = f'not planned: psst research plan "{r["city"]}" --country {r["country"]}'
        else:
            plan = "not planned: load boundaries for this country first (docs/DESIGN.md)"
        print(f"{r['cell']}  {r['views']:>5} views  near {r['lat']}, {r['lon']}  {where}  {plan}")
    if not rows:
        print("No requests in the last 90 days.")
    return 0


@command("research claim", "Claim a cell for a research run and write its brief to work/<cell>/.",
         arg("--cell", help="a specific cell (default: the most wanted open cell)"),
         arg("--city", help="pick from this city (English name)"), arg("--country"),
         arg("--near", metavar="LAT,LON", help="the open cell closest to this point, for a particular area"),
         arg("--no-sweep", action="store_true", help="skip the Wikipedia and OpenStreetMap leads"), RUN_ARG)
def research_claim(args) -> int:
    from . import research, runs
    with db.connect(actor="research", run=args.run) as conn:
        runs.require(conn, args.run, "research")
        city_id = research.find_city(conn, args.city, args.country)["id"] if args.city else None
        near = tuple(float(x) for x in args.near.split(",")) if args.near else None
        cell = research.claim(conn, args.run, args.cell, city_id, near)
    print(f"Claimed {cell['cell']} ({cell['city']}) until {cell['claimed_until']:%Y-%m-%d %H:%M %Z}. "
          f"It has {cell['places']} places and {cell['published']} published facts.")
    with db.connect() as conn:
        data = research.brief(conn, cell["cell"], sweep=not args.no_sweep)
    with db.connect(actor="research", run=args.run) as conn:
        leads = research.store_leads(conn, cell["cell"], data["candidates"], args.run)
        data["openLeads"] = research.open_leads(conn, cell["cell"])
    path = research.write_brief(data, WORK / cell["cell"])
    print(f"Brief: {path} ({len(data['existingPlaces'])} places nearby, {leads.get('open', 0)} leads to account for, "
          f"{leads.get('known', 0)} already in Psst).")
    print(f"Write the draft to {WORK / cell['cell'] / 'draft.json'} (format/draft.schema.json).")
    return 0


@command("research brief", "Write the brief for a cell again (for example after a failed sweep).",
         arg("cell"), arg("--no-sweep", action="store_true"))
def research_brief(args) -> int:
    from . import research
    with db.connect() as conn:
        data = research.brief(conn, args.cell, sweep=not args.no_sweep)
    print(research.write_brief(data, WORK / args.cell))
    return 0


@command("research release", "Give up a claim without submitting anything.", arg("cell"), RUN_ARG)
def research_release(args) -> int:
    from . import research
    with db.connect(actor="research", run=args.run) as conn:
        research.release(conn, args.cell)
    print(f"Released {args.cell}.")
    return 0


def _print_report(report, label: str) -> None:
    for error in report.errors:
        print(f"  error    {error}")
    for warning in report.warnings:
        print(f"  warning  {warning}")
    print(f"{label}: {len(report.errors)} errors, {len(report.warnings)} warnings.")


@command("draft check", "Check a research draft: format, writing rules, sources, tags, duplicates, coordinates.",
         arg("file"), arg("--offline", action="store_true", help="skip the coordinate lookups"))
def draft_check(args) -> int:
    from . import research
    draft = research.load_draft(Path(args.file))
    with db.connect() as conn:
        checked = research.check(conn, draft, online=not args.offline)
    _print_report(checked.report, args.file)
    return 0 if checked.report.ok else 1


@command("draft submit", "Store a checked draft. Everything is saved as a draft for review, never published.",
         arg("file"), RUN_ARG)
def draft_submit(args) -> int:
    from . import research, runs
    draft = research.load_draft(Path(args.file))
    with db.connect(actor="research", run=args.run, note="Submitted draft.") as conn:
        run = runs.require(conn, args.run, "research")
        checked = research.check(conn, draft)
        if not checked.report.ok:
            _print_report(checked.report, args.file)
            return 1
        counts = research.submit(conn, draft, run, checked)
    for warning in checked.report.warnings:
        print(f"  warning  {warning}")
    print(f"Stored {counts['places']} new places, {counts['facts']} draft facts, {counts['sources']} new sources "
          f"for {draft['cell']}." + (f" {counts['leads_left']} leads left for the next pass; the cell stays open."
                                      if counts["leads_left"] else ""))
    if counts["new_place_ids"]:
        try:
            with db.connect(actor="names") as conn:
                research.fetch_names(conn, counts["new_place_ids"])
        except RuntimeError as exc:
            print(f"Names not fetched ({exc}); run psst names fetch --missing-only later.")
    return 0


@command("sources check", "Check every cited link, and flag facts whose sources failed twice in a row.",
         arg("--limit", type=int, help="only check this many sources (for trying it out)"))
def sources_check(args) -> int:
    from . import links
    with db.connect() as conn:
        sources = links.live_sources(conn, args.limit)
    print(f"Checking {len(sources)} sources...")
    results = links.check(sources)
    with db.connect(actor="link-check") as conn:
        counts = links.record(conn, results)
    print(f"{counts['ok']} fine, {counts['failed']} failed, {counts['blocked']} refused the check. "
          f"Flagged {counts['flagged']} facts for review.")
    return 0


@command("fetch", "Read a web page as plain text, falling back to the Internet Archive when a site refuses.",
         arg("url"), arg("--max", type=int, default=20000, help="characters to show (default 20000)"),
         arg("--archive", action="store_true", help="read the newest archived copy directly"),
         arg("--links", action="store_true", help="also list every link on the page (a Wikipedia article's sources, say)"),
         arg("--run", default=os.environ.get("PSST_RUN"),
             help="the run reading it; reviews must read every source this way before approving"))
def fetch_command(args) -> int:
    from . import fetch, rules
    page = fetch.read(args.url, args.archive)
    print(fetch.render(page, args.max, args.links))
    ok = bool(page.text) and not page.text.startswith(("(Couldn't", "(The archived copy", "(This PDF"))
    if args.run:
        with db.connect() as conn:
            conn.execute("""INSERT INTO source_reads (run_id, url_key, ok) VALUES (%s, %s, %s)
                            ON CONFLICT (run_id, url_key) DO UPDATE SET ok = source_reads.ok OR EXCLUDED.ok,
                                read_at = now()""", (args.run, rules.read_key(args.url), ok))
    return 0 if ok else 1


# Review ------------------------------------------------------------------------------------------------

@command("review next", "Write the next facts to review (reported facts first, then drafts) to a file.",
         arg("--out", required=True), arg("--limit", type=int, default=25), arg("--cell"),
         arg("--city", help="only this city (English name)"),
         arg("--verify", action="store_true", help="published facts nobody has checked since the migration"),
         arg("--sample", action="store_true", help="a random selection, to estimate accuracy cheaply"),
         RUN_ARG)
def review_next(args) -> int:
    from . import review, runs
    with db.connect() as conn:
        runs.require(conn, args.run, "review")
        rows = review.queue(conn, args.limit, args.cell, args.run, args.city, args.verify, args.sample)
        waiting = conn.execute("SELECT count(*) FILTER (WHERE state = 'draft') AS drafts, "
                               "count(*) FILTER (WHERE needs_review AND state <> 'retired') AS flagged FROM facts").fetchone()
    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} facts to {args.out}. Waiting overall: {waiting['drafts']} drafts, "
          f"{waiting['flagged']} reported or flagged.")
    return 0


@command("review apply", "Apply review decisions from a JSON file (see CONTENT_GUIDE.md, Reviewing).",
         arg("file"), arg("--dry-run", action="store_true", help="only check the decisions"), RUN_ARG)
def review_apply(args) -> int:
    from . import review, runs
    decisions = json.loads(Path(args.file).read_text(encoding="utf-8"))
    with db.connect(actor="review", run=args.run, note="Review decision.") as conn:
        run = runs.require(conn, args.run, "review")
        report = review.check(conn, decisions, run)
        if not report.ok or args.dry_run:
            _print_report(report, args.file)
            return 0 if report.ok else 1
        counts = review.apply(conn, decisions, run)
    print(f"Approved {counts['approve']}, edited {counts['edit']}, rejected {counts['reject']}.")
    return 0


@command("review progress", "How much of each city's published content a skeptical review has verified.")
def review_progress(args) -> int:
    from . import review
    with db.connect() as conn:
        rows = review.progress(conn)
    for r in rows:
        print(f"{r['city']:<16} written by {r['written_by']:<18} {r['verified']:>5} of {r['facts']:>5} verified"
              + (f", {r['flagged']} flagged" if r["flagged"] else ""))
    return 0


@command("review flag", "Send a published fact back for review, for example after finding a problem yourself.",
         arg("fact"), arg("--reason", required=True, help="what looks wrong (kept in the fact's history)"))
def review_flag(args) -> int:
    with db.connect(actor="review", note=f"Flagged: {args.reason}") as conn:
        row = conn.execute("UPDATE facts SET needs_review = true WHERE id = %s AND state <> 'retired' RETURNING id",
                           (args.fact,)).fetchone()
    if not row:
        raise RuntimeError(f"No live fact {args.fact}")
    print(f"Flagged {args.fact}. It stays published until a review decides; psst review next lists it first.")
    return 0


@command("reports list", "Show open problem reports from the app.", arg("--json", action="store_true"))
def reports_list(args) -> int:
    with db.connect() as conn:
        rows = conn.execute("""
            SELECT r.id, r.fact_id, r.reason, r.message, r.app_version, r.created_at, f.headline, n.name AS place
            FROM reports r JOIN facts f ON f.id = r.fact_id
            JOIN place_names n ON n.place_id = f.place_id AND n.role = 'display'
            WHERE r.state = 'open' ORDER BY r.created_at""").fetchall()
    if args.json:
        print_json(rows)
        return 0
    for r in rows:
        print(f"#{r['id']} {r['created_at']:%Y-%m-%d} {r['reason']:<9} {r['fact_id']} {r['place']}: {r['headline']}")
        if r["message"]:
            print(f"      {r['message']}")
    print(f"{len(rows)} open reports. Reported facts come first in psst review next.")
    return 0


@command("coverage", "Build the coverage map and upload it to /coverage/ (use --out to only write it).",
         arg("--out", help="write the page here instead of uploading"))
def coverage_command(args) -> int:
    from . import coverage, publish
    with db.connect() as conn:
        html = coverage.build(conn)
    path = coverage.write(html, Path(args.out) if args.out else ROOT / "export" / "coverage" / "index.html")
    if args.out:
        print(f"Wrote {path}.")
        return 0
    config = publish.settings()
    coverage.upload(config["host"], path)
    print(f"Uploaded to {config['url']}/coverage/.")
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
    source = Path(args.source)
    _create_database(args.database, backup.schema_version(source))
    backup.restore(_database_url(args.database), source)
    applied = db.migrate(_database_url(args.database))
    print(f"Restored into {args.database}" + (f", then applied {', '.join(applied)}" if applied else "")
          + ". See docs/RESTORE.md for switching over.")
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
        _create_database(name, backup.schema_version(source))
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


def _create_database(name: str, migrations: list[str] | None = None) -> None:
    from . import backup
    backup.sudo_postgres(f"CREATE DATABASE {name} OWNER psst ENCODING 'UTF8' TEMPLATE template0")
    backup.sudo_postgres("CREATE EXTENSION postgis; CREATE EXTENSION pg_trgm; CREATE EXTENSION unaccent;", name)
    backup.sudo_postgres(f"REVOKE ALL ON DATABASE {name} FROM PUBLIC; GRANT CONNECT ON DATABASE {name} TO psst, psst_api;")
    db.migrate(_database_url(name), migrations)


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
        # A reviewed cell is done once everything in it is published or rejected.
        conn.execute("""UPDATE research_cells rc SET state = 'done' WHERE rc.state = 'reviewed' AND NOT EXISTS (
                            SELECT 1 FROM facts f JOIN places p ON p.id = f.place_id
                            WHERE p.h3_cell = rc.cell AND f.state IN ('draft', 'reviewed'))""")
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
