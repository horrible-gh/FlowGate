"""Portable entry point for the flowgate.default.0347 TS test cases.

Run from ``server/`` as ``python tools/run_0347_tests.py <pytest args>``, or
``python tools/run_0347_tests.py --bootstrap`` to build the virtualenv first.

Same shape as ``tools/run_0295_tests.py`` — the caller only ever names ``python``
(present on the FlowGate host's cmd.exe PATH) and this file locates the venv
interpreter for whichever layout exists (``Scripts`` on Windows, ``bin`` on
POSIX) and re-executes itself under it.  ``config.Settings`` declares
ALLOWED_ORIGIN / SECRET_KEY / CONTEXT / DB_TYPE as REQUIRED and the repo ships no
``.env``, so those are seeded here with ``setdefault`` rather than exported from
a per-shell TS step.  CWD is pinned to ``server/`` because ``config.py`` resolves
``sql/queries`` and ``sql/migrations`` relative to it.

Two things this file adds over 0295:

* ``--min-passed N`` / strict outcome guard.  The TS verdict is the exit code and
  nothing else, so a suite that collected nothing, or quietly turned green by
  skipping, would read as PASS.  This guard turns "0 tests ran", "anything was
  skipped/xfailed/xpassed", or "fewer than N passed" into a non-zero exit.
* ``--check-migrations``.  The 073 op-CHECK widening is DDL: pytest exercises it
  only through the sqlite harness, so the mysql/postgres files could drift out of
  agreement and no Python test would notice.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

_SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
_VENV_DIR = _SERVER_DIR / ".venv"
_REEXEC_FLAG = "FLOWGATE_0347_VENV"

_ENV_DEFAULTS = {
    "TESTING": "1",
    "SECRET_KEY": "test-secret-key-for-testing-only-32c",
    "ALLOWED_ORIGIN": "*",
    "CONTEXT": "/flowgate",
    "DB_TYPE": "sqlite3",
}

for _key, _value in _ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _value)

os.chdir(_SERVER_DIR)


def _venv_python() -> pathlib.Path | None:
    """The venv interpreter, whichever layout this OS uses (nt: Scripts, posix: bin)."""
    for relative in ("Scripts/python.exe", "Scripts/python", "bin/python3", "bin/python"):
        candidate = _VENV_DIR / relative
        if candidate.exists():
            return candidate
    return None


def _bootstrap() -> int:
    """Create the venv (idempotent) and install the server requirements into it."""
    if _venv_python() is None:
        created = subprocess.run([sys.executable, "-m", "venv", str(_VENV_DIR)])
        if created.returncode != 0:
            return created.returncode
    python = _venv_python()
    if python is None:
        print("BOOTSTRAP_FAILED no interpreter under {}".format(_VENV_DIR), file=sys.stderr)
        return 1
    install = subprocess.run(
        [
            str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q",
            "-r", str(_SERVER_DIR / "requirements.txt"),
        ]
    )
    return install.returncode


# ── 073 migration parity (DB0006 / TR0008) ────────────────────────────────────

_DIALECTS = ("sqlite", "mysql", "postgres")
_MIGRATION = "073_remote_tool_op_log_patch_stat.sql"
_EXPECTED_OPS = {"read", "write", "grep", "glob", "remove", "patch", "stat"}
_OP_CHECK_RE = re.compile(r"op\s+IN\s*\(([^)]*)\)", re.IGNORECASE)


def _check_migrations() -> int:
    """Every dialect ships 073, all three widen op to the same seven values, and
    no dialect has a second file claiming the number 073 (duplicate numbering has
    silently shadowed a migration here before — cf. the 063/064 pair)."""
    problems: list[str] = []
    for dialect in _DIALECTS:
        directory = _SERVER_DIR / "sql" / "migrations" / dialect
        path = directory / _MIGRATION
        if not path.exists():
            problems.append("{}: missing {}".format(dialect, _MIGRATION))
            continue

        same_number = sorted(p.name for p in directory.glob("073*.sql"))
        if same_number != [_MIGRATION]:
            problems.append("{}: duplicate 073 numbering {}".format(dialect, same_number))

        text = path.read_text(encoding="utf-8")
        match = _OP_CHECK_RE.search(text)
        if match is None:
            problems.append("{}: no `op IN (...)` CHECK found".format(dialect))
            continue
        ops = {value.strip().strip("'\"") for value in match.group(1).split(",")}
        ops.discard("")
        if ops != _EXPECTED_OPS:
            problems.append(
                "{}: op CHECK is {}, expected {}".format(
                    dialect, sorted(ops), sorted(_EXPECTED_OPS)
                )
            )

    for problem in problems:
        print("MIGRATION_CHECK_FAILED {}".format(problem), file=sys.stderr)
    if problems:
        return 1
    print("073 op CHECK widened to patch/stat in: {}".format(", ".join(_DIALECTS)))
    return 0


# ── strict outcome guard ──────────────────────────────────────────────────────

class _StrictOutcome:
    """Fails the run for outcomes pytest itself exits 0 on: nothing collected, or
    anything skipped / xfailed / xpassed.  Without this a `skipif` that stopped
    matching would read as a green TS case."""

    def __init__(self, min_passed: int) -> None:
        self.min_passed = min_passed
        self.problems: list[str] = []

    def pytest_terminal_summary(self, terminalreporter) -> None:
        stats = terminalreporter.stats
        counts = {
            key: len(stats.get(key, []))
            for key in ("passed", "skipped", "xfailed", "xpassed", "failed", "error")
        }
        print(
            "STRICT_OUTCOME " + " ".join("{}={}".format(k, v) for k, v in counts.items()),
        )
        for key in ("skipped", "xfailed", "xpassed"):
            if counts[key]:
                self.problems.append("{} {} test(s) — the TS expects none".format(counts[key], key))
        if counts["passed"] < self.min_passed:
            self.problems.append(
                "only {} passed, expected at least {}".format(counts["passed"], self.min_passed)
            )


def _pop_option(argv: list[str], name: str, default: int) -> int:
    if name in argv:
        index = argv.index(name)
        value = int(argv[index + 1])
        del argv[index : index + 2]
        return value
    return default


# ── entry ─────────────────────────────────────────────────────────────────────

if "--bootstrap" in sys.argv:
    raise SystemExit(_bootstrap())

if "--check-migrations" in sys.argv:
    raise SystemExit(_check_migrations())

if os.environ.get(_REEXEC_FLAG) != "1":
    _python = _venv_python()
    if _python is not None and pathlib.Path(sys.executable).resolve() != _python.resolve():
        _env = dict(os.environ, **{_REEXEC_FLAG: "1"})
        raise SystemExit(subprocess.run([str(_python), __file__, *sys.argv[1:]], env=_env).returncode)

if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

try:
    import pytest
except ImportError:  # pragma: no cover - surfaced as a plain sentence, not a traceback
    print(
        "MISSING_DEPS interpreter={} missing=pytest (run --bootstrap first)".format(sys.executable),
        file=sys.stderr,
    )
    raise SystemExit(1)

_args = sys.argv[1:]
_min_passed = _pop_option(_args, "--min-passed", 1)
_guard = _StrictOutcome(_min_passed)

# -q keeps the per-case output tail readable; no:cacheprovider avoids writing
# .pytest_cache into the checked-out source root.
_code = pytest.main([*_args, "-q", "-p", "no:cacheprovider"], plugins=[_guard])
if _guard.problems:
    for _problem in _guard.problems:
        print("STRICT_OUTCOME_FAILED {}".format(_problem), file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(_code)
