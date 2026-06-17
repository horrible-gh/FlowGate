"""FlowGate legacy DB/storage → new DB/storage migration (CLI wrapper).

The real logic is in server/modules/flow_gate/storage/migration.py.

Examples:
    py _tools/storage_migrate.py \\
        --from server/storage --to <new-root> \\
        --db-old server/storage/flow_gate.db --db-new server/flowgate.db \\
        --dry-run

    py _tools/storage_migrate.py --full \\
        --from server/storage --to <new-root> \\
        --db-old server/storage/flow_gate.db --db-new server/flowgate.db

Options:
    --dry-run        Print planned actions only
    --full           migrate → verify → automatically remove legacy on success
    --no-delete      When --full, skip deleting legacy even if verification passes
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Add server/ to sys.path to enable importing modules.*
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from modules.flow_gate.storage.migration import (  # noqa: E402
    migrate as do_migrate,
    verify as do_verify,
    run_full,
)


def _print(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="FlowGate storage / DB migration")
    p.add_argument("--from", dest="src", required=True)
    p.add_argument("--to", dest="dst", required=True)
    p.add_argument("--db-old", dest="db_old", required=True)
    p.add_argument("--db-new", dest="db_new", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--full", action="store_true",
                   help="migrate → verify → automatically remove legacy on success")
    p.add_argument("--no-delete", action="store_true",
                   help="When --full, skip deleting legacy even if verification passes")
    args = p.parse_args(argv)

    if args.full:
        result = run_full(
            args.src, args.dst, args.db_old, args.db_new,
            delete_legacy_after=not args.no_delete,
        )
        _print(result)
        return 0 if result.get("ok") else 1

    m = do_migrate(args.src, args.dst, args.db_old, args.db_new,
                   dry_run=args.dry_run)
    _print({"migrate": m})

    if args.dry_run:
        return 0 if m.get("ok") else 1

    v = do_verify(args.src, args.dst, args.db_old, args.db_new,
                  migrate_result=m)
    _print({"verify": v})
    return 0 if v.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
