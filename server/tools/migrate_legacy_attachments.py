#!/usr/bin/env python
"""Move pre-0060 attachments into their document's room — operational procedure, not boot code.

flowgate.default.0060 DB0013 §3-4, L0012 §2-11.

Migration 080 creates the table EMPTY on purpose. The old files live under
``{storage_root}/projects/{project_dir}/attachments/{doc_id}/`` and can only be found by
listing directories, which no ``INSERT ... SELECT`` can express; and the copy is bulk I/O
that would hold up server start-up if it rode along with the schema migration. So the
back-fill is this separate command.

Order of operations (DB0013 §3-4):

    1. apply 080 and confirm `attachments` exists
    2. python -m tools.migrate_legacy_attachments <project_id>            # dry run
    3. python -m tools.migrate_legacy_attachments <project_id> --apply    # for real
    4. anything left in `unresolved` is a directory whose document could not be found. Those
       files are untouched, on purpose — re-run later if the document comes back. Re-running
       is idempotent (same sha256 → skipped).

Usage:
    python server/tools/migrate_legacy_attachments.py <project_id> [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_id")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually move the files. Without it the run is a dry run and touches nothing.",
    )
    args = parser.parse_args(argv)

    from modules.flow_gate.documents.attachments.legacy import migrate_legacy_attachments

    report = migrate_legacy_attachments(args.project_id, dry_run=not args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["unresolved"]:
        print(
            f"\n{len(report['unresolved'])} directory(ies) had no matching document. "
            f"Their files were left exactly where they are — the directory name is the only "
            f"remaining clue to their owner.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
