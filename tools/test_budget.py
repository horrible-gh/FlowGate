"""Read-only area budget report; unknown timings are never counted as zero."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(ROOT / "tests"))
from area_selection import AreaMap
from timing_support import duration_tier, load_timings, usable


def budget_rows(mapping, records):
    rows = {a: {"fast": 0.0, "standard": 0.0, "full": 0.0, "unknown": 0} for a in mapping.areas}
    slow = []
    for key, area in mapping.owners.items():
        record = records.get(key)
        if record is None or not usable(record, mapping.files[key]):
            rows[area]["unknown"] += 1
            continue
        seconds = record["seconds"]
        buckets = record.get("seconds_by_marker") or {"auto": seconds}
        for marker, duration in buckets.items():
            tier = duration_tier(seconds) if marker == "auto" else marker
            for level in ("fast", "standard", "full")[("fast", "standard", "full").index(tier):]:
                rows[area][level] += duration
        if seconds > 60:
            slow.append((seconds, key))
    return rows, sorted(slow, reverse=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timings", type=Path, default=ROOT / "tests/timings.json")
    args = parser.parse_args()
    try:
        mapping = AreaMap(ROOT)
        rows, slow = budget_rows(mapping, load_timings(args.timings))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.error(str(exc))
    print("area | fast serial / 60s | standard serial work | full serial work | unknown files")
    for area, row in rows.items():
        flag = " OVER" if row["fast"] > 60 else ""
        print(f"{area} | {row['fast']:.3f}{flag} | {row['standard']:.3f} | {row['full']:.3f} | {row['unknown']}")
    print("Standard selections including one-hop also dependencies (2 workers, 120s budget):")
    for area in rows:
        names = mapping.select(area, None, "standard")
        work = sum(rows[name]["standard"] for name in names)
        unknown = sum(rows[name]["unknown"] for name in names)
        flag = " OVER (even ideal balancing)" if work / 2 > 120 else " measure wall time to confirm"
        print(f"  {area}: serial work {work:.3f}s, ideal lower bound {work / 2:.3f}s, unknown {unknown};{flag}")
    print("Standard budget is 120s with 2 workers: serial work is not wall time; measure to confirm.")
    print("Import/collection/startup excluded. Unknown files make totals incomplete. First table shows primary ownership only.")
    print("Files over 60s:")
    for seconds, path in slow:
        print(f"  {seconds:.3f}s {path}")


if __name__ == "__main__":
    main()
