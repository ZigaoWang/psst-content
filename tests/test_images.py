"""Photos: license filtering, our own copies, drafts, review, and export."""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from psst import export, images, rules, runs


def test_only_free_licenses_pass():
    for ok in ("CC BY-SA 4.0", "CC BY 2.0", "CC BY-SA 3.0 de", "CC0", "Public domain", "PDM"):
        assert images.is_free(ok), ok
    for bad in ("CC BY-NC 2.0", "CC BY-ND 4.0", "CC BY-NC-SA 2.0", "Fair use", "All rights reserved", "GFDL", ""):
        assert not images.is_free(bad), bad


def _photo(width=3000, height=2000) -> bytes:
    image = Image.new("RGB", (width, height), (120, 80, 40))
    exif = Image.Exif()
    exif[0x010F] = "Camera maker"
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif)
    return buffer.getvalue()


def test_copies_are_resized_hashed_and_carry_no_metadata():
    full, thumb = images.renditions(_photo())
    assert (full.width, full.height) == (1920, 1280)
    assert max(thumb.width, thumb.height) == images.THUMB_EDGE
    assert full.name != thumb.name and full.name.endswith(".jpg") and len(full.name) == 28
    reopened = Image.open(io.BytesIO(full.data))
    assert not reopened.getexif()
    # Small originals are never enlarged.
    small, _ = images.renditions(_photo(640, 480))
    assert (small.width, small.height) == (640, 480)


def test_commons_metadata_is_filtered(monkeypatch):
    def page(title, license_name, categories="Buildings", width=4000, artist='<a href="//commons.wikimedia.org/wiki/User:A">A</a>'):
        return {"title": title, "imageinfo": [{
            "mime": "image/jpeg", "width": width, "height": 3000, "url": "https://upload.wikimedia.org/x.jpg",
            "thumburl": "https://upload.wikimedia.org/2048px-x.jpg", "descriptionurl": f"https://commons.wikimedia.org/wiki/{title}",
            "extmetadata": {"LicenseShortName": {"value": license_name}, "Artist": {"value": artist},
                            "Categories": {"value": categories}, "DateTimeOriginal": {"value": "1905"},
                            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"}}}]}
    pages = [page("File:Good.jpg", "CC BY-SA 4.0"), page("File:NC.jpg", "CC BY-NC 2.0"),
             page("File:Made.jpg", "CC0", categories="AI-generated images"), page("File:Tiny.jpg", "CC0", width=400),
             page("File:Anon.jpg", "Public domain", artist="")]
    monkeypatch.setattr(images, "_commons", lambda params: {"query": {"pages": pages}})
    monkeypatch.setattr(images.time, "sleep", lambda s: None)
    found = images.commons_files([p["title"] for p in pages], "test")
    assert [c.title for c in found] == ["Good.jpg"]
    assert found[0].author == "A" and found[0].author_url == "https://commons.wikimedia.org/wiki/User:A"
    assert found[0].year == 1905


def test_alt_text_rules():
    for bad in ("Photo of the church", "short", "A church — tall"):
        r = rules.Report()
        images.check_alt("x", bad, r)
        assert r.errors, bad
    r = rules.Report()
    images.check_alt("x", "The brick front of the chapel, seen from the corner of the lane.", r)
    assert not r.errors


@pytest.fixture
def place(scratch):
    row = scratch.execute("""SELECT p.id FROM places p WHERE p.state = 'active' AND EXISTS (
                                 SELECT 1 FROM facts f WHERE f.place_id = p.id AND f.state = 'published')
                             AND NOT EXISTS (SELECT 1 FROM images i WHERE i.place_id = p.id) LIMIT 1""").fetchone()
    return row["id"]


def _candidate(place_id: str, tmp_path) -> images.Candidate:
    candidate = images.Candidate(
        key="commons:File:Test photo.jpg", source="commons", source_ref="File:Test photo.jpg",
        source_url="https://commons.wikimedia.org/wiki/File:Test_photo.jpg", image_url="https://example.org/x.jpg",
        preview_url="https://example.org/p.jpg", title="Test photo.jpg", author="A. Photographer", author_url=None,
        license="CC BY-SA 4.0", license_url="https://creativecommons.org/licenses/by-sa/4.0", width=3000,
        height=2000, year=2019, found_via="test")
    (tmp_path / place_id).mkdir()
    (tmp_path / place_id / "candidates.json").write_text(json.dumps([candidate.__dict__]))
    return candidate


def test_photo_draft_review_and_export(scratch, place, tmp_path, monkeypatch):
    monkeypatch.setattr(images, "fetch_bytes", lambda url: _photo())
    uploads = []
    monkeypatch.setattr(images, "upload", lambda host, files: uploads.extend(files))
    candidate = _candidate(place, tmp_path)
    alt = "The front of the building from across the street, on a clear day."

    assert not images.check_draft(scratch, [{"place": place, "key": "commons:File:Other.jpg", "alt": alt}], tmp_path).ok
    # A historic photo takes the year Commons records when none is given.
    historic = {"place": place, "key": candidate.key, "alt": alt, "kind": "historic"}
    assert images.check_draft(scratch, [historic], tmp_path).ok and historic["year"] == 2019
    entry = {"place": place, "key": candidate.key, "alt": alt, "focus": [0.5, 0.3]}
    assert images.check_draft(scratch, [entry], tmp_path).ok

    research_run = runs.require(scratch, runs.start(scratch, "research", "test-researcher"))
    image_id = images.add(scratch, "host", research_run, place, candidate, alt, [0.5, 0.3], "photo", None)
    assert len(uploads) == 2
    row = scratch.execute("SELECT * FROM images WHERE id = %s", (image_id,)).fetchone()
    assert row["state"] == "draft" and row["author"] == "A. Photographer" and row["license"] == "CC BY-SA 4.0"
    # The same photo can't be added twice.
    assert not images.check_draft(scratch, [entry], tmp_path).ok

    reviewer = runs.require(scratch, runs.start(scratch, "review", "test-reviewer"))
    decision = {image_id: {"decision": "approve", "notes": "Matches the listing photo and the street layout."}}
    assert any("never looked" in e for e in images.check_decisions(scratch, decision, reviewer).errors)
    scratch.execute("INSERT INTO image_views (run_id, image_id) VALUES (%s, %s)", (reviewer["id"], image_id))
    assert images.check_decisions(scratch, decision, reviewer).ok
    assert not images.check_decisions(scratch, {image_id: {"decision": "approve", "notes": ""}}, reviewer).ok
    edit = {image_id: {"decision": "edit", "notes": "Moved the focus onto the doorway.", "focus": [0.4, 0.6]}}
    images.apply_decisions(scratch, edit, reviewer)
    row = scratch.execute("SELECT * FROM images WHERE id = %s", (image_id,)).fetchone()
    assert row["state"] == "reviewed" and abs(row["focus_y"] - 0.6) < 1e-6
    changes = [e["changes"] for e in scratch.execute("SELECT changes FROM image_events WHERE image_id = %s ORDER BY id",
                                                     (image_id,))]
    assert ["focus"] in changes

    # Reviewed photos are exported only when a publish includes them.
    assert image_id not in export.build(scratch, tmp_path / "a").images
    result = export.build(scratch, tmp_path / "b", include_images=[image_id])
    assert image_id in result.images


def test_rejected_photos_are_retired(scratch, place, tmp_path, monkeypatch):
    monkeypatch.setattr(images, "fetch_bytes", lambda url: _photo())
    monkeypatch.setattr(images, "upload", lambda host, files: None)
    candidate = _candidate(place, tmp_path)
    research_run = runs.require(scratch, runs.start(scratch, "research", "test-researcher"))
    image_id = images.add(scratch, "host", research_run, place, candidate, "A row of houses on a wet afternoon.",
                          [0.5, 0.5], "photo", None)
    reviewer = runs.require(scratch, runs.start(scratch, "review", "test-reviewer"))
    images.apply_decisions(scratch, {image_id: {"decision": "reject", "notes": "Shows the building next door."}},
                           reviewer)
    row = scratch.execute("SELECT state, retire_reason FROM images WHERE id = %s", (image_id,)).fetchone()
    assert row["state"] == "retired" and "next door" in row["retire_reason"]
    assert image_id not in export.build(scratch, tmp_path / "c", include_images=[image_id]).images
