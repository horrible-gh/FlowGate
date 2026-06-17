#!/usr/bin/env python3
"""TS004 fixture reset script for T053.

Run from server/:
    py ts004_reset_fixtures.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, ".")
os.environ.setdefault("TESTING", "0")

from modules.flow_gate import db


PROJECT = "server"
MODULE = "ts004"

TARGET_GROUP_IDS = ("server-ts004-9401", "server-ts004-9402")
TARGET_DOC_IDS = (
    "server-ts004-9401-R0001",
    "server-ts004-9401-Q0001",
    "server-ts004-9401-A0001",
    "server-ts004-9401-AR0001",
    "server-ts004-9401-DS0001",
    "server-ts004-9402-DS0002",
    "server-ts004-9402-D0001",
    "server-ts004-9402-DC0001",
)

INBOX_FIXTURES = {
    "ts004_t051_base_ds_open.md": """---
group_id: server-ts004-9401
type: DS
project: server
module: ts004
title: TS004 DS path verification instructions
priority: medium
target_id: server-ts004-9401-AR0001
next: D
---

## Design instructions

- Verify that source_file / requirement_file / reference_qa.file are returned as paths relative to docs_root.
- Verify that the docs_root line is included in the copied message output.
""",
    "ts004_t051_base_dc_open.md": """---
group_id: server-ts004-9402
type: DC
project: server
module: ts004
title: TS004 DC project_root verification for T candidates
priority: medium
target_id: server-ts004-9402-DS0002
bundle_doc_ids:
  - server-ts004-9402-D0001
---

## Design completion

- Verify that project_root is correctly included in T candidate next_actions.
- Verify that the project_root line is added when selecting T in the copied message.
""",
}

GROUP_ROWS = (
    {
        "group_id": "server-ts004-9401",
        "title": "TS004 DS path verification",
        "status": "OPEN",
    },
    {
        "group_id": "server-ts004-9402",
        "title": "TS004 DC T-candidate verification",
        "status": "OPEN",
    },
)

DOC_ROWS = (
    {
        "doc_id": "server-ts004-9401-R0001",
        "type": "R",
        "title": "TS004 T051 requirements for path verification",
        "group_id": "server-ts004-9401",
        "status": "accepted",
        "target_id": None,
        "next": None,
        "direction": None,
    },
    {
        "doc_id": "server-ts004-9401-Q0001",
        "type": "Q",
        "title": "TS004 T051 requirement clarification questions",
        "group_id": "server-ts004-9401",
        "status": "accepted",
        "target_id": "server-ts004-9401-R0001",
        "next": None,
        "direction": None,
    },
    {
        "doc_id": "server-ts004-9401-A0001",
        "type": "A",
        "title": "TS004 T051 requirement clarification response",
        "group_id": "server-ts004-9401",
        "status": "accepted",
        "target_id": "server-ts004-9401-Q0001",
        "next": None,
        "direction": None,
    },
    {
        "doc_id": "server-ts004-9401-AR0001",
        "type": "AR",
        "title": "TS004 T051 requirement approval request",
        "group_id": "server-ts004-9401",
        "status": "accepted",
        "target_id": "server-ts004-9401-R0001",
        "next": None,
        "direction": None,
    },
    {
        "doc_id": "server-ts004-9401-DS0001",
        "type": "DS",
        "title": "TS004 DS path verification instructions",
        "group_id": "server-ts004-9401",
        "status": "open",
        "target_id": "server-ts004-9401-AR0001",
        "next": "D",
        "direction": "inbox",
    },
    {
        "doc_id": "server-ts004-9402-DS0002",
        "type": "DS",
        "title": "TS004 DC project_root verification instructions for T candidates",
        "group_id": "server-ts004-9402",
        "status": "accepted",
        "target_id": None,
        "next": "T",
        "direction": None,
    },
    {
        "doc_id": "server-ts004-9402-D0001",
        "type": "D",
        "title": "TS004 design document",
        "group_id": "server-ts004-9402",
        "status": "accepted",
        "target_id": None,
        "next": None,
        "direction": None,
    },
    {
        "doc_id": "server-ts004-9402-DC0001",
        "type": "DC",
        "title": "TS004 DC project_root verification for T candidates",
        "group_id": "server-ts004-9402",
        "status": "open",
        "target_id": "server-ts004-9402-DS0002",
        "next": None,
        "direction": "inbox",
    },
)

DELETE_BASE_DIRS = (
    db.INBOX_DIR,
    db.ACCEPT_DIR,
    db.OUTBOX_DIR,
    db.REJECT_DIR,
)


def _now() -> str:
    return datetime.now().isoformat()


def _ensure_dirs() -> None:
    for path in DELETE_BASE_DIRS:
        os.makedirs(path, exist_ok=True)


def _write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _matches_reset_file(name: str) -> bool:
    return (
        name in INBOX_FIXTURES
        or (name.startswith("AC") and name.endswith("_ts004_t051_base_ds_open.md"))
        or (name.startswith("AC") and name.endswith("_ts004_t051_base_dc_open.md"))
        or name.startswith("server-ts004-9401")
        or name.startswith("server-ts004-9402")
    )


def _collect_reset_files() -> list[str]:
    candidates: list[str] = []
    for base_dir in DELETE_BASE_DIRS:
        for root, _, files in os.walk(base_dir):
            for name in files:
                if _matches_reset_file(name):
                    candidates.append(os.path.join(root, name))
    return sorted(set(candidates))


def _delete_reset_files() -> list[str]:
    removed: list[str] = []
    for path in _collect_reset_files():
        os.remove(path)
        removed.append(path)
    return removed


def _print_rows(label: str, rows: list[dict]) -> None:
    print(label)
    if not rows:
        print("  (none)")
        return
    for row in rows:
        print(f"  {row}")


def _insert_group(conn, row: dict) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO groups (
            group_id, project, module, title, priority,
            status, created_at, updated_at, closed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["group_id"],
            PROJECT,
            MODULE,
            row["title"],
            "medium",
            row["status"],
            now,
            now,
            None,
        ),
    )


def _insert_document(conn, row: dict) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO documents (
            doc_id, type, project, module, target_id, group_id, owner,
            priority, due_date, title, status, next, direction,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["doc_id"],
            row["type"],
            PROJECT,
            MODULE,
            row["target_id"],
            row["group_id"],
            None,
            "medium",
            None,
            row["title"],
            row["status"],
            row["next"],
            row["direction"],
            now,
            now,
        ),
    )


def _insert_event(conn, doc_id: str, memo_file: str) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO events (
            doc_id, event_type, memo_file, file_hash, reason,
            related_doc_id, related_target_id, note, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (doc_id, "created", memo_file, None, None, None, None, None, now),
    )


def _validate_state() -> None:
    conn = db.get_connection()
    try:
        groups = conn.execute(
            """
            SELECT group_id, project, module, status
            FROM groups
            WHERE group_id IN (?, ?)
            ORDER BY group_id
            """,
            TARGET_GROUP_IDS,
        ).fetchall()
        docs = conn.execute(
            """
            SELECT doc_id, type, group_id, status, target_id, next, direction
            FROM documents
            WHERE doc_id IN (?, ?, ?, ?, ?, ?, ?, ?)
            ORDER BY doc_id
            """,
            TARGET_DOC_IDS,
        ).fetchall()
        events = conn.execute(
            """
            SELECT doc_id, event_type, memo_file
            FROM events
            WHERE doc_id IN (?, ?)
            ORDER BY event_id
            """,
            ("server-ts004-9401-DS0001", "server-ts004-9402-DC0001"),
        ).fetchall()
    finally:
        conn.close()

    expected_docs = {
        "server-ts004-9401-R0001": ("accepted", None, None),
        "server-ts004-9401-Q0001": ("accepted", "server-ts004-9401-R0001", None),
        "server-ts004-9401-A0001": ("accepted", "server-ts004-9401-Q0001", None),
        "server-ts004-9401-AR0001": ("accepted", "server-ts004-9401-R0001", None),
        "server-ts004-9401-DS0001": ("open", "server-ts004-9401-AR0001", "D"),
        "server-ts004-9402-DS0002": ("accepted", None, "T"),
        "server-ts004-9402-D0001": ("accepted", None, None),
        "server-ts004-9402-DC0001": ("open", "server-ts004-9402-DS0002", None),
    }

    if len(groups) != 2:
        raise RuntimeError(f"group count mismatch: {len(groups)}")
    for row in groups:
        if row["project"] != PROJECT or row["module"] != MODULE or row["status"] != "OPEN":
            raise RuntimeError(f"group mismatch: {dict(row)}")

    if len(docs) != 8:
        raise RuntimeError(f"document count mismatch: {len(docs)}")
    for row in docs:
        expected = expected_docs[row["doc_id"]]
        if row["status"] != expected[0] or row["target_id"] != expected[1] or row["next"] != expected[2]:
            raise RuntimeError(f"document mismatch: {dict(row)}")

    if len(events) != 2:
        raise RuntimeError(f"event count mismatch: {len(events)}")
    expected_events = {
        ("server-ts004-9401-DS0001", "ts004_t051_base_ds_open.md"),
        ("server-ts004-9402-DC0001", "ts004_t051_base_dc_open.md"),
    }
    for row in events:
        if (row["doc_id"], row["memo_file"]) not in expected_events or row["event_type"] != "created":
            raise RuntimeError(f"event mismatch: {dict(row)}")

    for name in INBOX_FIXTURES:
        path = os.path.join(db.INBOX_DIR, name)
        if not os.path.exists(path):
            raise RuntimeError(f"missing inbox file: {path}")


def main() -> int:
    _ensure_dirs()

    print("=== TS004 fixture reset ===")
    print(f"DB      : {db.DB_PATH}")
    print(f"Inbox   : {db.INBOX_DIR}")
    print(f"Accept  : {db.ACCEPT_DIR}")
    print(f"Outbox  : {db.OUTBOX_DIR}")
    print(f"Reject  : {db.REJECT_DIR}")
    print("")

    conn = db.get_connection()
    try:
        current_groups = conn.execute(
            """
            SELECT group_id, project, module, status
            FROM groups
            WHERE group_id IN (?, ?)
            ORDER BY group_id
            """,
            TARGET_GROUP_IDS,
        ).fetchall()
        current_docs = conn.execute(
            """
            SELECT doc_id, type, group_id, status, target_id, next
            FROM documents
            WHERE doc_id IN (?, ?, ?, ?, ?, ?, ?, ?)
            ORDER BY doc_id
            """,
            TARGET_DOC_IDS,
        ).fetchall()
        current_events = conn.execute(
            """
            SELECT event_id, doc_id, event_type, memo_file
            FROM events
            WHERE doc_id IN (?, ?, ?, ?, ?, ?, ?, ?)
               OR doc_id IN (?, ?)
            ORDER BY event_id
            """,
            (*TARGET_DOC_IDS, *TARGET_GROUP_IDS),
        ).fetchall()
    finally:
        conn.close()

    _print_rows("[before] groups", [dict(r) for r in current_groups])
    _print_rows("[before] documents", [dict(r) for r in current_docs])
    _print_rows("[before] events", [dict(r) for r in current_events])

    reset_files = _collect_reset_files()
    print("[before] files")
    if reset_files:
        for path in reset_files:
            print(f"  {path}")
    else:
        print("  (none)")
    print("")

    removed_files = _delete_reset_files()
    print(f"Removed files: {len(removed_files)}")
    for path in removed_files:
        print(f"  - {path}")
    print("")

    conn = db.get_connection()
    try:
        conn.execute("BEGIN")
        conn.execute(
            "DELETE FROM events WHERE doc_id IN (?, ?, ?, ?, ?, ?, ?, ?) OR doc_id IN (?, ?)",
            (*TARGET_DOC_IDS, *TARGET_GROUP_IDS),
        )
        conn.execute(
            "DELETE FROM documents WHERE doc_id IN (?, ?, ?, ?, ?, ?, ?, ?)",
            TARGET_DOC_IDS,
        )
        conn.execute(
            "DELETE FROM groups WHERE group_id IN (?, ?)",
            TARGET_GROUP_IDS,
        )

        for row in GROUP_ROWS:
            _insert_group(conn, row)
        for row in DOC_ROWS:
            _insert_document(conn, row)
        _insert_event(conn, "server-ts004-9401-DS0001", "ts004_t051_base_ds_open.md")
        _insert_event(conn, "server-ts004-9402-DC0001", "ts004_t051_base_dc_open.md")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    for name, content in INBOX_FIXTURES.items():
        _write_text(os.path.join(db.INBOX_DIR, name), content)
        print(f"Created inbox file: {name}")

    _validate_state()

    print("")
    print("=== validation ===")
    print("groups   : server-ts004-9401, server-ts004-9402 OPEN")
    print("documents: 8 TS004 records reset")
    print("events   : DS001/DC001 created events restored")
    print("files    : inbox fixtures recreated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
