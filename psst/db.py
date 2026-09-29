"""Database access. The database only listens on the VPS itself, so the CLI reaches it through an SSH
tunnel it opens and closes on its own. Set PSST_DATABASE_URL to connect directly instead (the server
scripts on the VPS do this)."""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "db" / "migrations"
CONFIG = Path(os.environ.get("PSST_CONFIG", Path.home() / ".config" / "psst" / "env"))

_tunnel: subprocess.Popen | None = None
_tunnel_port: int | None = None


def _settings() -> dict[str, str]:
    values: dict[str, str] = {}
    if CONFIG.exists():
        for line in CONFIG.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    values.update({k: v for k, v in os.environ.items() if k.startswith("PSST_")})
    return values


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _open_tunnel(host: str) -> int:
    global _tunnel, _tunnel_port
    if _tunnel and _tunnel.poll() is None and _tunnel_port:
        return _tunnel_port
    port = _free_port()
    _tunnel = subprocess.Popen(
        ["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
         "-L", f"127.0.0.1:{port}:127.0.0.1:5432", host],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    atexit.register(_close_tunnel)
    deadline = time.time() + 20
    while time.time() < deadline:
        if _tunnel.poll() is not None:
            raise RuntimeError(f"SSH tunnel to {host} failed: {_tunnel.stderr.read().decode().strip()}")
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                _tunnel_port = port
                return port
        time.sleep(0.2)
    raise RuntimeError(f"SSH tunnel to {host} did not open in time")


def _close_tunnel() -> None:
    if _tunnel and _tunnel.poll() is None:
        _tunnel.terminate()


def conninfo() -> str:
    settings = _settings()
    if settings.get("PSST_DATABASE_URL"):
        return settings["PSST_DATABASE_URL"]
    password = settings.get("PSST_DB_PASSWORD")
    if not password:
        raise RuntimeError(f"No PSST_DB_PASSWORD in {CONFIG} or the environment. See README.md, Setup.")
    port = _open_tunnel(settings.get("PSST_SSH_HOST", "bwh"))
    return f"host=127.0.0.1 port={port} dbname=psst user=psst password={password} application_name=psst-cli"


@contextmanager
def connect(actor: str | None = None, run: str | None = None, note: str | None = None):
    """A connection in one transaction: committed on success, rolled back on any error.
    `actor`, `run`, and `note` end up in fact_events for every lifecycle change made through it."""
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET LOCAL search_path = psst, public")
                for key, value in (("psst.actor", actor), ("psst.run", run), ("psst.note", note)):
                    if value:
                        cur.execute("SELECT set_config(%s, %s, true)", (key, value))
            yield conn


def migrate() -> list[str]:
    """Apply every migration in db/migrations that hasn't been applied yet, in order."""
    applied_now = []
    with psycopg.connect(conninfo(), autocommit=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS public.psst_schema_migrations "
                     "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {row[0] for row in conn.execute("SELECT version FROM public.psst_schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.stem in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO public.psst_schema_migrations (version) VALUES (%s)", (path.stem,))
            applied_now.append(path.stem)
    return applied_now
