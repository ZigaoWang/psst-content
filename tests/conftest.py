"""Shared fixtures. Tests that need the database or the network skip cleanly without them."""

from __future__ import annotations

import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from psst import db, publish

ROOT = Path(__file__).resolve().parent.parent
# The last commit that still had the schema 1 area files. The migration test compares against it forever.
# It lives in the private psst-content-archive repository, checked out next to this one.
LEGACY_REF = os.environ.get("PSST_LEGACY_REF", "legacy-areas")
ARCHIVE = Path(os.environ.get("PSST_ARCHIVE_REPO", ROOT.parent / "psst-content-archive"))


@pytest.fixture(scope="session")
def database():
    """A connection whose every change is rolled back at the end of each test."""
    try:
        info = db.conninfo()
    except RuntimeError as exc:
        pytest.skip(f"no database: {exc}")
    with psycopg.connect(info, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture
def scratch(database):
    with database.transaction(force_rollback=True):
        database.execute("SET LOCAL search_path = psst, public")
        yield database


@pytest.fixture(scope="session")
def legacy_repo() -> Path:
    """The schema 1 area files: $PSST_LEGACY_REPO, else the `legacy-areas` tag in the archive repository."""
    if os.environ.get("PSST_LEGACY_REPO"):
        return Path(os.environ["PSST_LEGACY_REPO"])
    repo = next((r for r in (ARCHIVE, ROOT) if r.is_dir() and subprocess.run(
        ["git", "-C", str(r), "rev-parse", "-q", "--verify", f"{LEGACY_REF}^{{commit}}"],
        capture_output=True).returncode == 0), None)
    if not repo:
        pytest.skip("no legacy area files (clone psst-content-archive next to this repository)")
    target = Path(tempfile.mkdtemp(prefix="psst-legacy-"))
    archive = subprocess.run(["git", "-C", str(repo), "archive", LEGACY_REF, "areas"],
                             check=True, capture_output=True).stdout
    tar_path = target / "areas.tar"
    tar_path.write_bytes(archive)
    with tarfile.open(tar_path) as tar:
        tar.extractall(target, filter="data")
    return target


@pytest.fixture(scope="session")
def production():
    try:
        live = publish.fetch_channel(publish.settings()["url"], "production")
    except OSError as exc:
        pytest.skip(f"production unreachable: {exc}")
    if not live:
        pytest.skip("production has no content yet")
    return live
