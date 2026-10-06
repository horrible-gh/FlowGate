"""Protected rows, row-id echoes and the card-id column — flowgate.default.0649 T#1.

NR0003 O1 (§3.6 / §3.9) and §5.4, against a real sqlite file with every migration applied:

  * migration 138 adds ``source_wp_card_id``; insert / read / backfill / in-place update
    round-trip through the registered SQL, and the three dialect files agree;
  * "started" is one predicate (result_doc_id), and the derived ``status`` agrees with it;
  * a protected row — a started row, or the pending report right after a started
    instruction — is never deleted, re-inserted or renumbered by a sequence edit, whether
    the payload echoes it (by item_id), leaves it out, shuffles it, or has several;
  * an echo may update a protected report's settings in place, may not change its identity
    (protected_row_modified), a stale id is refused (sequence_item_stale), and a lone
    id-less report next to a protected one is refused (protected_row_echo_ambiguous);
  * GET /workflow/sequence, the AI sequence-edit data and the pour candidates carry
    item_id / protected, and P1 never sends a protected row.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("ALLOWED_ORIGIN", "")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import workflow_sequences as db_wfseq  # noqa: E402
from modules.flow_gate.services import workflow_decision_service as wds  # noqa: E402

_PROJECT = "flowgate"
_GROUP = "flowgate.default.0649"
_ROOT = "flowgate.default.0649.0001-R"
_WP = "flowgate.default.0649.0004-WP"
_MIGRATIONS = _SERVER_DIR / "sql" / "migrations"

_SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
    VALUES('{_PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'default', 'mixed mode', 'OPEN', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
        doc_id, project_id, module, group_id, type_code, seq, title, status,
        doc_review_status, created_at, updated_at)
    VALUES('{_ROOT}', '{_PROJECT}', 'default', '{_GROUP}', 'R', 1, 'root', 'open',
           'wf_in_progress', datetime('now'), datetime('now')),
          ('{_WP}', '{_PROJECT}', 'default', '{_GROUP}', 'WP', 4, 'plan', 'open',
           'approved', datetime('now'), datetime('now')),
          ('{_GROUP}.0005-T', '{_PROJECT}', 'default', '{_GROUP}', 'T', 5, 't1', 'open',
           'approved', datetime('now'), datetime('now')),
          ('{_GROUP}.0007-T', '{_PROJECT}', 'default', '{_GROUP}', 'T', 7, 't2', 'open',
           'pending_review', datetime('now'), datetime('now'));
"""


class _SqliteStore:
    """Same minimal contract as test_sequence_meta_db_roundtrip_0406 — no ``_sql``, so the
    registered SQL in queries.json is what runs."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def transaction(self):
        yield self


@pytest.fixture
def store(migrated_sqlite_db, monkeypatch):
    db_path = migrated_sqlite_db("protected_rows_0649.db", seed_sql=_SEED_SQL)
    real = _SqliteStore(db_path)
    previous = db_connection.STORE
    db_connection.STORE = real
    # The edit's AC archive walk and owner reload are not under test here.
    monkeypatch.setattr(wds.db_documents, "list_documents", lambda **kwargs: [])
    try:
        yield real
    finally:
        db_connection.STORE = previous
        real._conn.close()


def _seed_rows(rows: list[dict]) -> int:
    """Insert rows (in order) through the real insert; returns the sequence id.

    Each row: type, optional result (doc id), source, rev, card, note, provider.
    """
    db_wfseq.insert_sequence(_ROOT)
    seq = db_wfseq.get_sequence_by_doc_id(_ROOT)
    for index, row in enumerate(rows):
        db_wfseq.insert_sequence_item(
            sequence_id=seq["id"], item_seq=index + 1, type_=row["type"],
            label=row.get("label", row["type"]), doc_class="R", sort_order=index,
            note=row.get("note", ""), source_doc_id=row.get("source"),
            source_revision_no=row.get("rev"), provider_id=row.get("provider"),
            source_wp_card_id=row.get("card"),
        )
    items = db_wfseq.get_sequence_items(seq["id"])
    for item, row in zip(items, rows):
        if row.get("result"):
            db_wfseq.set_item_result_doc_id(item["id"], row["result"])
    return seq["id"]


def _items(seq_id: int) -> list[dict]:
    return db_wfseq.get_sequence_items(seq_id)


def _snapshot(seq_id: int) -> list[tuple]:
    return [
        (r["id"], r["item_seq"], r["sort_order"], r["type"], r["result_doc_id"])
        for r in _items(seq_id)
    ]


def _by_type(seq_id: int, code: str) -> list[dict]:
    return [r for r in _items(seq_id) if r["type"] == code]


# ── §5.4: migration and DB round trip ─────────────────────────────────────────

def test_migration_138_exists_in_all_three_dialects_and_adds_the_column():
    texts = {
        dialect: (_MIGRATIONS / dialect / "138_workflow_sequence_card_id.sql").read_text(encoding="utf-8")
        for dialect in ("sqlite", "postgres", "mysql")
    }
    for dialect, text in texts.items():
        statements = re.sub(r"--[^\n]*", "", text)
        assert re.search(
            r"ALTER TABLE workflow_sequence_items\s+ADD COLUMN source_wp_card_id\s+"
            r"(TEXT|VARCHAR\(128\))\s+DEFAULT NULL",
            statements,
        ), dialect
    assert "VARCHAR(128)" in texts["postgres"] and "VARCHAR(128)" in texts["mysql"]


def test_card_id_round_trips_through_the_registered_sql(store):
    seq_id = _seed_rows([
        {"type": "T", "source": _WP, "rev": 3, "card": "c_card00000001"},
        {"type": "TR", "source": _WP, "rev": 3, "card": "c_card00000001"},
        {"type": "D"},
    ])
    rows = _items(seq_id)
    assert [r["source_wp_card_id"] for r in rows] == ["c_card00000001", "c_card00000001", None]

    # The backfill writes exactly that one column on exactly that one row.
    before = {k: v for k, v in rows[2].items() if k not in ("source_wp_card_id",)}
    db_wfseq.update_sequence_item_card_id(rows[2]["id"], seq_id, "retired:r0:D#1")
    after = _items(seq_id)[2]
    assert after["source_wp_card_id"] == "retired:r0:D#1"
    assert {k: v for k, v in after.items() if k != "source_wp_card_id"} == before
    # Scoped by sequence: a foreign sequence id writes nothing.
    db_wfseq.update_sequence_item_card_id(rows[0]["id"], seq_id + 99, "c_other")
    assert _items(seq_id)[0]["source_wp_card_id"] == "c_card00000001"

    # The plan-snapshot update keeps the card id unless it is given one.
    db_wfseq.update_sequence_item_plan_snapshot(
        rows[0]["id"], note="n", source_doc_id=_WP, source_revision_no=4,
        provider_id=None, provider_display_name=None,
    )
    assert _items(seq_id)[0]["source_wp_card_id"] == "c_card00000001"
    db_wfseq.update_sequence_item_plan_snapshot(
        rows[0]["id"], note="n", source_doc_id=_WP, source_revision_no=4,
        provider_id=None, provider_display_name=None, source_wp_card_id="c_card00000002",
    )
    assert _items(seq_id)[0]["source_wp_card_id"] == "c_card00000002"


def test_delete_unprotected_pending_items_keeps_the_named_rows(store):
    seq_id = _seed_rows([{"type": "T"}, {"type": "TR"}, {"type": "D"}])
    rows = _items(seq_id)
    db_wfseq.delete_unprotected_pending_items(seq_id, [rows[1]["id"]])
    assert [r["id"] for r in _items(seq_id)] == [rows[1]["id"]]
    db_wfseq.delete_unprotected_pending_items(seq_id, [])
    assert _items(seq_id) == []


def test_started_is_result_doc_and_status_agrees_with_it(store):
    """status is derived from result_doc_id: none -> pending, approved -> done, else in_progress."""
    seq_id = _seed_rows([
        {"type": "T", "result": f"{_GROUP}.0005-T"},
        {"type": "T", "result": f"{_GROUP}.0007-T"},
        {"type": "D"},
    ])
    rows = _items(seq_id)
    assert [r["status"] for r in rows] == ["done", "in_progress", "pending"]
    for row in rows:
        assert (row["status"] == "pending") == (row["result_doc_id"] is None)
        assert db_wfseq.is_started_row(row) == (row["result_doc_id"] is not None)


# ── O1: protected rows ────────────────────────────────────────────────────────

def test_protected_rows_are_started_rows_plus_the_report_after_a_started_instruction(store):
    seq_id = _seed_rows([
        {"type": "T", "result": f"{_GROUP}.0005-T"},   # started
        {"type": "TR"},                                 # protected: report of a started T
        {"type": "N"},                                  # pending
        {"type": "NR"},                                 # pending: its N has not started
        {"type": "TR"},                                 # pending: not right after a T
    ])
    rows = _items(seq_id)
    assert db_wfseq.protected_row_ids(rows) == {rows[0]["id"], rows[1]["id"]}

    from modules.flow_gate.services.work_plan_sequence_service import load_current_rows

    locked, pending = load_current_rows(rows)
    assert [(r["type"], r["item_id"]) for r in locked] == [("T", rows[0]["id"]), ("TR", rows[1]["id"])]
    assert [r["type"] for r in pending] == ["N", "NR", "TR"]


def _two_protected_reports() -> int:
    return _seed_rows([
        {"type": "T", "result": f"{_GROUP}.0005-T", "label": "T1"},
        {"type": "TR", "label": "TR1", "provider": "aip_one"},
        {"type": "T", "result": f"{_GROUP}.0007-T", "label": "T2"},
        {"type": "TR", "label": "TR2"},
        {"type": "D", "label": "X"},
    ])


def _x(**extra) -> dict:
    return {"type": "D", "label": "X", **extra}


@pytest.mark.parametrize("shape", ["in_order", "shuffled", "tr2_missing", "no_echo"])
def test_several_protected_reports_survive_any_echo_shape(store, shape):
    seq_id = _two_protected_reports()
    rows = _items(seq_id)
    tr1, tr2 = rows[1], rows[3]
    protected_before = [r for r in _snapshot(seq_id) if r[0] in (rows[0]["id"], tr1["id"], rows[2]["id"], tr2["id"])]
    echo1 = {"type": "TR", "label": "TR1", "item_id": tr1["id"]}
    echo2 = {"type": "TR", "label": "TR2", "item_id": tr2["id"]}
    payload = {
        "in_order": [echo1, echo2, _x()],
        "shuffled": [echo2, _x(), echo1],
        "tr2_missing": [echo1, _x()],
        "no_echo": [_x()],
    }[shape]

    wds.edit_workflow_pending(_ROOT, payload)

    after = _items(seq_id)
    assert len([r for r in after if r["type"] == "TR"]) == 2
    assert [r for r in _snapshot(seq_id) if r[0] in {p[0] for p in protected_before}] == protected_before
    # The rewritten X lands after the last protected row, never on one of its positions.
    x_rows = [r for r in after if r["type"] == "D"]
    assert len(x_rows) == 1
    assert x_rows[0]["sort_order"] > max(p[2] for p in protected_before)
    assert len({r["sort_order"] for r in after}) == len(after)


def test_protected_rows_that_are_not_a_sort_order_prefix_keep_their_places(store):
    """A pending row ahead of the started block: new rows go after the last protected row."""
    seq_id = _seed_rows([
        {"type": "D", "label": "early"},
        {"type": "T", "result": f"{_GROUP}.0005-T"},
        {"type": "TR"},
        {"type": "P", "label": "tail"},
    ])
    rows = _items(seq_id)
    kept = _snapshot(seq_id)[1:3]
    wds.edit_workflow_pending(_ROOT, [
        {"type": "D", "label": "early"}, {"type": "P", "label": "tail"},
    ])
    after = _snapshot(seq_id)
    assert [r for r in after if r[0] in (rows[1]["id"], rows[2]["id"])] == kept
    new_orders = [r[2] for r in after if r[0] not in (rows[1]["id"], rows[2]["id"])]
    assert min(new_orders) > kept[-1][2]
    assert len({r[2] for r in after}) == len(after)


def test_a_new_report_of_the_same_type_is_a_new_row_not_an_echo(store):
    seq_id = _two_protected_reports()
    rows = _items(seq_id)
    payload = [
        {"type": "TR", "label": "TR1", "item_id": rows[1]["id"]},
        {"type": "T", "label": "new T"},
        {"type": "TR", "label": "new TR"},
        {"type": "TR", "label": "TR2", "item_id": rows[3]["id"]},
    ]
    wds.edit_workflow_pending(_ROOT, payload)
    after = _items(seq_id)
    assert [r["label"] for r in after if r["type"] == "TR"] == ["TR1", "TR2", "new TR"]
    assert [r["label"] for r in after if r["type"] == "T"] == ["T1", "T2", "new T"]
    assert [r["type"] for r in after][-2:] == ["T", "TR"]


def test_a_lone_report_without_item_id_next_to_a_protected_report_is_refused(store):
    seq_id = _two_protected_reports()
    before = _snapshot(seq_id)
    with pytest.raises(wds.ProtectedRowEchoAmbiguous) as exc:
        wds.edit_workflow_pending(_ROOT, [{"type": "TR", "label": "TR1"}, _x()])
    assert exc.value.type_code == "TR"
    assert set(exc.value.protected_item_ids) == {before[1][0], before[3][0]}
    assert _snapshot(seq_id) == before


def test_a_lone_report_is_stored_as_before_when_nothing_is_protected(store):
    seq_id = _seed_rows([{"type": "D"}])
    wds.edit_workflow_pending(_ROOT, [{"type": "TR", "label": "loose"}, {"type": "D", "label": "D"}])
    assert [r["type"] for r in _items(seq_id)] == ["TR", "D"]


def test_echo_settings_change_is_an_in_place_update(store):
    seq_id = _two_protected_reports()
    rows = _items(seq_id)
    tr1 = rows[1]
    result = wds.edit_workflow_pending(_ROOT, [
        {"type": "TR", "label": "TR1", "item_id": tr1["id"], "provider_id": "aip_two",
         "review_count": 2, "note": "re-check"},
        {"type": "TR", "label": "TR2", "item_id": rows[3]["id"]},
        _x(),
    ])
    assert result.get("workflow_changed") is not False
    assert result["protected_rows_updated"] == [tr1["id"]]
    updated = next(r for r in _items(seq_id) if r["id"] == tr1["id"])
    assert (updated["item_seq"], updated["sort_order"]) == (tr1["item_seq"], tr1["sort_order"])
    assert (updated["provider_id"], updated["review_count"], updated["note"]) == ("aip_two", 2, "re-check")


def test_an_unchanged_echo_and_unchanged_rows_are_a_no_op(store):
    seq_id = _two_protected_reports()
    rows = _items(seq_id)
    before = _snapshot(seq_id)
    # The editor sends every row's note back; an item that omits it would clear it.
    result = wds.edit_workflow_pending(_ROOT, [
        {"type": "TR", "label": "TR1", "item_id": rows[1]["id"]},
        {"type": "D", "label": "X", "note": "", "item_id": rows[4]["id"]},
    ])
    assert result["workflow_changed"] is False
    assert _snapshot(seq_id) == before


@pytest.mark.parametrize("change", [{"type": "NR"}, {"source_doc_id": _WP}, {"source_wp_card_id": "c_x"}])
def test_an_echo_may_not_change_a_protected_rows_identity(store, change):
    seq_id = _two_protected_reports()
    rows = _items(seq_id)
    before = _snapshot(seq_id)
    echo = {"type": "TR", "label": "TR1", "item_id": rows[1]["id"], **change}
    with pytest.raises(wds.ProtectedRowModified) as exc:
        wds.edit_workflow_pending(_ROOT, [echo, _x()])
    assert exc.value.item_id == rows[1]["id"]
    assert _snapshot(seq_id) == before


def test_a_started_rows_echo_is_ignored_whatever_its_settings_say(store):
    seq_id = _two_protected_reports()
    rows = _items(seq_id)
    before = _snapshot(seq_id)
    wds.edit_workflow_pending(_ROOT, [
        {"type": "T", "label": "T1", "item_id": rows[0]["id"], "note": "rewritten", "provider_id": "aip_two"},
        {"type": "D", "label": "X", "note": "", "item_id": rows[4]["id"]},
    ])
    started = next(r for r in _items(seq_id) if r["id"] == rows[0]["id"])
    assert (started["note"], started["provider_id"]) == ("", None)
    assert _snapshot(seq_id) == before


def test_an_item_id_the_sequence_no_longer_has_is_stale(store):
    seq_id = _two_protected_reports()
    before = _snapshot(seq_id)
    with pytest.raises(wds.SequenceItemStale) as exc:
        wds.edit_workflow_pending(_ROOT, [{"type": "D", "label": "X", "item_id": 999999}])
    assert exc.value.item_id == 999999
    assert _snapshot(seq_id) == before


def test_an_unprotected_rows_item_id_restores_its_own_metadata_even_with_twin_labels(store):
    seq_id = _seed_rows([
        {"type": "D", "label": "same", "provider": "aip_one", "card": "c_aaaaaaaaaaaa"},
        {"type": "D", "label": "same", "provider": "aip_two", "card": "c_bbbbbbbbbbbb"},
    ])
    rows = _items(seq_id)
    # Swap the two twins and omit every metadata key: each is restored from its own row.
    wds.edit_workflow_pending(_ROOT, [
        {"type": "D", "label": "same", "item_id": rows[1]["id"]},
        {"type": "D", "label": "same", "item_id": rows[0]["id"]},
    ])
    after = _items(seq_id)
    assert [(r["provider_id"], r["source_wp_card_id"]) for r in after] == [
        ("aip_two", "c_bbbbbbbbbbbb"), ("aip_one", "c_aaaaaaaaaaaa"),
    ]


def test_report_rows_take_their_instructions_card_id(store):
    seq_id = _seed_rows([{"type": "D"}])
    wds.edit_workflow_pending(_ROOT, [
        {"type": "T", "label": "T", "source_doc_id": _WP, "source_revision_no": 2,
         "source_wp_card_id": "c_tttttttttttt"},
    ])
    assert [(r["type"], r["source_wp_card_id"]) for r in _items(seq_id)] == [
        ("T", "c_tttttttttttt"), ("TR", "c_tttttttttttt"),
    ]


def test_attached_report_without_card_id_takes_its_instructions(store):
    # The client modal sends T and its TR together; the TR leaves source_wp_card_id out.
    seq_id = _seed_rows([{"type": "D"}])
    wds.edit_workflow_pending(_ROOT, [
        {"type": "T", "label": "T", "source_doc_id": _WP, "source_revision_no": 2,
         "source_wp_card_id": "c_tttttttttttt"},
        {"type": "TR", "label": "TR", "source_doc_id": _WP, "source_revision_no": 2},
    ])
    assert [(r["type"], r["source_wp_card_id"]) for r in _items(seq_id)] == [
        ("T", "c_tttttttttttt"), ("TR", "c_tttttttttttt"),
    ]


def test_attached_report_with_a_different_card_id_takes_its_instructions(store):
    # rev1 review: a new TR right after a new T that names another card must not keep it —
    # the automatic report belongs to its instruction's card (NR O0). DB round trip.
    seq_id = _seed_rows([{"type": "D"}])
    wds.edit_workflow_pending(_ROOT, [
        {"type": "T", "label": "T", "source_doc_id": _WP, "source_revision_no": 2,
         "source_wp_card_id": "c_tttttttttttt"},
        {"type": "TR", "label": "TR", "source_doc_id": _WP, "source_revision_no": 2,
         "source_wp_card_id": "c_otherother0"},
    ])
    assert [(r["type"], r["source_wp_card_id"]) for r in _items(seq_id)] == [
        ("T", "c_tttttttttttt"), ("TR", "c_tttttttttttt"),
    ]
    # Same for an instruction without a card (explicitly none): its report claims none.
    # (Leaving the key out would restore the stored row's card by type+label.)
    wds.edit_workflow_pending(_ROOT, [
        {"type": "T", "label": "T", "source_wp_card_id": None},
        {"type": "TR", "label": "TR", "source_wp_card_id": "c_otherother0"},
    ])
    assert [(r["type"], r["source_wp_card_id"]) for r in _items(seq_id)] == [
        ("T", None), ("TR", None),
    ]


def test_expand_steps_with_reports_aligns_new_attached_report_card_id_only():
    sequence = [
        {"type": "T", "label": "T", "source_wp_card_id": "c_tttttttttttt"},
        {"type": "TR", "label": "TR"},
        {"type": "T", "label": "T", "source_wp_card_id": "c_uuuuuuuuuuuu"},
        {"type": "TR", "label": "TR", "source_wp_card_id": "c_mismatch0000"},
        {"type": "T", "label": "T", "source_wp_card_id": "c_vvvvvvvvvvvv"},
        {"type": "TR", "label": "TR", "item_id": 41},
    ]
    expanded = wds.expand_steps_with_reports(sequence)
    assert [(i["type"], i.get("source_wp_card_id")) for i in expanded] == [
        ("T", "c_tttttttttttt"), ("TR", "c_tttttttttttt"),
        # A new report (no item_id) always takes its instruction's card id.
        ("T", "c_uuuuuuuuuuuu"), ("TR", "c_uuuuuuuuuuuu"),
        # A report naming its row keeps what that row says (restored from the row).
        ("T", "c_vvvvvvvvvvvv"), ("TR", None),
    ]
    # The caller's payload is left untouched.
    assert "source_wp_card_id" not in sequence[1]
    assert sequence[3]["source_wp_card_id"] == "c_mismatch0000"
    assert "id" not in sequence[0]


# ── Read paths and callers ────────────────────────────────────────────────────

def test_get_sequence_and_ai_edit_data_carry_item_id_and_protected(store, monkeypatch):
    seq_id = _two_protected_reports()
    rows = _items(seq_id)
    view = wds.get_workflow_sequence(_ROOT)
    assert [(i["item_id"], i["protected"]) for i in view["items"]] == [
        (rows[0]["id"], True), (rows[1]["id"], True), (rows[2]["id"], True),
        (rows[3]["id"], True), (rows[4]["id"], False),
    ]

    captured = {}

    def fake_mention(**kwargs):
        captured.update(kwargs)
        return "mention"

    monkeypatch.setattr(wds.mention_service, "build_sequence_edit_mention", fake_mention)
    monkeypatch.setattr(wds.token_service, "issue", lambda **kw: {
        "raw_token": "tok", "token_id": "tid", "expires_at": None, "scratch_dir": "C:/s",
    })
    wds.request_sequence_edit(_ROOT, "worker", "http://x/flowgate/api/v1", locale="en")
    data = captured["sequence_items"]
    assert [(i["item_id"], i["protected"]) for i in data] == [
        (r["id"], r["id"] != rows[4]["id"]) for r in rows
    ]


def test_sequence_edit_mention_lists_protected_rows_as_locked_with_their_ids():
    from modules.flow_gate.services import mention_service

    text = mention_service.build_sequence_edit_mention(
        token_rec={"project": "flowgate", "group_id": _GROUP},
        target_doc={"doc_id": _ROOT, "type_code": "R", "seq": 1, "title": "root"},
        api_base_url="http://x/flowgate/api/v1", raw_token="tok", locale="en",
        sequence_items=[
            {"item_id": 1, "protected": True, "type": "T", "label": "T1", "status": "done"},
            {"item_id": 2, "protected": True, "type": "TR", "label": "TR1", "status": "pending"},
            {"item_id": 3, "protected": False, "type": "D", "label": "X", "status": "pending",
             "note": "n", "source_doc_id": None, "source_revision_no": None},
        ],
    )
    locked = text.split("Locked steps", 1)[1].split("Pending steps", 1)[0]
    pending = text.split("Pending steps", 1)[1]
    assert "[TR] TR1 (item_id=2)" in locked
    assert "[TR]" not in pending.split("```json", 1)[0]
    assert '"item_id": 3' in pending
    assert "protected_row_echo_ambiguous" in text


def test_final_auto_expand_never_sends_a_protected_row(monkeypatch):
    from modules.flow_gate.services import work_plan_sequence_service as wpseq

    candidate = {
        "rows": [
            {"type": "T", "label": "T1", "status": "in_progress", "protected": True, "item_id": 1},
            {"type": "TR", "label": "TR1", "status": "pending", "protected": True, "item_id": 2},
            {"type": "D", "label": "new", "status": "pending", "protected": False,
             "source_doc_id": _WP, "source_revision_no": 2, "source_wp_card_id": "D#1"},
        ],
        "row_count_change": {"deleted": 0},
        "plan_step_count": 1,
        "workflow_tag": "tag",
        "acknowledgement_required": [],
    }
    seen = {}
    monkeypatch.setattr(wpseq, "build_candidates", lambda **kw: candidate)
    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_by_doc_id", lambda d: None)
    monkeypatch.setattr(wds, "edit_workflow_pending", lambda owner, rows, **kw: seen.update(rows=rows) or {"status": "updated"})
    doc = {"doc_id": _WP, "doc_review_status": "approved", "revision_no": 2, "target_id": _ROOT}
    wpseq.expand_final_work_plan(doc=doc, plan={"steps": []})
    assert [(r["type"], r["source_wp_card_id"]) for r in seen["rows"]] == [("D", "D#1")]

    # Unresolved legacy rows cannot be acknowledged by approval: hand over to the dialog.
    candidate["acknowledgement_required"] = ["legacy_card_unresolved"]
    seen.clear()
    result = wpseq.expand_final_work_plan(doc=doc, plan={"steps": []})
    assert result == {"status": "needs_selection", "reason": "legacy_card_unresolved", "revision_no": 2}
    assert seen == {}


# ── Route mapping (PATCH /workflow/sequence) ─────────────────────────────────

def test_patch_route_maps_the_three_guards_to_409(store, monkeypatch):
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import workflow_decision_routes as routes
    from routers.main import app

    monkeypatch.setattr(routes, "verify_bearer", lambda request: {"_is_user_jwt": True, "issued_to": "usr"})
    monkeypatch.setattr(routes, "_active_ai_run_response_for_user", lambda d, a: None)
    seq_id = _two_protected_reports()
    rows = _items(seq_id)
    client = TestClient(app, raise_server_exceptions=False)
    path = "/flowgate/api/v1/workflow/sequence"

    resp = client.patch(path, json={"doc_id": _ROOT, "items": [{"type": "TR", "label": "TR1"}, _x()]})
    assert resp.status_code == 409 and resp.json()["error"] == "protected_row_echo_ambiguous"

    resp = client.patch(path, json={"doc_id": _ROOT, "items": [
        {"type": "NR", "label": "TR1", "item_id": rows[1]["id"]}, _x()]})
    assert resp.status_code == 409 and resp.json()["error"] == "protected_row_modified"
    assert resp.json()["fields"] == ["type"]

    resp = client.patch(path, json={"doc_id": _ROOT, "items": [{**_x(), "item_id": 424242}]})
    assert resp.status_code == 409 and resp.json()["error"] == "sequence_item_stale"

    resp = client.patch(path, json={"doc_id": _ROOT, "items": [
        {"type": "TR", "label": "TR1", "item_id": rows[1]["id"]},
        {"type": "TR", "label": "TR2", "item_id": rows[3]["id"]},
        {**_x(), "item_id": rows[4]["id"], "note": "changed"},
    ]})
    assert resp.status_code == 200, resp.text
    assert len(_by_type(seq_id, "TR")) == 2
