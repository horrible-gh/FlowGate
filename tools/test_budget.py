"""Read-only sub-area budget report; unknown timings are never counted as zero."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(ROOT / "tests"))
from area_selection import AreaMap
from timing_support import (AREA_FILE_LIMIT, AREA_TARGETS, INSTALL, TIERS, duration_tier,
                            load_slow_hints, load_timings, module_tier)


def budget_rows(mapping, records, hints=None):
    """Per area: cumulative measured seconds and file counts per tier.

    A file with no usable measurement is counted in the tier it runs in (`files`) and
    in `unknown`, never as zero seconds: `unknown_at` says how many such files each
    tier's total is missing.
    """
    hints = hints or {}
    rows = {a: {**{t: 0.0 for t in TIERS}, INSTALL: 0.0, "unknown": 0,
                "files": {t: 0 for t in (*TIERS, INSTALL)},
                "unknown_at": {t: 0 for t in (*TIERS, INSTALL)}} for a in mapping.areas}
    slow = []
    order = (*TIERS, INSTALL)
    for key, area in mapping.owners.items():
        row = rows[area]
        tier, seconds, _basis = module_tier(records, hints, mapping.files[key], mapping.root)
        row["files"][tier] += 1
        if seconds is None:
            row["unknown"] += 1
            for level in order[order.index(tier):]:
                row["unknown_at"][level] += 1
            continue
        buckets = records[key].get("seconds_by_marker") or {"auto": seconds}
        for marker, duration in buckets.items():
            own = duration_tier(seconds) if marker == "auto" else marker
            for level in order[order.index(own):]:
                row[level] += duration
        if seconds > AREA_TARGETS["standard"]:
            slow.append((seconds, key))
    return rows, sorted(slow, reverse=True)


def flags(row):
    """What is over target for one area, cheapest tier first (ideal 2-worker bound)."""
    found = []
    total = sum(row["files"].values())
    if total > AREA_FILE_LIMIT:
        found.append(f"files {total}>{AREA_FILE_LIMIT}")
    for tier in TIERS:
        if row[tier] / 2 > AREA_TARGETS[tier]:
            found.append(f"{tier} {row[tier] / 2:.0f}s>{AREA_TARGETS[tier]}s")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timings", type=Path, default=ROOT / "tests/timings.json")
    parser.add_argument("--records-offset", type=int, default=None,
                        help="Print a page of timing records as JSON instead of the budget report.")
    parser.add_argument("--records-limit", type=int, default=20,
                        help="Records per JSON page (1-20; default 20).")
    args = parser.parse_args()
    if args.records_offset is not None:
        if args.records_offset < 0 or not 1 <= args.records_limit <= 20:
            parser.error("records offset must be nonnegative and limit must be 1-20")
        try:
            records = load_timings(args.timings)
            metadata = json.loads(args.timings.read_text(encoding="utf-8"))
            keys = sorted(records)
            end = min(args.records_offset + args.records_limit, len(keys))
            page = {key: records[key] for key in keys[args.records_offset:end]}
            print(json.dumps({
                "version": 1, "measured_at": metadata.get("measured_at"),
                "exitstatus": metadata.get("exitstatus"), "total": len(keys),
                "offset": args.records_offset,
                "next_offset": end if end < len(keys) else None, "files": page,
            }, separators=(",", ":"), sort_keys=True))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            parser.error(str(exc))
        return
    try:
        mapping = AreaMap(ROOT)
        rows, slow = budget_rows(mapping, load_timings(args.timings), load_slow_hints(args.timings))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.error(str(exc))
    targets = " / ".join(f"{t} {AREA_TARGETS[t]}s" for t in TIERS)
    print(f"Targets per area, 2 workers, lower tiers included: {targets}; at most {AREA_FILE_LIMIT} files.")
    print("Seconds are measured serial work per cumulative tier; +N = files in that total with no measurement.")
    print("area | files | " + " | ".join(TIERS) + " | install-only files | split candidates")
    for area, row in rows.items():
        cells = [f"{row[t]:.1f}" + (f" +{row['unknown_at'][t]}" if row["unknown_at"][t] else "")
                 for t in TIERS]
        found = flags(row)
        print(f"{area} | {sum(row['files'].values())} | " + " | ".join(cells)
              + f" | {row['files'][INSTALL]} | " + ("SPLIT: " + ", ".join(found) if found else "-"))
    print("Default run (standard) per area including one-hop also dependencies:")
    for area in rows:
        names = mapping.select(area, None, "standard")
        work = sum(rows[name]["standard"] for name in names)
        unknown = sum(rows[name]["unknown_at"]["standard"] for name in names)
        over = work / 2 > AREA_TARGETS["standard"]
        flag = " OVER (even ideal balancing)" if over else ""
        print(f"  {area}: serial work {work:.1f}s, ideal 2-worker bound {work / 2:.1f}s, unmeasured {unknown};{flag}")
    print("Serial work is not wall time: import/collection/startup are excluded; measure to confirm.")
    print(f"Files over {AREA_TARGETS['standard']}s (heavy and above):")
    for seconds, path in slow:
        print(f"  {seconds:.1f}s {duration_tier(seconds)} {path}")


if __name__ == "__main__":
    main()
