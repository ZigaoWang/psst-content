"""Skeptical review: every draft, and every published fact someone reported, is checked by a different
run before it can be published (again). A review approves, edits, or rejects each fact, with notes on
what was checked."""

from __future__ import annotations

from . import rules, tags

DECISIONS = ("approve", "edit", "reject")
EDITABLE = ("category", "veracity", "headline", "short", "long", "sources", "tags")
MIN_NOTES = 20

QUEUE = """
    SELECT f.id, f.state, f.needs_review, f.category, f.veracity, f.headline, f.short, f.long,
           f.researched_at, f.researched_by, f.research_run, f.review_notes, p.h3_cell AS cell,
           json_build_object('id', p.id, 'name', dn.name, 'localName', ln.name, 'kind', p.kind,
                             'location', p.coord_source_ref, 'lat', round(ST_Y(p.geom)::numeric, 6),
                             'lon', round(ST_X(p.geom)::numeric, 6), 'neighborhood', nb.name, 'city', ci.name,
                             'otherFacts', (SELECT coalesce(json_agg(o.headline ORDER BY o.position), '[]')
                                            FROM facts o WHERE o.place_id = p.id AND o.id <> f.id
                                              AND o.state <> 'retired')) AS place,
           (SELECT coalesce(json_agg(json_build_object('url', s.url, 'title', s.title, 'publisher', s.publisher,
                                                       'lastCheck', s.last_check_status, 'failedChecks', s.failed_checks)
                                     ORDER BY fs.position), '[]')
            FROM fact_sources fs JOIN sources s ON s.id = fs.source_id WHERE fs.fact_id = f.id) AS sources,
           (SELECT e.note FROM fact_events e WHERE e.fact_id = f.id AND 'flagged' = ANY(e.changes)
            ORDER BY e.id DESC LIMIT 1) AS flagged_because,
           (SELECT coalesce(json_agg(json_build_object('id', t.id, 'name', t.canonical_name) ORDER BY t.canonical_name), '[]')
            FROM fact_tags ft JOIN tags t ON t.id = ft.tag_id WHERE ft.fact_id = f.id) AS tags,
           (SELECT coalesce(json_agg(json_build_object('id', r.id, 'reason', r.reason, 'message', r.message,
                                                       'at', r.created_at) ORDER BY r.created_at), '[]')
            FROM reports r WHERE r.fact_id = f.id AND r.state = 'open') AS reports
    FROM facts f JOIN places p ON p.id = f.place_id
    JOIN place_names dn ON dn.place_id = p.id AND dn.role = 'display'
    LEFT JOIN place_names ln ON ln.place_id = p.id AND ln.role = 'local'
    LEFT JOIN admin_areas nb ON nb.id = p.neighborhood_id LEFT JOIN admin_areas ci ON ci.id = p.city_id
    WHERE CASE WHEN %(verify)s THEN f.state = 'published' AND f.last_verified_at IS NULL AND NOT f.needs_review
               ELSE f.state = 'draft' OR (f.needs_review AND f.state IN ('reviewed', 'published')) END
      AND (%(cell)s::text IS NULL OR p.h3_cell = %(cell)s)
      AND (%(city)s::text IS NULL OR ci.name = %(city)s)
      AND (%(run)s::text IS NULL OR f.research_run <> %(run)s)
    ORDER BY CASE WHEN %(sample)s THEN random() END, f.needs_review DESC, f.state = 'draft' DESC, nb.name, dn.name,
             f.position
    LIMIT %(limit)s"""


def queue(conn, limit: int, cell: str | None = None, reviewer_run: str | None = None, city: str | None = None,
          verify: bool = False, sample: bool = False) -> list[dict]:
    """Facts waiting for review: reported and flagged facts first, then drafts, grouped by neighborhood and
    place. With `verify`, published facts nobody has checked since they were migrated. A run never gets
    its own research. With `sample`, a random selection instead,
    to estimate how accurate a batch of content is without checking all of it."""
    return conn.execute(QUEUE, {"cell": cell, "city": city, "run": reviewer_run,
                                "verify": verify, "sample": sample, "limit": limit}).fetchall()


def progress(conn) -> list[dict]:
    """How much of each city's published content has been verified by a skeptical review."""
    return conn.execute("""
        SELECT coalesce(ci.name, '(no city)') AS city, f.researched_by AS written_by,
               count(*) AS facts, count(f.last_verified_at) AS verified,
               count(*) FILTER (WHERE f.needs_review) AS flagged
        FROM facts f JOIN places p ON p.id = f.place_id LEFT JOIN admin_areas ci ON ci.id = p.city_id
        WHERE f.state = 'published' GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()


def check(conn, decisions: list[dict], run: dict) -> rules.Report:
    report = rules.Report()
    if not isinstance(decisions, list):
        report.error("decisions", "must be a list")
        return report
    fact_ids = [d.get("fact") for d in decisions if isinstance(d, dict)]
    facts = {r["id"]: r for r in conn.execute("""
        SELECT f.*, (SELECT coalesce(array_agg(tag_id), '{}') FROM fact_tags WHERE fact_id = f.id) AS tag_ids,
               (SELECT coalesce(json_agg(json_build_object('url', s.url, 'title', s.title, 'publisher', s.publisher)
                                         ORDER BY fs.position), '[]')
                FROM fact_sources fs JOIN sources s ON s.id = fs.source_id WHERE fs.fact_id = f.id) AS source_list
        FROM facts f WHERE f.id = ANY(%s)""", (fact_ids,))}
    known_tags = {r["id"] for r in conn.execute("SELECT id FROM tags")}
    seen = set()
    for index, decision in enumerate(decisions):
        where = f"decisions[{index}]"
        if not isinstance(decision, dict):
            report.error(where, "must be an object")
            continue
        fact_id = decision.get("fact")
        where = f"{where} ({fact_id})"
        fact = facts.get(fact_id)
        if not fact:
            report.error(where, "no such fact")
            continue
        if fact_id in seen:
            report.error(where, "decided twice")
        seen.add(fact_id)
        unverified = fact["state"] == "published" and fact["last_verified_at"] is None
        if not (fact["state"] == "draft" or fact["needs_review"] or unverified):
            report.error(where, f"is {fact['state']} and not flagged; nothing to review")
        if fact["research_run"] == run["id"]:
            report.error(where, "a run can't review its own research")
        choice = decision.get("decision")
        if choice not in DECISIONS:
            report.error(where, f"decision must be one of {', '.join(DECISIONS)}")
        notes = decision.get("notes")
        if not isinstance(notes, str) or len(notes.strip()) < MIN_NOTES:
            report.error(where, "notes must say what you checked (at least a sentence)")
        changes = decision.get("changes") or {}
        unknown = set(changes) - set(EDITABLE)
        if unknown:
            report.error(where, f"can't change {', '.join(sorted(unknown))}")
        if choice == "edit" and not changes:
            report.error(where, "an edit needs changes")
        if choice != "edit" and changes:
            report.error(where, "only an edit can have changes")
        if choice == "reject" and not decision.get("reason"):
            report.error(where, "a rejection needs a reason (shown in the fact's history)")
        if choice in ("approve", "edit"):
            merged = {"category": fact["category"], "veracity": fact["veracity"], "headline": fact["headline"],
                      "short": fact["short"], "long": fact["long"], "sources": fact["source_list"]}
            merged.update({k: v for k, v in changes.items() if k != "tags"})
            rules.check_fact(report, where, merged)
            for tag_id in changes.get("tags", []):
                if tag_id not in known_tags:
                    report.error(where, f"{tag_id} is not a tag")
    return report


def apply(conn, decisions: list[dict], run: dict) -> dict[str, int]:
    """Apply checked decisions in one transaction. `conn` must carry the review run as psst.run."""
    reviewer = run["model"] or run["operator"]
    counts = dict.fromkeys(DECISIONS, 0)
    for decision in decisions:
        fact_id, choice, notes = decision["fact"], decision["decision"], decision["notes"].strip()
        changes = decision.get("changes") or {}
        if choice == "reject":
            conn.execute("""UPDATE facts SET state = 'retired', retired_at = now(), retire_reason = %s,
                            needs_review = false, review_notes = %s, reviewed_by = %s, review_run = %s,
                            reviewed_at = now() WHERE id = %s""",
                         (decision["reason"], notes, reviewer, run["id"], fact_id))
        else:
            fields = {k: changes[k] for k in ("category", "veracity", "headline", "short", "long") if k in changes}
            assignments = "".join(f", {k} = %({k})s" for k in fields)
            conn.execute(f"""UPDATE facts SET state = CASE WHEN state = 'draft' THEN 'reviewed' ELSE state END,
                             reviewed_at = now(), reviewed_by = %(by)s, review_run = %(run)s, review_notes = %(notes)s,
                             last_verified_at = now(), needs_review = false{assignments} WHERE id = %(id)s""",
                         {**fields, "by": reviewer, "run": run["id"], "notes": notes, "id": fact_id})
            if "sources" in changes:
                _replace_sources(conn, fact_id, changes["sources"])
            if "tags" in changes:
                tags.assign(conn, fact_id, changes["tags"])
        conn.execute("""UPDATE reports SET state = 'resolved', resolved_at = now(), resolution = %s
                        WHERE fact_id = %s AND state = 'open'""", (f"{choice}: {notes}", fact_id))
        counts[choice] += 1
    # A cell is reviewed once none of its facts are drafts.
    conn.execute("""UPDATE research_cells rc SET state = 'reviewed' WHERE rc.state = 'drafted' AND NOT EXISTS (
                        SELECT 1 FROM facts f JOIN places p ON p.id = f.place_id
                        WHERE p.h3_cell = rc.cell AND f.state = 'draft')""")
    return counts


def _replace_sources(conn, fact_id: str, sources: list[dict]) -> None:
    from . import ids
    conn.execute("DELETE FROM fact_sources WHERE fact_id = %s", (fact_id,))
    for position, source in enumerate(sources):
        key = rules.normalize_url(source["url"])
        conn.execute("""INSERT INTO sources (id, url, url_key, title, publisher) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (url_key) DO NOTHING""",
                     (ids.new("so"), source["url"], key, source["title"], source["publisher"]))
        conn.execute("""INSERT INTO fact_sources (fact_id, source_id, position)
                        SELECT %s, id, %s FROM sources WHERE url_key = %s ON CONFLICT DO NOTHING""",
                     (fact_id, position, key))
