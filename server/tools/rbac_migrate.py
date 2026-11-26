"""Operator tool for the one-time RBAC grant migration (rbac/migration_0090.py).

Run from ``server/`` with the server's environment (the same database settings):

    python tools/rbac_migrate.py --status    show the migration record
    python tools/rbac_migrate.py --preview   compute the change list and roll it back (no writes)
    python tools/rbac_migrate.py --retry     start a failed or interrupted migration again

The server runs the migration once at start. It never retries a failure by itself: this tool is
the only way to start it again. A completed migration is never run again (``--retry`` refuses).
``--preview`` is meant for a copy of the production database before the upgrade: the person
who deploys reads the difference rows and their categories before applying it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(os.path.join(SERVER_DIR, ".env"))


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="show the migration record")
    group.add_argument("--preview", action="store_true", help="compute the change list without writing")
    group.add_argument("--retry", action="store_true", help="start a failed or interrupted migration again")
    args = parser.parse_args(argv)

    _load_env()
    from modules.flow_gate.rbac import migration_0090

    if args.status:
        record = migration_0090.get_record()
        if record is None:
            _print({"status": None, "note": "no record: the server has not run the migration yet"})
            return 0
        shown = dict(record)
        for key in ("summary",):
            if shown.get(key):
                shown[key] = json.loads(shown[key])
        shown.pop("diff", None)
        _print(shown)
        return 0 if record.get("status") == "completed" else 1
    if args.preview:
        report = migration_0090.run(preview=True)
        _print(report)
        return 0 if report.get("status") == "would_complete" else 1
    result = migration_0090.run(retry=True)
    _print(result)
    return 0 if result.get("status") == "completed" and not result.get("retry_refused") else 1


if __name__ == "__main__":
    sys.exit(main())
