"""Preflight guard for the 0291 request-scope-cache test scenario (TS0014).

Run from ``server/`` as ``python tools/check_0291_env.py``.

Exists because the TS0014 setup steps kept dying before a single test case ran,
which leaves the test-run panel with no per-case detail at all.  Rather than
inlining a multi-clause python -c into a cmd.exe step (where quoting differs per
shell), the check lives here as a file so the TS step is a single portable
``python tools/check_0291_env.py``.

Fails loudly with the reason instead of letting pytest report a confusing
collection error later:

* wrong working directory / wrong source root -> the files under test are absent
* interpreter without the runtime deps -> pytest, fastapi or starlette missing
"""

from __future__ import annotations

import importlib
import pathlib
import sys

# Files this scenario exercises. Their absence means the runner is not sitting in
# the 0291 source root (or the implementation was never committed).
REQUIRED_FILES = (
    "modules/flow_gate/db/request_cache.py",
    "modules/flow_gate/api/request_scope_middleware.py",
    "modules/flow_gate/db/connection.py",
    "tests/test_request_scope_cache_0291.py",
)

# Imported by the test module itself; a missing one surfaces here as a plain
# sentence rather than as a pytest collection traceback.
REQUIRED_MODULES = ("pytest", "fastapi", "starlette")


def main() -> int:
    cwd = pathlib.Path.cwd()

    missing_files = [p for p in REQUIRED_FILES if not (cwd / p).exists()]
    if missing_files:
        print(
            "SOURCE_ROOT_MISMATCH cwd={} missing={}".format(cwd, ",".join(missing_files)),
            file=sys.stderr,
        )
        return 1

    missing_modules = []
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except ImportError:
            missing_modules.append(name)
    if missing_modules:
        print(
            "MISSING_DEPS interpreter={} missing={}".format(
                sys.executable, ",".join(missing_modules)
            ),
            file=sys.stderr,
        )
        return 1

    print("OK python={} cwd={}".format(sys.version.split()[0], cwd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
