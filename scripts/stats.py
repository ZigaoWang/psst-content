#!/usr/bin/env python3
"""Summarize the content: places and facts per area, and how the facts split by category and status.

Usage:
    python3 scripts/stats.py            # every area
    python3 scripts/stats.py london     # only areas whose id starts with "london"
"""

import json
import sys
from collections import Counter
from pathlib import Path

AREAS_DIR = Path(__file__).resolve().parent.parent / "areas"


def main() -> int:
    prefix = sys.argv[1] if len(sys.argv) > 1 else ""
    rows = []
    categories: Counter = Counter()
    statuses: Counter = Counter()
    kinds: Counter = Counter()
    for path in sorted(AREAS_DIR.glob(f"{prefix}*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        spots = data.get("spots", [])
        facts = [f for s in spots for f in s.get("facts", [])]
        single = sum(1 for s in spots if len(s.get("facts", [])) == 1)
        rows.append((data["id"], data.get("city", ""), len(spots), len(facts), single, data.get("researchedOn", "")))
        categories.update(f.get("category") for f in facts)
        statuses.update(f.get("status") for f in facts)
        kinds.update(s.get("kind") for s in spots)

    if not rows:
        print("No matching areas.")
        return 1
    width = max(len(r[0]) for r in rows)
    print(f"{'area'.ljust(width)}  {'city':<14} places  facts  one-fact  researched")
    for area, city, places, facts, single, date in rows:
        print(f"{area.ljust(width)}  {city:<14} {places:>6}  {facts:>5}  {single:>8}  {date}")
    total_places = sum(r[2] for r in rows)
    total_facts = sum(r[3] for r in rows)
    print(f"\n{len(rows)} areas, {total_places} places, {total_facts} facts")
    print("kinds:      " + ", ".join(f"{k} {n}" for k, n in kinds.most_common()))
    print("categories: " + ", ".join(f"{k} {n}" for k, n in categories.most_common()))
    print("statuses:   " + ", ".join(f"{k} {n}" for k, n in statuses.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
