#!/usr/bin/env python
"""Bulk-migrate legacy CH conversations into the turn store (T4, L0004 §2-14).

T1 (``conversation_turn_service.migrate_conversation``) already migrates one CH
document, completely and idempotently, the moment anything reads or writes it. But an
old conversation nobody opens is never read, so it never migrates, never shows up in
turn search, and any migration failure on it goes unnoticed (D0002 §7 T4 item 1). This
tool is the operator-facing entry point that walks every not-yet-``migrated`` CH and
migrates it explicitly, without needing to load each one in the UI first.

It does not reimplement migration — every document still goes through the exact same
``migrate_conversation()`` (lock, batching, 5000-turn ceiling, ``failed`` isolation) the
lazy read path uses. This tool only supplies the "for every CH" loop, one document's
failure isolated from the next, plus a summary an operator can read.

Idempotent and safely re-run: a document already ``migrated`` is not re-selected (the
LEFT JOIN judgment in ``list_ch_docs_needing_migration``), so calling this repeatedly
— e.g. on a schedule, or with ``--limit`` to bound one call's cost — makes steady
forward progress through the backlog with no risk of double-migrating anything.

Run from the ``server/`` directory so config + storage roots resolve::

    python tools/migrate_conversations_bulk.py --dry-run   # report only, no writes
    python tools/migrate_conversations_bulk.py              # migrate up to the default cap
    python tools/migrate_conversations_bulk.py --limit 500   # raise/lower the per-call cap
"""
import argparse
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVER_DIR = os.path.dirname(_THIS_DIR)
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from modules.flow_gate.db import conversation_turns as turn_store  # noqa: E402
from modules.flow_gate.services import conversation_turn_service  # noqa: E402

# Documents processed per invocation. A single call intentionally does not drain an
# unbounded backlog — this keeps one run's cost predictable and re-runnable; forward
# progress across runs comes from list_ch_docs_needing_migration() no longer selecting
# a document once it lands in 'migrated'.
BULK_MIGRATE_DOC_LIMIT_DEFAULT = 200


def bulk_migrate(*, dry_run: bool = False, limit: "int | None" = None) -> dict:
    """Migrate every pending/failed CH document (up to ``limit``); return a summary.

    ``failures`` lists ``{doc_id, reason}`` for every document that did NOT end this
    call in ``migrated`` state — a genuine parse/size failure (``reason`` from
    ``conversation_docs.failure_reason``) as well as the benign case of another runner
    already holding that document's migration lock (``reason: "lock_held"``).
    """
    effective_limit = BULK_MIGRATE_DOC_LIMIT_DEFAULT if limit is None else int(limit)
    stats = {"scanned": 0, "migrated": 0, "failed": 0, "skipped": 0, "failures": []}
    rows = turn_store.list_ch_docs_needing_migration(limit=effective_limit)
    for row in rows:
        doc_id = row["doc_id"]
        stats["scanned"] += 1
        if dry_run:
            continue
        try:
            ok = conversation_turn_service.migrate_conversation(doc_id)
        except conversation_turn_service.ConversationTurnError as exc:
            stats["failed"] += 1
            stats["failures"].append({"doc_id": doc_id, "reason": str(exc)})
            continue
        if ok:
            stats["migrated"] += 1
            continue
        state_row = turn_store.get_migration(doc_id) or {}
        state = state_row.get("migration_state")
        if state == "migrated":
            # Raced with another runner that finished it first — not a failure.
            stats["skipped"] += 1
        elif state == "failed":
            stats["failed"] += 1
            stats["failures"].append(
                {"doc_id": doc_id, "reason": state_row.get("failure_reason") or "unknown"}
            )
        else:
            # Lock held by another in-progress runner (TTL not yet expired).
            stats["skipped"] += 1
            stats["failures"].append({"doc_id": doc_id, "reason": "lock_held"})
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report the candidate count without migrating")
    ap.add_argument(
        "--limit", type=int, default=None,
        help=f"process at most N documents this call (default {BULK_MIGRATE_DOC_LIMIT_DEFAULT})",
    )
    args = ap.parse_args()

    stats = bulk_migrate(dry_run=args.dry_run, limit=args.limit)
    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(
        f"[{mode}] scanned={stats['scanned']} migrated={stats['migrated']} "
        f"failed={stats['failed']} skipped={stats['skipped']}"
    )
    for item in stats["failures"]:
        print(f"  - {item['doc_id']}: {item['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
