#!/usr/bin/env python3
"""Rewrite area files in the one canonical format: two-space indent, UTF-8 as-is, trailing newline.

Usage:
    python3 scripts/format.py                 # format every area file
    python3 scripts/format.py areas/x.json    # format specific files
    python3 scripts/format.py --check         # report files that need formatting, change nothing

Canonical formatting keeps diffs small and reviewable. Numbers are kept exactly as written.
"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AREAS_DIR = ROOT / "areas"


def canonical(text: str) -> str:
    return json.dumps(json.loads(text), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--check", action="store_true", help="only report, don't write")
    args = parser.parse_args()

    files = args.files or sorted(AREAS_DIR.glob("*.json"))
    needs = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        try:
            formatted = canonical(text)
        except json.JSONDecodeError as exc:
            print(f"{path}: invalid JSON: {exc}", file=sys.stderr)
            return 1
        # Refuse to change a coordinate's digits: the guide says to copy them exactly.
        before = json.loads(text, parse_float=Decimal)
        after = json.loads(formatted, parse_float=Decimal)
        if before != after:
            print(f"{path}: formatting would change a number; fix it by hand", file=sys.stderr)
            return 1
        if formatted != text:
            needs.append(path)
            if not args.check:
                path.write_text(formatted, encoding="utf-8")
    verb = "need formatting" if args.check else "formatted"
    for path in needs:
        print(f"{verb}: {path}")
    if args.check and needs:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
