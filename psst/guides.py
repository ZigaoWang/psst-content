"""Guide information for places: a one-line identifier, a short neutral About, and key facts from Wikidata.

It sits beside the stories and never mixes with them. A research run writes the identifier and About for a
batch of places (`psst guide prepare`, then `check` and `submit`); the key facts are read from Wikidata by the
tools, each with the property it came from, and sanity checked. A different run reviews them more lightly
than stories (`psst guide next`, `apply`), and `psst publish` ships them. See CONTENT_GUIDE.md, section 13.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import ids, net, rules

CLAIM_HOURS = 12
MAX_VALUES = 3
DECISIONS = ("approve", "edit", "reject")
EDITABLE = ("identifier", "about", "sources")
MIN_NOTES = 15

# The Wikidata properties shown as key facts, in display order, with their English label and value type.
# A label can depend on the place's kind (a statue is "made", a museum "founded"). The app translates labels
# by property, so a new property here needs a label in the app's String Catalog too.
PROPERTIES: list[tuple[str, str, str]] = [
    ("P170", "Creator", "person"),
    ("P84", "Architect", "person"),
    ("P631", "Structural engineer", "person"),
    ("P193", "Builder", "person"),
    ("P88", "Commissioned by", "item"),
    ("P571", "Built", "date"),
    ("P1619", "Opened", "date"),
    ("P149", "Style", "item"),
    ("P186", "Material", "item"),
    ("P2048", "Height", "length"),
    ("P2043", "Length", "length"),
    ("P2046", "Area", "area"),
    ("P2044", "Elevation", "length"),
    ("P1101", "Floors", "count"),
    ("P1083", "Capacity", "count"),
    ("P547", "Commemorates", "item"),
    ("P825", "Dedicated to", "item"),
    ("P138", "Named after", "item"),
    ("P1435", "Heritage status", "item"),
]
PROPERTY = {p: (label, kind) for p, label, kind in PROPERTIES}
BUILT_LABEL = {"memorial": "Made", "culture": "Founded", "green": "Established"}

# The info box shows at most this many lines, chosen by PRIORITY and shown in PROPERTIES order, so a famous
# building's box is no longer than the facts a reader looks for first.
KEY_FACTS_SHOWN = 6
PRIORITY = ["P170", "P84", "P571", "P1619", "P149", "P1435", "P2048", "P186", "P547", "P825", "P1083", "P2043",
            "P2046", "P1101", "P2044", "P631", "P193", "P88", "P138"]

# Units Wikidata uses for lengths and areas, converted to meters, kilometers, square meters, and hectares.
UNITS = {
    "Q11573": ("m", 1.0), "Q828224": ("km", 1.0), "Q174728": ("m", 0.01), "Q3710": ("m", 0.3048),
    "Q482798": ("m", 0.9144), "Q253276": ("km", 1.609344), "Q174789": ("m", 0.001),
    "Q25343": ("m²", 1.0), "Q35852": ("ha", 1.0), "Q712226": ("km²", 1.0), "Q81292": ("ha", 0.40468564),
    "Q857027": ("m²", 0.09290304), "Q232291": ("km²", 2.58998811),
}
CIRCA = "Q5727902"
HUMAN = "Q5"
# What a style should be (or be a kind of): an architectural style, an art movement or style, or a style in
# general. Anything else (a political movement, a person) in "Style" is almost always vandalism.
STYLE_CLASSES = {"Q32880", "Q968159", "Q1792644", "Q1292119", "Q2198855"}
WIKIPEDIA_LANGUAGES = {"CN": "zh", "HK": "zh", "TW": "zh", "MO": "zh", "MY": "ms", "JP": "ja", "KR": "ko",
                       "FR": "fr", "DE": "de", "ES": "es", "IT": "it"}


# Key facts from Wikidata ------------------------------------------------------------------------------------

@dataclass
class KeyFact:
    property: str
    label: str
    value: str
    value_id: str | None = None
    flag: str | None = None
    year: int | None = None  # for dates, to compare them with each other and with people's lives
    amount: float | None = None  # for quantities, in the shown unit

    def as_dict(self) -> dict:
        out = {"property": self.property, "label": self.label, "value": self.value}
        if self.value_id:
            out["valueId"] = self.value_id
        if self.flag:
            out["flag"] = self.flag
        return out


def _claims(entity: dict, prop: str) -> list[dict]:
    """The claims to use: preferred ones if any, else normal ones; never deprecated ones, values that are
    unknown or none, or statements that have ended (a former operator, a heritage status since removed)."""
    claims = [c for c in entity.get("claims", {}).get(prop, [])
              if c.get("rank") != "deprecated" and c.get("mainsnak", {}).get("snaktype") == "value"
              and not c.get("qualifiers", {}).get("P582")]
    preferred = [c for c in claims if c.get("rank") == "preferred"]
    return preferred or claims


def _year(claim_value: dict) -> int | None:
    match = re.match(r"^([+-])(\d+)-", claim_value.get("time", ""))
    if not match:
        return None
    year = int(match.group(2))
    return -year if match.group(1) == "-" else year


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def format_time(value: dict, circa: bool = False) -> str | None:
    """A Wikidata time as people write it, at the precision Wikidata gives (US style: "May 12, 1843")."""
    match = re.match(r"^([+-])(\d+)-(\d\d)-(\d\d)", value.get("time", ""))
    if not match:
        return None
    sign, year, month, day = match.group(1), int(match.group(2)), int(match.group(3)), int(match.group(4))
    precision = value.get("precision", 9)
    era = " BC" if sign == "-" else ""
    if precision >= 11 and month and day:
        text = f"{MONTHS[month - 1]} {day}, {year}{era}"
    elif precision == 10 and month:
        text = f"{MONTHS[month - 1]} {year}{era}"
    elif precision == 9:
        text = f"{year}{era}"
    elif precision == 8:
        text = f"{year // 10 * 10}s{era}"
    elif precision == 7:
        century = (year - 1) // 100 + 1
        suffix = "th" if 10 <= century % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(century % 10, "th")
        text = f"{century}{suffix} century{era}"
    else:
        return None
    return f"about {text}" if circa and precision >= 9 else text


def format_amount(amount: float) -> str:
    if abs(amount) >= 100:
        return f"{amount:,.0f}"
    return f"{amount:.1f}".rstrip("0").rstrip(".")


def _quantity(value: dict, kind: str) -> tuple[str, float] | None:
    try:
        amount = float(value["amount"])
    except (KeyError, ValueError):
        return None
    unit = value.get("unit", "1").rsplit("/", 1)[-1]
    if kind == "count":
        return (f"{amount:,.0f}", amount) if unit == "1" and amount == int(amount) else None
    if unit not in UNITS:
        return None
    symbol, factor = UNITS[unit]
    if (kind == "length") != (symbol in ("m", "km")):
        return None
    amount *= factor
    return f"{format_amount(amount)} {symbol}", amount


def _label(entity: dict | None) -> str | None:
    labels = (entity or {}).get("labels", {})
    for code in ("en", "en-us", "en-gb", "mul"):
        if code in labels:
            return labels[code]["value"]
    return None


def _clean_label(prop: str, label: str) -> str:
    if prop == "P149":
        label = re.sub(r" architecture$", "", label)
    return label[0].upper() + label[1:] if label and label[0].islower() else label


def wikidata_items(qids: list[str]) -> tuple[dict[str, dict], dict[str, dict]]:
    """The places' items (claims and sitelinks) and the items their key facts point at (labels, plus the
    life dates of people)."""
    places = net.wikidata_entities(sorted(set(qids)), props="claims|sitelinks")
    # People and styles are checked against their own items (life dates, what kind of thing they are).
    checked, others = set(), set()
    for entity in places.values():
        for prop, (_, kind) in PROPERTY.items():
            if kind not in ("person", "item"):
                continue
            for claim in _claims(entity, prop)[:MAX_VALUES]:
                target = claim["mainsnak"]["datavalue"]["value"].get("id")
                if target:
                    (checked if kind == "person" or prop == "P149" else others).add(target)
    for entity in places.values():
        # What the place is (P31), for the suggested identifier.
        others.update(c["mainsnak"]["datavalue"]["value"]["id"] for c in _claims(entity, "P31")[:1])
    values = net.wikidata_entities(sorted(checked), props="labels|claims")
    values.update(net.wikidata_entities(sorted(others - checked), props="labels"))
    return places, values


def key_facts(entity: dict, values: dict[str, dict], kind: str = "building", size: str = "medium",
              today: date | None = None) -> list[KeyFact]:
    """The key facts for one place's Wikidata item, each sanity checked. Values that can't be shown plainly
    (no English label, an unknown unit) are left out rather than guessed."""
    this_year = (today or date.today()).year
    found: list[KeyFact] = []
    for prop, (label, value_kind) in PROPERTY.items():
        if prop == "P571":
            label = BUILT_LABEL.get(kind, label)
        claims = _claims(entity, prop)
        kept: list[KeyFact] = []
        for claim in claims[:MAX_VALUES]:
            value = claim["mainsnak"]["datavalue"]["value"]
            fact: KeyFact | None = None
            if value_kind in ("person", "item") and isinstance(value, dict) and value.get("id"):
                name = _label(values.get(value["id"]))
                if name:
                    fact = KeyFact(prop, label, _clean_label(prop, name), value["id"])
            elif value_kind == "date" and isinstance(value, dict):
                circa = any(q.get("datavalue", {}).get("value", {}).get("id") == CIRCA
                            for q in claim.get("qualifiers", {}).get("P1480", []))
                text = format_time(value, circa)
                if text:
                    fact = KeyFact(prop, label, text, year=_year(value))
            elif value_kind in ("length", "area", "count") and isinstance(value, dict):
                quantity = _quantity(value, value_kind)
                if quantity:
                    fact = KeyFact(prop, label, quantity[0], amount=quantity[1])
            if fact and fact.value not in [k.value for k in kept]:
                kept.append(fact)
        if value_kind in ("date", "length", "area", "count") and len(kept) > 1:
            # One date or measure is a fact; several are a disagreement for the reviewer to settle.
            kept[0].flag = "Wikidata gives several values: " + ", ".join(k.value for k in kept)
            kept = kept[:1]
        found.extend(kept)
    _sanity_check(found, values, kind, size, this_year)
    return found


def _sanity_check(found: list[KeyFact], values: dict[str, dict], kind: str, size: str, this_year: int) -> None:
    def flag(fact: KeyFact, reason: str) -> None:
        fact.flag = f"{fact.flag}; {reason}" if fact.flag else reason

    built = next((f.year for f in found if f.property == "P571" and f.year is not None), None)
    opened = next((f.year for f in found if f.property == "P1619" and f.year is not None), None)
    for fact in found:
        if fact.year is not None:
            if fact.year > this_year:
                flag(fact, f"{fact.year} is in the future")
            elif fact.year < -3000:
                flag(fact, f"{fact.value} is older than almost anything still standing")
        if fact.property == "P1619" and built is not None and opened is not None and opened < built:
            flag(fact, f"opened in {opened}, before it was built in {built}")
        if fact.amount is not None:
            limits = {"P2048": (0, 830), "P2043": (0, 60_000 if fact.value.endswith(" m") else 60),
                      "P2044": (-500, 9000), "P1101": (0, 170), "P1083": (0, 200_000)}
            low, high = limits.get(fact.property, (0, float("inf")))
            if fact.property == "P2046":
                high = 2_000_000 if fact.value.endswith(" m²") else (20_000 if fact.value.endswith(" ha") else 200)
            if not low < fact.amount <= high:
                flag(fact, f"{fact.value} is implausible for one place")
            elif fact.property == "P2048" and (size == "small" or kind == "memorial") and fact.amount > 60:
                flag(fact, f"{fact.value} is very tall for a {size} {kind}")
        style = values.get(fact.value_id or "", {})
        if fact.property == "P149" and fact.value_id and not STYLE_CLASSES & (_classes(style) | _classes(style, "P279")):
            flag(fact, f"{fact.value} isn't recorded on Wikidata as a style")
        if PROPERTY[fact.property][1] == "person" and fact.value_id and built is not None:
            person = values.get(fact.value_id, {})
            if HUMAN not in _classes(person):
                continue
            born = [_year(c["mainsnak"]["datavalue"]["value"]) for c in _claims(person, "P569")]
            died = [_year(c["mainsnak"]["datavalue"]["value"]) for c in _claims(person, "P570")]
            if born and born[0] is not None and born[0] > built:
                flag(fact, f"born in {born[0]}, after it was built in {built}")
            elif died and died[0] is not None and died[0] < built - 25:
                flag(fact, f"died in {died[0]}, {built - died[0]} years before it was built in {built}")


def _classes(entity: dict, prop: str = "P31") -> set[str]:
    """What an item is an instance of (P31), or with P279, a subclass of."""
    return {c["mainsnak"].get("datavalue", {}).get("value", {}).get("id")
            for c in entity.get("claims", {}).get(prop, [])} - {None}


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower())) - {"the", "of", "and", "a"}


def drop_echoes(found: list[KeyFact], name: str) -> list[KeyFact]:
    """"Named after: Belsize Park" on Belsize Park station says nothing; leave it out."""
    return [k for k in found if not (k.property == "P138" and _words(k.value) <= _words(name))]


def suggest_identifier(entity: dict, values: dict[str, dict], found: list[KeyFact]) -> str:
    """A starting point in the standard shape, "[style] <what it is>, <year>[, by <maker>]", from Wikidata's
    unflagged values. The writer checks and rewrites it; empty when Wikidata doesn't say what the place is."""
    kind = next((_label(values.get(c["mainsnak"]["datavalue"]["value"]["id"])) for c in _claims(entity, "P31")[:1]), None)
    if not kind:
        return ""
    clean = [k for k in found if not k.flag]
    style = next((k.value for k in clean if k.property == "P149"), None)
    year = next((re.sub(r"^.*?(\d{3,4}s?|\d+\w\w century)( BC)?$", r"\1\2", k.value)
                 for p in ("P571", "P1619") for k in clean if k.property == p), None)
    maker = next((k.value for p in ("P170", "P84") for k in clean if k.property == p), None)
    text = (f"{style} {kind.lower()}" if style else kind[0].upper() + kind[1:])
    text += f", {year}" if year else ""
    text += f", by {maker}" if maker else ""
    return text if len(text) <= rules.IDENTIFIER_MAX else ""


def wikipedia_page(entity: dict, country: str | None) -> dict | None:
    """The place's English Wikipedia article, or the local one when there's no English one."""
    links = entity.get("sitelinks", {})
    for lang in ("en", WIKIPEDIA_LANGUAGES.get(country or "", "")):
        site = links.get(f"{lang}wiki") if lang else None
        if site:
            title = site["title"]
            return {"lang": lang, "title": title,
                    "url": f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe="/(),'")}
    return None


def wikipedia_intros(lang: str, titles: list[str]) -> dict[str, str]:
    """The lead section of each article as plain text, keyed by the title asked for."""
    found: dict[str, str] = {}
    for start in range(0, len(titles), 20):
        chunk = titles[start:start + 20]
        payload = net.fetch_json(f"https://{lang}.wikipedia.org/w/api.php?action=query&format=json&formatversion=2"
                                 "&prop=extracts&exintro=1&explaintext=1&redirects=1&titles="
                                 + urllib.parse.quote("|".join(chunk)))
        query = payload.get("query", {})
        renamed = {n["to"]: n["from"] for n in query.get("normalized", [])}
        for r in query.get("redirects", []):
            renamed[r["to"]] = renamed.get(r["from"], r["from"])
        for page in query.get("pages", []):
            asked = renamed.get(page.get("title"), page.get("title"))
            if page.get("extract"):
                found[asked] = page["extract"].strip()
    return found


# Preparing work ---------------------------------------------------------------------------------------------

PLACES_NEEDING_GUIDES = """
    SELECT p.id, p.kind, p.size, p.wikidata_id, p.osm_ref, p.country_code, p.h3_cell AS cell,
           ci.name AS city, nb.name AS neighborhood,
           (SELECT name FROM place_names WHERE place_id = p.id AND role = 'display') AS name,
           (SELECT name FROM place_names WHERE place_id = p.id AND role = 'local') AS local_name,
           (SELECT count(*) FROM facts f WHERE f.place_id = p.id AND f.state = 'published') AS published
    FROM places p LEFT JOIN admin_areas ci ON ci.id = coalesce(p.city_id, p.region_id)
    LEFT JOIN admin_areas nb ON nb.id = p.neighborhood_id
    WHERE p.state = 'active'
      AND EXISTS (SELECT 1 FROM facts f WHERE f.place_id = p.id AND f.state <> 'retired')
      AND NOT EXISTS (SELECT 1 FROM guides g WHERE g.place_id = p.id AND g.state <> 'retired')
      AND NOT EXISTS (SELECT 1 FROM guide_claims c WHERE c.place_id = p.id AND c.claimed_until > now()
                      AND c.run_id <> %(run)s)
      AND (%(city)s::text IS NULL OR ci.name = %(city)s)
      AND (%(cell)s::text IS NULL OR p.h3_cell = %(cell)s)
      AND (%(places)s::text[] IS NULL OR p.id = ANY(%(places)s))
    ORDER BY ci.name, published DESC, nb.name, p.id
    LIMIT %(limit)s"""


def claim(conn, run_id: str, limit: int, city: str | None = None, cell: str | None = None,
          places: list[str] | None = None) -> list[dict]:
    """Places with stories and no guide, claimed for this run so no other session writes the same ones."""
    rows = conn.execute(PLACES_NEEDING_GUIDES, {"run": run_id, "city": city, "cell": cell, "places": places,
                                                "limit": limit}).fetchall()
    # Two sessions can pick the same places at once; only the one whose claim lands keeps each place.
    claimed = {r["place_id"] for r in conn.execute("""
        INSERT INTO guide_claims (place_id, run_id, claimed_until)
        SELECT unnest(%s::text[]), %s, now() + make_interval(hours => %s)
        ON CONFLICT (place_id) DO UPDATE SET run_id = EXCLUDED.run_id, claimed_until = EXCLUDED.claimed_until
        WHERE guide_claims.claimed_until < now() OR guide_claims.run_id = EXCLUDED.run_id
        RETURNING place_id""", ([r["id"] for r in rows], run_id, CLAIM_HOURS))}
    return [r for r in rows if r["id"] in claimed]


def stories(conn, place_ids: list[str]) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for row in conn.execute("""
            SELECT f.place_id, f.headline, f.short, f.long, f.veracity, f.state,
                   coalesce((SELECT json_agg(json_build_object('url', s.url, 'title', s.title, 'publisher', s.publisher)
                                             ORDER BY fs.position)
                             FROM fact_sources fs JOIN sources s ON s.id = fs.source_id WHERE fs.fact_id = f.id), '[]')
                       AS sources
            FROM facts f WHERE f.place_id = ANY(%s) AND f.state <> 'retired' ORDER BY f.place_id, f.position""",
                            (place_ids,)):
        found.setdefault(row["place_id"], []).append(row)
    return found


def gather(conn, places: list[dict]) -> list[dict]:
    """Everything a writer needs for each place: key facts, the Wikipedia lead, and the place's stories."""
    qids = [p["wikidata_id"] for p in places if p["wikidata_id"]]
    entities, values = wikidata_items(qids) if qids else ({}, {})
    pages: dict[str, dict] = {}
    for p in places:
        entity = entities.get(p["wikidata_id"] or "")
        page = wikipedia_page(entity, p["country_code"]) if entity else None
        if page:
            pages[p["id"]] = page
    intros: dict[tuple[str, str], str] = {}
    for lang in {page["lang"] for page in pages.values()}:
        titles = [page["title"] for page in pages.values() if page["lang"] == lang]
        intros.update({(lang, t): text for t, text in wikipedia_intros(lang, titles).items()})
    by_place = stories(conn, [p["id"] for p in places])
    out = []
    for p in places:
        entity = entities.get(p["wikidata_id"] or "")
        page = pages.get(p["id"])
        if page:
            page = {**page, "intro": intros.get((page["lang"], page["title"]), "")}
        found = drop_echoes(key_facts(entity, values, p["kind"], p["size"]), p["name"]) if entity else []
        out.append({**p, "keyFacts": [k.as_dict() for k in found],
                    "suggestion": suggest_identifier(entity, values, found) if entity else "",
                    "wikipedia": page, "stories": by_place.get(p["id"], [])})
    return out


def write_work(gathered: list[dict], directory: Path) -> tuple[Path, Path]:
    """A readable brief and a draft to fill in."""
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["# Guide brief", "",
             "For each place, write `identifier` and `about` in draft.json (CONTENT_GUIDE.md, section 13). Key facts "
             "come from Wikidata: keep them, or delete any that don't fit the place. Remove a whole entry to skip "
             "a place. The Wikipedia lead below was read for this run; open any other source with `psst fetch`.", ""]
    draft = []
    for p in gathered:
        title = p["name"] + (f" ({p['local_name']})" if p["local_name"] else "")
        lines += [f"## {p['id']}  {title}", "",
                  f"{p['kind']}, {p['size']}, {p['neighborhood'] or '-'}, {p['city'] or '-'}"
                  + (f", https://www.wikidata.org/wiki/{p['wikidata_id']}" if p["wikidata_id"] else "")
                  + (f", https://www.openstreetmap.org/{p['osm_ref']}" if p["osm_ref"] else ""), ""]
        if p["suggestion"]:
            lines += [f"Suggested identifier (from Wikidata; check and rewrite it): {p['suggestion']}", ""]
        if p["keyFacts"]:
            lines.append("Key facts from Wikidata:")
            for k in p["keyFacts"]:
                lines.append(f"- {k['label']} ({k['property']}): {k['value']}" + (f"  FLAGGED: {k['flag']}" if k.get("flag") else ""))
            lines.append("")
        if p["wikipedia"]:
            lines += [f"Wikipedia ({p['wikipedia']['lang']}): {p['wikipedia']['url']}", "",
                      "> " + (p["wikipedia"]["intro"] or "(no lead section)").replace("\n", "\n> "), ""]
        lines.append("Stories in Psst (the About is the plain summary; leave their surprises to them):")
        for s in p["stories"]:
            urls = ", ".join(src["url"] for src in s["sources"])
            lines.append(f"- [{s['state']}, {s['veracity']}] {s['headline']}: {s['short']}\n  {s['long']}\n  Sources: {urls}")
        lines.append("")
        sources = []
        if p["wikipedia"]:
            sources.append({"url": p["wikipedia"]["url"], "title": p["wikipedia"]["title"],
                            "publisher": "Wikipedia" if p["wikipedia"]["lang"] == "en" else f"Wikipedia ({p['wikipedia']['lang']})"})
        draft.append({"place": p["id"], "name": p["name"], "identifier": p["suggestion"], "about": "", "sources": sources,
                      "keyFacts": [{"property": k["property"], "value": k["value"]} for k in p["keyFacts"]]})
    brief = directory / "brief.md"
    brief.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path = directory / "draft.json"
    path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return brief, path


def record_reads(conn, run_id: str, gathered: list[dict]) -> None:
    """The tools read each Wikipedia lead and put it in the brief, which counts as reading it."""
    for p in gathered:
        if p["wikipedia"] and p["wikipedia"]["intro"]:
            conn.execute("""INSERT INTO source_reads (run_id, url_key, ok) VALUES (%s, %s, true)
                            ON CONFLICT (run_id, url_key) DO UPDATE SET ok = true, read_at = now()""",
                         (run_id, rules.read_key(p["wikipedia"]["url"])))


# Checking and submitting ------------------------------------------------------------------------------------

ENTRY_KEYS = {"place", "name", "identifier", "about", "sources", "keyFacts"}


def check(conn, entries: object, run_id: str | None = None, online: bool = True) -> tuple[rules.Report, dict]:
    """Every check a guide draft must pass. Returns the report and, when online, the fresh key facts per
    place (re-read from Wikidata, so nothing in the file can change what Wikidata says)."""
    report = rules.Report()
    if not isinstance(entries, list):
        report.error("draft", "must be a list of guides")
        return report, {}
    place_ids = [e.get("place") for e in entries if isinstance(e, dict)]
    places = {r["id"]: r for r in conn.execute("""
        SELECT p.id, p.kind, p.size, p.wikidata_id, p.state,
               (SELECT name FROM place_names WHERE place_id = p.id AND role = 'display') AS name,
               (SELECT g.id FROM guides g WHERE g.place_id = p.id AND g.state IN ('draft', 'reviewed')) AS waiting,
               (SELECT c.run_id FROM guide_claims c WHERE c.place_id = p.id AND c.claimed_until > now()) AS claimed_by
        FROM places p WHERE p.id = ANY(%s)""", (place_ids,))}
    seen = set()
    for index, entry in enumerate(entries):
        where = f"[{index}]"
        if not isinstance(entry, dict):
            report.error(where, "must be an object")
            continue
        where = f"[{index}] ({entry.get('place')})"
        unknown = set(entry) - ENTRY_KEYS
        if unknown:
            report.error(where, f"unknown fields: {', '.join(sorted(unknown))}")
        place = places.get(entry.get("place"))
        if not place or place["state"] != "active":
            report.error(where, "not an active place")
            continue
        if place["id"] in seen:
            report.error(where, "listed twice")
        seen.add(place["id"])
        if place["waiting"]:
            report.error(where, f"already has a guide waiting for review ({place['waiting']})")
        if run_id and place["claimed_by"] and place["claimed_by"] != run_id:
            report.error(where, f"another run ({place['claimed_by']}) is writing this place's guide")
        rules.check_guide(report, where, entry)
        if isinstance(entry.get("identifier"), str) and place["name"] \
                and entry["identifier"].strip().lower() == place["name"].strip().lower():
            report.error(where, "identifier repeats the name; say what the place is")
        key_facts_in = entry.get("keyFacts", [])
        if not isinstance(key_facts_in, list) or not all(
                isinstance(k, dict) and set(k) <= {"property", "value", "label", "valueId", "flag"}
                and isinstance(k.get("property"), str) and isinstance(k.get("value"), str) for k in key_facts_in):
            report.error(where, "keyFacts must be a list of {property, value} copied from the brief")
        elif key_facts_in and not place["wikidata_id"]:
            report.error(where, "key facts come from the place's Wikidata item, and it has none")
    fresh: dict[str, list[KeyFact]] = {}
    if online and report.ok:
        wanted = {e["place"]: places[e["place"]] for e in entries if e.get("keyFacts")}
        qids = [p["wikidata_id"] for p in wanted.values()]
        if qids:
            entities, values = wikidata_items(qids)
            for index, entry in enumerate(entries):
                place = wanted.get(entry["place"])
                if not place:
                    continue
                available = key_facts(entities.get(place["wikidata_id"], {}), values, place["kind"], place["size"])
                chosen = {(k["property"], k["value"]) for k in entry["keyFacts"]}
                missing = chosen - {(k.property, k.value) for k in available}
                for prop, value in sorted(missing):
                    report.error(f"[{index}] ({entry['place']})", f"{prop} {value!r} isn't on Wikidata (any more); "
                                                                    "key facts are copied from the brief, never typed")
                fresh[entry["place"]] = [k for k in available if (k.property, k.value) in chosen]
    return report, fresh


def unread_sources(conn, entries: list[dict], run_id: str) -> list[str]:
    read = {r["url_key"] for r in conn.execute("SELECT url_key FROM source_reads WHERE run_id = %s", (run_id,))}
    urls = dict.fromkeys(s["url"] for e in entries for s in e["sources"])
    return [u for u in urls if rules.read_key(u) not in read]


def _store_sources(conn, guide_id: str, sources: list[dict]) -> None:
    conn.execute("DELETE FROM guide_sources WHERE guide_id = %s", (guide_id,))
    for position, source in enumerate(sources):
        key = rules.normalize_url(source["url"])
        conn.execute("""INSERT INTO sources (id, url, url_key, title, publisher) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (url_key) DO NOTHING""",
                     (ids.new("so"), source["url"], key, source["title"], source["publisher"]))
        conn.execute("""INSERT INTO guide_sources (guide_id, source_id, position)
                        SELECT %s, id, %s FROM sources WHERE url_key = %s ON CONFLICT DO NOTHING""",
                     (guide_id, position, key))


def submit(conn, entries: list[dict], run: dict, fresh: dict[str, list[KeyFact]]) -> list[str]:
    """Store checked guides as drafts, with the key facts read from Wikidata just now."""
    if not run["model"]:
        raise RuntimeError("Research runs record the model that did the work; start the run with --model.")
    unread = unread_sources(conn, entries, run["id"])
    if unread:
        raise RuntimeError("Open every source you cite with `psst fetch <url> --run <run id>` before submitting. "
                           "Not read in this run: " + ", ".join(unread[:15]) + (" and more" if len(unread) > 15 else ""))
    stored = [store(conn, entry["place"], entry, run, fresh.get(entry["place"], [])) for entry in entries]
    conn.execute("DELETE FROM guide_claims WHERE run_id = %s", (run["id"],))
    return stored


def store(conn, place_id: str, entry: dict, run: dict, key_facts_found: list[KeyFact]) -> str:
    """One guide as a draft: its text and sources as written, its key facts as read from Wikidata."""
    wikidata = conn.execute("SELECT wikidata_id FROM places WHERE id = %s", (place_id,)).fetchone()["wikidata_id"]
    guide_id = ids.new("gd")
    conn.execute("""INSERT INTO guides (id, place_id, identifier, about, wikidata_id, researched_at, researched_by,
                                        research_run)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                 (guide_id, place_id, entry["identifier"], entry["about"], wikidata if key_facts_found else None,
                  date.today().isoformat(), run["model"], run["id"]))
    _store_sources(conn, guide_id, entry["sources"])
    for position, k in enumerate(key_facts_found):
        conn.execute("""INSERT INTO guide_key_facts (guide_id, position, property, label, value, value_id, flag)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                     (guide_id, position, k.property, k.label, k.value, k.value_id, k.flag))
    return guide_id


def key_facts_for(places: list[dict]) -> dict[str, list[KeyFact]]:
    """Every key fact Wikidata has for new places ({id, wikidata_id, kind, size}), sanity checked. A review
    drops the ones that don't fit."""
    qids = [p["wikidata_id"] for p in places if p.get("wikidata_id")]
    if not qids:
        return {}
    entities, values = wikidata_items(qids)
    return {p["id"]: drop_echoes(key_facts(entities.get(p["wikidata_id"], {}), values, p["kind"], p.get("size") or "medium"),
                                 p.get("name", ""))
            for p in places if p.get("wikidata_id")}


# Reviewing --------------------------------------------------------------------------------------------------

def _guide_rows(conn, where: str, params: dict) -> list[dict]:
    return conn.execute(f"""
        SELECT g.id, g.state, g.needs_review, g.identifier, g.about, g.wikidata_id, g.research_run, g.researched_by,
               g.review_notes, g.last_verified_at,
               json_build_object('id', p.id, 'name', dn.name, 'localName', ln.name, 'kind', p.kind, 'size', p.size,
                                 'neighborhood', nb.name, 'city', ci.name, 'wikidata', p.wikidata_id, 'osm', p.osm_ref)
                   AS place,
               (SELECT coalesce(json_agg(json_build_object('url', s.url, 'title', s.title, 'publisher', s.publisher)
                                         ORDER BY gs.position), '[]')
                FROM guide_sources gs JOIN sources s ON s.id = gs.source_id WHERE gs.guide_id = g.id) AS sources,
               (SELECT coalesce(json_agg(json_build_object('property', k.property, 'label', k.label, 'value', k.value,
                                                           'valueId', k.value_id, 'flag', k.flag,
                                                           'confirmed', k.flag_confirmed) ORDER BY k.position), '[]')
                FROM guide_key_facts k WHERE k.guide_id = g.id) AS "keyFacts",
               (SELECT coalesce(json_agg(json_build_object('headline', f.headline, 'short', f.short) ORDER BY f.position), '[]')
                FROM facts f WHERE f.place_id = p.id AND f.state <> 'retired') AS stories,
               (SELECT e.note FROM guide_events e WHERE e.guide_id = g.id AND 'flagged' = ANY(e.changes)
                ORDER BY e.id DESC LIMIT 1) AS flagged_because
        FROM guides g JOIN places p ON p.id = g.place_id
        JOIN place_names dn ON dn.place_id = p.id AND dn.role = 'display'
        LEFT JOIN place_names ln ON ln.place_id = p.id AND ln.role = 'local'
        LEFT JOIN admin_areas nb ON nb.id = p.neighborhood_id
        LEFT JOIN admin_areas ci ON ci.id = coalesce(p.city_id, p.region_id)
        WHERE {where}""", params).fetchall()


def queue(conn, limit: int, reviewer_run: str | None = None, city: str | None = None,
          from_run: str | None = None) -> list[dict]:
    """Guides waiting for review: flagged ones first, then drafts. A run never gets guides it wrote. With
    `from_run`, only the guides one research run wrote, so several reviewers can work side by side."""
    return _guide_rows(conn, """(g.state = 'draft' OR (g.needs_review AND g.state IN ('reviewed', 'published')))
          AND (%(run)s::text IS NULL OR g.research_run <> %(run)s)
          AND (%(city)s::text IS NULL OR ci.name = %(city)s)
          AND (%(from_run)s::text IS NULL OR g.research_run = %(from_run)s)
        ORDER BY g.needs_review DESC, ci.name, nb.name, dn.name LIMIT %(limit)s""",
                       {"run": reviewer_run, "city": city, "from_run": from_run, "limit": limit})


def check_decisions(conn, decisions: object, run: dict) -> rules.Report:
    report = rules.Report()
    if not isinstance(decisions, list):
        report.error("decisions", "must be a list")
        return report
    guide_ids = [d.get("guide") for d in decisions if isinstance(d, dict)]
    # Locked, so a second reviewer deciding the same guides waits and then sees they're already decided.
    conn.execute("SELECT id FROM guides WHERE id = ANY(%s) FOR UPDATE", (guide_ids,))
    guides = {g["id"]: g for g in _guide_rows(conn, "g.id = ANY(%(ids)s)", {"ids": guide_ids})}
    read = {r["url_key"] for r in conn.execute("SELECT url_key FROM source_reads WHERE run_id = %s", (run["id"],))}
    note_counts: dict[str, int] = {}
    for d in decisions:
        if isinstance(d, dict) and d.get("notes"):
            key = " ".join(str(d["notes"]).lower().split())
            note_counts[key] = note_counts.get(key, 0) + 1
    seen = set()
    for index, decision in enumerate(decisions):
        where = f"decisions[{index}]"
        if not isinstance(decision, dict):
            report.error(where, "must be an object")
            continue
        guide = guides.get(decision.get("guide"))
        where = f"{where} ({decision.get('guide')})"
        if not guide:
            report.error(where, "no such guide")
            continue
        if guide["id"] in seen:
            report.error(where, "decided twice")
        seen.add(guide["id"])
        unknown = set(decision) - {"guide", "decision", "notes", "reason", "changes", "dropKeyFacts", "confirmKeyFacts"}
        if unknown:
            report.error(where, f"unknown fields: {', '.join(sorted(unknown))}")
        if not (guide["state"] == "draft" or guide["needs_review"]):
            report.error(where, f"is {guide['state']} and not flagged; nothing to review")
        if guide["research_run"] == run["id"]:
            report.error(where, "a run can't review its own research")
        choice = decision.get("decision")
        if choice not in DECISIONS:
            report.error(where, f"decision must be one of {', '.join(DECISIONS)}")
        notes = decision.get("notes")
        if not isinstance(notes, str) or len(notes.strip()) < MIN_NOTES:
            report.error(where, "notes must say what you checked")
        elif note_counts.get(" ".join(notes.lower().split()), 0) > 2:
            report.error(where, "the same notes are on several decisions; say what you checked for this place")
        changes = decision.get("changes") or {}
        if set(changes) - set(EDITABLE):
            report.error(where, f"can't change {', '.join(sorted(set(changes) - set(EDITABLE)))}")
        if choice == "edit" and not changes and not decision.get("dropKeyFacts"):
            report.error(where, "an edit needs changes or key facts to drop")
        if choice != "edit" and changes:
            report.error(where, "only an edit can have changes")
        if choice == "reject":
            if not decision.get("reason"):
                report.error(where, "a rejection needs a reason")
            continue
        properties = {k["property"] for k in guide["keyFacts"]}
        drop = set(decision.get("dropKeyFacts") or [])
        confirm = set(decision.get("confirmKeyFacts") or [])
        for prop in sorted((drop | confirm) - properties):
            report.error(where, f"{prop} isn't one of this guide's key facts")
        flagged = {k["property"] for k in guide["keyFacts"] if k["flag"] and not k["confirmed"]}
        for prop in sorted(flagged - drop - confirm):
            report.error(where, f"{prop} is flagged ({next(k['flag'] for k in guide['keyFacts'] if k['property'] == prop)}); "
                                "check it and put it in confirmKeyFacts or dropKeyFacts")
        merged = {"identifier": guide["identifier"], "about": guide["about"], "sources": guide["sources"]}
        merged.update(changes)
        rules.check_guide(report, where, merged)
        if isinstance(merged.get("sources"), list) and not any(
                isinstance(s, dict) and s.get("url") and rules.read_key(s["url"]) in read for s in merged["sources"]):
            report.error(where, "open at least one of its sources with `psst fetch <url> --run <run id>` and check "
                                "the About against it")
    return report


def apply_decisions(conn, decisions: list[dict], run: dict) -> dict[str, int]:
    reviewer = run["model"] or run["operator"]
    counts = dict.fromkeys(DECISIONS, 0)
    for decision in decisions:
        guide_id, choice, notes = decision["guide"], decision["decision"], decision["notes"].strip()
        if choice == "reject":
            conn.execute("""UPDATE guides SET state = 'retired', retired_at = now(), retire_reason = %s,
                            needs_review = false, review_notes = %s, reviewed_by = %s, review_run = %s,
                            reviewed_at = now() WHERE id = %s""",
                         (decision["reason"], notes, reviewer, run["id"], guide_id))
        else:
            changes = decision.get("changes") or {}
            fields = {k: changes[k] for k in ("identifier", "about") if k in changes}
            assignments = "".join(f", {k} = %({k})s" for k in fields)
            conn.execute(f"""UPDATE guides SET state = CASE WHEN state = 'draft' THEN 'reviewed' ELSE state END,
                             reviewed_at = now(), reviewed_by = %(by)s, review_run = %(run)s, review_notes = %(notes)s,
                             last_verified_at = now(), needs_review = false{assignments} WHERE id = %(id)s""",
                         {**fields, "by": reviewer, "run": run["id"], "notes": notes, "id": guide_id})
            if "sources" in changes:
                _store_sources(conn, guide_id, changes["sources"])
            if decision.get("dropKeyFacts"):
                conn.execute("DELETE FROM guide_key_facts WHERE guide_id = %s AND property = ANY(%s)",
                             (guide_id, list(decision["dropKeyFacts"])))
            if decision.get("confirmKeyFacts"):
                conn.execute("UPDATE guide_key_facts SET flag_confirmed = true WHERE guide_id = %s AND property = ANY(%s)",
                             (guide_id, list(decision["confirmKeyFacts"])))
        counts[choice] += 1
    return counts


def shown(key_facts: list[dict]) -> list[dict]:
    """The key fact lines to show (each line one property): the KEY_FACTS_SHOWN with the highest PRIORITY, kept
    in display order. Properties no longer shown at all (religion, operator) are dropped."""
    rank = {p: i for i, p in enumerate(PRIORITY)}
    chosen = sorted((k for k in key_facts if k["property"] in rank), key=lambda k: rank[k["property"]])[:KEY_FACTS_SHOWN]
    order = {p: i for i, (p, _, _) in enumerate(PROPERTIES)}
    return sorted(chosen, key=lambda k: order[k["property"]])


def publishable(conn, guide_ids: list[str]) -> tuple[list[str], list[str]]:
    """The reviewed guides that can go live now, and the cities held back. A city's guides go live together,
    once every place live there has an approved guide, so readers never see half a city with guides."""
    rows = conn.execute("""
        WITH live AS (
            SELECT p.id, coalesce(p.city_id, p.region_id) AS city FROM places p
            WHERE p.state = 'active' AND EXISTS (SELECT 1 FROM facts f WHERE f.place_id = p.id AND f.state = 'published'))
        SELECT live.city, a.name,
               bool_and(EXISTS (SELECT 1 FROM guides g WHERE g.place_id = live.id
                                AND (g.state = 'published' OR g.id = ANY(%(ids)s::text[])))) AS complete
        FROM live LEFT JOIN admin_areas a ON a.id = live.city GROUP BY live.city, a.name""", {"ids": guide_ids}).fetchall()
    held = {r["city"] for r in rows if not r["complete"]}
    kept = [r["id"] for r in conn.execute("""
        SELECT g.id FROM guides g JOIN places p ON p.id = g.place_id
        WHERE g.id = ANY(%s::text[]) AND NOT (coalesce(p.city_id, p.region_id) = ANY(%s::bigint[]))""", (guide_ids, list(held)))]
    return kept, sorted(r["name"] or str(r["city"]) for r in rows if not r["complete"])


def sample(conn, review_run: str, limit: int) -> list[dict]:
    """A random sample of what a review run approved or edited, for a second reviewer to check. Anything found
    wrong goes back to review with `psst guide flag`."""
    return _guide_rows(conn, """g.review_run = %(run)s AND g.state IN ('reviewed', 'published')
        ORDER BY random() LIMIT %(limit)s""", {"run": review_run, "limit": limit})


def retire_replaced(conn, published: list[str]) -> int:
    """Once a new guide is live, the one it replaced is retired (kept, with the reason)."""
    rows = conn.execute("""
        UPDATE guides old SET state = 'retired', retired_at = now(), retire_reason = 'Replaced by ' || new.id
        FROM guides new WHERE new.id = ANY(%s) AND new.state = 'published' AND old.place_id = new.place_id
          AND old.id <> new.id AND old.state = 'published' RETURNING old.id""", (published,)).fetchall()
    return len(rows)


def progress(conn) -> list[dict]:
    """Per city: places with stories, and where each one's guide stands: live, approved and waiting for publish,
    waiting for review, or none at all (never written, or rejected)."""
    state = lambda s: f"EXISTS (SELECT 1 FROM guides g WHERE g.place_id = p.id AND g.state = '{s}')"
    return conn.execute(f"""
        SELECT coalesce(ci.name, '(no city)') AS city, count(*) AS places,
               count(*) FILTER (WHERE {state('published')}) AS live,
               count(*) FILTER (WHERE {state('reviewed')} AND NOT {state('published')}) AS approved,
               count(*) FILTER (WHERE {state('draft')}) AS in_review,
               count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM guides g WHERE g.place_id = p.id
                                                  AND g.state <> 'retired')) AS missing
        FROM places p LEFT JOIN admin_areas ci ON ci.id = coalesce(p.city_id, p.region_id)
        WHERE p.state = 'active' AND EXISTS (SELECT 1 FROM facts f WHERE f.place_id = p.id AND f.state <> 'retired')
        GROUP BY 1 ORDER BY 2 DESC""").fetchall()
