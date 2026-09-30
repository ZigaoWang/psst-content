"""Publishing: export, upload to staging, check staging over HTTPS, promote to production.
See docs/DESIGN.md, "App format" and "Staging check".

Nothing in the database changes until production has the new content, so a failed publish leaves both
the database and users exactly as they were.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import shlex
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import jsonschema

from . import db, export, net

REMOTE_ROOT = "/www/wwwroot/psst/public/content"
MAX_SHRINK = 0.02
KEEP_VERSIONS = 10


def settings() -> dict[str, str]:
    values = db._settings()
    return {"host": values.get("PSST_SSH_HOST", "bwh"),
            "url": values.get("PSST_CONTENT_URL", "https://psst.zigao.wang").rstrip("/")}


def _ssh(host: str, command: str) -> str:
    return subprocess.run(["ssh", host, command], check=True, capture_output=True, text=True).stdout


def _channel(channel: str) -> str:
    return f"{REMOTE_ROOT}/{channel}/v{export.FORMAT_VERSION}"


def upload_staging(host: str, directory: Path) -> None:
    remote = _channel("staging")
    subprocess.run(["rsync", "-a", f"{directory}/packs/", f"{host}:{remote}/packs/"], check=True)
    subprocess.run(["scp", "-q", str(directory / "manifest.json"), f"{host}:{remote}/manifest.json.tmp"], check=True)
    _ssh(host, f"mv {remote}/manifest.json.tmp {remote}/manifest.json")


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def fetch_channel(base_url: str, channel: str) -> tuple[dict, dict, dict[str, dict]] | None:
    """Download a channel exactly as the app would, checking every hash. None if it has nothing yet."""
    root = f"{base_url}/content/{channel}/v{export.FORMAT_VERSION}"
    try:
        manifest = json.loads(_get(f"{root}/manifest.json"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise

    def pack(entry: dict) -> dict:
        data = _get(f"{root}/{entry['file']}")
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise export.ExportError(f"{channel}: {entry['file']} doesn't match its hash")
        return json.loads(gzip.decompress(data))

    common = pack(manifest["common"])
    cities = {entry["cityId"]: pack(entry) for entry in manifest["cities"]}
    return manifest, common, cities


def _served(url: str) -> bool:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": net.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status == 200 and response.headers.get("Content-Type", "").startswith("image/")
    except (urllib.error.URLError, TimeoutError):
        return False


def image_files(cities: dict[str, dict]) -> set[str]:
    return {image[size]["file"] for city in cities.values() for place in city["places"]
            for image in place.get("images", []) for size in ("full", "thumb")}


def check_staging(base_url: str, allow_shrink: str | None = None) -> list[str]:
    """Everything that must be true before users get this content. Returns problems; empty means go."""
    problems: list[str] = []
    staged = fetch_channel(base_url, "staging")
    if not staged:
        return ["staging has no content"]
    manifest, common, cities = staged
    try:
        jsonschema.validate(manifest, export._schema("manifest"))
        jsonschema.validate(common, export._schema("common"))
        for city in cities.values():
            jsonschema.validate(city, export._schema("city"))
    except jsonschema.ValidationError as error:
        problems.append(f"schema: {error.message} at {'/'.join(map(str, error.absolute_path))}")

    place_ids = {p["id"] for city in cities.values() for p in city["places"]}
    fact_count = sum(len(p["facts"]) for city in cities.values() for p in city["places"])
    if manifest["counts"] != {"places": len(place_ids), "facts": fact_count}:
        problems.append("the manifest's counts don't match the packs")
    listed = {c["id"] for c in common["cities"]}
    if listed != set(cities):
        problems.append("the city list doesn't match the city packs")
    tag_ids = {t["id"] for t in common["tags"]}
    area_ids = {a["id"] for a in common["areas"]}
    for city in cities.values():
        for place in city["places"]:
            for key in ("districtId", "neighborhoodId"):
                if place.get(key) and place[key] not in area_ids:
                    problems.append(f"{place['id']} refers to a missing area {place[key]}")
            for fact in place["facts"]:
                missing = [t for t in fact.get("tags", []) if t not in tag_ids]
                if missing:
                    problems.append(f"{fact['id']} refers to unpublished tags {missing}")

    live = fetch_channel(base_url, "production")
    # Every photo file the packs name must be served; new ones are checked one by one.
    new_files = image_files(cities) - (image_files(live[2]) if live else set())
    for name in sorted(new_files):
        if not _served(f"{base_url}/images/{name}"):
            problems.append(f"photo file {name} isn't served at /images/")
    if live:
        live_manifest, live_common, _ = live
        lost = [old for old in live_common["legacyIds"] if old not in common["legacyIds"]]
        if lost:
            problems.append(f"{len(lost)} old ids would stop resolving, e.g. {lost[:3]}")
        for key in ("places", "facts"):
            before, after = live_manifest["counts"][key], manifest["counts"][key]
            if after < before * (1 - MAX_SHRINK) and not allow_shrink:
                problems.append(f"{key} would drop from {before} to {after}; pass --allow-shrink with a reason")
    return problems


def promote(host: str) -> dict:
    """Copy staging's packs into production and swap production's manifest in one atomic step."""
    staging, production = _channel("staging"), _channel("production")
    manifest = json.loads(_ssh(host, f"cat {staging}/manifest.json"))
    files = [manifest["common"]["file"]] + [c["file"] for c in manifest["cities"]]
    copies = " && ".join(f"cp -n {staging}/{shlex.quote(f)} {production}/{shlex.quote(f)}" for f in files)
    version = manifest["contentVersion"]
    _ssh(host, f"mkdir -p {production}/packs {production}/history && {copies} && "
               f"cp {staging}/manifest.json {production}/history/{version}.json && "
               f"cp {staging}/manifest.json {production}/manifest.json.tmp && "
               f"mv {production}/manifest.json.tmp {production}/manifest.json")
    return manifest


def rollback(host: str, version: str | None) -> str:
    """Point production back at an earlier manifest (the one before the current, by default)."""
    production = _channel("production")
    history = sorted(_ssh(host, f"ls {production}/history").split())
    current = json.loads(_ssh(host, f"cat {production}/manifest.json"))["contentVersion"]
    if version is None:
        earlier = [h[:-5] for h in history if h[:-5] < current]
        if not earlier:
            raise RuntimeError("There is no earlier version to roll back to.")
        version = earlier[-1]
    if f"{version}.json" not in history:
        raise RuntimeError(f"No production version {version}. Known: {', '.join(h[:-5] for h in history)}")
    _ssh(host, f"cp {production}/history/{version}.json {production}/manifest.json.tmp && "
               f"mv {production}/manifest.json.tmp {production}/manifest.json")
    return version


def prune(host: str) -> int:
    """Delete packs no longer referenced by staging or the last KEEP_VERSIONS production manifests."""
    staging, production = _channel("staging"), _channel("production")
    history = sorted(_ssh(host, f"ls {production}/history 2>/dev/null || true").split())[-KEEP_VERSIONS:]
    keep = set()
    manifests = [f"{production}/history/{h}" for h in history] + [f"{staging}/manifest.json"]
    for path in manifests:
        try:
            m = json.loads(_ssh(host, f"cat {path}"))
        except subprocess.CalledProcessError:
            continue
        keep.update([m["common"]["file"]] + [c["file"] for c in m["cities"]])
    removed = 0
    for channel_dir in (staging, production):
        for name in _ssh(host, f"ls {channel_dir}/packs 2>/dev/null || true").split():
            if f"packs/{name}" not in keep:
                _ssh(host, f"rm {channel_dir}/packs/{shlex.quote(name)}")
                removed += 1
    return removed


def bundle(base_url: str, app: Path) -> dict:
    """Write production's current content into the app, as the snapshot it ships with."""
    live = fetch_channel(base_url, "production")
    if not live:
        raise RuntimeError("Production has no content yet; publish first.")
    manifest, _, _ = live
    target = app / "Content" / f"v{export.FORMAT_VERSION}"
    if target.exists():
        for old in target.rglob("*"):
            if old.is_file() and old.name != ".gitkeep":
                old.unlink()
    (target / "packs").mkdir(parents=True, exist_ok=True)
    root = f"{base_url}/content/production/v{export.FORMAT_VERSION}"
    for entry in [manifest["common"]] + manifest["cities"]:
        (target / entry["file"]).write_bytes(_get(f"{root}/{entry['file']}"))
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
