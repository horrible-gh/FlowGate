"""0444 T0005 — NR0003 §4-1 · §4-4 regressions.

Two defects with one symptom: a new work plan's provider/note reaches nothing.

* §4-1 (NR0003 §2-3) — ``build_step_map`` matched by type+ordinal over the WHOLE sequence,
  so a finished row another plan left behind stole the new plan's slot and ``project``
  reported it ``already_started`` while silently dropping provider/note.
* §4-4 (NR0003 §2-2) — the pour path's ``resolve_step_provider`` only checked the id
  *format*. An unregistered or disabled provider passed the pour screen and was stopped
  only much later, on the apply path, by ``_usable_provider``.

NR0003 §6 recorded that most server suites monkeypatch the DB away and therefore could not
see either defect. So this suite builds a **real migrated sqlite** sequence (the
``real_sequence`` pattern from test_workflow_sequence_provider_0408.py) and reads the rows
back through the production query — ``status`` is a derived column there, not a dict key a
test author chose.
"""
from __future__ import annotations

import os
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
from modules.flow_gate.services import work_plan_apply_service as apply_svc  # noqa: E402
from modules.flow_gate.services import work_plan_sequence_service as pour_svc  # noqa: E402
from modules.flow_gate.services import workflow_decision_service as decision_svc  # noqa: E402

PROJECT = "flowgate"
GROUP = "flowgate.default.0444"
ROOT = "flowgate.default.0444.0001-B"
OLD_WP = "flowgate.default.0444.0002-WP"
NEW_WP = "flowgate.default.0444.0004-WP"
DONE_D = "flowgate.default.0444.0011-D"
DONE_N = "flowgate.default.0444.0012-N"
DONE_NR = "flowgate.default.0444.0013-NR"
PROVIDER = "plan-provider"
PROVIDER_NAME = "Plan Provider"
FALLBACK = "plan-default-provider"
FALLBACK_NAME = "Plan Default Provider"

SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
 VALUES('{PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
 VALUES('{GROUP}', '{PROJECT}', 'default', 'pour matching', 'OPEN', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
 doc_id, project_id, module, group_id, type_code, seq, title, status,
 doc_review_status, created_at, updated_at)
 VALUES('{ROOT}', '{PROJECT}', 'default', '{GROUP}', 'B', 1, 'pour matching', 'open', NULL, datetime('now'), datetime('now')),
       ('{OLD_WP}', '{PROJECT}', 'default', '{GROUP}', 'WP', 2, 'old plan', 'open', 'approved', datetime('now'), datetime('now')),
       ('{NEW_WP}', '{PROJECT}', 'default', '{GROUP}', 'WP', 4, 'new plan', 'open', 'approved', datetime('now'), datetime('now')),
       ('{DONE_D}', '{PROJECT}', 'default', '{GROUP}', 'D', 11, 'finished design', 'open', 'approved', datetime('now'), datetime('now')),
       ('{DONE_N}', '{PROJECT}', 'default', '{GROUP}', 'N', 12, 'finished survey', 'open', 'approved', datetime('now'), datetime('now')),
       ('{DONE_NR}', '{PROJECT}', 'default', '{GROUP}', 'NR', 13, 'finished survey report', 'open', 'approved', datetime('now'), datetime('now'));
"""


class SqliteStore:
    """Minimal real-SQL store: the query text still comes from the production registry."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(row) for row in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def transaction(self):
        yield self


@pytest.fixture
def real_sequence(migrated_sqlite_db):
    path = migrated_sqlite_db("work_plan_apply_0444.db", seed_sql=SEED_SQL)
    store = SqliteStore(path)
    previous = db_connection.STORE
    db_connection.STORE = store
    db_wfseq.insert_sequence(ROOT)
    seq = db_wfseq.get_sequence_by_doc_id(ROOT)
    try:
        yield store, seq
    finally:
        db_connection.STORE = previous
        store._conn.close()


def add_row(seq, item_seq, type_, *, source=None, result=None, note="", provider=None):
    """Insert one real sequence row. ``status`` is derived by the query from ``result``."""
    db_wfseq.insert_sequence_item(
        sequence_id=seq["id"], item_seq=item_seq, type_=type_, label=type_,
        doc_class="B", sort_order=item_seq, note=note,
        source_doc_id=source, source_revision_no=1 if source else None,
        provider_id=provider, provider_display_name=provider,
    )
    if result is not None:
        db_connection.STORE._execute(
            "UPDATE workflow_sequence_items SET result_doc_id = ? "
            "WHERE sequence_id = ? AND item_seq = ?",
            [result, seq["id"], item_seq],
        )


def items_of(seq):
    return db_wfseq.get_sequence_items(seq["id"])


def step(key, *, provider=PROVIDER, note="from the new plan", pair=None, locked=False):
    code, ordinal = key.split("#")
    return {
        "key": key, "type": code, "ordinal": int(ordinal), "pair_key": pair,
        "provider_id": provider, "provider_display_name": PROVIDER_NAME,
        "note": note, "locked": locked,
    }


REGISTRY = [{"id": PROVIDER, "name": PROVIDER_NAME, "enabled": True},
            {"id": FALLBACK, "name": FALLBACK_NAME, "enabled": True}]


def view(*rows, readable=True):
    return {"readable": readable, "providers": {row["id"]: row for row in rows}}


REGISTERED_VIEW = view(*REGISTRY)


# ── §4-1 / NR0003 §2-3 ────────────────────────────────────────────────────────
# Contract: the sequence cells this plan may fill are (a) rows still ``pending`` or
# (b) rows this very plan poured. Rows somebody else already started or finished are
# out of the ordinal count and out of the candidate set.

def test_new_plan_fills_the_pending_row_not_another_plans_finished_row(real_sequence):
    _store, seq = real_sequence
    add_row(seq, 1, "D", source=OLD_WP, result=DONE_D)   # another plan finished this one
    add_row(seq, 2, "D")                                  # nobody has touched this one
    items = items_of(seq)
    assert [(x["item_seq"], x["status"]) for x in items] == [(1, "done"), (2, "pending")]

    steps = [step("D#1")]
    mapping = apply_svc.build_step_map(steps, items, NEW_WP)
    assert [(x["key"], x["item_seq"], x["status"]) for x in mapping] == [("D#1", 2, "pending")]

    projection = apply_svc.project(steps, mapping, items, "auto_approved", REGISTRY)
    assert projection["provider_overrides"] == {"2": PROVIDER}
    assert projection["note_overrides"] == {"2": "from the new plan"}
    assert [x for x in projection["unfilled"] if x.get("reason") == "already_started"] == []


def test_skipped_finished_rows_are_reported_not_dropped_in_silence(real_sequence):
    _store, seq = real_sequence
    add_row(seq, 1, "D", source=OLD_WP, result=DONE_D)
    add_row(seq, 2, "D")
    items = items_of(seq)

    pool, skipped = apply_svc._slot_pool(items, NEW_WP)
    assert [x["item_seq"] for x in pool] == [2]
    assert skipped == [1]

    steps = [step("D#1")]
    mapping = apply_svc.build_step_map(steps, items, NEW_WP)
    warnings = apply_svc.build_warnings(
        plan_steps=steps, step_map=mapping, provider_registry=REGISTRY,
        projection=apply_svc.project(steps, mapping, items, "auto_approved", REGISTRY),
        sequence_decided=True, added=[], extra_item_seqs=[], unplaceable_keys=[],
        order_differs_keys=[], wp_review_status="approved", unmatched_keys=[],
        skipped_done_item_seqs=skipped, locale="ko",
    )
    fired = [w for w in warnings if w["code"] == "done_rows_skipped"]
    assert len(fired) == 1
    assert fired[0]["count"] == 1 and fired[0]["item_seqs"] == [1]
    assert fired[0]["severity"] == "warning"
    # A different fact from steps_already_done, so a different sentence.
    others = {w["code"]: w["message"] for w in warnings}
    assert others.get("steps_already_done") != fired[0]["message"]


def test_preview_reaches_the_pending_row_and_warns_end_to_end(real_sequence):
    _store, seq = real_sequence
    add_row(seq, 1, "D", source=OLD_WP, result=DONE_D)
    add_row(seq, 2, "D")

    result = apply_svc.preview(
        doc={"doc_id": NEW_WP, "target_id": ROOT, "revision_no": 1,
             "doc_review_status": "approved"},
        plan={"steps": [step("D#1")], "defaults": {"note": ""}},
        providers=REGISTRY, instruction_mode="auto_approved", locale="ko",
    )
    assert result["fill_preview"]["provider_overrides"] == {"2": PROVIDER}
    assert result["fill_preview"]["target_seq"] == 2
    assert "done_rows_skipped" in {w["code"] for w in result["warnings"]}


def test_a_row_this_plan_poured_itself_still_maps_and_reports_already_started(real_sequence):
    """A-1 (b): re-applying the same plan must not duplicate the rows it already poured."""
    _store, seq = real_sequence
    add_row(seq, 1, "D", source=NEW_WP, result=DONE_D)
    items = items_of(seq)

    steps = [step("D#1")]
    mapping = apply_svc.build_step_map(steps, items, NEW_WP)
    assert [(x["key"], x["item_seq"], x["status"]) for x in mapping] == [("D#1", 1, "done")]

    projection = apply_svc.project(steps, mapping, items, "auto_approved", REGISTRY)
    assert {"key": "D#1", "reason": "already_started", "item_seq": 1} in projection["unfilled"]
    assert projection["provider_overrides"] == {}
    # Its own poured block is the pool, so nothing is reported as skipped.
    assert apply_svc._slot_pool(items, NEW_WP)[1] == []


# ── §4-1 A-3: _missing_items must offer new rows over a finished-only sequence ──

def test_missing_items_offers_new_rows_when_only_other_plans_rows_are_finished(real_sequence):
    _store, seq = real_sequence
    add_row(seq, 1, "N", source=OLD_WP, result=DONE_N)
    add_row(seq, 2, "NR", source=OLD_WP, result=DONE_NR)
    items = items_of(seq)

    steps = [step("N#1", pair="NR#1")]
    added, unplaceable = apply_svc._missing_items(steps, items, "ko", NEW_WP)
    assert unplaceable == []
    assert [(x["type"], x["item_seq"]) for x in added] == [("N", 3), ("NR", 4)]
    assert all(x["status"] == "pending" for x in added)

    # Idempotent: the rows it just proposed are pending, so a second pass adds nothing.
    again, _ = apply_svc._missing_items(steps, items + added, "ko", NEW_WP)
    assert again == []


def test_missing_items_does_not_duplicate_this_plans_own_finished_rows(real_sequence):
    _store, seq = real_sequence
    add_row(seq, 1, "N", source=NEW_WP, result=DONE_N)
    add_row(seq, 2, "NR", source=NEW_WP, result=DONE_NR)
    items = items_of(seq)

    added, _ = apply_svc._missing_items([step("N#1", pair="NR#1")], items, "ko", NEW_WP)
    assert added == []


def test_new_rows_are_appended_after_every_existing_row(real_sequence):
    """next_seq/next_order stay whole-sequence: a new row is always at the end."""
    _store, seq = real_sequence
    add_row(seq, 7, "N", source=OLD_WP, result=DONE_N)
    add_row(seq, 8, "NR", source=OLD_WP, result=DONE_NR)
    items = items_of(seq)

    added, _ = apply_svc._missing_items([step("N#1", pair="NR#1")], items, "ko", NEW_WP)
    assert [x["item_seq"] for x in added] == [9, 10]
    assert min(x["sort_order"] for x in added) > max(x["sort_order"] for x in items)


# ── §4-4 / NR0003 §2-2 — the pour path checks registration too ────────────────

PLAN_WITH_DEFAULT = {
    "steps": [], "defaults": {"provider_id": FALLBACK, "note": ""},
    "provider_candidates": [{"provider_id": PROVIDER, "display_name": PROVIDER_NAME}],
}


def test_resolve_step_provider_drops_an_unregistered_id():
    got = pour_svc.resolve_step_provider(
        {"provider_id": "gone"}, PLAN_WITH_DEFAULT, provider_view=REGISTERED_VIEW,
    )
    assert got == (None, None)


def test_resolve_step_provider_drops_a_disabled_provider():
    disabled = view({"id": PROVIDER, "name": PROVIDER_NAME, "enabled": False})
    got = pour_svc.resolve_step_provider(
        {"provider_id": PROVIDER}, PLAN_WITH_DEFAULT, provider_view=disabled,
    )
    assert got == (None, None)


def test_resolve_step_provider_uses_the_same_condition_as_the_apply_path():
    """B-2 rule 3: the pour and apply paths must not drift apart again."""
    for flag in ("enabled", "is_enabled"):
        row = {"id": PROVIDER, "name": PROVIDER_NAME, flag: False}
        assert pour_svc.resolve_step_provider(
            {"provider_id": PROVIDER}, PLAN_WITH_DEFAULT, provider_view=view(row),
        ) == (None, None)
        assert apply_svc._usable_provider({"provider_id": PROVIDER}, {PROVIDER: row}) is None


def test_resolve_step_provider_is_fail_open_when_the_view_is_unreadable():
    """Not being able to read the settings must not erase what the person chose."""
    unreadable = {"readable": False, "providers": {}}
    assert pour_svc.resolve_step_provider(
        {"provider_id": "gone"}, PLAN_WITH_DEFAULT, provider_view=unreadable,
    ) == ("gone", None)
    assert pour_svc.resolve_step_provider(
        {"provider_id": "gone"}, PLAN_WITH_DEFAULT,
    ) == ("gone", None)


def test_resolve_step_provider_keeps_a_registered_id():
    assert pour_svc.resolve_step_provider(
        {"provider_id": PROVIDER}, PLAN_WITH_DEFAULT, provider_view=REGISTERED_VIEW,
    ) == (PROVIDER, PROVIDER_NAME)


def test_plan_to_rows_reports_the_drop_and_falls_back_to_the_plan_default():
    plan = {
        "steps": [{"key": "D#1", "type": "D", "ordinal": 1, "note": "n", "provider_id": "gone"}],
        "defaults": {"provider_id": FALLBACK, "note": ""},
        "provider_candidates": [{"provider_id": FALLBACK, "display_name": FALLBACK_NAME}],
    }
    rows, dropped, _uid = pour_svc.plan_to_rows(
        plan, NEW_WP, 1, "ko", provider_view=REGISTERED_VIEW,
    )
    assert dropped == [{"plan_key": "D#1", "type": "D",
                       "reason": "provider_not_registered", "provider_id": "gone"}]
    # B-3: the drop leaves provider_id None, so the existing default substitution fires.
    assert [(r["type"], r["provider_id"]) for r in rows] == [("D", FALLBACK)]
    assert rows[0]["provider_display_name"] == FALLBACK_NAME


def test_plan_to_rows_reports_an_unregistered_default_once_and_leaves_both_empty():
    plan = {
        "steps": [{"key": "D#1", "type": "D", "ordinal": 1, "note": "n", "provider_id": "gone"}],
        "defaults": {"provider_id": "also-gone", "note": ""},
    }
    rows, dropped, _uid = pour_svc.plan_to_rows(
        plan, NEW_WP, 1, "ko", provider_view=REGISTERED_VIEW,
    )
    assert [(d["plan_key"], d["provider_id"]) for d in dropped] == [
        ("D#1", "gone"), ("defaults", "also-gone"),
    ]
    assert rows[0]["provider_id"] is None and rows[0]["provider_display_name"] is None


def test_plan_to_rows_leaves_a_registered_provider_alone():
    plan = {
        "steps": [{"key": "D#1", "type": "D", "ordinal": 1, "note": "n",
                   "provider_id": PROVIDER, "provider_display_name": PROVIDER_NAME}],
        "defaults": {"provider_id": FALLBACK, "note": ""},
    }
    rows, dropped, _uid = pour_svc.plan_to_rows(
        plan, NEW_WP, 1, "ko", provider_view=REGISTERED_VIEW,
    )
    assert dropped == []
    assert rows[0]["provider_id"] == PROVIDER


def test_plan_to_rows_without_a_view_still_pours_the_id_unchecked():
    """Positional callers (six of them, tests included) keep working, fail-open."""
    plan = {
        "steps": [{"key": "D#1", "type": "D", "ordinal": 1, "note": "n", "provider_id": "gone"}],
        "defaults": {"note": ""},
    }
    rows, dropped, _uid = pour_svc.plan_to_rows(plan, NEW_WP, 1, "ko", 0)
    assert dropped == [] and rows[0]["provider_id"] == "gone"


def test_pair_step_provider_is_checked_too():
    plan = {
        "steps": [
            {"key": "T#1", "type": "T", "ordinal": 1, "pair_key": "TR#1", "note": "t",
             "provider_id": PROVIDER, "provider_display_name": PROVIDER_NAME},
            {"key": "TR#1", "type": "TR", "ordinal": 1, "pair_key": "T#1", "note": "tr",
             "provider_id": "gone"},
        ],
        "defaults": {"note": ""},
    }
    rows, dropped, _uid = pour_svc.plan_to_rows(
        plan, NEW_WP, 1, "ko", provider_view=REGISTERED_VIEW,
    )
    assert [(d["plan_key"], d["reason"]) for d in dropped] == [
        ("TR#1", "provider_not_registered"),
    ]
    assert rows[0]["pair_provider_id"] is None


def test_notification_envelope_is_emitted_in_order():
    dropped = [
        {"plan_key": "X#1", "type": "X", "reason": "type_not_placeable"},
        {"plan_key": "D#1", "type": "D", "reason": "provider_not_registered",
         "provider_id": "gone"},
    ]
    notes = pour_svc.build_notifications(
        rows=[], overlap=[], deleted_rows=[], dropped=dropped, truncated_count=0,
    )
    assert [n["code"] for n in notes] == ["type_not_placeable", "provider_not_registered"]
    envelope = notes[1]
    assert envelope["severity"] == "warning" and envelope["count"] == 1
    assert envelope["items"] == [{"plan_key": "D#1", "provider_id": "gone"}]


def test_no_notification_when_every_provider_is_registered():
    """Positive control for the assertion above: same call, registered id, no envelope."""
    notes = pour_svc.build_notifications(
        rows=[], overlap=[], deleted_rows=[], dropped=[], truncated_count=0,
    )
    assert [n["code"] for n in notes] == []


def test_build_candidates_reads_the_provider_view_once_and_warns(real_sequence, monkeypatch):
    """B-5: the view is read before plan_to_rows and the same value serves _public_row."""
    _store, seq = real_sequence
    add_row(seq, 1, "D", source=OLD_WP, result=DONE_D)

    calls = []

    def resolve_effective(project_id):
        calls.append(project_id)
        return {"providers": [dict(row) for row in REGISTRY]}

    from modules.flow_gate.settings import ai_settings_service
    monkeypatch.setattr(ai_settings_service, "resolve_effective", resolve_effective)

    result = pour_svc.build_candidates(
        doc={"doc_id": NEW_WP, "target_id": ROOT, "revision_no": 1,
             "project_id": PROJECT, "doc_review_status": "approved"},
        plan={
            "steps": [{"key": "P#1", "type": "P", "ordinal": 1, "note": "n",
                       "provider_id": "gone"}],
            "defaults": {"provider_id": PROVIDER, "note": ""},
        },
        mode="append", locale="ko",
    )
    assert calls == [PROJECT]                              # read once, not twice
    codes = [n["code"] for n in result["notifications"]]
    assert "provider_not_registered" in codes
    poured = [r for r in result["rows"] if r["type"] == "P"]
    assert [r["provider_id"] for r in poured] == [PROVIDER]
    assert [r["provider_registered"] for r in poured] == [True]


def test_provider_view_of_is_the_three_valued_contract_this_relies_on(real_sequence, monkeypatch):
    from modules.flow_gate.settings import ai_settings_service
    monkeypatch.setattr(ai_settings_service, "resolve_effective",
                        lambda pid: (_ for _ in ()).throw(RuntimeError("settings down")))
    assert decision_svc.provider_view_of(PROJECT) == {"readable": False, "providers": {}}
