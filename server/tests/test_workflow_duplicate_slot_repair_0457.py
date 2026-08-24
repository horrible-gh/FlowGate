"""0457 T0007 — restore the slot B0001 broke, and make one document / one slot a rule.

Regression **I** of NR0003 §8, plus the ordering and uniqueness work that hangs off it.

The incident, from NR0003 §2/§3: a rejected resubmit of `flowgate.default.0454.0005-TR`
was registered into the *sixth* slot of sequence 737 (item id 5674) because inbox Step 7.5
asked which slot was the "in-progress head" rather than which slot the document came from.
`flowgate.default.0454.0007-TR` was evicted and belonged to no slot at all; `0005-TR` was
left holding slots 4 **and** 6. T0005 closed the code path. This suite covers the other
half — putting slot 6 back and making the two-slots-one-document state unreachable.

What is fixed here:

* **The repair** (migration 090). Every identifying fact from the report is in its WHERE
  clause, so it applies to that row and to nothing else: change any one of the sequence,
  the root document, the slot id, `item_seq`, the slot type, the value the slot holds, or
  the sibling slot's value, and it becomes a no-op. Re-running it after it has applied is a
  no-op too, and it is a no-op on a database that never saw the incident.
* **Nothing else moves.** Slot 4 keeps `0005-TR`; the other six slots, the
  `workflow_item_results` ledger (rows 4996 → 4999 → 5004 — the audit trail NR0003 §3 read
  to date the eviction) and every document row are byte-for-byte identical afterwards.
* **The reverse lookup is deterministic.** `get_sequence_item_by_result_doc_id` used to be
  an unordered `LIMIT 1`, so in the duplicated state the engine decided whether `0005-TR`
  belonged to slot 4 or slot 6. It now sorts, and answers the earlier slot.
* **The constraint.** `uq_wfseq_items_result_doc` refuses a second slot for a non-NULL
  document while leaving empty slots unconstrained, and `register_workflow_result` turns
  that refusal into `WorkflowDocumentAlreadyLinkedError` rather than a driver error —
  without disturbing T0005's occupancy conflict, which still guards the other direction.

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

_SERVER_DIR = Path(__file__).resolve().parents[1]
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
# 4996 → 4999 → 5004 on slot 6 is the eviction NR0003 §3 dated. It is evidence, not damage.
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
    """Both TR slots hold 0005-TR, the ledger carries the eviction, 0007-TR is loose.

    Every later assertion is read against this state, so it is pinned first — including
    the fact that the duplicate is possible at all before 090.
    """
    assert _slot(incident_db, SLOT_FOUR)["result_doc_id"] == TR_FOUR
    assert _slot(incident_db, SLOT_SIX)["result_doc_id"] == TR_FOUR

    six_ledger = [r for r in _ledger(incident_db) if r["item_id"] == SLOT_SIX]
    assert [r["registered_doc_id"] for r in six_ledger] == [TR_SIX, TR_FOUR, TR_FOUR]
    assert [r["id"] for r in six_ledger] == [4996, 4999, 5004]

    # 0007-TR is in no slot at all — the orphan B0001 reported.
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


def test_I_0007_tr_is_no_longer_an_orphan_and_0005_tr_holds_one_slot(repaired_db):
    holders = {
        doc: [r["id"] for r in repaired_db.execute(
            "SELECT id FROM workflow_sequence_items WHERE result_doc_id = ? ORDER BY id", [doc])]
        for doc in (TR_FOUR, TR_SIX)
    }
    assert holders == {TR_FOUR: [SLOT_FOUR], TR_SIX: [SLOT_SIX]}


# ══════════════════════════════════════════════════════════════════════════════
# The whole-table duplicate audit
# ══════════════════════════════════════════════════════════════════════════════

def test_the_audit_finds_the_one_known_duplicate_before_the_repair(incident_db):
    rows = _duplicates(incident_db)
    assert [r["result_doc_id"] for r in rows] == [TR_FOUR, TR_FOUR]
    assert [(r["sequence_id"], r["item_id"], r["item_seq"], r["sort_order"]) for r in rows] == [
        (SEQUENCE_ID, SLOT_FOUR, 4, 3),
        (SEQUENCE_ID, SLOT_SIX, 6, 5),
    ], "the audit must name both sides by sequence, slot id, item_seq and sort_order"


def test_the_audit_is_empty_after_the_repair(repaired_db):
    assert _duplicates(repaired_db) == []


# ══════════════════════════════════════════════════════════════════════════════
# The repair is targeted, idempotent, and silent everywhere else
# ══════════════════════════════════════════════════════════════════════════════

def test_re_running_the_repair_changes_nothing(repaired_db):
    before = _slots(repaired_db)
    ledger_before = _ledger(repaired_db)
    counts = _apply_repair(repaired_db)
    assert counts[0] == 0, "the second application must match no rows"
    assert _slots(repaired_db) == before
    assert _ledger(repaired_db) == ledger_before


def test_a_database_that_never_saw_the_incident_is_untouched(tmp_path):
    """A fresh install: the file applies, the constraint appears, no data is invented."""
    conn = _build_db(tmp_path, "fresh.db", include_repair=False)
    counts = _apply_repair(conn)
    assert counts[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM workflow_sequence_items").fetchone()[0] == 0
    assert "uq_wfseq_items_result_doc" in _indexes(conn)
    conn.close()


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


@pytest.mark.parametrize("difference", sorted(GUARDS))
def test_the_repair_is_a_no_op_when_any_identifying_fact_differs(incident_db, difference):
    """Change one fact from NR0003 §2 and the repair declines to act.

    Only 090's UPDATE runs here. These cases deliberately leave the duplicate in place, so
    the file's CREATE UNIQUE INDEX would refuse to build — which would tell us nothing
    about whether the *repair* held its fire.
    """
    if difference == "the slot belongs to a different sequence":
        incident_db.execute(
            "INSERT INTO workflow_sequences (id, doc_id, created_at, updated_at) VALUES "
            "(999, 'flowgate.default.0454.0004-T', '2026-01-01', '2026-01-01')")
    sql, params = GUARDS[difference]
    incident_db.execute(sql, params)
    incident_db.commit()

    before = _slots(incident_db)
    assert _apply_repair_update_only(incident_db) == 0, (
        f"the repair fired even though {difference}"
    )
    assert _slots(incident_db) == before


# ══════════════════════════════════════════════════════════════════════════════
# The constraint
# ══════════════════════════════════════════════════════════════════════════════

def test_the_repair_replaces_the_plain_index_with_a_unique_one(repaired_db):
    indexes = _indexes(repaired_db)
    assert "uq_wfseq_items_result_doc" in indexes
    assert "idx_wfseq_items_result_doc" not in indexes, (
        "the plain index served the same reverse lookup; leaving both would be two "
        "definitions of one thing"
    )
    ddl = repaired_db.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'uq_wfseq_items_result_doc'").fetchone()[0]
    assert "UNIQUE" in ddl.upper()
    assert "WHERE result_doc_id IS NOT NULL" in ddl


def test_a_second_slot_cannot_take_a_document_another_slot_already_holds(repaired_db):
    with pytest.raises(sqlite3.IntegrityError):
        repaired_db.execute(
            "UPDATE workflow_sequence_items SET result_doc_id = ? WHERE id = ?",
            [TR_FOUR, 5675])
    repaired_db.rollback()
    assert _slot(repaired_db, 5675)["result_doc_id"] is None


def test_many_slots_may_stay_empty(repaired_db):
    repaired_db.execute(
        "UPDATE workflow_sequence_items SET result_doc_id = NULL WHERE id IN (?,?)",
        [5671, 5673])
    repaired_db.commit()
    empty = repaired_db.execute(
        "SELECT COUNT(*) FROM workflow_sequence_items WHERE result_doc_id IS NULL").fetchone()[0]
    assert empty == 4, "NULL is not a value the uniqueness rule applies to"


def test_a_slot_may_be_rewritten_with_the_document_it_already_holds(repaired_db):
    repaired_db.execute(
        "UPDATE workflow_sequence_items SET result_doc_id = ? WHERE id = ?", [TR_SIX, SLOT_SIX])
    repaired_db.commit()
    assert _slot(repaired_db, SLOT_SIX)["result_doc_id"] == TR_SIX


def test_the_constraint_refuses_to_build_over_an_unresolved_duplicate(incident_db):
    """The failure mode the migration comment documents, pinned rather than assumed.

    A database carrying a duplicate other than 0454's does not get the constraint applied
    quietly around it — the migration stops. That is the intended behaviour for an
    integrity constraint, and the audit query is how the offending rows are found.
    """
    index_stmts = [
        s for s in _statements(_repair_sql()) if s.upper().startswith("CREATE UNIQUE INDEX")
    ]
    assert len(index_stmts) == 1
    # Leave the duplicate unrepaired by making the repair's guard miss.
    incident_db.execute("UPDATE workflow_sequence_items SET type = 'TSR' WHERE id = ?", [SLOT_SIX])
    incident_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        incident_db.execute(index_stmts[0])
    incident_db.rollback()
    assert len(_duplicates(incident_db)) == 2


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


def test_a_document_in_two_slots_resolves_to_the_earlier_one(incident_db):
    """The legacy shape: the lookup must not be at the engine's discretion.

    Before the ordering was added the query was `WHERE result_doc_id = ? LIMIT 1` with no
    `ORDER BY`, so which slot 0005-TR belonged to was whatever came back first. Databases
    that have not yet run 090 still hold states like this one.
    """
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    with _store_over(incident_db):
        item = db_wfseq.get_item_by_result_doc_id(TR_FOUR)
        assert item["id"] == SLOT_FOUR, "the earlier slot (sort_order 3) is the answer"
        assert item["item_seq"] == 4
        # …and repeatedly, not by luck.
        for _ in range(5):
            assert db_wfseq.get_item_by_result_doc_id(TR_FOUR)["id"] == SLOT_FOUR
        assert db_wfseq.get_item_by_result_doc_id(TR_SIX) is None


# The query as it stood before this change. Kept verbatim so the control test below
# measures the real difference and not a paraphrase of it.
_UNORDERED_LOOKUP = "SELECT * FROM workflow_sequence_items WHERE result_doc_id = ? LIMIT 1"

EARLY_SLOT, LATE_SLOT = 8800, 8100
SHARED_DOC = "flowgate.default.0454.0010-TR"


def _seed_disagreeing_row_order(conn) -> None:
    """A duplicated document whose earlier slot has the *higher* row id.

    0454's own rows cannot tell the ordering apart: there, ascending id and ascending
    sort_order happen to agree, so an unordered lookup lands on the right slot by luck.
    NR0003 §4.4 called that out as the second-order risk rather than a second bug — "the
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


def test_the_lookup_follows_sort_order_and_not_the_row_id(incident_db):
    """The earlier slot wins even when it is the later row.

    This is the assertion the ordering exists for. Without `ORDER BY sort_order ASC` the
    answer here is the *later* slot — see the control below, which runs the previous query
    text against the same rows.
    """
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    _seed_disagreeing_row_order(incident_db)

    with _store_over(incident_db):
        item = db_wfseq.get_item_by_result_doc_id(SHARED_DOC)

    assert item["id"] == EARLY_SLOT
    assert item["sort_order"] == 1
    assert item["item_seq"] == 1


def test_control_the_previous_unordered_lookup_answers_the_later_slot(incident_db):
    """The control for the test above: the old query text really does get this wrong.

    Without it, a green ordering test cannot be told apart from a fixture in which any
    query would have answered correctly — which is exactly what 0454's own rows are.
    """
    _seed_disagreeing_row_order(incident_db)

    row = incident_db.execute(_UNORDERED_LOOKUP, [SHARED_DOC]).fetchone()
    assert row["id"] == LATE_SLOT, (
        "if this ever stops picking the later slot the control has gone stale and the "
        "ordering test above is no longer measuring anything"
    )
    assert row["sort_order"] == 5


def test_after_the_repair_each_document_resolves_to_its_own_slot(repaired_db):
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    with _store_over(repaired_db):
        assert db_wfseq.get_item_by_result_doc_id(TR_FOUR)["id"] == SLOT_FOUR
        assert db_wfseq.get_item_by_result_doc_id(TR_SIX)["id"] == SLOT_SIX


def test_the_orphan_verdict_flips_only_for_0007_tr(incident_db):
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    with _store_over(incident_db):
        assert db_wfseq.is_orphaned_workflow_member(TR_SIX) is True
        assert db_wfseq.is_orphaned_workflow_member(TR_FOUR) is False

    _apply_repair(incident_db)

    with _store_over(incident_db):
        assert db_wfseq.is_orphaned_workflow_member(TR_SIX) is False, (
            "this is what B0001 asked for: 0007-TR belongs to slot 6 again"
        )
        assert db_wfseq.is_orphaned_workflow_member(TR_FOUR) is False
        assert db_wfseq.get_sequence_for_member_doc(TR_SIX)["id"] == SEQUENCE_ID


def test_the_duplicate_audit_reads_the_same_through_the_db_layer(incident_db):
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    with _store_over(incident_db):
        rows = db_wfseq.find_duplicate_result_doc_slots()
    assert [(r["result_doc_id"], r["item_id"]) for r in rows] == [
        (TR_FOUR, SLOT_FOUR), (TR_FOUR, SLOT_SIX)]

    _apply_repair(incident_db)
    with _store_over(incident_db):
        assert db_wfseq.find_duplicate_result_doc_slots() == []


# ══════════════════════════════════════════════════════════════════════════════
# The constraint seen from the API side — and T0005's conflict left intact
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


def test_linking_a_document_to_a_second_empty_slot_is_refused(repaired_db):
    """The uniqueness rule reaches the caller as a named workflow conflict, not a DB error."""
    from modules.flow_gate.workflow.pipeline_service import (
        WorkflowDocumentAlreadyLinkedError,
    )

    before = _slots(repaired_db)
    ledger_before = _ledger(repaired_db)
    events_before = len(_conflict_events(repaired_db))

    with _store_over(repaired_db):
        with pytest.raises(WorkflowDocumentAlreadyLinkedError) as excinfo:
            _register(5675, TR_FOUR)     # slot 7 is empty; 0005-TR is already in slot 4

    exc = excinfo.value
    assert exc.error["code"] == "workflow_document_already_linked"
    assert exc.item_id == 5675
    assert exc.existing_item_id == SLOT_FOUR
    assert exc.requested_doc_id == TR_FOUR
    assert exc.body() == {"error": exc.error}

    assert _slots(repaired_db) == before, "a refused link must leave the data alone"
    assert _ledger(repaired_db) == ledger_before, "and no ledger row that reads like a success"

    events = _conflict_events(repaired_db)
    assert len(events) == events_before + 1
    meta = json.loads(events[-1]["metadata"])
    assert meta["code"] == "workflow_document_already_linked"
    assert meta["item_id"] == 5675
    assert meta["existing_item_id"] == SLOT_FOUR


def test_a_document_may_still_re_register_into_its_own_slot(repaired_db):
    """The compatibility the rejected-resubmit path depends on, unchanged."""
    before = _slot(repaired_db, SLOT_SIX)["result_doc_id"]
    rows_before = len([r for r in _ledger(repaired_db) if r["item_id"] == SLOT_SIX])

    with _store_over(repaired_db):
        _register(SLOT_SIX, TR_SIX)

    assert _slot(repaired_db, SLOT_SIX)["result_doc_id"] == before == TR_SIX
    rows_after = len([r for r in _ledger(repaired_db) if r["item_id"] == SLOT_SIX])
    assert rows_after == rows_before + 1


def test_the_occupancy_conflict_keeps_its_own_meaning(repaired_db):
    """T0005's refusal is a different one and must not be blurred into the new code.

    Slot 6 holds 0007-TR; a document that is in no slot at all tries to take it. Nothing
    about uniqueness is involved — this is the eviction guard, and it must still answer
    `workflow_slot_occupied`.
    """
    from modules.flow_gate.workflow.pipeline_service import (
        WorkflowDocumentAlreadyLinkedError,
        WorkflowSlotConflictError,
    )

    repaired_db.execute(
        "INSERT INTO documents (doc_id, project_id, module, group_id, type_code, seq, title, "
        "status, owner_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ["flowgate.default.0454.0008-TR", PROJECT_ID, "default", GROUP_ID, "TR", 8,
         "loose", "open", USER_ID, "2026-08-24T00:00:00Z", "2026-08-24T00:00:00Z"])
    repaired_db.commit()
    before = _slots(repaired_db)

    with _store_over(repaired_db):
        with pytest.raises(WorkflowSlotConflictError) as excinfo:
            _register(SLOT_SIX, "flowgate.default.0454.0008-TR")

    exc = excinfo.value
    assert not isinstance(exc, WorkflowDocumentAlreadyLinkedError)
    assert exc.error["code"] == "workflow_slot_occupied"
    assert exc.existing_doc_id == TR_SIX
    assert _slots(repaired_db) == before


def test_an_empty_slot_still_accepts_a_document_that_is_in_no_slot(repaired_db):
    """The refusals are refusals, not a freeze: the normal registration still works."""
    repaired_db.execute(
        "INSERT INTO documents (doc_id, project_id, module, group_id, type_code, seq, title, "
        "status, owner_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ["flowgate.default.0454.0009-TS", PROJECT_ID, "default", GROUP_ID, "TS", 9,
         "scenario", "open", USER_ID, "2026-08-24T00:00:00Z", "2026-08-24T00:00:00Z"])
    repaired_db.commit()

    with _store_over(repaired_db):
        _register(5675, "flowgate.default.0454.0009-TS")

    assert _slot(repaired_db, 5675)["result_doc_id"] == "flowgate.default.0454.0009-TS"
    assert _duplicates(repaired_db) == []
