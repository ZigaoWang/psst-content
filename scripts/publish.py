#!/usr/bin/env python3
"""Validate every area and copy the set into the app, ready to build.

Usage:
    python3 scripts/publish.py                    # into ../psst-map (or $PSST_APP_DIR)
    python3 scripts/publish.py --app /path/to/psst-map
    python3 scripts/publish.py --online           # also re-check every coordinate first

The app bundles whatever is in its Content/areas folder, so publishing is a sync: new and changed
files are copied, and files for areas that no longer exist here are removed. Nothing is copied if any
area fails validation, so a broken file never reaches a build.
"""

import argparse
import filecmp
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AREAS_DIR = ROOT / "areas"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    default_app = os.environ.get("PSST_APP_DIR", str(ROOT.parent / "psst-map"))
    parser.add_argument("--app", default=default_app, help=f"the app repository (default {default_app})")
    parser.add_argument("--online", action="store_true", help="also verify coordinates online")
    args = parser.parse_args()

    target = Path(args.app).expanduser().resolve() / "Content" / "areas"
    if not target.parent.parent.joinpath("project.yml").exists():
        print(f"{target.parent.parent} doesn't look like the psst-map app repository", file=sys.stderr)
        return 1

    check = [sys.executable, str(ROOT / "scripts" / "validate.py"), "--quiet-warnings"]
    if args.online:
        check.append("--online")
    print("Validating...", file=sys.stderr)
    if subprocess.run(check).returncode != 0:
        print("\nNot published: fix the errors above first.", file=sys.stderr)
        return 1

    target.mkdir(parents=True, exist_ok=True)
    sources = {p.name: p for p in sorted(AREAS_DIR.glob("*.json"))}
    added, updated, removed = [], [], []
    for name, source in sources.items():
        destination = target / name
        if not destination.exists():
            added.append(name)
        elif not filecmp.cmp(source, destination, shallow=False):
            updated.append(name)
        else:
            continue
        shutil.copy2(source, destination)
    for stale in sorted(target.glob("*.json")):
        if stale.name not in sources:
            stale.unlink()
            removed.append(stale.name)

    for label, names in (("added", added), ("updated", updated), ("removed", removed)):
        for name in names:
            print(f"{label}: {name}")
    print(f"Published {len(sources)} areas to {target} "
          f"({len(added)} added, {len(updated)} updated, {len(removed)} removed). Rebuild the app to see them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
