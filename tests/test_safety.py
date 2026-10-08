"""Safety rules that keep parallel sessions, the admin page, and the page reader from doing harm."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from psst import fetch, guides, publish, research, runs

ROOT = Path(__file__).resolve().parent.parent


def api_module():
    spec = importlib.util.spec_from_file_location("psst_api", ROOT / "server" / "api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("headers, allowed", [
    ({"X-Psst-Admin": "1", "Origin": "https://psst.zigao.wang", "Sec-Fetch-Site": "same-origin"}, True),
    ({"X-Psst-Admin": "1"}, True),  # curl from the owner's machine
    ({}, False),  # a plain form post from anywhere
    ({"X-Psst-Admin": "1", "Origin": "https://evil.example"}, False),
    ({"X-Psst-Admin": "1", "Sec-Fetch-Site": "cross-site"}, False),
])
def test_admin_buttons_only_answer_the_admin_page(headers, allowed):
    assert api_module().admin_request_allowed(headers) is allowed


@pytest.mark.parametrize("url, public", [
    ("http://127.0.0.1:5432/", False), ("http://localhost/", False), ("http://10.0.0.5/", False),
    ("http://169.254.169.254/latest/meta-data", False), ("file:///etc/passwd", False),
])
def test_the_page_reader_stays_on_the_public_web(url, public):
    assert fetch.is_public(url) is public


def test_the_server_refuses_to_read_a_private_address(monkeypatch):
    monkeypatch.setattr(fetch, "PUBLIC_ONLY", True)
    assert fetch._get("http://127.0.0.1:5432/")[0] == 403


def start(conn, kind):
    return runs.require(conn, runs.start(conn, kind, "test"), kind)


def test_a_guide_claim_is_never_taken_over(scratch):
    first, second = start(scratch, "research"), start(scratch, "research")
    places = guides.claim(scratch, first["id"], 3)
    if not places:
        pytest.skip("no places need guides")
    ids = [p["id"] for p in places]
    assert guides.claim(scratch, second["id"], 3, places=ids) == []
    owners = {r["run_id"] for r in scratch.execute("SELECT run_id FROM guide_claims WHERE place_id = ANY(%s)", (ids,))}
    assert owners == {first["id"]}


def test_a_research_cell_is_never_taken_over(scratch):
    first, second = start(scratch, "research"), start(scratch, "research")
    cell = research.claim(scratch, first["id"])["cell"]
    assert research._take(scratch, cell, second["id"]) is False
    assert research._take(scratch, cell, first["id"]) is True


def test_a_run_cant_review_its_own_photos(scratch):
    from psst import images
    row = scratch.execute("SELECT id, added_run FROM images LIMIT 1").fetchone()
    if not row:
        pytest.skip("no photos")
    report = images.check_decisions(scratch, [{"image": row["id"], "decision": "approve", "notes": "Looked at it."}],
                                    {"id": row["added_run"]})
    assert any("its own" in e or "photos it added" in e for e in report.errors)


def test_promote_refuses_a_staging_version_it_didnt_check(monkeypatch):
    monkeypatch.setattr(publish, "_ssh", lambda host, command: '{"contentVersion": "20990101T000000Z"}')
    with pytest.raises(RuntimeError, match="Staging changed"):
        publish.promote("local", "20260101T000000Z")


def test_one_publish_at_a_time(database):
    import psycopg
    from psycopg.rows import dict_row
    from psst import db
    other = psycopg.connect(db.conninfo(), row_factory=dict_row, autocommit=True)
    try:
        publish.lock(other)
        mine = psycopg.connect(db.conninfo(), row_factory=dict_row, autocommit=True)
        try:
            with pytest.raises(RuntimeError, match="Another publish"):
                publish.lock(mine)
        finally:
            mine.close()
    finally:
        other.close()
