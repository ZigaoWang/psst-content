"""Pipeline runs: every batch of work (research, review, tagging, publishing) is a run, and every change
it makes points back to it."""

from __future__ import annotations

import getpass
import os

from . import ids

KINDS = ("import", "research", "review", "tagging", "names", "verify", "publish", "manual")


def operator() -> str:
    return os.environ.get("PSST_OPERATOR") or getpass.getuser()


def start(conn, kind: str, model: str | None, cell: str | None = None, notes: str | None = None) -> str:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    run_id = ids.new("run")
    conn.execute("INSERT INTO pipeline_runs (id, kind, model, operator, cell, notes) VALUES (%s, %s, %s, %s, %s, %s)",
                 (run_id, kind, model, operator(), cell, notes))
    return run_id


def finish(conn, run_id: str) -> None:
    conn.execute("UPDATE pipeline_runs SET finished_at = now() WHERE id = %s", (run_id,))


def require(conn, run_id: str | None, kind: str | None = None) -> dict:
    """The run a command is working under. Every change must belong to an open run."""
    if not run_id:
        raise RuntimeError("Start a run first (psst run start ...) and pass --run or set PSST_RUN.")
    run = conn.execute("SELECT * FROM pipeline_runs WHERE id = %s", (run_id,)).fetchone()
    if not run:
        raise RuntimeError(f"No run {run_id}")
    if run["finished_at"]:
        raise RuntimeError(f"Run {run_id} is already finished; start a new one")
    if kind and run["kind"] != kind:
        raise RuntimeError(f"Run {run_id} is a {run['kind']} run, not {kind}")
    return run
