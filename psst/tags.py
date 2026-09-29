"""The tag vocabulary. See docs/DESIGN.md, "Tags", and CONTENT_GUIDE.md, "Tags".

Every tag has one canonical name and any number of aliases, all stored normalized in `tag_labels`,
whose primary key makes two tags with the same normalized label impossible. `propose` is the only way
to add a tag: it returns an existing tag for the same Wikidata item or the same normalized label, and
refuses to create one that is merely similar to an existing tag unless told explicitly that they differ.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from . import ids

TYPES = ("person_or_group", "event", "era", "theme", "movement")
SIMILARITY_THRESHOLD = 0.55
MIN_PLACES_TO_PUBLISH = 3


def normalize(label: str) -> str:
    """Lowercase, no accents, no leading "the", "&" as "and", punctuation and spacing collapsed."""
    text = unicodedata.normalize("NFKD", label)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = text.replace("&", " and ").replace("'", "").replace("’", "")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^the ", "", text)
    return text


@dataclass
class Proposal:
    status: str  # "existing", "created", or "similar"
    tag: dict | None
    similar: list[dict]


def find(conn, text: str, limit: int = 10) -> list[dict]:
    """Tags whose name or alias matches or resembles the text, best first."""
    key = normalize(text)
    return conn.execute("""
        SELECT t.id, t.canonical_name, t.type, t.wikidata_id, max(similarity(l.normalized, %(key)s)) AS score,
               (SELECT count(DISTINCT f.place_id) FROM fact_tags ft JOIN facts f ON f.id = ft.fact_id
                WHERE ft.tag_id = t.id AND f.state <> 'retired') AS places
        FROM tag_labels l JOIN tags t ON t.id = l.tag_id
        WHERE l.normalized %% %(key)s OR l.normalized LIKE '%%' || %(key)s || '%%'
        GROUP BY t.id ORDER BY score DESC, places DESC LIMIT %(limit)s""",
                        {"key": key, "limit": limit}).fetchall()


def propose(conn, name: str, type_: str, wikidata_id: str | None = None, aliases: tuple[str, ...] = (),
            description: str | None = None, distinct_from: tuple[str, ...] = (), run: str | None = None,
            names: dict[str, str] | None = None) -> Proposal:
    if type_ not in TYPES:
        raise ValueError(f"type must be one of {', '.join(TYPES)}")
    key = normalize(name)
    if not key:
        raise ValueError("a tag needs a name")
    conn.execute("SELECT set_config('pg_trgm.similarity_threshold', %s, true)", (str(SIMILARITY_THRESHOLD),))

    if wikidata_id:
        row = conn.execute("SELECT * FROM tags WHERE wikidata_id = %s", (wikidata_id,)).fetchone()
        if row:
            _add_aliases(conn, row["id"], (name, *aliases))
            return Proposal("existing", row, [])
    row = conn.execute("SELECT t.* FROM tag_labels l JOIN tags t ON t.id = l.tag_id WHERE l.normalized = %s",
                       (key,)).fetchone()
    if row:
        if wikidata_id and not row["wikidata_id"]:
            conn.execute("UPDATE tags SET wikidata_id = %s WHERE id = %s", (wikidata_id, row["id"]))
        _add_aliases(conn, row["id"], aliases)
        return Proposal("existing", row, [])

    similar = [t for t in find(conn, name) if t["id"] not in distinct_from and t["score"] >= SIMILARITY_THRESHOLD]
    for alias in aliases:
        similar += [t for t in find(conn, alias) if t["id"] not in distinct_from
                    and t["score"] >= SIMILARITY_THRESHOLD and t not in similar]
    if similar:
        return Proposal("similar", None, similar)

    tag_id = ids.new("tg")
    note = description
    if distinct_from:
        note = (note + " " if note else "") + f"(Checked as distinct from {', '.join(distinct_from)}.)"
    conn.execute("INSERT INTO tags (id, canonical_name, type, wikidata_id, description, created_by_run) "
                 "VALUES (%s, %s, %s, %s, %s, %s)", (tag_id, name.strip(), type_, wikidata_id, note, run))
    conn.execute("INSERT INTO tag_labels (normalized, tag_id, label, is_canonical) VALUES (%s, %s, %s, true)",
                 (key, tag_id, name.strip()))
    _add_aliases(conn, tag_id, aliases)
    for lang, text in (names or {}).items():
        conn.execute("INSERT INTO tag_names (tag_id, lang, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                     (tag_id, lang, text))
    return Proposal("created", conn.execute("SELECT * FROM tags WHERE id = %s", (tag_id,)).fetchone(), [])


def _add_aliases(conn, tag_id: str, aliases) -> None:
    for alias in aliases:
        key = normalize(alias)
        if key:
            conn.execute("INSERT INTO tag_labels (normalized, tag_id, label) VALUES (%s, %s, %s) "
                         "ON CONFLICT (normalized) DO NOTHING", (key, tag_id, alias.strip()))


def merge(conn, source: str, target: str) -> None:
    """Fold one tag into another: its facts, labels, and names move over, and it disappears."""
    if source == target:
        raise ValueError("a tag can't merge into itself")
    conn.execute("""INSERT INTO fact_tags (fact_id, tag_id) SELECT fact_id, %s FROM fact_tags WHERE tag_id = %s
                    ON CONFLICT DO NOTHING""", (target, source))
    conn.execute("DELETE FROM fact_tags WHERE tag_id = %s", (source,))
    conn.execute("UPDATE tag_labels SET tag_id = %s, is_canonical = false WHERE tag_id = %s", (target, source))
    conn.execute("""INSERT INTO tag_names (tag_id, lang, name) SELECT %s, lang, name FROM tag_names WHERE tag_id = %s
                    ON CONFLICT DO NOTHING""", (target, source))
    wikidata = conn.execute("SELECT wikidata_id FROM tags WHERE id = %s", (source,)).fetchone()["wikidata_id"]
    conn.execute("DELETE FROM tags WHERE id = %s", (source,))
    if wikidata:
        conn.execute("UPDATE tags SET wikidata_id = coalesce(wikidata_id, %s) WHERE id = %s", (wikidata, target))


def audit(conn) -> list[dict]:
    """Pairs of tags that look like the same thing, for a person to merge or leave alone."""
    conn.execute("SELECT set_config('pg_trgm.similarity_threshold', %s, true)", (str(SIMILARITY_THRESHOLD),))
    return conn.execute("""
        SELECT DISTINCT ON (least(a.tag_id, b.tag_id), greatest(a.tag_id, b.tag_id))
               a.tag_id AS a, ta.canonical_name AS a_name, b.tag_id AS b, tb.canonical_name AS b_name,
               similarity(a.normalized, b.normalized) AS score
        FROM tag_labels a JOIN tag_labels b ON a.tag_id < b.tag_id AND a.normalized %% b.normalized
        JOIN tags ta ON ta.id = a.tag_id JOIN tags tb ON tb.id = b.tag_id
        WHERE coalesce(ta.description, '') NOT LIKE '%%' || b.tag_id || '%%'
          AND coalesce(tb.description, '') NOT LIKE '%%' || a.tag_id || '%%'
        ORDER BY least(a.tag_id, b.tag_id), greatest(a.tag_id, b.tag_id), score DESC""").fetchall()


def assign(conn, fact_id: str, tag_ids: list[str]) -> None:
    """Set a fact's tags to exactly these. Unknown tag ids fail, so text can never sneak in."""
    known = {r["id"] for r in conn.execute("SELECT id FROM tags WHERE id = ANY(%s)", (tag_ids,))}
    unknown = [t for t in tag_ids if t not in known]
    if unknown:
        raise ValueError(f"unknown tag ids: {', '.join(unknown)}")
    conn.execute("DELETE FROM fact_tags WHERE fact_id = %s", (fact_id,))
    for tag_id in dict.fromkeys(tag_ids):
        conn.execute("INSERT INTO fact_tags (fact_id, tag_id) VALUES (%s, %s)", (fact_id, tag_id))


def assign_many(conn, assignments: dict[str, list[str]]) -> None:
    """Set the tags of many facts at once. Every fact and tag id must exist, or nothing changes."""
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE s_fact_tags (fact_id text, tag_id text) ON COMMIT DROP")
        cur.execute("CREATE TEMP TABLE s_facts (fact_id text) ON COMMIT DROP")
        with cur.copy("COPY s_facts FROM STDIN") as copy:
            for fact_id in assignments:
                copy.write_row((fact_id,))
        with cur.copy("COPY s_fact_tags FROM STDIN") as copy:
            for fact_id, tag_ids in assignments.items():
                for tag_id in dict.fromkeys(tag_ids):
                    copy.write_row((fact_id, tag_id))
        bad_facts = [r["fact_id"] for r in cur.execute(
            "SELECT fact_id FROM s_facts WHERE fact_id NOT IN (SELECT id FROM facts)").fetchall()]
        bad_tags = [r["tag_id"] for r in cur.execute(
            "SELECT DISTINCT tag_id FROM s_fact_tags WHERE tag_id NOT IN (SELECT id FROM tags)").fetchall()]
        if bad_facts or bad_tags:
            raise RuntimeError("Nothing changed. Unknown fact ids: " + (", ".join(bad_facts[:10]) or "none")
                               + ". Unknown tag ids: " + (", ".join(bad_tags[:10]) or "none") + ".")
        cur.execute("DELETE FROM fact_tags WHERE fact_id IN (SELECT fact_id FROM s_facts)")
        cur.execute("INSERT INTO fact_tags (fact_id, tag_id) SELECT fact_id, tag_id FROM s_fact_tags")
