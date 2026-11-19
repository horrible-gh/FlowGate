"""File timings: diagnostic runs stay separate from complete serial baselines."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import pytest

TIERS = ("fast", "standard", "full")


def duration_tier(seconds):
    return "fast" if seconds <= 5 else "standard" if seconds <= 30 else "full"


def load_timings(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("files"), dict):
        raise ValueError("expected timings version 1 and files object")
    for key, record in data["files"].items():
        if not key.startswith("tests/") or ".." in Path(key).parts or "\\" in key:
            raise ValueError(f"invalid timing path: {key}")
        seconds = record.get("seconds")
        if (isinstance(seconds, bool) or not isinstance(seconds, (float, int))
                or not math.isfinite(seconds) or seconds < 0):
            raise ValueError(f"invalid timing seconds: {key}")
        if not isinstance(record.get("eligible"), bool):
            raise ValueError(f"missing eligibility: {key}")
        buckets = record.get("seconds_by_marker", {})
        if not isinstance(buckets, dict) or any(
            k not in (*TIERS, "auto") or isinstance(v, bool)
            or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
            for k, v in buckets.items()
        ):
            raise ValueError(f"invalid marker timings: {key}")
    return data["files"]


def usable(record, path):
    return (record.get("eligible") is True and path.is_file()
            and record.get("sha256") == hashlib.sha256(path.read_bytes()).hexdigest())


def measured_tier(records, path, root):
    try:
        key = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    record = records.get(key)
    if record is not None and usable(record, path):
        return duration_tier(record["seconds"])
    return None


class TimingRecorder:
    def __init__(self, config, root, output):
        self.config, self.root, self.output = config, root.resolve(), Path(output)
        self.records = {}
        self.expected = defaultdict(set)
        self.seen = defaultdict(set)
        self.bad = set()
        self.hashes = {}
        option = config.option
        self.eligible = not (getattr(option, "numprocesses", None)
            or getattr(option, "workerinput", None)
            or getattr(option, "keyword", "") or getattr(option, "markexpr", "")
            or config.getoption("--tier") != "full"
            or any("::" in str(arg) for arg in config.args))

    def key(self, nodeid):
        path = Path(str(self.config.rootpath)) / nodeid.split("::", 1)[0]
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return None

    def pytest_collection_finish(self, session):
        for item in session.items:
            key = self.key(item.nodeid)
            self.expected[key].add(item.nodeid)
            if key and key not in self.hashes:
                self.hashes[key] = hashlib.sha256((self.root / key).read_bytes()).hexdigest()

    def pytest_deselected(self, items):
        # Includes --lf and selection plugins, not only -k/-m.
        self.bad.update(self.key(item.nodeid) for item in items)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        marker = next((t for t in TIERS if item.get_closest_marker(t)), "auto")
        report.user_properties.append(("fg_timing_marker", marker))

    def pytest_runtest_logreport(self, report):
        key = self.key(report.nodeid)
        if key is None or report.when not in ("setup", "call", "teardown"):
            return
        record = self.records.setdefault(key, {"seconds": 0.0, "seconds_by_marker": {}})
        record["seconds"] += report.duration
        marker = dict(report.user_properties).get("fg_timing_marker", "auto")
        buckets = record["seconds_by_marker"]
        buckets[marker] = buckets.get(marker, 0.0) + report.duration
        self.seen[report.nodeid].add(report.when)
        if report.outcome != "passed":
            self.bad.add(key)

    def pytest_sessionfinish(self, session, exitstatus):
        # xdist sends reports to the controller; workers must never race the output.
        if hasattr(self.config, "workerinput"):
            return
        for key, record in self.records.items():
            expected = self.expected.get(key, set())
            complete = bool(expected) and all(self.seen[n] == {"setup", "call", "teardown"} for n in expected)
            record["eligible"] = bool(self.eligible and complete and key not in self.bad
                                      and int(exitstatus) in (0, 1))
            record["sha256"] = self.hashes.get(key)
        data = {"version": 1, "measured_at": datetime.now(timezone.utc).isoformat(),
                "exitstatus": int(exitstatus), "files": dict(sorted(self.records.items()))}
        self.output.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.output.parent, prefix=".timings-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(name, self.output)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def pytest_terminal_summary(self, terminalreporter):
        count = sum(r.get("eligible", False) for r in self.records.values())
        terminalreporter.write_line(f"timings: {self.output} | {len(self.records)} files, {count} complete serial baselines")
