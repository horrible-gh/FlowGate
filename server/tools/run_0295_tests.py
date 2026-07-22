"""Portable entry point for the flowgate.default.0295 TS0006 test cases.

Run from ``server/`` as ``python tools/run_0295_tests.py <pytest args>``, or
``python tools/run_0295_tests.py --bootstrap`` to build the virtualenv first.

Every host-specific decision the TS steps used to make in shell syntax was moved
in here, because each of those decisions has already broken a run:

* ``trun_20260722_000013`` died on ``/bin/sh: 1: python: not found``.
* ``trun_20260722_000016`` died on ``python3`` (exit 9009, the Windows Store
  alias stub) and would have died again on ``.venv/bin/python``, which does not
  exist on Windows — CPython puts the venv interpreter in ``.venv/Scripts``.

So: the caller only ever names ``python`` (present on the FlowGate host's cmd.exe
PATH), and this file locates the venv interpreter for whichever layout exists and
re-executes itself under it.  Nothing below depends on the shell.

Two more reasons this file exists at all:

* ``config.Settings`` declares ALLOWED_ORIGIN / SECRET_KEY / CONTEXT / DB_TYPE as
  REQUIRED fields and the repo ships no ``.env`` (only ``.env.sample``), so
  importing anything under ``modules/`` raises a pydantic ValidationError unless
  they are in the environment.  Exporting them from a TS step would need
  per-shell syntax — ``set X=v &&`` on cmd.exe, ``X=v cmd`` on /bin/sh.
* ``os.environ.setdefault`` (not assignment) so a host that already exports a
  real value keeps it; this file must not override an operator's environment.

The working directory is pinned to ``server/`` because ``config.py`` resolves
``sql/queries`` and ``sql/migrations`` relative to the CWD.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

_SERVER_DIR = pathlib.Path(__file__).resolve().parents[1]
_VENV_DIR = _SERVER_DIR / ".venv"
_REEXEC_FLAG = "FLOWGATE_0295_VENV"

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
        print(
            "BOOTSTRAP_FAILED no interpreter under {}".format(_VENV_DIR),
            file=sys.stderr,
        )
        return 1
    install = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-q",
            "-r",
            str(_SERVER_DIR / "requirements.txt"),
        ]
    )
    return install.returncode


def _reexec_into_venv() -> int | None:
    """Hand the run to the venv interpreter; returns its exit code, or None if we are it."""
    if os.environ.get(_REEXEC_FLAG) == "1":
        return None
    python = _venv_python()
    if python is None or pathlib.Path(sys.executable).resolve() == python.resolve():
        return None
    env = dict(os.environ, **{_REEXEC_FLAG: "1"})
    return subprocess.run([str(python), __file__, *sys.argv[1:]], env=env).returncode


if "--bootstrap" in sys.argv:
    raise SystemExit(_bootstrap())

_delegated = _reexec_into_venv()
if _delegated is not None:
    raise SystemExit(_delegated)

if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

try:
    import pytest
except ImportError:  # pragma: no cover - surfaced as a plain sentence, not a traceback
    print(
        "MISSING_DEPS interpreter={} missing=pytest (run --bootstrap first)".format(
            sys.executable
        ),
        file=sys.stderr,
    )
    raise SystemExit(1)

# -q keeps the per-case output tail readable; no:cacheprovider avoids writing
# .pytest_cache into the checked-out source root.
raise SystemExit(pytest.main([*sys.argv[1:], "-q", "-p", "no:cacheprovider"]))
