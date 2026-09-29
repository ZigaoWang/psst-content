"""The Psst API: two endpoints, both write-only and anonymous.

    POST /api/v1/reports  {"factId": "fa_...", "reason": "wrong", "message": "...", "appVersion": "1.1 (7)"}
    POST /api/v1/demand   {"cell": "85194ad3fffffff"}   (an H3 resolution 5 cell of an empty map view)

It runs behind nginx (which rate limits and caps request size) on 127.0.0.1:8787, connects as the
psst_api role, and can only call psst.submit_report and psst.record_demand. It stores no IP addresses
and no identifiers. Standard library plus psycopg only.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import h3
import psycopg

REASONS = {"wrong", "outdated", "location", "offensive", "other"}
FACT_ID = re.compile(r"^fa_[0-9a-hjkmnp-tv-z]{10}$")
DEMAND_RESOLUTION = 5  # hexagons of about 250 km²; nothing finer is ever stored
MAX_BODY = 4096

log = logging.getLogger("psst-api")
_local = threading.local()


def connection() -> psycopg.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None or conn.closed:
        conn = psycopg.connect(os.environ["PSST_API_DATABASE_URL"], autocommit=True,
                               application_name="psst-api")
        _local.conn = conn
    return conn


class Handler(BaseHTTPRequestHandler):
    server_version = "psst-api"
    sys_version = ""

    def log_message(self, format, *args):  # no IPs in logs
        log.info("%s %s", self.command, self.path)

    def _reply(self, status: int, body: dict | None = None) -> None:
        payload = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/api/v1/health":
            try:
                connection().execute("SELECT 1")
                return self._reply(200, {"ok": True})
            except psycopg.Error:
                return self._reply(503, {"ok": False})
        self._reply(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._reply(413, {"error": "body too large or empty"})
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._reply(400, {"error": "invalid JSON"})
        try:
            if self.path == "/api/v1/reports":
                return self._report(body)
            if self.path == "/api/v1/demand":
                return self._demand(body)
        except (psycopg.errors.RaiseException, psycopg.errors.NoDataFound, psycopg.errors.InvalidParameterValue):
            return self._reply(422, {"error": "rejected"})
        except psycopg.Error:
            log.exception("database error")
            _local.conn = None
            return self._reply(503, {"error": "try again later"})
        self._reply(404, {"error": "not found"})

    def _report(self, body: dict):
        fact_id, reason = body.get("factId"), body.get("reason")
        message = body.get("message") or ""
        version = str(body.get("appVersion") or "")[:40]
        if not isinstance(fact_id, str) or not FACT_ID.match(fact_id) or reason not in REASONS \
                or not isinstance(message, str):
            return self._reply(400, {"error": "factId and a known reason are required"})
        connection().execute("SELECT psst.submit_report(%s, %s, %s, %s)", (fact_id, reason, message[:1000], version))
        self._reply(204)

    def _demand(self, body: dict):
        # Only a coarse cell id is accepted. Anything else, coordinates included, is refused unread.
        cell = body.get("cell")
        if set(body) != {"cell"} or not isinstance(cell, str) or not h3.is_valid_cell(cell) \
                or h3.get_resolution(cell) != DEMAND_RESOLUTION:
            return self._reply(400, {"error": f"send only a resolution {DEMAND_RESOLUTION} cell id"})
        connection().execute("SELECT psst.record_demand(%s)", (cell,))
        self._reply(204)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PSST_API_PORT", "8787"))), Handler)
    server.daemon_threads = True
    log.info("listening on %s:%s", *server.server_address)
    server.serve_forever()


if __name__ == "__main__":
    main()
