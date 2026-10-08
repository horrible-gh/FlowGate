"""Direct work-plan apply over card identity and protected rows — flowgate.default.0649 T#1.

NR0003 §5.2 O0/O1 and §5.4 on the ``/work-plan/apply`` path, against a migrated sqlite file:

  * a started legacy row whose card cannot be proven blocks preview (blocker + warning +
    ``acknowledgement_required``) and refuses keep/change apply with 409
    ``legacy_card_unresolved`` — nothing is written; with ``acknowledged_codes`` the apply
    records ``retired:unresolved:{id}`` in its own transaction and never asks again;
  * the plan snapshot never rewrites a protected pending report (the report right after a
    started instruction): its source_doc_id / source_revision_no / card id and position stay,
    only its execution settings are updated in place.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from modules.flow_gate.db import connection as db_connection
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.services import work_plan_apply_service as wpa
from modules.flow_gate.services import work_plan_card_identity as cards
from modules.flow_gate.services import work_plan_service as wp
from modules.flow_gate.services import workflow_decision_service as wds
from tests.test_work_plan_card_identity_0649 import _current, _legacy
from tests.test_workflow_protected_rows_0649 import (
    _GROUP, _ROOT, _SEED_SQL, _WP, _SqliteStore, _items, _seed_rows, _snapshot,
)

PROVIDERS = [{"id": "p1", "name": "P1", "enabled": True}]


@pytest.fixture
def store(migrated_sqlite_db, monkeypatch):
    real = _SqliteStore(migrated_sqlite_db("apply_cards_0649.db", seed_sql=_SEED_SQL))
    previous = db_connection.STORE
    db_connection.STORE = real
    # 0649 T#2: a change-apply writes through edit_workflow_pending, which re-checks the
    # plan's revision against the documents table and archives AC documents.
    real._execute("UPDATE documents SET revision_no = 2 WHERE doc_id = ?", [_WP])
    monkeypatch.setattr(wds.db_documents, "list_documents", lambda **kwargs: [])
    try:
        yield real
    finally:
        db_connection.STORE = previous
        real._conn.close()


def _doc(revision_no: int) -> dict:
    return {"doc_id": _WP, "revision_no": revision_no, "project_id": "flowgate",
            "target_id": _ROOT, "doc_review_status": "approved"}


def _plan_with_values(body: dict) -> dict:
    plan = _current(body)
    for step in plan["steps"]:
        step["provider_id"] = "p1"
        step["provider_display_name"] = "P1"
        step["note"] = f"note {step['key']}"
    return plan


def _apply(plan: dict, revision_no: int, tmp_path, *, change: bool, ack=None) -> dict:
    sequence = db_wfseq.get_sequence_by_doc_id(_ROOT)
    tag = wpa.build_workflow_tag(sequence, db_wfseq.get_sequence_items(sequence["id"]))
    return wpa.apply(
        doc=_doc(revision_no), owner_doc={"doc_id": _ROOT, "type_code": "R"}, plan=plan,
        plan_path=tmp_path / "plan.json", providers=PROVIDERS, instruction_mode="ai_direct",
        change_workflow=change, workflow_tag=tag, wp_revision_no=revision_no,
        applied_by="tester", acknowledged_codes=ack,
    )


@pytest.fixture
def unresolved_history(store, monkeypatch, tmp_path):
    """T(started)+TR poured at r0, snapshot r1 missing, plan now r2: unprovable."""
    revisions = {0: _legacy({"T": 2}), 1: None, 2: _legacy({"T": 2})}
    plan_file = tmp_path / "current.json"
    plan_file.write_text(json.dumps(revisions[2]), encoding="utf-8")
    monkeypatch.setattr(wp, "plan_path_for_doc", lambda doc: plan_file)
    monkeypatch.setattr(cards, "stored_revision_loader", lambda doc: revisions.get)
    monkeypatch.setattr(cards, "read_result_provenance", lambda doc_id: None)
    seq_id = _seed_rows([
        {"type": "T", "source": _WP, "rev": 0, "result": f"{_GROUP}.0005-T"},
        {"type": "TR", "source": _WP, "rev": 0},
        {"type": "T", "source": _WP, "rev": 0},
        {"type": "TR", "source": _WP, "rev": 0},
    ])
    return seq_id, _plan_with_values(revisions[2])


def _card_ids(seq_id: int) -> list:
    return [r["source_wp_card_id"] for r in _items(seq_id)]


# ── O0: legacy_card_unresolved on preview / apply ─────────────────────────────

def test_preview_blocks_both_modes_until_acknowledged(unresolved_history):
    seq_id, plan = unresolved_history
    result = wpa.preview(doc=_doc(2), plan=plan, providers=PROVIDERS,
                         instruction_mode="ai_direct")
    assert result["apply_blockers"] == {
        "keep_workflow": "legacy_card_unresolved", "change_workflow": "legacy_card_unresolved",
    }
    assert result["can_apply"] is False and result["can_apply_without_workflow"] is False
    assert result["acknowledgement_required"] == ["legacy_card_unresolved"]
    warning = next(w for w in result["warnings"] if w["code"] == "legacy_card_unresolved")
    rows = _items(seq_id)
    assert [row["item_id"] for row in warning["detail"]["rows"]] == [rows[0]["id"], rows[1]["id"]]
    assert warning["detail"]["acknowledge_code"] == "legacy_card_unresolved"
    assert warning["detail"]["acknowledged"] is False

    acked = wpa.preview(doc=_doc(2), plan=plan, providers=PROVIDERS,
                        instruction_mode="ai_direct",
                        acknowledged_codes=["legacy_card_unresolved"])
    assert "legacy_card_unresolved" not in acked["apply_blockers"].values()
    assert acked["acknowledgement_required"] == ["legacy_card_unresolved"]
    assert next(
        w for w in acked["warnings"] if w["code"] == "legacy_card_unresolved"
    )["detail"]["acknowledged"] is True
    # Preview is read-only: nothing was recorded.
    assert _card_ids(seq_id) == [None, None, None, None]


@pytest.mark.parametrize("change", [False, True])
def test_apply_refuses_unacknowledged_unresolved_rows_and_writes_nothing(
    unresolved_history, tmp_path, change,
):
    seq_id, plan = unresolved_history
    before = [dict(r) for r in _items(seq_id)]
    with pytest.raises(wpa.ApplyConflict) as raised:
        _apply(plan, 2, tmp_path, change=change)
    assert raised.value.code == "legacy_card_unresolved"
    assert raised.value.payload["acknowledge_code"] == "legacy_card_unresolved"
    assert [row["item_id"] for row in raised.value.payload["rows"]] == [
        before[0]["id"], before[1]["id"],
    ]
    assert [dict(r) for r in _items(seq_id)] == before
    assert not (tmp_path / "0004-WP_applications.jsonl").exists()


def test_acknowledged_keep_apply_is_refused_by_the_orphan_it_would_keep(
    unresolved_history, tmp_path,
):
    """0649 T#2 (NR0003 O4): after the acknowledgement the unresolved pending T row serves
    no card (an orphan), so keeping the workflow would keep it — keep is refused, and the
    refusal comes before anything (the marker included) is written. The apply refuses with
    the preview's own keep answer: no plan card has a row to keep, which outranks the
    orphans (still listed by the preview)."""
    seq_id, plan = unresolved_history
    before = [dict(r) for r in _items(seq_id)]
    preview = wpa.preview(doc=_doc(2), plan=plan, providers=PROVIDERS,
                          instruction_mode="ai_direct",
                          acknowledged_codes=["legacy_card_unresolved"])
    assert preview["apply_blockers"]["keep_workflow"] == "unmatched_plan_steps"
    assert [row["item_id"] for row in preview["comparison"]["removed"]["items"]] == [
        before[2]["id"], before[3]["id"],
    ]
    assert "orphan_plan_rows" in [w["code"] for w in preview["warnings"]]
    with pytest.raises(wpa.ApplyConflict) as raised:
        _apply(plan, 2, tmp_path, change=False, ack=["legacy_card_unresolved"])
    assert raised.value.code == preview["apply_blockers"]["keep_workflow"]
    assert [dict(r) for r in _items(seq_id)] == before
    assert not (tmp_path / "0004-WP_applications.jsonl").exists()


@pytest.mark.parametrize("change", [True])
def test_acknowledged_apply_records_the_marker_once_in_place(unresolved_history, tmp_path, change):
    seq_id, plan = unresolved_history
    before = _snapshot(seq_id)
    result = _apply(plan, 2, tmp_path, change=change, ack=["legacy_card_unresolved"])
    rows = _items(seq_id)
    assert _card_ids(seq_id)[:2] == [
        f"retired:unresolved:{rows[0]['id']}", f"retired:unresolved:{rows[1]['id']}",
    ]
    # Only the column: the started T and its protected TR keep id/position/result/identity.
    assert _snapshot(seq_id)[:2] == before[:2]
    assert [(r["source_doc_id"], r["source_revision_no"]) for r in rows[:2]] == [(_WP, 0), (_WP, 0)]
    assert "legacy_card_unresolved" in [w["code"] for w in result["warnings"]]
    journal = wpa.read_applications(tmp_path / "plan.json", _WP)["items"][0]
    assert journal["card_backfill"][:2] == [
        [rows[0]["id"], f"retired:unresolved:{rows[0]['id']}"],
        [rows[1]["id"], f"retired:unresolved:{rows[1]['id']}"],
    ]

    # 0649 T#2 (NR0003 O3): the card the unresolved rows might have been is poured again
    # behind them, and the unresolved pending T#2 row is replaced by the card's own row.
    assert [(r["type"], r["source_wp_card_id"]) for r in rows[2:]] == [
        ("T", "T#1"), ("TR", "T#1"), ("T", "T#2"), ("TR", "T#2"),
    ]

    # Recorded once: the next apply is not asked again.
    again = _apply(plan, 2, tmp_path, change=change)
    assert "legacy_card_unresolved" not in [w["code"] for w in again["warnings"]]


def test_backfill_and_snapshot_share_one_transaction(unresolved_history, tmp_path, monkeypatch):
    """The marker, the added rows and the plan snapshot are written in one outer frame."""
    seq_id, plan = unresolved_history
    frames: list = []
    state = {"depth": 0, "frame": None}
    inner = wpa.get_store()

    class _Tracking:
        @contextmanager
        def transaction(self):
            state["depth"] += 1
            if state["depth"] == 1:
                state["frame"] = object()
            try:
                with inner.transaction():
                    yield self
            finally:
                state["depth"] -= 1
                if state["depth"] == 0:
                    state["frame"] = None

    def tracked(name):
        real = getattr(db_wfseq, name)

        def wrapper(*args, **kwargs):
            frames.append((name, state["frame"]))
            return real(*args, **kwargs)

        monkeypatch.setattr(db_wfseq, name, wrapper)

    for name in ("update_sequence_item_card_id", "update_sequence_item_plan_snapshot",
                 "update_sequence_item_echo_settings", "insert_sequence_item",
                 "delete_unprotected_pending_items"):
        tracked(name)
    monkeypatch.setattr(wpa, "get_store", lambda: _Tracking())

    # 0649 T#2: keep is refused here (orphan_plan_rows), so the reflection that writes is the
    # change-apply — its card-id marker and its row rewrite share the one outer frame.
    _apply(plan, 2, tmp_path, change=True, ack=["legacy_card_unresolved"])
    names = {name for name, _frame in frames}
    assert "update_sequence_item_card_id" in names
    assert {"insert_sequence_item", "delete_unprotected_pending_items"} <= names
    assert all(frame is not None for _name, frame in frames)
    assert len({id(frame) for _name, frame in frames}) == 1


# ── O1: the plan snapshot keeps a protected report's identity ─────────────────

@pytest.mark.parametrize("change", [False, True])
def test_apply_snapshot_keeps_protected_report_identity(store, monkeypatch, tmp_path, change):
    revisions = {1: _legacy({"T": 2}), 2: _legacy({"T": 2})}
    plan_file = tmp_path / "current.json"
    plan_file.write_text(json.dumps(revisions[2]), encoding="utf-8")
    monkeypatch.setattr(wp, "plan_path_for_doc", lambda doc: plan_file)
    monkeypatch.setattr(cards, "stored_revision_loader", lambda doc: revisions.get)
    monkeypatch.setattr(cards, "read_result_provenance", lambda doc_id: None)
    seq_id = _seed_rows([
        {"type": "T", "source": _WP, "rev": 1, "card": "T#1", "result": f"{_GROUP}.0005-T"},
        {"type": "TR", "source": _WP, "rev": 1, "card": "T#1"},
        {"type": "T", "source": _WP, "rev": 1, "card": "T#2"},
        {"type": "TR", "source": _WP, "rev": 1, "card": "T#2"},
    ])
    before = _items(seq_id)
    assert db_wfseq.protected_row_ids(before) == {before[0]["id"], before[1]["id"]}

    _apply(_plan_with_values(revisions[2]), 2, tmp_path, change=change)
    after = _items(seq_id)

    identity = ("id", "item_seq", "sort_order", "type", "result_doc_id",
                "source_doc_id", "source_revision_no", "source_wp_card_id")
    # The started T and its protected TR: identity and position exactly as before.
    for old, new in zip(before[:2], after[:2]):
        assert {k: new[k] for k in identity} == {k: old[k] for k in identity}
    # The protected TR still takes the plan's execution settings, in place.
    assert after[1]["provider_id"] == "p1"
    # An unprotected pending row is re-snapshotted onto the new revision as before.
    assert [(r["source_revision_no"], r["source_wp_card_id"]) for r in after[2:4]] == [
        (2, "T#2"), (2, "T#2"),
    ]
    assert len(after) == len(before)


# ── router contract ───────────────────────────────────────────────────────────

def test_router_passes_acknowledged_codes_and_answers_409(monkeypatch):
    from modules.flow_gate.documents.routers import work_plan as router

    seen = {}
    monkeypatch.setattr(router, "_load_doc", lambda doc_id: {"doc_id": doc_id, "target_id": _ROOT})
    monkeypatch.setattr(router, "_plan_path", lambda doc: "plan.json")
    monkeypatch.setattr(router.wp, "load_body", lambda *a, **k: {"steps": []})
    monkeypatch.setattr(router.db_docs, "get_by_id", lambda doc_id: {"doc_id": doc_id})
    monkeypatch.setattr(router, "_providers", lambda project_id: [])

    def fake_preview(**kwargs):
        seen["preview"] = kwargs.get("acknowledged_codes")
        return {"ok": True}

    def fake_apply(**kwargs):
        seen["apply"] = kwargs.get("acknowledged_codes")
        raise wpa.ApplyConflict("legacy_card_unresolved", {
            "code": "legacy_card_unresolved", "rows": [{"item_id": 1}],
            "acknowledge_code": "legacy_card_unresolved",
        })

    monkeypatch.setattr(router.wpa, "preview", fake_preview)
    monkeypatch.setattr(router.wpa, "apply", fake_apply)

    body = router.WorkPlanApplyPreview(acknowledged_codes=["legacy_card_unresolved"])
    router._preview_sync(_WP, body, "en")
    assert seen["preview"] == ["legacy_card_unresolved"]
    assert router.WorkPlanApplyPreview().acknowledged_codes is None

    response = router._apply_sync(_WP, router.WorkPlanApply(
        change_workflow=False, workflow_tag="t", wp_revision_no=1,
    ), "en", "tester")
    assert seen["apply"] is None
    assert response.status_code == 409
    payload = json.loads(response.body)
    assert payload["code"] == "legacy_card_unresolved"
    assert payload["acknowledge_code"] == "legacy_card_unresolved"
    assert payload["message"]
