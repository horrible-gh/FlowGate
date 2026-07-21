"""Preflight guard for the flowgate.default.0291 test scenarios.

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
    # P3-1 (TS0014)
    "modules/flow_gate/db/request_cache.py",
    "modules/flow_gate/api/request_scope_middleware.py",
    "modules/flow_gate/db/connection.py",
    "tests/test_request_scope_cache_0291.py",
    # T1 — auth preamble collapsed into one query
    "modules/flow_gate/auth/auth_preamble.py",
    "modules/flow_gate/utils/ttl_cache.py",
    "tests/test_auth_preamble_0291.py",
    # T2 — return-point quartet folded into one query
    "modules/flow_gate/db/workflow_return_points.py",
    "tests/test_return_point_summary_0291.py",
    # T3 — a group's documents read once per document response
    "modules/flow_gate/documents/routers/documents.py",
    "tests/test_document_read_query_budget_0291.py",
    # T3 builds its fixture DB straight from the committed sqlite migrations
    "sql/migrations/sqlite",
)

# Imported by the test module itself; a missing one surfaces here as a plain
# sentence rather than as a pytest collection traceback.
REQUIRED_MODULES = ("pytest", "fastapi", "starlette", "httpx")


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
