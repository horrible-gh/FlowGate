"""File timings: diagnostic runs stay separate from complete serial baselines.

Tiers are cumulative and cheapest first. A file slower than the last tier, an e2e file
and anything marked `install` belong to no tier: they run only under --install-check.
"""
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

TIERS = ("veryfast", "fast", "standard", "heavy", "slow")
INSTALL = "install"
DEFAULT_TIER = "standard"
# Per-file ceiling in seconds for each tier.
FILE_LIMITS = {"veryfast": 1, "fast": 5, "standard": 15, "heavy": 30, "slow": 120}
# Target wall time in seconds for one sub-area run with 2 workers, tiers below included.
AREA_TARGETS = {"veryfast": 10, "fast": 30, "standard": 60, "heavy": 120, "slow": 300}
AREA_FILE_LIMIT = 40

# Unmeasured files: never below standard. Git/subprocess families start one tier up.
_HEAVY_TEXT = ("subprocess", '"git"', "'git'")
_HEAVY_NAME = ("dialect", "migration", "worktree", "git_")
_INSTALL_NAME = ("_e2e",)


def duration_tier(seconds):
    for tier in TIERS:
        if seconds <= FILE_LIMITS[tier]:
            return tier
    return INSTALL


def fallback_tier(path):
    """Tier for a file with no usable measurement, from its name and source text."""
    path = Path(path)
    if "e2e" in path.parts or any(needle in path.name for needle in _INSTALL_NAME):
        return INSTALL
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    if (any(needle in text for needle in _HEAVY_TEXT)
            or any(needle in path.name for needle in _HEAVY_NAME)):
        return "heavy"
    return DEFAULT_TIER


def load_slow_hints(path):
    """Seconds seen for files that ran too long but failed, so have no baseline.

    A hint can only move a file up from its fallback tier, never down.
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    hints = {}
    for entry in (data.get("measurement") or {}).get("ineligible_files_over_60s") or []:
        seconds = entry.get("seconds")
        if (isinstance(entry.get("path"), str) and isinstance(seconds, (int, float))
                and not isinstance(seconds, bool) and math.isfinite(seconds) and seconds >= 0):
            hints[entry["path"]] = seconds
    return hints


def module_tier(records, hints, path, root):
    """(tier, seconds or None, basis) with basis measured / hint / fallback."""
    try:
        key = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        key = None
    record = records.get(key) if key else None
    if record is not None and usable(record, path):
        return duration_tier(record["seconds"]), record["seconds"], "measured"
    tier = fallback_tier(path)
    seconds = hints.get(key) if key else None
    if seconds is not None:
        order = (*TIERS, INSTALL)
        hinted = duration_tier(seconds)
        if order.index(hinted) > order.index(tier):
            return hinted, None, "hint"
    return tier, None, "fallback"


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
            k not in (*TIERS, INSTALL, "auto") or isinstance(v, bool)
            or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
            for k, v in buckets.items()
        ):
            raise ValueError(f"invalid marker timings: {key}")
    return data["files"]


def usable(record, path):
    return (record.get("eligible") is True and path.is_file()
            and record.get("sha256") == hashlib.sha256(path.read_bytes()).hexdigest())


def measured_tier(records, path, root):
    tier, _seconds, basis = module_tier(records, {}, path, root)
    return tier if basis == "measured" else None


class TimingRecorder:
    def __init__(self, config, root, output):
        self.config, self.root, self.output = config, root.resolve(), Path(output)
        self.records = {}
        self.keys = {}
        self.expected = defaultdict(set)
        self.seen = defaultdict(set)
        self.bad = set()
        self.hashes = {}
        option = config.option
        # No tier condition: a file with any tier-skipped test is not a baseline anyway,
        # because every outcome other than passed disqualifies it below.
        self.eligible = not (getattr(option, "numprocesses", None)
            or getattr(option, "workerinput", None)
            or getattr(option, "keyword", "") or getattr(option, "markexpr", "")
            or any("::" in str(arg) for arg in config.args))

    def key(self, nodeid):
        # Collection runs before tests can monkeypatch os.name or path handling.
        # Reports must reuse that stable identity until teardown restores them.
        if nodeid not in self.keys:
            path = Path(str(self.config.rootpath)) / nodeid.split("::", 1)[0]
            try:
                self.keys[nodeid] = path.resolve().relative_to(self.root).as_posix()
            except ValueError:
                self.keys[nodeid] = None
        return self.keys[nodeid]

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
        marker = next((t for t in (*TIERS, INSTALL) if item.get_closest_marker(t)), "auto")
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
        if str(self.output) == "-":
            # Remote command callers can retain the measurement without writing
            # scratch output into the checkout. ASCII JSON survives host consoles.
            self._stdout_data = data
            return
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
        if hasattr(self, "_stdout_data"):
            terminalreporter.write_line("FLOWGATE-TIMINGS " + json.dumps(
                self._stdout_data, sort_keys=True, separators=(",", ":")))
        count = sum(r.get("eligible", False) for r in self.records.values())
        terminalreporter.write_line(f"timings: {self.output} | {len(self.records)} files, {count} complete serial baselines")
