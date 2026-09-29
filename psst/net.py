"""HTTP helpers for Wikidata, OpenStreetMap, and Wikipedia, with the retries and fallbacks those shared
services need. Requests carry a plain User-Agent and nothing personal."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "PsstContent/1.0"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def fetch_json(url: str, data: bytes | None = None, attempts: int = 5, timeout: int = 90):
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 404:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            time.sleep(min(float(retry_after) if retry_after and retry_after.isdigit() else 3 * (attempt + 1), 30))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError) as exc:
            last_error = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"request failed after {attempts} attempts: {url[:120]} ({last_error})")


def overpass(query: str) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode()
    errors = []
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            return fetch_json(endpoint, data=data, attempts=3, timeout=180)
        except RuntimeError as exc:
            errors.append(str(exc))
    raise RuntimeError("Overpass unavailable: " + "; ".join(errors))


def osm_elements(refs: list[str], batch: int = 100) -> dict[str, dict]:
    """Tags and center for OSM elements ("node/1", "way/2"), keyed by ref."""
    found: dict[str, dict] = {}
    for start in range(0, len(refs), batch):
        chunk = refs[start:start + batch]
        parts = "".join(f"{r.split('/')[0]}({r.split('/')[1]});" for r in chunk)
        payload = overpass(f"[out:json][timeout:120];({parts});out center tags;")
        for element in payload.get("elements", []):
            ref = f"{element['type']}/{element['id']}"
            center = element.get("center") or {"lat": element.get("lat"), "lon": element.get("lon")}
            found[ref] = {"tags": element.get("tags", {}), "lat": center.get("lat"), "lon": center.get("lon")}
        time.sleep(1)
    return found


def osm_tags(refs: list[str], batch: int = 100) -> dict[str, dict]:
    """Tags for OSM elements through the main API's multi-fetch, which is faster and more reliable than
    Overpass when only tags are needed. Deleted elements are simply missing from the result."""
    found: dict[str, dict] = {}
    by_type: dict[str, list[str]] = {}
    for ref in refs:
        kind, number = ref.split("/")
        by_type.setdefault(kind, []).append(number)
    for kind, numbers in by_type.items():
        for start in range(0, len(numbers), batch):
            chunk = numbers[start:start + batch]
            url = f"https://api.openstreetmap.org/api/0.6/{kind}s.json?{kind}s=" + ",".join(chunk)
            try:
                payload = fetch_json(url, attempts=4)
            except urllib.error.HTTPError:
                # One gone element fails the whole batch; fall back to asking one at a time.
                payload = {"elements": []}
                for number in chunk:
                    try:
                        payload["elements"] += fetch_json(
                            f"https://api.openstreetmap.org/api/0.6/{kind}/{number}.json", attempts=3)["elements"]
                    except (urllib.error.HTTPError, RuntimeError):
                        continue
            for element in payload.get("elements", []):
                found[f"{element['type']}/{element['id']}"] = {"tags": element.get("tags", {})}
            time.sleep(0.5)
    return found


def wikidata_entities(qids: list[str], props: str = "labels|claims", batch: int = 50) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for start in range(0, len(qids), batch):
        chunk = qids[start:start + batch]
        url = ("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props="
               + urllib.parse.quote(props) + "&ids=" + "|".join(chunk))
        payload = fetch_json(url)
        for qid, entity in payload.get("entities", {}).items():
            if "missing" not in entity:
                found[qid] = entity
                redirected = entity.get("redirects", {}).get("from")
                if redirected:
                    found[redirected] = entity
        time.sleep(0.5)
    return found
