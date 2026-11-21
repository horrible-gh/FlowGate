"""restore the slot the regression broke, and make one document / one slot a rule.

Regression **I** of the review, plus the ordering and uniqueness work that hangs off it.

The incident, from the review: a rejected resubmit of `the earlier change`
was registered into the *sixth* slot of sequence 737 (item id 5674) because inbox Step 7.5
asked which slot was the "in-progress head" rather than which slot the document came from.
`the earlier change` was evicted and belonged to no slot at all; `the earlier implementation` was
left holding slots 4 **and** 6. the earlier implementation closed the code path. This suite covers the other
half — putting slot 6 back and making the two-slots-one-document state unreachable.

What is fixed here:

* **The repair** (migration 090). Every identifying fact from the report is in its WHERE
  clause, so it applies to that row and to nothing else: change any one of the sequence,
  the root document, the slot id, `item_seq`, the slot type, the value the slot holds, or
  the sibling slot's value, and it becomes a no-op. Re-running it after it has applied is a
  no-op too, and it is a no-op on a database that never saw the incident.
* **Nothing else moves.** Slot 4 keeps `the earlier implementation`; the other six slots, the
  `workflow_item_results` ledger (rows 4996 → 4999 → 5004 — the audit trail the review read
  to date the eviction) and every document row are byte-for-byte identical afterwards.
* **The reverse lookup is deterministic.** `get_sequence_item_by_result_doc_id` used to be
  an unordered `LIMIT 1`, so in the duplicated state the engine decided whether `the earlier implementation`
  belonged to slot 4 or slot 6. It now sorts, and answers the earlier slot.
* **The constraint.** `uq_wfseq_items_result_doc` refuses a second slot for a non-NULL
  document while leaving empty slots unconstrained, and `register_workflow_result` turns
  that refusal into `WorkflowDocumentAlreadyLinkedError` rather than a driver error —
  without disturbing the earlier implementation's occupancy conflict, which still guards the other direction.

The fixture is the real thing on both ends: a SQLite database built by applying the actual
migration files, seeded with the row values read out of the live PostgreSQL database
(sequence 737, items 5667–5676, ledger rows 4994/4996/4999/5004), and the migration under
test is read from disk and executed statement by statement so a repair can be counted in
rows affected rather than inferred.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[3]
_MIGRATIONS = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_QUERIES_JSON = _SERVER_DIR / "sql" / "queries" / "queries.json"

sys.path.insert(0, str(_SERVER_DIR))

REPAIR_MIGRATION = "090_workflow_slot_result_doc_unique.sql"

PROJECT_ID = "flowgate"
GROUP_ID = "flowgate.default.0454"
USER_ID = "usr_0457_t0007"
ROOT = "flowgate.default.0454.0001-B"
TR_FOUR = "flowgate.default.0454.0005-TR"
TR_SIX = "flowgate.default.0454.0007-TR"
SEQUENCE_ID = 737
SLOT_FOUR = 5672
SLOT_SIX = 5674

# The live rows, verbatim: (id, item_seq, type, sort_order, result_doc_id, updated_at).
INCIDENT_SLOTS = [
    (5667, 1, "CH", 0, "flowgate.default.0454.0002-CH", "2026-08-23T13:09:34.512Z"),
    (5670, 2, "WP", 1, "flowgate.default.0454.0003-WP", "2026-08-23T13:25:17.976Z"),
    (5671, 3, "T", 2, "flowgate.default.0454.0004-T", "2026-08-23T13:39:35.118Z"),
    (5672, 4, "TR", 3, TR_FOUR, "2026-08-23T14:02:28.476Z"),
    (5673, 5, "T", 4, "flowgate.default.0454.0006-T", "2026-08-23T14:09:38.658Z"),
    (5674, 6, "TR", 5, TR_FOUR, "2026-08-23T22:29:48.165Z"),  # ← held TR_SIX until the eviction
    (5675, 7, "TS", 6, None, "2026-08-23T13:31:17.482Z"),
    (5676, 8, "TSR", 7, None, "2026-08-23T13:31:17.482Z"),
]

# (doc_id, type_code, seq, doc_review_status, status, revision_no)
INCIDENT_DOCS = [
    (ROOT, "B", 1, "wf_in_progress", "closed", 0),
    ("flowgate.default.0454.0002-CH", "CH", 2, "approved", "draft", 0),
    ("flowgate.default.0454.0003-WP", "WP", 3, "approved", "open", 1),
    ("flowgate.default.0454.0004-T", "T", 4, "approved", "open", 0),
    (TR_FOUR, "TR", 5, "revised", "open", 2),
    ("flowgate.default.0454.0006-T", "T", 6, "pending_review", "open", 0),
    (TR_SIX, "TR", 7, "pending_review", "open", 0),
]

# workflow_item_results, verbatim: (id, item_id, registered_doc_id, registered_at).
# 4996 → 4999 → 5004 on slot 6 is the eviction the review dated. It is evidence, not damage.
INCIDENT_LEDGER = [
    (4994, SLOT_FOUR, TR_FOUR, "2026-08-23T23:02:28+09:00"),
    (4996, SLOT_SIX, TR_SIX, "2026-08-23T23:38:55+09:00"),
    (4999, SLOT_SIX, TR_FOUR, "2026-08-24T06:59:40+09:00"),
    (5004, SLOT_SIX, TR_FOUR, "2026-08-24T07:29:48+09:00"),
]


# ── migration plumbing ────────────────────────────────────────────────────────

def _migration_files(include_repair: bool) -> list[Path]:
    files = sorted(_MIGRATIONS.glob("*.sql"))
    if include_repair:
        return files
    return [p for p in files if p.name != REPAIR_MIGRATION]


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


_TXN = re.compile(r"^\s*(BEGIN|COMMIT|END)\b", re.IGNORECASE)


def _statements(sql: str) -> list[str]:
    """Executable statements of a migration, comments and transaction control removed.

    Splitting on `;` is safe for this file: it carries no string literal or trigger body
    containing one. Running the statements individually rather than through
    `executescript` is what lets a test read `cursor.rowcount` — "the repair changed
    exactly one row" and "the repair changed nothing" are the two facts this suite is
    mostly about, and neither is observable from a script run.
    """
    out = []
    for raw in _strip_comments(sql).split(";"):
        stmt = raw.strip()
        if stmt and not _TXN.match(stmt):
            out.append(stmt)
    return out


def _repair_sql() -> str:
    return (_MIGRATIONS / REPAIR_MIGRATION).read_text(encoding="utf-8")


def _run_statements(conn: sqlite3.Connection, statements: list[str]) -> list[int]:
    counts = []
    for stmt in statements:
        counts.append(conn.execute(stmt).rowcount)
    conn.commit()
    return counts


def _apply_repair(conn: sqlite3.Connection) -> list[int]:
    """Apply the whole 090 file. Returns one rowcount per statement."""
    return _run_statements(conn, _statements(_repair_sql()))


def _apply_repair_update_only(conn: sqlite3.Connection) -> int:
    """Apply only 090's UPDATE, and return the rows it changed.

    The guard cases deliberately leave the duplicate in place, so the file's
    CREATE UNIQUE INDEX would (correctly) refuse to build. Isolating the UPDATE is what
    lets those cases assert what the *repair* did — nothing — instead of only observing
    that the file as a whole failed.
    """
    updates = [s for s in _statements(_repair_sql()) if s.upper().startswith("UPDATE")]
    assert len(updates) == 1, f"090 should carry exactly one UPDATE, found {len(updates)}"
    counts = _run_statements(conn, updates)
    return counts[0]


def _build_db(tmp_path: Path, name: str, *, include_repair: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / name))
    conn.row_factory = sqlite3.Row
    for path in _migration_files(include_repair):
        conn.executescript(path.read_text(encoding="utf-8"))
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── the incident fixture ──────────────────────────────────────────────────────

def _seed_incident(conn: sqlite3.Connection) -> None:
    """Recreate the live 0454 rows, duplicate and all."""
    now = "2026-08-23T13:00:00.000Z"
    conn.execute(
        "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
        "VALUES (?,?,1,?,?)", [PROJECT_ID, "FlowGate", now, now])
    conn.execute(
        "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)", [USER_ID, "worker", "w@test", "x", now, now])
    conn.execute(
        "INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [GROUP_ID, PROJECT_ID, "default", "0454", "OPEN", now, now])
    for doc_id, type_code, seq, review, status, revision in INCIDENT_DOCS:
        conn.execute(
            "INSERT INTO documents (doc_id, project_id, module, group_id, type_code, seq, title, "
            "file_path, status, owner_id, revision_no, doc_review_status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [doc_id, PROJECT_ID, "default", GROUP_ID, type_code, seq, doc_id,
             f"documents/flowgate/main/default/0454/{doc_id.rsplit('.', 1)[-1]}_document.md",
             status, USER_ID, revision, review, now, now])
    conn.execute(
        "INSERT INTO workflow_sequences (id, doc_id, created_at, updated_at) VALUES (?,?,?,?)",
        [SEQUENCE_ID, ROOT, now, now])
    for item_id, item_seq, type_code, sort_order, result_doc_id, updated_at in INCIDENT_SLOTS:
        conn.execute(
            "INSERT INTO workflow_sequence_items (id, sequence_id, item_seq, type, label, "
            "doc_class, sort_order, result_doc_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [item_id, SEQUENCE_ID, item_seq, type_code, type_code, "R", sort_order,
             result_doc_id, now, updated_at])
    for row_id, item_id, doc_id, registered_at in INCIDENT_LEDGER:
        conn.execute(
            "INSERT INTO workflow_item_results (id, item_id, registered_path, registered_doc_id, "
            "status, registered_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            [row_id, item_id, f"documents/{doc_id}.md", doc_id, "pending_approval",
             registered_at, now, now])
    conn.commit()


@pytest.fixture
def incident_db(tmp_path):
    """A database in the exact pre-repair state, with 090 not yet applied."""
    conn = _build_db(tmp_path, "incident.db", include_repair=False)
    _seed_incident(conn)
    yield conn
    conn.close()


@pytest.fixture
def repaired_db(incident_db):
    _apply_repair(incident_db)
    return incident_db


# ── readers ───────────────────────────────────────────────────────────────────

def _slot(conn, item_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM workflow_sequence_items WHERE id = ?", [item_id]).fetchone()
    return dict(row) if row else None


def _slots(conn) -> dict[int, dict]:
    return {
        r["id"]: dict(r)
        for r in conn.execute("SELECT * FROM workflow_sequence_items ORDER BY id")
    }


def _documents(conn) -> dict[str, dict]:
    return {r["doc_id"]: dict(r) for r in conn.execute("SELECT * FROM documents ORDER BY doc_id")}


def _ledger(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM workflow_item_results ORDER BY id")]


def _duplicates(conn) -> list[dict]:
    """The whole-table duplicate audit, run straight from queries.json."""
    sql = json.loads(_QUERIES_JSON.read_text(encoding="utf-8"))
    sql = sql["workflow_sequences"]["find_duplicate_result_doc_slots"]
    return [dict(r) for r in conn.execute(sql)]


def _indexes(conn) -> set[str]:
    return {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='workflow_sequence_items'")
    }


# ══════════════════════════════════════════════════════════════════════════════
# The fixture is the incident
# ══════════════════════════════════════════════════════════════════════════════

def test_the_fixture_reproduces_the_live_0454_shape(incident_db):
    """Both TR slots hold the earlier implementation, the ledger carries the eviction, the earlier implementation is loose.

    Every later assertion is read against this state, so it is pinned first — including
    the fact that the duplicate is possible at all before 090.
    """
    assert _slot(incident_db, SLOT_FOUR)["result_doc_id"] == TR_FOUR
    assert _slot(incident_db, SLOT_SIX)["result_doc_id"] == TR_FOUR

    six_ledger = [r for r in _ledger(incident_db) if r["item_id"] == SLOT_SIX]
    assert [r["registered_doc_id"] for r in six_ledger] == [TR_SIX, TR_FOUR, TR_FOUR]
    assert [r["id"] for r in six_ledger] == [4996, 4999, 5004]

    # is in no slot at all — the orphan the regression reported.
    assert incident_db.execute(
        "SELECT COUNT(*) FROM workflow_sequence_items WHERE result_doc_id = ?",
        [TR_SIX]).fetchone()[0] == 0

    assert "uq_wfseq_items_result_doc" not in _indexes(incident_db)


# ══════════════════════════════════════════════════════════════════════════════
# Regression I — the repair
# ══════════════════════════════════════════════════════════════════════════════

def test_I_the_repair_returns_slot_six_to_0007_tr(incident_db):
    counts = _apply_repair(incident_db)
    assert counts[0] == 1, "the repair should change exactly one row"
    assert _slot(incident_db, SLOT_SIX)["result_doc_id"] == TR_SIX


def test_I_slot_four_keeps_0005_tr(incident_db):
    before = _slot(incident_db, SLOT_FOUR)
    _apply_repair(incident_db)
    assert _slot(incident_db, SLOT_FOUR) == before, (
        "slot 4 is not part of the incident and must come out identical, "
        "down to updated_at"
    )


def test_I_no_other_slot_moves_and_only_result_doc_id_changes(incident_db):
    before = _slots(incident_db)
    _apply_repair(incident_db)
    after = _slots(incident_db)

    assert set(before) == set(after)
    for item_id in before:
        if item_id == SLOT_SIX:
            continue
        assert after[item_id] == before[item_id], f"slot {item_id} was written to"

    changed = {
        k for k, v in after[SLOT_SIX].items() if before[SLOT_SIX][k] != v
    }
    assert changed == {"result_doc_id"}, (
        f"the repair touched {changed - {'result_doc_id'}} as well; updated_at is left at "
        f"the moment of the eviction on purpose"
    )
    assert before[SLOT_SIX]["updated_at"] == "2026-08-23T22:29:48.165Z"


def test_I_the_ledger_and_the_documents_are_untouched(incident_db):
    ledger_before = _ledger(incident_db)
    docs_before = _documents(incident_db)
    _apply_repair(incident_db)

    assert _ledger(incident_db) == ledger_before, (
        "workflow_item_results is the audit trail of the eviction, not damage to undo — "
        "no row deleted, no row rewritten, and no success row invented for the repair"
    )
    assert _documents(incident_db) == docs_before, (
        "review status, file path and revision of both documents stay as they are"
    )




# ══════════════════════════════════════════════════════════════════════════════
# The whole-table duplicate audit
# ══════════════════════════════════════════════════════════════════════════════





# ══════════════════════════════════════════════════════════════════════════════
# The repair is targeted, idempotent, and silent everywhere else
# ══════════════════════════════════════════════════════════════════════════════





GUARDS = {
    "the slot already holds something else": (
        "UPDATE workflow_sequence_items SET result_doc_id = ? WHERE id = ?",
        ["flowgate.default.0454.0006-T", SLOT_SIX],
    ),
    "the slot is empty": (
        "UPDATE workflow_sequence_items SET result_doc_id = NULL WHERE id = ?", [SLOT_SIX],
    ),
    "the slot type is not TR": (
        "UPDATE workflow_sequence_items SET type = 'TSR' WHERE id = ?", [SLOT_SIX],
    ),
    "the slot sits at a different item_seq": (
        "UPDATE workflow_sequence_items SET item_seq = 9 WHERE id = ?", [SLOT_SIX],
    ),
    "the slot belongs to a different sequence": (
        "UPDATE workflow_sequence_items SET sequence_id = 999 WHERE id = ?", [SLOT_SIX],
    ),
    "the sequence has a different root document": (
        "UPDATE workflow_sequences SET doc_id = ? WHERE id = ?",
        ["flowgate.default.0454.0006-T", SEQUENCE_ID],
    ),
    "the sibling slot no longer holds 0005-TR": (
        "UPDATE workflow_sequence_items SET result_doc_id = NULL WHERE id = ?", [SLOT_FOUR],
    ),
    "the document being restored does not exist": (
        "DELETE FROM documents WHERE doc_id = ?", [TR_SIX],
    ),
}




# ══════════════════════════════════════════════════════════════════════════════
# The constraint
# ══════════════════════════════════════════════════════════════════════════════











# ══════════════════════════════════════════════════════════════════════════════
# The reverse lookup, through the real store
# ══════════════════════════════════════════════════════════════════════════════

_QUERIES: dict[str, str] = {}
for _section, _entries in json.loads(_QUERIES_JSON.read_text(encoding="utf-8")).items():
    if isinstance(_entries, dict):
        for _key, _sql in _entries.items():
            if isinstance(_sql, str):
                _QUERIES[f"{_section}.{_key}"] = _sql.replace("%s", "?")


class _Backend:
    """The store backend shape `FlowGateStore` expects, over one SQLite connection."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        yield _Txn(self._conn)


class _Txn:
    def __init__(self, conn):
        self._conn = conn
        self._cursor = None

    def execute(self, sql, params=None):
        self._cursor = self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self):
        row = self._cursor.fetchone() if self._cursor else None
        return dict(row) if row else None

    def fetch_all(self):
        return [dict(r) for r in self._cursor.fetchall()] if self._cursor else []


@contextmanager
def _store_over(conn: sqlite3.Connection):
    """Point the real db layer at this connection.

    The STORE *object* is swapped rather than `get_store` patched: patching the function
    leaks into modules imported later, which have already bound the name.
    """
    from modules.flow_gate.db import connection as conn_mod

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = _Backend(conn)
            self._sq = None

        def _sql(self, key: str) -> str:
            if key in _QUERIES:
                return _QUERIES[key]
            raise KeyError(f"Query not found: {key}")

    original = conn_mod.STORE
    conn_mod.STORE = _PatchedStore()
    try:
        yield
    finally:
        conn_mod.STORE = original




# The query as it stood before this change. Kept verbatim so the control test below
# measures the real difference and not a paraphrase of it.
_UNORDERED_LOOKUP = "SELECT * FROM workflow_sequence_items WHERE result_doc_id = ? LIMIT 1"

EARLY_SLOT, LATE_SLOT = 8800, 8100
SHARED_DOC = "flowgate.default.0454.0010-TR"


def _seed_disagreeing_row_order(conn) -> None:
    """A duplicated document whose earlier slot has the *higher* row id.

    0454's own rows cannot tell the ordering apart: there, ascending id and ascending
    sort_order happen to agree, so an unordered lookup lands on the right slot by luck.
    called that out as the second-order risk rather than a second bug — "the
    database decides". A sequence whose rows were inserted out of order is all it takes for
    luck to run the other way, and that is what this seeds: sort_order 1 at id 8800,
    sort_order 5 at id 8100, both holding the same document.
    """
    now = "2026-08-23T13:00:00.000Z"
    for doc_id, type_code, seq in ((SHARED_DOC, "TR", 10), ("flowgate.default.0454.0011-B", "B", 11)):
        conn.execute(
            "INSERT INTO documents (doc_id, project_id, module, group_id, type_code, seq, title, "
            "status, owner_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [doc_id, PROJECT_ID, "default", GROUP_ID, type_code, seq, doc_id, "open",
             USER_ID, now, now])
    conn.execute(
        "INSERT INTO workflow_sequences (id, doc_id, created_at, updated_at) VALUES (?,?,?,?)",
        [900, "flowgate.default.0454.0011-B", now, now])
    for item_id, item_seq, sort_order in ((LATE_SLOT, 5, 5), (EARLY_SLOT, 1, 1)):
        conn.execute(
            "INSERT INTO workflow_sequence_items (id, sequence_id, item_seq, type, label, "
            "doc_class, sort_order, result_doc_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            [item_id, 900, item_seq, "TR", "TR", "R", sort_order, SHARED_DOC, now, now])
    conn.commit()












# ══════════════════════════════════════════════════════════════════════════════
# The constraint seen from the API side — and the earlier implementation's conflict left intact
# ══════════════════════════════════════════════════════════════════════════════

def _register(item_id: int, doc_id: str):
    from modules.flow_gate.workflow.pipeline_service import register_workflow_result

    return register_workflow_result(
        item_id=item_id,
        registered_path=f"documents/{doc_id}.md",
        registered_doc_id=doc_id,
        registered_at="2026-08-24T12:00:00Z",
        actor_user_id=USER_ID,
    )


def _conflict_events(conn) -> list[dict]:
    return [
        dict(r) for r in conn.execute(
            "SELECT * FROM workflow_events WHERE event_type = 'workflow_slot_conflict' "
            "ORDER BY id")
    ]
