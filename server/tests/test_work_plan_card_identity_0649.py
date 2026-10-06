"""Legacy sequence rows → work-plan cards — flowgate.default.0649 T#1 (NR0003 O0, §3.8).

A row poured before ``source_wp_card_id`` existed is classified by evidence and revision
continuity, never by "same key today": linked / retired / unresolved. The G9 history — a T#2
deleted by lowering the quantity and re-added as a new card that got the key T#2 back — must
retire the old started row instead of making the new card look started. Unprovable rows are
not guessed: a reflecting write is refused (legacy_card_unresolved) until acknowledged, then
recorded once as ``retired:unresolved:{id}`` and never asked about again.

Most cases run classify_rows() with injected revision/provenance readers (the stored-body
reader is exercised by the PUT tests in test_work_plan_card_id_0649); the last test drives
the real sequence edit on a migrated sqlite file.
"""
from __future__ import annotations

import json

import pytest

from modules.flow_gate.services import work_plan_card_identity as cards
from modules.flow_gate.services import work_plan_service as wp

WP = "flowgate.default.0649.0004-WP"


def _legacy(counts: dict[str, int]) -> dict:
    """A stored legacy revision body: quantities + fixed-order steps, no card ids."""
    quantities = {c: {"unit": wp.WORK_PLAN_TYPE_UNITS[c], "count": n} for c, n in counts.items()}
    steps = wp.expand_steps(list(counts), quantities)
    for step in steps:
        step.pop("card_id")
    return {"wp_version": 2, "binding": "advisory", "counted_types": list(counts),
            "quantities": quantities, "provider_candidates": [],
            "defaults": {"provider_id": None, "note": ""}, "steps": steps}


def _current(stored: dict) -> dict:
    """The current body as load_body() returns it (legacy ids filled)."""
    return wp.validate(json.loads(json.dumps(stored)), enforce_provider_scope=False)


def _row(row_id, code, *, rev=0, result=None, card=None, source=WP):
    return {
        "id": row_id, "item_seq": row_id, "sort_order": row_id, "type": code,
        "source_doc_id": source, "source_revision_no": rev, "result_doc_id": result,
        "source_wp_card_id": card, "status": "pending" if result is None else "done",
    }


def _classify(revisions: dict[int, dict | None], items, provenance=None, current_no=None):
    current_no = max(revisions) if current_no is None else current_no
    return cards.classify_rows(
        wp_doc={"doc_id": WP, "revision_no": current_no, "project_id": "flowgate"},
        plan=_current(revisions[current_no]),
        items=items,
        revision_loader=lambda r: revisions.get(r),
        provenance_reader=lambda doc_id: (provenance or {}).get(doc_id),
    )


def _classes(result) -> dict[int, tuple]:
    return {row_id: (info["classification"], info["value"]) for row_id, info in result["rows"].items()}


# ── linked: continuous history ────────────────────────────────────────────────

def test_continuous_history_links_rows_to_the_legacy_card_ids():
    revisions = {0: _legacy({"T": 2}), 1: _legacy({"T": 2})}
    items = [
        _row(1, "T", result="doc-t1"), _row(2, "TR"),  # A started, its report protected
        _row(3, "T"), _row(4, "TR"),                   # B pending
    ]
    result = _classify(revisions, items)
    assert _classes(result) == {
        1: ("linked", "T#1"), 2: ("linked", "T#1"), 3: ("linked", "T#2"), 4: ("linked", "T#2"),
    }
    assert result["started_card_ids"] == ["T#1"]
    # Only rows that stay are recorded: the started T and its protected report.
    assert result["writes"] == [(1, "T#1"), (2, "T#1")]
    assert result["unresolved_started"] == [] and result["retired_started"] == []


# ── retired: the G9 deletion-and-re-add history ──────────────────────────────

G9 = {0: _legacy({"T": 2}), 1: _legacy({"T": 1}), 2: _legacy({"T": 2})}


def test_g9_old_t2_row_is_retired_by_continuity_e3():
    items = [
        _row(1, "T", result="doc-t1"), _row(2, "TR", result="doc-tr1"),
        _row(3, "T", result="doc-old-t2"), _row(4, "TR"),
    ]
    result = _classify(G9, items)
    assert _classes(result)[3] == ("retired", "retired:r0:T#2")
    assert _classes(result)[4] == ("retired", "retired:r0:T#2")
    assert result["started_card_ids"] == ["T#1"]
    assert [row["item_id"] for row in result["retired_started"]] == [3, 4]
    assert (3, "retired:r0:T#2") in result["writes"] and (4, "retired:r0:T#2") in result["writes"]


def test_g9_with_e1_provenance_retires_even_without_a_revision_on_the_row():
    items = [
        _row(1, "T", result="doc-t1"), _row(2, "TR", result="doc-tr1"),
        {**_row(3, "T", result="doc-old-t2"), "source_revision_no": None},
    ]
    provenance = {"doc-old-t2": {"source_wp_doc_id": WP, "source_wp_revision_no": 0,
                                  "source_wp_step_key": "T#2"}}
    result = _classify(G9, items, provenance)
    assert result["rows"][3]["evidence"] == "E1"
    assert _classes(result)[3] == ("retired", "retired:r0:T#2")


def test_control_without_the_deletion_the_same_row_is_linked():
    revisions = {0: _legacy({"T": 2}), 1: _legacy({"T": 2}), 2: _legacy({"T": 2})}
    items = [
        _row(1, "T", result="doc-t1"), _row(2, "TR", result="doc-tr1"),
        _row(3, "T", result="doc-t2"), _row(4, "TR"),
    ]
    result = _classify(revisions, items)
    assert _classes(result)[3] == ("linked", "T#2")
    assert result["started_card_ids"] == ["T#1", "T#2"]


def test_a_pending_retired_row_is_an_orphan_and_is_not_reused():
    items = [_row(1, "T", result="doc-t1"), _row(2, "TR", result="doc-tr1"), _row(3, "T"), _row(4, "TR")]
    result = _classify(G9, items)
    assert _classes(result)[3][0] == "retired"
    assert [row["item_id"] for row in result["orphans"]] == [3, 4]
    assert all(row_id not in (3, 4) for row_id, _ in result["writes"])
    assert result["started_card_ids"] == ["T#1"]


def test_e1_wins_over_a_disagreeing_e3_estimate():
    revisions = {0: _legacy({"T": 2}), 1: _legacy({"T": 2})}
    # E3 would call row 1 "T#1" (first T in revision 0 and in the block); the document
    # that actually ran says it was T#2.
    items = [_row(1, "T", result="doc-x")]
    provenance = {"doc-x": {"source_wp_doc_id": WP, "source_wp_revision_no": 0,
                            "source_wp_step_key": "T#2"}}
    result = _classify(revisions, items, provenance)
    assert result["rows"][1]["key_b"] == "T#2"
    assert _classes(result)[1] == ("linked", "T#2")


def test_two_started_rows_of_one_card_keep_the_first_and_retire_the_rest():
    revisions = {0: _legacy({"T": 1})}
    items = [_row(1, "T", result="doc-a"), _row(2, "T", result="doc-b")]
    provenance = {
        "doc-a": {"source_wp_doc_id": WP, "source_wp_revision_no": 0, "source_wp_step_key": "T#1"},
        "doc-b": {"source_wp_doc_id": WP, "source_wp_revision_no": 0, "source_wp_step_key": "T#1"},
    }
    result = _classify(revisions, items, provenance)
    assert _classes(result)[1] == ("linked", "T#1")
    assert _classes(result)[2][0] == "retired"
    assert result["started_card_ids"] == ["T#1"]
    assert [row["item_id"] for row in result["retired_started"]] == [2]


# ── unresolved: no proof, no guess ───────────────────────────────────────────

def test_a_missing_snapshot_in_the_range_is_unresolved():
    revisions = {0: _legacy({"T": 2}), 1: None, 2: _legacy({"T": 2})}
    items = [_row(1, "T", result="doc-t1"), _row(2, "TR")]
    result = _classify(revisions, items, current_no=2)
    assert _classes(result)[1] == ("unresolved", None)
    assert [row["item_id"] for row in result["unresolved_started"]] == [1, 2]
    assert result["unresolved_started"][0]["r_b"] == 0
    assert result["started_card_ids"] == []


def test_disagreeing_e3_counts_from_a_past_double_pour_are_unresolved():
    revisions = {0: _legacy({"T": 1}), 1: _legacy({"T": 1})}
    # Revision 1 was poured again after revision 0 already started: "first T of r1" says
    # T#1, "second T of the block" says T#2.
    items = [_row(1, "T", rev=0, result="doc-a"), _row(2, "T", rev=1, result="doc-b")]
    result = _classify(revisions, items)
    assert _classes(result)[1] == ("linked", "T#1")
    assert _classes(result)[2] == ("unresolved", None)
    assert result["rows"][2]["candidate_keys"] == ["T#1", "T#2"]


def test_record_backfill_refuses_then_records_the_acknowledgement_once(monkeypatch):
    revisions = {0: _legacy({"T": 2}), 1: None, 2: _legacy({"T": 2})}
    items = [_row(1, "T", result="doc-t1"), _row(2, "TR")]
    result = _classify(revisions, items, current_no=2)
    written: list[tuple] = []
    monkeypatch.setattr(
        cards.db_wfseq, "update_sequence_item_card_id",
        lambda item_id, seq_id, value: written.append((item_id, seq_id, value)),
    )
    with pytest.raises(cards.LegacyCardUnresolved) as exc:
        cards.record_backfill(7, result, wp_doc_id=WP)
    assert [row["item_id"] for row in exc.value.rows] == [1, 2]
    assert written == []

    cards.record_backfill(7, result, wp_doc_id=WP, acknowledged_codes=["legacy_card_unresolved"])
    assert written == [(1, 7, "retired:unresolved:1"), (2, 7, "retired:unresolved:2")]

    # Next reflection: the rows carry the marker, so nothing is unresolved any more.
    stored = [{**row, "source_wp_card_id": value} for row, (_, _, value) in zip(items, written)]
    again = _classify(revisions, stored, current_no=2)
    assert again["unresolved_started"] == []
    assert _classes(again)[1] == ("retired", "retired:unresolved:1")
    assert again["writes"] == []


# ── rows that already carry an id ────────────────────────────────────────────

def test_stored_card_ids_are_trusted_and_a_removed_card_leaves_an_orphan():
    current = _legacy({"T": 1})
    current_body = wp.validate(json.loads(json.dumps(current)))
    current_body["steps"][0]["card_id"] = current_body["steps"][1]["card_id"] = "c_live00000001"
    items = [
        _row(1, "T", result="doc-a", card="c_live00000001"),
        _row(2, "T", card="c_gone00000001"),
        _row(3, "T", result="doc-b", card="retired:r0:T#2"),
    ]
    result = cards.classify_rows(
        wp_doc={"doc_id": WP, "revision_no": 0}, plan=current_body, items=items,
        revision_loader=lambda r: None, provenance_reader=lambda d: None,
    )
    assert _classes(result) == {
        1: ("linked", "c_live00000001"),
        2: ("orphan", "c_gone00000001"),
        3: ("retired", "retired:r0:T#2"),
    }
    assert result["writes"] == []
    assert result["started_card_ids"] == ["c_live00000001"]


def test_a_new_format_bound_revision_supplies_the_card_id_directly():
    stored_r1 = _current(_legacy({"T": 1}))
    stored_r1["steps"][0]["card_id"] = stored_r1["steps"][1]["card_id"] = "c_assigned0001"
    revisions = {0: _legacy({"T": 1}), 1: stored_r1}
    items = [_row(1, "T", rev=1, result="doc-a")]
    result = cards.classify_rows(
        wp_doc={"doc_id": WP, "revision_no": 1}, plan=stored_r1, items=items,
        revision_loader=lambda r: revisions.get(r), provenance_reader=lambda d: None,
    )
    assert _classes(result)[1] == ("linked", "c_assigned0001")


# ── the real reflecting write ────────────────────────────────────────────────

def test_plan_reflecting_edit_refuses_unresolved_then_backfills_only_the_column(
    migrated_sqlite_db, monkeypatch, tmp_path,
):
    from tests.test_workflow_protected_rows_0649 import (
        _SEED_SQL, _ROOT, _SqliteStore, _WP, _GROUP, _items, _seed_rows, _snapshot,
    )
    from modules.flow_gate.db import connection as db_connection
    from modules.flow_gate.services import workflow_decision_service as wds

    store = _SqliteStore(migrated_sqlite_db("card_backfill_0649.db", seed_sql=_SEED_SQL))
    previous_store = db_connection.STORE
    db_connection.STORE = store
    try:
        monkeypatch.setattr(wds.db_documents, "list_documents", lambda **kwargs: [])
        store._execute("UPDATE documents SET revision_no = 2 WHERE doc_id = ?", [_WP])
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(_legacy({"T": 2})), encoding="utf-8")
        monkeypatch.setattr(wp, "plan_path_for_doc", lambda doc: plan_file)
        revisions = {0: _legacy({"T": 2}), 1: None, 2: _legacy({"T": 2})}
        monkeypatch.setattr(cards, "stored_revision_loader", lambda doc: revisions.get)
        monkeypatch.setattr(cards, "read_result_provenance", lambda doc_id: None)

        seq_id = _seed_rows([
            {"type": "T", "source": _WP, "rev": 0, "result": f"{_GROUP}.0005-T"},
            {"type": "TR", "source": _WP, "rev": 0},
            {"type": "T", "source": _WP, "rev": 0},
            {"type": "TR", "source": _WP, "rev": 0},
        ])
        before = _snapshot(seq_id)
        payload = [{"type": "T", "label": "T", "source_doc_id": _WP, "source_revision_no": 2,
                    "source_wp_card_id": "T#2"}]
        expected = {"wp_doc_id": _WP, "wp_revision_no": 2}

        with pytest.raises(cards.LegacyCardUnresolved):
            wds.edit_workflow_pending(_ROOT, list(payload), expected_plan=expected)
        assert _snapshot(seq_id) == before

        # With the continuity proven (snapshot r1 present) the started rows are linked and
        # only their source_wp_card_id is written — position and result document untouched.
        revisions[1] = _legacy({"T": 2})
        wds.edit_workflow_pending(_ROOT, list(payload), expected_plan=expected)
        after = _items(seq_id)
        assert [(r["id"], r["item_seq"], r["sort_order"], r["result_doc_id"]) for r in after[:2]] == [
            (b[0], b[1], b[2], b[4]) for b in before[:2]
        ]
        assert [r["source_wp_card_id"] for r in after] == ["T#1", "T#1", "T#2", "T#2"]
        assert [r["type"] for r in after] == ["T", "TR", "T", "TR"]
    finally:
        db_connection.STORE = previous_store
        store._conn.close()


def test_plan_reflecting_edit_records_the_acknowledged_unresolved_rows(
    migrated_sqlite_db, monkeypatch, tmp_path,
):
    from tests.test_workflow_protected_rows_0649 import (
        _SEED_SQL, _ROOT, _SqliteStore, _WP, _GROUP, _items, _seed_rows,
    )
    from modules.flow_gate.db import connection as db_connection
    from modules.flow_gate.services import workflow_decision_service as wds

    store = _SqliteStore(migrated_sqlite_db("card_ack_0649.db", seed_sql=_SEED_SQL))
    previous_store = db_connection.STORE
    db_connection.STORE = store
    try:
        monkeypatch.setattr(wds.db_documents, "list_documents", lambda **kwargs: [])
        store._execute("UPDATE documents SET revision_no = 2 WHERE doc_id = ?", [_WP])
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(_legacy({"T": 2})), encoding="utf-8")
        monkeypatch.setattr(wp, "plan_path_for_doc", lambda doc: plan_file)
        revisions = {0: _legacy({"T": 2}), 1: None, 2: _legacy({"T": 2})}
        monkeypatch.setattr(cards, "stored_revision_loader", lambda doc: revisions.get)
        monkeypatch.setattr(cards, "read_result_provenance", lambda doc_id: None)
        seq_id = _seed_rows([
            {"type": "T", "source": _WP, "rev": 0, "result": f"{_GROUP}.0005-T"},
            {"type": "TR", "source": _WP, "rev": 0},
        ])
        # 0649 T#2 (NR0003 O5): a reflecting save pours every card that has not started —
        # the post-write check refuses a payload that leaves the plan's T#2 out.
        payload = [
            {"type": "T", "label": "T", "source_doc_id": _WP, "source_revision_no": 2,
             "source_wp_card_id": "T#1"},
            {"type": "T", "label": "T", "source_doc_id": _WP, "source_revision_no": 2,
             "source_wp_card_id": "T#2"},
        ]
        result = wds.edit_workflow_pending(
            _ROOT, payload, expected_plan={"wp_doc_id": _WP, "wp_revision_no": 2},
            acknowledged_codes=["legacy_card_unresolved"],
        )
        rows = _items(seq_id)
        assert [r["source_wp_card_id"] for r in rows[:2]] == [
            f"retired:unresolved:{rows[0]['id']}", f"retired:unresolved:{rows[1]['id']}",
        ]
        # The card the unresolved rows might have been is poured again (after them).
        assert [r["type"] for r in rows] == ["T", "TR", "T", "TR", "T", "TR"]
        assert result["protected_count"] == 2
    finally:
        db_connection.STORE = previous_store
        store._conn.close()
