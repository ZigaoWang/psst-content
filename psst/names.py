"""Multilingual place names from Wikidata labels and OpenStreetMap name tags. Never machine translated.

- `display` (English) stays as researched; it's what the app shows.
- `local` is the name on the signs: OpenStreetMap's `name` tag, or the Wikidata label in the country's
  language, when it differs from the English name.
- `alt` holds established names in other languages.

Also records each place's Wikidata id when only OpenStreetMap knew it, which strengthens duplicate checks.
"""

from __future__ import annotations

import re

from . import net

# Languages kept, as BCP 47. Wikidata codes on the right map onto them, in order of preference.
WIKIDATA_LANGUAGES = {
    "en": ["en"], "zh-Hans": ["zh-hans", "zh-cn", "zh-sg", "zh-my", "zh"], "zh-Hant": ["zh-hant", "zh-tw", "zh-hk"],
    "ja": ["ja"], "ko": ["ko"], "fr": ["fr"], "de": ["de"], "es": ["es"], "it": ["it"], "pt": ["pt", "pt-br"],
    "ru": ["ru"], "ar": ["ar"], "hi": ["hi"], "ms": ["ms"], "id": ["id"], "th": ["th"], "vi": ["vi"],
    "tr": ["tr"], "nl": ["nl"], "pl": ["pl"], "sv": ["sv"], "ta": ["ta"], "he": ["he"], "el": ["el"],
    "uk": ["uk"], "fa": ["fa"], "bn": ["bn"], "ur": ["ur"],
}
OSM_LANGUAGES = {"zh-Hans": ["name:zh-Hans", "name:zh"], "zh-Hant": ["name:zh-Hant"]}
# The language of street signs, for countries with one main language. Countries with several (Singapore,
# Switzerland, India) are left out, so no local name is guessed there.
COUNTRY_LANGUAGE = {"GB": "en", "IE": "en", "US": "en", "AU": "en", "NZ": "en", "CN": "zh-Hans", "HK": "zh-Hant",
                    "TW": "zh-Hant", "MO": "zh-Hant", "JP": "ja", "KR": "ko", "MY": "ms", "ID": "id", "TH": "th",
                    "VN": "vi", "FR": "fr", "DE": "de", "AT": "de", "ES": "es", "MX": "es", "AR": "es", "IT": "it",
                    "PT": "pt", "BR": "pt", "NL": "nl", "PL": "pl", "SE": "sv", "RU": "ru", "UA": "uk", "TR": "tr",
                    "GR": "el", "IL": "he", "IR": "fa"}
CJK = re.compile(r"[㐀-鿿぀-ヿ가-힯]")
HAN = re.compile(r"[㐀-鿿]")


def _same(a: str, b: str) -> bool:
    return a.casefold().strip() == b.casefold().strip()


def language_of_local(name: str, country: str) -> str:
    if HAN.search(name):
        return "zh-Hant" if country in ("HK", "TW", "MO") else "zh-Hans"
    return COUNTRY_LANGUAGE.get(country, "und")


def collect(places: list[dict]) -> tuple[list[tuple], list[tuple], list[str]]:
    """Given places (id, display, local, country, wikidata_id, osm_ref), returns name rows, new Wikidata
    ids as (place_id, qid), and notes about anything skipped."""
    notes: list[str] = []
    osm = net.osm_tags([p["osm_ref"] for p in places if p["osm_ref"]])
    new_qids: list[tuple[str, str]] = []
    qid_for: dict[str, str] = {}
    for p in places:
        qid = p["wikidata_id"]
        tags = osm.get(p["osm_ref"], {}).get("tags", {}) if p["osm_ref"] else {}
        if not qid and re.fullmatch(r"Q[1-9][0-9]*", tags.get("wikidata", "")):
            qid = tags["wikidata"]
            new_qids.append((p["id"], qid))
        if qid:
            qid_for[p["id"]] = qid
    entities = net.wikidata_entities(sorted(set(qid_for.values())), props="labels")

    rows: list[tuple] = []
    for p in places:
        tags = osm.get(p["osm_ref"], {}).get("tags", {}) if p["osm_ref"] else {}
        labels = entities.get(qid_for.get(p["id"], ""), {}).get("labels", {})
        names: dict[str, tuple[str, str]] = {}
        for lang, codes in WIKIDATA_LANGUAGES.items():
            for code in codes:
                if code in labels:
                    names[lang] = (labels[code]["value"], "wikidata")
                    break
        for lang, keys in OSM_LANGUAGES.items():
            for key in keys:
                if key in tags and lang not in names:
                    names[lang] = (tags[key], "osm")
                    break
        for key, value in tags.items():
            m = re.fullmatch(r"name:([a-z]{2})", key)
            if m and m.group(1) in WIKIDATA_LANGUAGES and m.group(1) not in names:
                names[m.group(1)] = (value, "osm")

        # The name on the signs, which is always in the country's language: Wikidata's label in that
        # language, else OSM's. OSM's plain name is used only when its script proves its language (Chinese
        # characters in China); a Latin-script plain name could be English as easily as Malay.
        country = p["country"] or ""
        local_lang = COUNTRY_LANGUAGE.get(country)
        if not p["local"] and local_lang and local_lang != "en":
            candidate = names.get(local_lang)
            # (In Hong Kong the plain name is often Chinese and English together; that doesn't count.)
            if not candidate and tags.get("name") and CJK.search(tags["name"]) \
                    and not re.search(r"[A-Za-z]", tags["name"]) and language_of_local(tags["name"], country) == local_lang:
                candidate = (tags["name"], "osm")
            if candidate and not _same(candidate[0], p["display"]):
                rows.append((p["id"], "local", local_lang, candidate[0], candidate[1]))

        for lang, (name, source) in names.items():
            if lang == "en" and _same(name, p["display"]):
                continue
            rows.append((p["id"], "alt", lang, name, source))
    return rows, new_qids, notes


def misplaced_local_names(conn) -> list[dict]:
    """Local names that aren't in their country's language (the name on the signs must be)."""
    return [r for r in conn.execute("""
        SELECT n.place_id, n.lang, n.name, p.country_code AS country FROM place_names n
        JOIN places p ON p.id = n.place_id WHERE n.role = 'local'""").fetchall()
            if COUNTRY_LANGUAGE.get(r["country"]) not in (None, "en", r["lang"])]


def fix_local_names(conn, rows: list[dict], keep_as_alt: bool = True) -> int:
    """Replace misplaced local names: drop them, look up the real local name and every other name, and keep
    a misplaced name as an alternative only where Wikidata and OSM have none in its language. Returns how
    many places now have a local name."""
    ids = [r["place_id"] for r in rows]
    conn.execute("DELETE FROM place_names WHERE place_id = ANY(%s) AND role = 'local'", (ids,))
    places = conn.execute("""
        SELECT p.id, p.country_code AS country, p.wikidata_id, p.osm_ref,
               (SELECT name FROM place_names WHERE place_id = p.id AND role = 'display') AS display, NULL AS local
        FROM places p WHERE p.id = ANY(%s)""", (ids,)).fetchall()
    if places:
        apply(conn, places)
    for r in rows if keep_as_alt else []:
        conn.execute("""INSERT INTO place_names (place_id, role, lang, name, source) VALUES (%s, 'alt', %s, %s, 'research')
                        ON CONFLICT (place_id, role, lang) DO NOTHING""", (r["place_id"], r["lang"], r["name"]))
    return conn.execute("SELECT count(*) AS n FROM place_names WHERE role = 'local' AND place_id = ANY(%s)",
                        (ids,)).fetchone()["n"]


def apply(conn, places: list[dict]) -> dict[str, int]:
    rows, new_qids, _ = collect(places)
    taken = {r["wikidata_id"] for r in conn.execute("SELECT wikidata_id FROM places WHERE wikidata_id IS NOT NULL")}
    added_qids = 0
    for place_id, qid in new_qids:
        if qid in taken:
            continue  # Another place already has this item; `psst places duplicates` lists these.
        conn.execute("UPDATE places SET wikidata_id = %s WHERE id = %s AND wikidata_id IS NULL", (qid, place_id))
        taken.add(qid)
        added_qids += 1
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE s_names (place_id text, role text, lang text, name text, source text) "
                    "ON COMMIT DROP")
        with cur.copy("COPY s_names FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
        cur.execute("DELETE FROM place_names n WHERE n.role = 'alt' AND n.source <> 'research' "
                    "AND n.place_id IN (SELECT DISTINCT place_id FROM s_names)")
        cur.execute("""INSERT INTO place_names (place_id, role, lang, name, source)
                       SELECT DISTINCT ON (place_id, role, lang) place_id, role, lang, name, source FROM s_names
                       ON CONFLICT (place_id, role, lang) DO NOTHING""")
    return {"names": len(rows), "wikidata_ids": added_qids}
