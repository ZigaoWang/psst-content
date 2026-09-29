"""The weekly link check: every source a live fact cites is requested, and facts whose sources are dead
are flagged for review.

A source counts as dead only after failing two checks in a row (a week apart when run from cron), so a
site that's down for an afternoon flags nothing. Sites that refuse scripts (401, 403, 429, and the like)
are recorded as blocked, which is neither a pass nor a failure.
"""

from __future__ import annotations

import concurrent.futures
import http.client
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import net

FAILURES_TO_FLAG = 2
BLOCKED = {401, 403, 405, 406, 429, 451, 999}
TIMEOUT = 25
WORKERS = 8


@dataclass
class Check:
    source_id: str
    status: str   # the HTTP status, or dns, refused, timeout, tls, error
    outcome: str  # ok, failed, or blocked


def classify(status: int | str) -> str:
    if isinstance(status, int):
        if 200 <= status < 400:
            return "ok"
        return "blocked" if status in BLOCKED else "failed"
    return "failed"


def fetch_status(url: str) -> int | str:
    request = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.gaierror):
            return "dns"
        if isinstance(reason, ConnectionRefusedError):
            return "refused"
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "timeout"
        if isinstance(reason, ssl.SSLError):
            return "tls"
        return "error"
    except (TimeoutError, socket.timeout):
        return "timeout"
    except (http.client.HTTPException, ConnectionError, ValueError):
        return "error"


def check(sources: list[dict], fetch=fetch_status) -> list[Check]:
    def one(source: dict) -> Check:
        status = fetch(source["url"])
        return Check(source["id"], str(status), classify(status))
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(one, sources))


def record(conn, results: list[Check]) -> dict[str, int]:
    """Store the results and flag facts whose sources have now failed twice in a row."""
    counts = {"ok": 0, "failed": 0, "blocked": 0, "flagged": 0}
    for r in results:
        counts[r.outcome] += 1
        conn.execute("""UPDATE sources SET last_checked_at = now(), last_check_status = %s,
                        failed_checks = CASE %s WHEN 'failed' THEN failed_checks + 1
                                                WHEN 'ok' THEN 0 ELSE failed_checks END
                        WHERE id = %s""", (r.status, r.outcome, r.source_id))
    dead = conn.execute("""
        SELECT f.id AS fact_id, s.url, s.last_check_status AS status
        FROM sources s JOIN fact_sources fs ON fs.source_id = s.id JOIN facts f ON f.id = fs.fact_id
        WHERE s.failed_checks >= %s AND f.state IN ('published', 'reviewed') AND NOT f.needs_review
        ORDER BY f.id""", (FAILURES_TO_FLAG,)).fetchall()
    flagged: dict[str, list[str]] = {}
    for row in dead:
        flagged.setdefault(row["fact_id"], []).append(f"{row['url']} ({row['status']})")
    for fact_id, urls in flagged.items():
        conn.execute("SELECT set_config('psst.note', %s, true)",
                     ("Flagged: a source failed two link checks in a row: " + "; ".join(urls),))
        conn.execute("UPDATE facts SET needs_review = true WHERE id = %s", (fact_id,))
    counts["flagged"] = len(flagged)
    return counts


def live_sources(conn, limit: int | None = None) -> list[dict]:
    return conn.execute("""
        SELECT DISTINCT s.id, s.url FROM sources s JOIN fact_sources fs ON fs.source_id = s.id
        JOIN facts f ON f.id = fs.fact_id WHERE f.state <> 'retired'
        ORDER BY s.id LIMIT %s""", (limit,)).fetchall()
