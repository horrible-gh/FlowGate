"""Bounded server test entry point: work <=60s; verification <=300s per area."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(ROOT / "tests"))
from area_selection import AreaMap

LIMITS = {"work": 60, "verify": 300}
TIMEOUT_EXIT = 124


def plan(mapping, mode, area=None, changed=None, all_areas=False, tier="standard"):
    if mode not in LIMITS:
        raise ValueError("mode must be work or verify")
    if all_areas and (mode != "verify" or area or changed):
        raise ValueError("--all is only for verification without --area/--changed")
    if not all_areas and not (area or changed):
        raise ValueError("choose --area or --changed (or --all for verification)")
    selected = (set(mapping.areas) if all_areas else
                mapping.select(area, changed, tier if mode == "work" else "slow"))
    groups = [("work", selected)] if mode == "work" else [(a, {a}) for a in sorted(selected)]
    result = []
    for name, names in groups:
        files = sorted(p for p, owner in mapping.owners.items() if owner in names)
        if not files:
            raise ValueError(f"no tests selected: {name}")
        # Explicit owned files avoid --area's parent expansion and one-hop additions
        # being repeated for every child. Dependencies were expanded above once.
        args = [sys.executable, "-m", "pytest", *files, "-q", "-rEf", "--tb=short",
                "--fg-timings-out=-"]
        args += ["--install-check"] if mode == "verify" else [f"--tier={tier}"]
        result.append({"name": name, "areas": sorted(names), "files": files,
                       "seconds": LIMITS[mode], "command": args})
    return result


def stop_tree(process):
    if os.name == "nt":
        # /T covers pytest workers and their Git/other subprocesses.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def run_command(command, cwd, seconds):
    start = time.monotonic()
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(command, cwd=cwd, **options)
    try:
        code = process.wait(timeout=max(0, seconds - (time.monotonic() - start)))
    except subprocess.TimeoutExpired:
        stop_tree(process)
        return TIMEOUT_EXIT
    except BaseException:
        stop_tree(process)
        raise
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=LIMITS)
    parser.add_argument("--area")
    parser.add_argument("--changed")
    parser.add_argument("--all", action="store_true", dest="all_areas")
    parser.add_argument("--tier", choices=("veryfast", "fast", "standard"), default="standard")
    parser.add_argument("--plan", action="store_true", help="print the exact runs without executing")
    args = parser.parse_args(argv)
    try:
        mapping = AreaMap(ROOT)
        mapping.validate_patterns()
        runs = plan(mapping, args.mode, args.area, args.changed, args.all_areas, args.tier)
    except (OSError, ValueError, KeyError, SyntaxError) as exc:
        parser.error(str(exc))
    if args.plan:
        print(json.dumps(runs, ensure_ascii=True))
        return 0
    results = []
    for run in runs:
        print(f"AREA-RUN {run['name']}: {len(run['files'])} files, {run['seconds']}s hard limit", flush=True)
        start = time.monotonic()
        try:
            code = run_command(run["command"], ROOT, run["seconds"])
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"AREA-RUN ERROR: {exc}", file=sys.stderr, flush=True)
            code = 1
        result = {"area": run["name"], "exit_code": code, "timed_out": code == TIMEOUT_EXIT,
                  "elapsed_seconds": round(time.monotonic() - start, 3)}
        results.append(result)
        print("AREA-RESULT " + json.dumps(result), flush=True)
    print("AREA-SUMMARY " + json.dumps(results), flush=True)
    return 1 if any(r["exit_code"] != 0 for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
