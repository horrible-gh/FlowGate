#!/usr/bin/env python
"""Backfill body fingerprints into ``documents.meta`` (NR0003 §5.2 / §6).

The inbox duplicate-body guard refuses a substantial new/edited body that is a
clone of an existing document (the submission-layer contamination signature:
correct title, stale/reused body). But the guard can only *match* a twin whose
``meta`` already carries ``content_sha256`` — and every document created before
the guard shipped has ``meta = NULL``. Those un-fingerprinted originals are
exactly the ones that get cloned, so the guard stays blind to them and the
contamination recurs ("문서 오염 4회차", B0001). This is the cold-start gap.

This tool closes it: it walks every document, reads its stored body, and writes
both fingerprints (``content_sha256`` exact + ``content_sha256_norm``
whitespace-normalized) into ``meta`` — additively, preserving any existing keys
such as ``related_doc_ids``. It is idempotent (a row already carrying the
correct fingerprints is skipped) and reuses the very same hashing helpers the
live guard uses, so a backfilled hash is guaranteed to match what a future
clone submission would compute.

Run from the ``server/`` directory so config + storage roots resolve::

    python tools/backfill_content_fingerprint.py --dry-run   # report only
    python tools/backfill_content_fingerprint.py             # apply

Short bodies (< the guard's ``_dup_min_chars`` threshold) get no fingerprint —
approval stubs and boilerplate legitimately repeat and must never trip the
guard. They are counted as "skipped (short)".
"""
import argparse
import json
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVER_DIR = os.path.dirname(_THIS_DIR)
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from modules.flow_gate.api.inbox_routes import _content_fingerprints  # noqa: E402
from modules.flow_gate.db import documents as db_docs  # noqa: E402
from modules.flow_gate.storage.paths import resolve_storage_path  # noqa: E402


def _read_body(doc: dict) -> "str | None":
    """Resolve a document's stored file path and return its full text, or None."""
    stored = doc.get("file_path")
    if not stored:
        return None
    branch = (doc.get("branch") or "main") or "main"
    path = resolve_storage_path(stored, doc.get("project_id"), branch=branch)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def backfill(*, dry_run: bool, limit: "int | None" = None) -> dict:
    """Backfill fingerprints; return a counts summary.

    Counts: ``total`` rows seen, ``updated`` rows written, ``already`` rows that
    already carried matching fingerprints, ``short`` rows whose body is below the
    guard threshold (no fingerprint), ``no_body`` rows whose file could not be read.
    """
    stats = {"total": 0, "updated": 0, "already": 0, "short": 0, "no_body": 0}
    rows = db_docs.get_all_documents()
    for doc in rows:
        if limit is not None and stats["total"] >= limit:
            break
        stats["total"] += 1

        body = _read_body(doc)
        if body is None:
            stats["no_body"] += 1
            continue

        fps = _content_fingerprints(body)
        if not fps:
            stats["short"] += 1
            continue

        raw_meta = doc.get("meta")
        try:
            meta_obj = json.loads(raw_meta) if raw_meta else {}
        except (TypeError, ValueError):
            meta_obj = {}
        if not isinstance(meta_obj, dict):
            meta_obj = {}

        if all(meta_obj.get(k) == v for k, v in fps.items()):
            stats["already"] += 1
            continue

        meta_obj.update(fps)  # additive: preserves related_doc_ids etc.
        if not dry_run:
            db_docs.update(doc["doc_id"], {"meta": json.dumps(meta_obj)})
        stats["updated"] += 1

    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report counts without writing")
    ap.add_argument("--limit", type=int, default=None, help="process at most N rows (testing)")
    args = ap.parse_args()

    stats = backfill(dry_run=args.dry_run, limit=args.limit)
    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(
        f"[{mode}] total={stats['total']} updated={stats['updated']} "
        f"already={stats['already']} short={stats['short']} no_body={stats['no_body']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
