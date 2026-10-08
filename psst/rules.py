"""The writing and structure rules from CONTENT_GUIDE.md that a script can check.

Used by `psst draft check` before anything reaches the database, and by the export as a last guard.
"""

from __future__ import annotations

import re
import urllib.parse

HEADLINE_MAX = 60
SHORT_MAX = 220
LONG_MIN = 300
LONG_MAX = 1200

KINDS = ("transit", "crossing", "street", "building", "worship", "memorial", "green", "water", "culture")
SIZES = ("small", "medium", "large")
CATEGORIES = ("name", "hidden", "history", "design", "engineering", "people", "pop", "quirk")
VERACITIES = ("fact", "legend", "disputed")

ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
QID_RE = re.compile(r"^Q[1-9][0-9]*$")
OSM_RE = re.compile(r"^(node|way|relation)/[1-9][0-9]*$")
PLACE_ID_RE = re.compile(r"^pl_[0-9a-hjkmnp-tv-z]{10}$")
TAG_ID_RE = re.compile(r"^tg_[0-9a-hjkmnp-tv-z]{8}$")


class Report:
    """Collects errors (which block) and warnings (which a reviewer should read)."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def warn(self, where: str, message: str) -> None:
        self.warnings.append(f"{where}: {message}")

    @property
    def ok(self) -> bool:
        return not self.errors


def is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and value.strip() != "" and value == value.strip()


# Phrases that make writing read as machine-made or as marketing. Errors.
BANNED_PHRASES = [
    "nestled", "boasts", "boasting", "testament to", "rich tapestry", "tapestry of", "vibrant", "delve",
    "bustling", "iconic", "a must-see", "must-see", "steeped in", "whispers of the past", "hidden gem",
    "fun fact", "did you know", "little-known", "little known fact", "psst", "stands as a", "stands as an",
    "it's worth noting", "it is worth noting", "interestingly,", "in conclusion", "a true gem",
    "treasure trove", "bucket list", "breathtaking", "awe-inspiring", "unforgettable", "embark on",
    "journey through time", "step back in time", "a feast for the eyes", "look no further",
]

# Phrases that are often a sign of padding. Warnings.
SOFT_PHRASES = [
    "fascinating", "remarkable", "stunning", "amazing", "incredible", "truly", "unique", "a nod to",
    "not just", "not only", "rich history", "storied", "charming", "quaint", "picturesque",
]

BRITISH_WORDS = {
    "colour": "color", "colours": "colors", "coloured": "colored", "colourful": "colorful",
    "favourite": "favorite", "favourites": "favorites", "flavour": "flavor", "honour": "honor",
    "honoured": "honored", "honours": "honors", "labour": "labor", "labourers": "laborers", "labourer": "laborer",
    "neighbour": "neighbor", "neighbours": "neighbors", "neighbouring": "neighboring", "neighbourhood": "neighborhood",
    "neighbourhoods": "neighborhoods", "harbour": "harbor", "harbours": "harbors", "behaviour": "behavior",
    "rumour": "rumor", "rumours": "rumors", "rumoured": "rumored", "humour": "humor", "vapour": "vapor",
    "armour": "armor", "armoured": "armored", "parlour": "parlor", "odour": "odor", "splendour": "splendor",
    "endeavour": "endeavor", "glamour": "glamor", "valour": "valor", "vigour": "vigor", "saviour": "savior",
    "centre": "center", "centres": "centers", "centred": "centered", "theatre": "theater", "theatres": "theaters",
    "metre": "meter", "metres": "meters", "kilometre": "kilometer", "kilometres": "kilometers",
    "centimetre": "centimeter", "centimetres": "centimeters", "millimetre": "millimeter", "millimetres": "millimeters",
    "litre": "liter", "litres": "liters", "fibre": "fiber", "sabre": "saber", "spectre": "specter",
    "calibre": "caliber", "lustre": "luster", "sombre": "somber", "meagre": "meager", "sepulchre": "sepulcher",
    "grey": "gray", "greying": "graying", "programme": "program", "programmes": "programs",
    "storey": "story", "storeys": "stories", "kerb": "curb", "kerbs": "curbs", "tyre": "tire", "tyres": "tires",
    "aluminium": "aluminum", "defence": "defense", "defences": "defenses", "offence": "offense", "offences": "offenses",
    "licence": "license", "licences": "licenses", "pretence": "pretense", "jewellery": "jewelry", "catalogue": "catalog",
    "analogue": "analog", "cheque": "check", "cheques": "checks", "plough": "plow", "ploughed": "plowed",
    "travelled": "traveled", "travelling": "traveling", "traveller": "traveler", "travellers": "travelers",
    "cancelled": "canceled", "cancelling": "canceling", "modelled": "modeled", "modelling": "modeling",
    "labelled": "labeled", "fuelled": "fueled", "levelled": "leveled", "signalling": "signaling", "signalled": "signaled",
    "marvellous": "marvelous", "channelled": "channeled", "tunnelled": "tunneled", "tunnelling": "tunneling",
    "counsellor": "counselor", "jeweller": "jeweler", "jewellers": "jewelers", "quarrelled": "quarreled",
    "whilst": "while", "amongst": "among", "learnt": "learned", "spelt": "spelled", "burnt": "burned",
    "manoeuvre": "maneuver", "manoeuvres": "maneuvers", "encyclopaedia": "encyclopedia", "mould": "mold",
    "moulded": "molded", "sceptic": "skeptic", "sceptical": "skeptical", "sulphur": "sulfur", "ageing": "aging",
    "draught": "draft", "gaol": "jail", "pyjamas": "pajamas", "moustache": "mustache", "cosy": "cozy",
    "annexe": "annex", "artefact": "artifact", "artefacts": "artifacts", "enrol": "enroll", "fulfil": "fulfill",
    "instalment": "installment", "judgement": "judgment", "mediaeval": "medieval", "oestrogen": "estrogen",
    "practise": "practice", "practised": "practiced",
}

# -ise/-yse verbs that US English writes as -ize/-yze. Matched as stem + ending.
BRITISH_ISE_STEMS = [
    "organ", "recogn", "real", "special", "urban", "modern", "standard", "civil", "colon", "commercial", "critic",
    "apolog", "author", "capital", "central", "character", "custom", "final", "formal", "harmon", "immortal",
    "industrial", "legal", "memorial", "minim", "mobil", "national", "neutral", "normal", "optim", "popular",
    "priorit", "public", "revolution", "romantic", "sanit", "stabil", "summar", "symbol", "sympath", "util",
    "visual", "weapon", "fantas", "glamor", "idol", "emphas", "hospital", "patron", "privat", "pedestrian",
    "rational", "regular", "scandal", "secular", "social", "subsid", "terror", "trivial", "vandal", "western",
    "agon", "energ", "equal", "familiar", "fertil", "galvan", "hypnot", "initial", "local", "magnet", "maxim",
    "memor", "mesmer", "monopol", "motor", "natural", "polar", "pressur", "random", "canon", "computer",
    "decentral", "demoral", "digit", "dramat", "econom", "epitom", "evangel", "fossil", "general", "human",
    "ideal", "immun", "italic", "jeopard", "legitim", "liberal", "material", "militar", "moral", "nation",
    "person", "politic", "radical", "rubber", "scrutin", "sensit", "signal", "spiritual",
    "steril", "stigmat", "synchron", "synthes", "theor", "traumat", "tyrann", "unional", "vapor", "victim",
    "vulcan",
]
BRITISH_ISE_RE = re.compile(
    r"\b(" + "|".join(sorted(set(BRITISH_ISE_STEMS), key=len, reverse=True)) + r")is(e|es|ed|ing|ation|ations)\b"
)
BRITISH_YSE_RE = re.compile(r"\b(analy|paraly|cataly|dialy)s(e|ed|ing)\b")  # not "analyses", a valid noun

WORD_RE = re.compile(r"[A-Za-z]+")

# Stock phrases that make an area read like a template when they keep coming back. Warnings.
STOCK_PHRASES = [
    "look closely", "look up", "most visitors", "most people", "walk past", "walk right past", "to this day",
    "remains to this day", "still stands", "today it", "these days", "what few people know", "few people realize",
    "blink and you", "easy to miss", "if you look", "hiding in plain sight", "at first glance",
]
STOCK_PHRASE_LIMIT = 2

# Countries whose signs use a non-Latin script, where localName helps people match the pin to the sign.
NON_LATIN_COUNTRIES = {
    "CN", "HK", "MO", "TW", "JP", "KR", "TH", "RU", "UA", "BY", "BG", "RS", "MK", "GR", "IL", "SA", "AE", "QA",
    "EG", "IR", "IN", "LK", "NP", "MM", "KH", "LA", "GE", "AM", "MN",
}


def check_prose(report: Report, where: str, field: str, text: str) -> None:
    """Style rules that apply to anything the app shows as our own writing."""
    loc = f"{where}.{field}"
    if "—" in text:
        report.error(loc, "contains an em dash; rewrite with a period, comma, colon, or parentheses")
    if "–" in text:
        report.error(loc, "contains an en dash; use 'to' for ranges or rewrite")
    if "--" in text:
        report.error(loc, "contains '--'; rewrite without a dash")
    if " - " in text:
        report.warn(loc, "contains a spaced hyphen used as a dash; rewrite")
    if "!" in text:
        report.error(loc, "contains an exclamation mark")
    if "  " in text:
        report.warn(loc, "contains a double space")
    lowered = text.lower()
    for phrase in BANNED_PHRASES:
        if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", lowered):
            report.error(loc, f"uses the phrase '{phrase}'")
    for phrase in SOFT_PHRASES:
        if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", lowered):
            report.warn(loc, f"uses '{phrase}'; is it earning its place?")
    for match in WORD_RE.finditer(text):
        word = match.group(0)
        if word[0].isupper():
            continue  # Proper names such as "Southbank Centre" keep their own spelling.
        suggestion = BRITISH_WORDS.get(word)
        if suggestion:
            report.error(loc, f"British spelling '{word}'; use '{suggestion}'")
    for regex in (BRITISH_ISE_RE, BRITISH_YSE_RE):
        for match in regex.finditer(text):
            word = match.group(0)
            if word[0].isupper():
                continue
            report.error(loc, f"British spelling '{word}'; use the -ize or -yze form")


# Abbreviations whose period doesn't end a sentence: initials ("C. W. Smith") and a few common short forms.
NOT_SENTENCE_ENDS = re.compile(r"(?:\b[A-Z]|\b(?:No|St|Mr|Mrs|Dr|Jr|Sr|Ltd|Co|vs|c|ca))\.$")


def count_sentences(text: str) -> int:
    # Rough count: a terminator followed by whitespace and an uppercase letter or quote, plus the final one.
    boundaries = [m for m in re.finditer(r"[.?!][\"')”]?\s+(?=[A-Z0-9\"'“(])", text)
                  if not NOT_SENTENCE_ENDS.search(text[:m.start() + 1])]
    return len(boundaries) + 1




WIKI_HOSTS = ("wikipedia.org", "wikidata.org", "wikimedia.org")
SEARCH_OR_AI = re.compile(r"(google\.[a-z.]+/search|bing\.com/search|chatgpt|openai\.com|perplexity\.ai)")


def normalize_url(url: str) -> str:
    """The key a source is stored under, so the same page cited twice is one source."""
    parsed = urllib.parse.urlsplit(url.strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith("wikipedia.org"):
        host = host.replace(".m.wikipedia.org", ".wikipedia.org")
    path = parsed.path.rstrip("/") or "/"
    query = urllib.parse.urlencode(sorted(
        (k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")))
    return f"{host}{path}" + (f"?{query}" if query else "")


ARCHIVE_COPY = re.compile(r"^https?://web\.archive\.org/web/\d+(?:id_)?/(.+)$", re.I)


def read_key(url: str) -> str:
    """The key a read of a page is recorded under: an Internet Archive copy counts as a read of the original."""
    match = ARCHIVE_COPY.match(url.strip())
    original = match.group(1) if match else url
    if not re.match(r"^https?://", original, re.I):
        original = "https://" + original
    return normalize_url(re.sub(r"^http://", "https://", original, flags=re.I))


def check_source(report: Report, where: str, source: object) -> bool:
    if not isinstance(source, dict):
        report.error(where, "source must be an object")
        return False
    for key in ("title", "publisher"):
        if not is_nonempty_string(source.get(key)):
            report.error(where, f"'{key}' must be a non-empty string")
    url = source.get("url")
    parsed = urllib.parse.urlparse(url) if isinstance(url, str) else None
    if not parsed or parsed.scheme != "https" or not parsed.netloc or " " in url:
        report.error(where, f"url must be a full https URL without spaces: {url!r}")
        return False
    if SEARCH_OR_AI.search(url):
        report.error(where, f"url is a search or AI result, not a source: {url}")
        return False
    return True


def is_wiki(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host.endswith(h) for h in WIKI_HOSTS)


def check_fact(report: Report, where: str, fact: dict) -> None:
    """Everything about one fact's text, labels, and sources."""
    if fact.get("category") not in CATEGORIES:
        report.error(where, f"category must be one of {', '.join(CATEGORIES)}")
    if fact.get("veracity") not in VERACITIES:
        report.error(where, f"veracity must be one of {', '.join(VERACITIES)}")

    headline, short, long = fact.get("headline"), fact.get("short"), fact.get("long")
    if not is_nonempty_string(headline):
        report.error(where, "headline must be a non-empty string")
    else:
        if len(headline) > HEADLINE_MAX:
            report.error(where, f"headline is {len(headline)} characters; max {HEADLINE_MAX}")
        if "?" in headline:
            report.error(where, "headline must not be a question")
        check_prose(report, where, "headline", headline)
    if not is_nonempty_string(short):
        report.error(where, "short must be a non-empty string")
    else:
        if len(short) > SHORT_MAX:
            report.error(where, f"short is {len(short)} characters; max {SHORT_MAX}")
        if count_sentences(short) > 2:
            report.warn(where, "short looks like more than two sentences")
        check_prose(report, where, "short", short)
    if not is_nonempty_string(long):
        report.error(where, "long must be a non-empty string")
    else:
        if not LONG_MIN <= len(long) <= LONG_MAX:
            report.error(where, f"long is {len(long)} characters; must be {LONG_MIN} to {LONG_MAX}")
        if isinstance(short, str) and long.strip() == short.strip():
            report.error(where, "long repeats short")
        check_prose(report, where, "long", long)
    if fact.get("veracity") == "legend" and isinstance(long, str) and not re.search(
            r"\b(evidence|record|records|unproven|untrue|myth|legend|story|stories|claim|claims|probably|"
            r"likely|unlikely|say|says|said|told|tell)\b", long, re.I):
        report.warn(where, "a legend's long version should say what the evidence shows")

    sources = fact.get("sources")
    if not isinstance(sources, list) or not sources:
        report.error(where, "needs at least one source")
        return
    urls = []
    for index, source in enumerate(sources):
        if check_source(report, f"{where}.sources[{index}]", source):
            urls.append(source["url"])
    keys = [normalize_url(u) for u in urls]
    if len(set(keys)) != len(keys):
        report.error(where, "the same source is cited twice")
    if urls and all(is_wiki(u) for u in urls):
        report.error(where, "every fact needs at least one source that is not Wikipedia or Wikidata")


# Guide information (CONTENT_GUIDE.md, section 13): the identifier and About are plain reference text, not
# stories, so they have their own limits and stricter rules on hype.
IDENTIFIER_MIN = 3
IDENTIFIER_MAX = 70
ABOUT_MIN = 100
ABOUT_MAX = 700

# Hype and judgment words. Errors in guide text, which must stay neutral. Lowercase words only, so names
# such as "Grand Union Canal" or "Most Holy Redeemer" are left alone.
GUIDE_HYPE_WORDS = [
    "famous", "world-famous", "renowned", "celebrated", "legendary", "world-class", "spectacular", "magnificent",
    "impressive", "beautiful", "beautifully", "stunning", "majestic", "splendid", "glorious", "gorgeous",
    "striking", "elegant", "exquisite", "masterpiece", "beloved", "popular", "treasured", "acclaimed", "landmark",
]
# Superlatives. A plain record belongs in a key fact or a story, not in the About.
GUIDE_SUPERLATIVES = [
    "best", "finest", "greatest", "largest", "biggest", "tallest", "oldest", "longest", "highest",
    "grandest", "busiest", "smallest", "newest", "earliest", "widest", "deepest", "youngest",
]
GUIDE_PHRASES = ["one of the", "among the", "the most", "the least", "known for its", "a must"]


def check_guide(report: Report, where: str, guide: dict) -> None:
    """Everything about a guide's text and sources that a script can check."""
    identifier, about = guide.get("identifier"), guide.get("about")
    if not is_nonempty_string(identifier):
        report.error(where, "identifier must be a non-empty string")
    else:
        if not IDENTIFIER_MIN <= len(identifier) <= IDENTIFIER_MAX:
            report.error(where, f"identifier is {len(identifier)} characters; must be {IDENTIFIER_MIN} to {IDENTIFIER_MAX}")
        if identifier.endswith("."):
            report.error(where, "identifier is a label, not a sentence; drop the final period")
        if not identifier[0].isupper() and not identifier[0].isdigit():
            report.error(where, "identifier starts with a capital letter")
        check_prose(report, where, "identifier", identifier)
        _check_neutral(report, f"{where}.identifier", identifier)
        # One shape everywhere: "[style or material] <what it is>, <year>[, by <maker>]" (CONTENT_GUIDE.md, 13).
        if re.search(r"\bGrade (I|II)|\blisted\b|\b(declared|scheduled) monument", identifier, re.I):
            report.error(where, "identifier repeats the heritage status; that's a key fact")
        if re.search(r"\b(on|in|off|at|near) (the )?[A-Z][a-z]+ (Street|Road|Lane|Avenue|Square|Place|Hill|Row|Court|Way)\b",
                     identifier):
            report.error(where, "identifier names the street; the place page already says where it is")
        if re.search(r"\b\d{4} to \d{4}\b", identifier):
            report.warn(where, "identifier gives a range; use the year it was completed or opened")
    if not is_nonempty_string(about):
        report.error(where, "about must be a non-empty string")
    else:
        if not ABOUT_MIN <= len(about) <= ABOUT_MAX:
            report.error(where, f"about is {len(about)} characters; must be {ABOUT_MIN} to {ABOUT_MAX}")
        sentences = count_sentences(about)
        if sentences < 2:
            report.error(where, "about is two or three sentences")
        elif sentences > 3:
            report.warn(where, "about looks like more than three sentences")
        if not about.endswith((".", ".\"", ".”", ")")):
            report.error(where, "about ends with a full stop")
        check_prose(report, where, "about", about)
        _check_neutral(report, f"{where}.about", about)
        if re.search(r"\b(you|your)\b", about, re.I):
            report.warn(f"{where}.about", "addresses the reader; the About describes the place")
    sources = guide.get("sources")
    if not isinstance(sources, list) or not sources:
        report.error(where, "needs at least one source")
        return
    keys = []
    for index, source in enumerate(sources):
        if check_source(report, f"{where}.sources[{index}]", source):
            keys.append(normalize_url(source["url"]))
    if len(set(keys)) != len(keys):
        report.error(where, "the same source is cited twice")


def _check_neutral(report: Report, loc: str, text: str) -> None:
    words = set(re.findall(r"(?<![A-Za-z])[a-z]+(?:-[a-z]+)*(?![A-Za-z])", text))
    for word in GUIDE_HYPE_WORDS:
        if word in words:
            report.error(loc, f"'{word}' is a judgment; say what the place is, plainly")
    for word in GUIDE_SUPERLATIVES:
        if word in words:
            report.error(loc, f"'{word}' is a superlative; guide text has none (records belong in stories)")
    lowered = text.lower()
    for phrase in GUIDE_PHRASES:
        if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", lowered):
            report.error(loc, f"'{phrase}' is filler; say what the place is")
