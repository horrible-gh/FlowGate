"""Card order on an existing sequence — flowgate.default.0649 T#2 (NR0003 O2-O6, §11).

Against a sqlite file with every migration applied and a store whose transaction really
rolls back:

  * O2 — started cards are judged by card id, not by key: the G4 swap (B moved in front of
    a finished A, so B becomes T#1) is refused on every reflecting path — apply/preview
    keep and change, pour replace_after/append, P1, a direct plan-reflecting PATCH — and
    nothing is written; the same holds when A and B carry identical values. A started card
    removed from the plan is refused too. Control: reordering only cards that have not
    started succeeds and each card keeps its own values even though its key changed.
  * O3/O4 — apply change rewrites this plan's unprotected rows in card order (item_seq and
    sort_order both increase, the head is the first card that has not started), a card
    added to the plan lands at its plan position (G2), rows of removed cards go; keep is
    refused while the order differs (G1) or an orphan row would stay; foreign pending rows
    keep their side of the plan block, and one inside it refuses change.
  * O5 — append is refused while this plan has pending rows (G7), allowed with only foreign
    rows (foreign_rows_before); pours and P1 never pour a started card again (G3); a
    plan-reflecting save whose rows break the card order, or name a card on a row of
    another type, is rolled back (plan_order_violation), an ordinary edit is not checked;
    rows a pre-card client sends are placed by the settings they carry, so a same-type
    swap is refused and rows that cannot be told apart are not guessed (ambiguous_card).
  * O0 legacy history (G9) — a T#2 deleted (r1) and re-added (r2) is a new card: the old
    started T#2 row is retired, the new card is poured at its position, and the post-write
    check does not trip on the retired row; E1 (result provenance) and E3 alike; control
    with no deletion links it. A later move of the new card ahead of the started one is
    refused, a move behind it is allowed.
  * O6 — the materializer's step key comes from the row's card in its revision snapshot (G5).
  * flowgate.v02.0112 — a paired design card (card_stage_model "paired") is poured as its
    DS row and its result row; WP0005 rev 1 verbatim saves, saves again without duplicates,
    and rows that break the card (another card's id, another type, reversed, missing) roll
    back; a started DS row is not mistaken for another card's row.
  * CAS / execution — a stale workflow tag refuses the apply untouched; a change-apply asks
    the live run to re-resolve its run-to-end target; P1 leaves a sequence alone while the
    group's run works on a row that has no document yet.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from modules.flow_gate.db import connection as db_connection
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.documents.constants import WORK_PLAN_PAIR_MAP
from modules.flow_gate.services import work_plan_apply_service as wpa
from modules.flow_gate.services import work_plan_card_identity as cards
from modules.flow_gate.services import work_plan_card_order as order
from modules.flow_gate.services import work_plan_sequence_service as wpseq
from modules.flow_gate.services import work_plan_service as wp
from modules.flow_gate.services import workflow_decision_service as wds
from tests.regression.work_plan.test_work_plan_card_identity_0649 import _legacy
from tests.regression.work_plan.test_workflow_protected_rows_0649 import (
    _GROUP, _PROJECT, _ROOT, _SEED_SQL, _WP, _SqliteStore, _items, _seed_rows,
)

PROVIDERS = [{"id": "p1", "name": "P1", "enabled": True},
             {"id": "p2", "name": "P2", "enabled": True}]
# Result documents for started rows: <group>.01NN-<type>, approved.
_DOCS = {f"{_GROUP}.01{n:02d}-{code}": code for n, code in enumerate(
    ["T", "TR", "T", "TR", "T", "TR", "N", "NR"], start=1)}
_EXTRA_SQL = "INSERT OR IGNORE INTO documents(doc_id, project_id, module, group_id, type_code, " \
    "seq, title, status, doc_review_status, created_at, updated_at) VALUES " + ", ".join(
        f"('{doc_id}', '{_PROJECT}', 'default', '{_GROUP}', '{code}', {100 + i}, 'd', 'open', "
        f"'approved', datetime('now'), datetime('now'))"
        for i, (doc_id, code) in enumerate(_DOCS.items())
    ) + ";"
DOC = {code_n: doc_id for code_n, doc_id in zip(
    ["T1", "TR1", "T2", "TR2", "T3", "TR3", "N1", "NR1"], _DOCS)}


class _TxStore(_SqliteStore):
    """The registered SQL against sqlite, with a transaction that really rolls back."""

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self._depth = 0

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        if self._depth == 0:
            self._conn.commit()

    def table_exists(self, name: str) -> bool:
        # The real store's sqlite answer (connection.table_exists). P1 asks it before it
        # writes a v0.1 sequence: is the v0.2 Graph definition table there (0074 T#4)?
        return self._fetch_one(
            "SELECT 1 AS ok FROM sqlite_master WHERE type='table' AND name=?", [name],
        ) is not None

    @contextmanager
    def transaction(self):
        self._depth += 1
        try:
            yield self
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self._conn.rollback()
            raise
        self._depth -= 1
        if self._depth == 0:
            self._conn.commit()


class Env:
    def __init__(self, store, tmp_path):
        self.store = store
        self.plan_file = tmp_path / "0004-WP_document.json"
        self.revisions: dict[int, dict | None] = {}
        self.provenance: dict[str, dict] = {}
        self.revision = 0

    def plan(self, revision_no: int, body: dict) -> dict:
        """Make ``body`` revision ``revision_no`` and the current plan; returns it as read."""
        self.revisions[revision_no] = json.loads(json.dumps(body))
        self.revision = revision_no
        self.plan_file.write_text(json.dumps(body), encoding="utf-8")
        self.store._execute("UPDATE documents SET revision_no = ? WHERE doc_id = ?",
                            [revision_no, _WP])
        return wp.load_body(self.plan_file, project_id=_PROJECT, doc_id=_WP)

    def doc(self) -> dict:
        return {"doc_id": _WP, "revision_no": self.revision, "project_id": _PROJECT,
                "target_id": _ROOT, "group_id": _GROUP, "doc_review_status": "approved",
                "type_code": "WP"}


@pytest.fixture
def env(migrated_sqlite_db, monkeypatch, tmp_path):
    store = _TxStore(migrated_sqlite_db("card_order_0649.db", seed_sql=_SEED_SQL + _EXTRA_SQL))
    previous = db_connection.STORE
    db_connection.STORE = store
    state = Env(store, tmp_path)
    monkeypatch.setattr(wds.db_documents, "list_documents", lambda **kwargs: [])
    monkeypatch.setattr(wp, "plan_path_for_doc", lambda doc: state.plan_file)
    monkeypatch.setattr(cards, "stored_revision_loader", lambda doc: state.revisions.get)
    monkeypatch.setattr(cards, "read_result_provenance", lambda doc_id: state.provenance.get(doc_id))
    monkeypatch.setattr(wpseq, "provider_view_of", lambda project_id: {"readable": False})
    try:
        yield state
    finally:
        db_connection.STORE = previous
        store._conn.close()


# ── builders ─────────────────────────────────────────────────────────────────

def _body(spec: list, *, values: bool = True, same_values: bool = False) -> dict:
    """A stored new-format body. ``spec`` = [(type, card_id)] in card order."""
    steps, counts = [], {}
    for code, card_id in spec:
        counts[code] = counts.get(code, 0) + 1
        n = counts[code]
        tag = "same" if same_values else card_id
        extra = {"provider_id": "p1" if same_values or card_id.endswith("a") else "p2",
                 "provider_display_name": "P", "note": f"note {tag}",
                 "pre_instruction_text": f"brief {tag}"} if values else {}
        if code in WORK_PLAN_PAIR_MAP:
            result = WORK_PLAN_PAIR_MAP[code]
            steps.append({**wp.make_step(code, n, wp.make_key(result, n), "instruction",
                                         card_id=card_id), **extra})
            steps.append({**wp.make_step(result, n, wp.make_key(code, n), "result",
                                         card_id=card_id),
                          **({"note": f"report {tag}"} if values else {})})
        else:
            steps.append({**wp.make_step(code, n, None, "single", card_id=card_id), **extra})
    counted = list(dict.fromkeys(code for code, _ in spec))
    return {
        "wp_version": 2, "binding": "advisory", "counted_types": counted,
        "quantities": {c: {"unit": wp.WORK_PLAN_TYPE_UNITS[c], "count": counts[c]} for c in counted},
        "provider_candidates": [], "defaults": {"provider_id": None, "note": ""}, "steps": steps,
    }


def _rows(spec: list) -> list[dict]:
    """Seed spec: [(type, card, rev, result_doc or None)] -> _seed_rows input (this WP)."""
    return [{"type": code, "source": _WP, "rev": rev, "card": card, "result": result,
             "label": code} for code, card, rev, result in spec]


def _full(seq_id: int) -> list[dict]:
    return [dict(r) for r in _items(seq_id)]


def _ident(row: dict) -> tuple:
    return (row["id"], row["item_seq"], row["sort_order"], row["type"], row["result_doc_id"])


def _cards_of(seq_id: int) -> list:
    return [(r["type"], r["source_wp_card_id"]) for r in _items(seq_id)]


def _tag(seq_id: int) -> str:
    sequence = db_wfseq.get_sequence_by_doc_id(_ROOT)
    return wpa.build_workflow_tag(sequence, db_wfseq.get_sequence_items(sequence["id"]))


def _apply(env: Env, plan: dict, *, change: bool, tag: str | None = None, ack=None) -> dict:
    sequence = db_wfseq.get_sequence_by_doc_id(_ROOT)
    return wpa.apply(
        doc=env.doc(), owner_doc={"doc_id": _ROOT, "type_code": "R", "group_id": _GROUP},
        plan=plan, plan_path=env.plan_file, providers=PROVIDERS, instruction_mode="ai_direct",
        change_workflow=change, workflow_tag=tag or _tag(sequence["id"]),
        wp_revision_no=env.revision, applied_by="tester", acknowledged_codes=ack,
    )


def _preview(env: Env, plan: dict) -> dict:
    return wpa.preview(doc=env.doc(), plan=plan, providers=PROVIDERS, instruction_mode="ai_direct")


def _candidates(env: Env, plan: dict, mode: str) -> dict:
    return wpseq.build_candidates(doc=env.doc(), plan=plan, mode=mode)


def _sent_rows(candidate: dict) -> list[dict]:
    """The rows the pour dialog sends back for ``candidate`` (card ids included)."""
    return [{k: row.get(k) for k in (
        "type", "label", "note", "source_doc_id", "source_revision_no", "provider_id",
        "provider_display_name", "review_count", "reviewer_provider_id",
        "reviewer_provider_display_name", "pre_instruction_text", "pre_instruction_attachment",
        "source_wp_card_id", "item_id",
    ) if row.get(k) is not None} for row in candidate["rows"] if not row.get("protected")]


def _save_candidates(env: Env, candidate: dict, mode: str):
    rows = _sent_rows(candidate)
    return wds.edit_workflow_pending(
        _ROOT, rows, expected_workflow_tag=candidate["workflow_tag"],
        expected_plan={"wp_doc_id": _WP, "wp_revision_no": env.revision, "mode": mode},
    )


def _monotonic(seq_id: int) -> None:
    rows = _items(seq_id)
    assert [r["sort_order"] for r in rows] == sorted(r["sort_order"] for r in rows)
    assert len({r["sort_order"] for r in rows}) == len(rows)
    assert [r["item_seq"] for r in rows] == sorted(r["item_seq"] for r in rows)


def _codes(warnings) -> list:
    return [w["code"] for w in warnings]


# ══ O2 — G4: a started card is judged by identity ═══════════════════════════

def _g4(env: Env, *, same_values: bool = False):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")], same_values=same_values))
    seq_id = _seed_rows(_rows([
        ("T", "c_a", 0, DOC["T1"]), ("TR", "c_a", 0, DOC["TR1"]),
        ("T", "c_b", 0, None), ("TR", "c_b", 0, None),
    ]))
    # rev1: B moved in front of the finished A — B is now T#1, A is T#2, ids unchanged.
    plan = env.plan(1, _body([("T", "c_b"), ("T", "c_a")], same_values=same_values))
    assert [s["key"] for s in plan["steps"] if s["card_id"] == "c_b"] == ["T#1", "TR#1"]
    return seq_id, plan


@pytest.mark.parametrize("same_values", [False, True])
def test_g4_swap_ahead_of_a_started_card_is_refused_on_every_path(env, same_values):
    seq_id, plan = _g4(env, same_values=same_values)
    before = _full(seq_id)

    preview = _preview(env, plan)
    assert preview["apply_blockers"] == {
        "keep_workflow": "order_conflicts_started", "change_workflow": "order_conflicts_started",
    }
    warning = next(w for w in preview["warnings"] if w["code"] == "order_conflicts_started")
    assert warning["severity"] == "blocker"
    assert [c["card_id"] for c in warning["detail"]["cards"]] == ["c_a"]
    assert warning["detail"]["cards"][0]["key"] == "T#2"  # A's key now

    for change in (False, True):
        with pytest.raises(wpa.ApplyConflict) as raised:
            _apply(env, plan, change=change)
        assert raised.value.code == "order_conflicts_started"
        assert _full(seq_id) == before

    for mode in ("replace_after", "append"):
        candidate = _candidates(env, plan, mode)
        assert "order_conflicts_started" in candidate["blockers"]
        assert next(n for n in candidate["notifications"]
                    if n["code"] == "order_conflicts_started")["severity"] == "blocker"

    # The candidate rows sent straight to the save: refused before anything is written.
    candidate = _candidates(env, plan, "replace_after")
    with pytest.raises(order.PlanOrderBlocked) as blocked:
        _save_candidates(env, candidate, "replace_after")
    assert blocked.value.code == "order_conflicts_started"
    # Nowhere did A's values reach B's pending row (or anything else change).
    assert _full(seq_id) == before

    # P1 does not save either.
    result = wpseq.expand_final_work_plan(doc=env.doc(), plan=plan)
    assert result["status"] == "needs_selection"
    assert result["reason"] in ("order_conflicts_started", "editable_tail_exists")
    assert _full(seq_id) == before


def test_p1_refuses_a_started_card_moved_when_there_is_no_tail(env):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")]))
    # A started, its TR protected; B never poured: no editable tail.
    seq_id = _seed_rows(_rows([("T", "c_a", 0, DOC["T1"]), ("TR", "c_a", 0, None)]))
    before = _full(seq_id)
    plan = env.plan(1, _body([("T", "c_b"), ("T", "c_a")]))
    result = wpseq.expand_final_work_plan(doc=env.doc(), plan=plan)
    assert result == {"status": "needs_selection", "reason": "order_conflicts_started",
                      "revision_no": 1}
    assert _full(seq_id) == before


def test_started_card_removed_is_refused(env):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")]))
    seq_id = _seed_rows(_rows([
        ("T", "c_a", 0, DOC["T1"]), ("TR", "c_a", 0, DOC["TR1"]),
        ("T", "c_b", 0, None), ("TR", "c_b", 0, None),
    ]))
    before = _full(seq_id)
    plan = env.plan(1, _body([("T", "c_b")]))  # quantity 2 -> 1 removed the started A
    preview = _preview(env, plan)
    assert set(preview["apply_blockers"].values()) == {"started_card_removed"}
    for change in (False, True):
        with pytest.raises(wpa.ApplyConflict) as raised:
            _apply(env, plan, change=change)
        assert raised.value.code == "started_card_removed"
    assert _candidates(env, plan, "replace_after")["blockers"] == ["started_card_removed"]
    with pytest.raises(order.PlanOrderBlocked) as blocked:
        _save_candidates(env, _candidates(env, plan, "replace_after"), "replace_after")
    assert blocked.value.code == "started_card_removed"
    assert _full(seq_id) == before


def test_card_identity_mismatch_is_refused(env):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")]))
    seq_id = _seed_rows(_rows([
        ("T", "c_a", 0, DOC["T1"]), ("TR", "c_a", 0, DOC["TR1"]),
        ("T", "c_b", 0, None), ("TR", "c_b", 0, None),
    ]))
    # A body that bypassed the save check (§5.1 would refuse it): c_a is an N card now.
    plan = env.plan(1, _body([("N", "c_a"), ("T", "c_b")]))
    blocked = order.check_started_prefix(
        plan, cards.classify_rows(wp_doc=env.doc(), plan=plan, items=_items(seq_id)),
        _items(seq_id),
    )
    assert blocked["code"] == "card_identity_mismatch"
    assert _preview(env, plan)["apply_blockers"]["change_workflow"] == "card_identity_mismatch"


# ══ O2 control + O3: reordering cards that have not started ═════════════════

def _abc(env: Env):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b"), ("T", "c_c")]))
    return _seed_rows(_rows([
        ("T", "c_a", 0, DOC["T1"]), ("TR", "c_a", 0, DOC["TR1"]),
        ("T", "c_b", 0, None), ("TR", "c_b", 0, None),
        ("T", "c_c", 0, None), ("TR", "c_c", 0, None),
    ]))


def test_started_prefix_allowed_reorder_keeps_values_on_their_own_cards(env):
    seq_id = _abc(env)
    before = _full(seq_id)
    plan = env.plan(1, _body([("T", "c_a"), ("T", "c_c"), ("T", "c_b")]))
    keys = {s["card_id"]: s["key"] for s in plan["steps"] if s["pair_role"] == "instruction"}
    assert keys == {"c_a": "T#1", "c_c": "T#2", "c_b": "T#3"}

    preview = _preview(env, plan)
    differs = next(w for w in preview["warnings"] if w["code"] == "order_differs")
    assert differs["severity"] == "warning"
    assert preview["apply_blockers"] == {"keep_workflow": "order_differs", "change_workflow": None}
    reordered = {x["card_id"]: x for x in preview["comparison"]["reordered"]["items"]}
    assert set(reordered) == {"c_b", "c_c"}
    assert reordered["c_c"]["position_before"] == before[4]["item_seq"]
    assert reordered["c_c"]["position_after"] < reordered["c_b"]["position_after"]
    assert preview["comparison"]["removed"]["count"] == 0

    with pytest.raises(wpa.ApplyConflict) as raised:
        _apply(env, plan, change=False)
    assert raised.value.code == "order_differs"
    assert _full(seq_id) == before

    result = _apply(env, plan, change=True)
    after = _items(seq_id)
    # The started card's rows: untouched.
    assert [_ident(r) for r in after[:2]] == [_ident(r) for r in before[:2]]
    assert _cards_of(seq_id) == [
        ("T", "c_a"), ("TR", "c_a"), ("T", "c_c"), ("TR", "c_c"), ("T", "c_b"), ("TR", "c_b"),
    ]
    _monotonic(seq_id)
    # Each card took its own values although its key moved (T#2 <-> T#3).
    assert [(r["note"], r["pre_instruction_text"]) for r in after[2:]] == [
        ("note c_c", "brief c_c"), ("report c_c", None),
        ("note c_b", "brief c_b"), ("report c_b", None),
    ]
    assert {r["source_revision_no"] for r in after[2:]} == {1}
    # Execution follows the card order: the head is the first card that has not started.
    assert db_wfseq.get_effective_head(seq_id)["id"] == after[2]["id"]
    assert result["workflow_changed"] is True
    assert {x["card_id"] for x in result["reordered"]} == {"c_b", "c_c"}


def test_g1_g2_change_rewrites_order_and_places_an_added_card_in_position(env):
    env.plan(0, _body([("T", "c_a"), ("N", "c_n")]))
    seq_id = _seed_rows(_rows([
        ("T", "c_a", 0, None), ("TR", "c_a", 0, None), ("N", "c_n", 0, None), ("NR", "c_n", 0, None),
    ]))
    # rev1: N first, a new T card (server id c_new) between N and the old T.
    plan = env.plan(1, _body([("N", "c_n"), ("T", "c_new"), ("T", "c_a")]))
    preview = _preview(env, plan)
    assert preview["apply_blockers"]["keep_workflow"] in ("unmatched_plan_steps", "order_differs")
    added = preview["comparison"]["added"]["items"]
    assert [(x["plan_key"], x["card_id"]) for x in added] == [("T#1", "c_new"), ("TR#1", "c_new")]
    _apply(env, plan, change=True)
    assert _cards_of(seq_id) == [
        ("N", "c_n"), ("NR", "c_n"), ("T", "c_new"), ("TR", "c_new"), ("T", "c_a"), ("TR", "c_a"),
    ]
    _monotonic(seq_id)


def test_orphan_rows_refuse_keep_and_are_removed_by_change(env):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")]))
    seq_id = _seed_rows(_rows([
        ("T", "c_a", 0, None), ("TR", "c_a", 0, None), ("T", "c_b", 0, None), ("TR", "c_b", 0, None),
    ]))
    before = _full(seq_id)
    plan = env.plan(1, _body([("T", "c_a")]))
    preview = _preview(env, plan)
    assert preview["apply_blockers"]["keep_workflow"] == "orphan_plan_rows"
    assert [x["item_id"] for x in preview["comparison"]["removed"]["items"]] == [
        before[2]["id"], before[3]["id"],
    ]
    assert "orphan_plan_rows" in _codes(preview["warnings"])
    with pytest.raises(wpa.ApplyConflict) as raised:
        _apply(env, plan, change=False)
    assert raised.value.code == "orphan_plan_rows"
    assert _full(seq_id) == before
    result = _apply(env, plan, change=True)
    assert _cards_of(seq_id) == [("T", "c_a"), ("TR", "c_a")]
    assert [x["item_id"] for x in result["removed_items"]] == [before[2]["id"], before[3]["id"]]


def test_keep_is_allowed_when_only_values_changed(env):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")], values=False))
    seq_id = _seed_rows(_rows([
        ("T", "c_a", 0, None), ("TR", "c_a", 0, None), ("T", "c_b", 0, None), ("TR", "c_b", 0, None),
    ]))
    before = _full(seq_id)
    plan = env.plan(1, _body([("T", "c_a"), ("T", "c_b")]))
    assert _preview(env, plan)["apply_blockers"] == {"keep_workflow": None, "change_workflow": None}
    _apply(env, plan, change=False)
    after = _items(seq_id)
    assert [_ident(r) for r in after] == [_ident(r) for r in before]
    assert [r["note"] for r in after] == ["note c_a", "report c_a", "note c_b", "report c_b"]


def test_apply_refuses_what_its_preview_refuses(env):
    # Only card A (finished) has rows; the plan adds card B and carries no value to fill.
    env.plan(0, _body([("T", "c_a")], values=False))
    seq_id = _seed_rows(_rows([("T", "c_a", 0, DOC["T1"]), ("TR", "c_a", 0, DOC["TR1"])]))
    before = _full(seq_id)
    history = wpa._applications_path(env.plan_file, _WP)
    plan = env.plan(1, _body([("T", "c_a"), ("T", "c_b")], values=False))
    preview = _preview(env, plan)
    assert preview["apply_blockers"]["keep_workflow"] == "unmatched_plan_steps"
    with pytest.raises(wpa.ApplyConflict) as raised:
        _apply(env, plan, change=False)
    assert raised.value.code == "unmatched_plan_steps"
    assert raised.value.payload["keys"] == ["T#2", "TR#2"]
    assert _full(seq_id) == before
    assert not history.exists()
    # Adding B is what a change-apply is for: allowed although nothing is filled.
    assert preview["apply_blockers"]["change_workflow"] is None
    _apply(env, plan, change=True)
    assert _cards_of(seq_id) == [("T", "c_a"), ("TR", "c_a"), ("T", "c_b"), ("TR", "c_b")]

    # Same cards in the same order and still nothing to fill: both modes refuse, as the
    # preview says, and nothing is written or recorded.
    before = _full(seq_id)
    recorded = history.read_text(encoding="utf-8")
    plan = env.plan(2, _body([("T", "c_a"), ("T", "c_b")], values=False))
    preview = _preview(env, plan)
    assert preview["apply_blockers"] == {
        "keep_workflow": "nothing_to_fill", "change_workflow": "nothing_to_fill",
    }
    for change in (False, True):
        with pytest.raises(wpa.ApplyConflict) as raised:
            _apply(env, plan, change=change)
        assert raised.value.code == "nothing_to_fill"
    assert _full(seq_id) == before
    assert history.read_text(encoding="utf-8") == recorded


def test_foreign_rows_keep_their_side_and_interleaving_refuses_change(env):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")]))
    seq_id = _seed_rows([
        {"type": "D", "label": "before"},
        *_rows([("T", "c_a", 0, None), ("TR", "c_a", 0, None),
                ("T", "c_b", 0, None), ("TR", "c_b", 0, None)]),
        {"type": "P", "label": "after"},
    ])
    plan = env.plan(1, _body([("T", "c_b"), ("T", "c_a")]))
    _apply(env, plan, change=True)
    assert [(r["type"], r["label"] if r["source_doc_id"] is None else r["source_wp_card_id"])
            for r in _items(seq_id)] == [
        ("D", "before"), ("T", "c_b"), ("TR", "c_b"), ("T", "c_a"), ("TR", "c_a"), ("P", "after"),
    ]
    _monotonic(seq_id)


def test_foreign_row_inside_the_plan_block_refuses_change(env):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")]))
    seq_id = _seed_rows([
        *_rows([("T", "c_a", 0, None), ("TR", "c_a", 0, None)]),
        {"type": "D", "label": "inside"},
        *_rows([("T", "c_b", 0, None), ("TR", "c_b", 0, None)]),
    ])
    before = _full(seq_id)
    plan = env.plan(1, _body([("T", "c_b"), ("T", "c_a")]))
    preview = _preview(env, plan)
    assert preview["apply_blockers"]["change_workflow"] == "foreign_rows_interleaved"
    assert next(w for w in preview["warnings"]
                if w["code"] == "foreign_rows_interleaved")["detail"]["rows"][0]["label"] == "inside"
    with pytest.raises(wpa.ApplyConflict) as raised:
        _apply(env, plan, change=True)
    assert raised.value.code == "foreign_rows_interleaved"
    assert _full(seq_id) == before


# ══ O5 — append, replace_after, P1 ══════════════════════════════════════════

def test_g7_append_is_refused_while_this_plans_rows_are_pending(env):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")]))
    seq_id = _seed_rows(_rows([
        ("T", "c_a", 0, None), ("TR", "c_a", 0, None), ("T", "c_b", 0, None), ("TR", "c_b", 0, None),
    ]))
    before = _full(seq_id)
    plan = env.plan(1, _body([("T", "c_b"), ("T", "c_a")]))
    candidate = _candidates(env, plan, "append")
    assert candidate["blockers"] == ["plan_rows_pending"]
    note = next(n for n in candidate["notifications"] if n["code"] == "plan_rows_pending")
    assert note["severity"] == "blocker" and note["suggested_mode"] == "replace_after"
    assert note["count"] == 4

    with pytest.raises(order.PlanOrderBlocked) as blocked:
        _save_candidates(env, candidate, "append")
    assert blocked.value.code == "plan_rows_pending"
    assert _full(seq_id) == before
    # The same rows without the mode: the post-write check catches the duplicated cards.
    rows = [{k: v for k, v in row.items() if k in ("type", "label", "source_doc_id",
             "source_revision_no", "source_wp_card_id", "item_id") and v is not None}
            for row in candidate["rows"]]
    with pytest.raises(order.PlanOrderViolation):
        wds.edit_workflow_pending(_ROOT, rows, expected_workflow_tag=candidate["workflow_tag"],
                                  expected_plan={"wp_doc_id": _WP, "wp_revision_no": 1})
    assert _full(seq_id) == before


def test_append_with_only_foreign_rows_pours_the_rest_in_card_order(env):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b"), ("N", "c_n")]))
    seq_id = _seed_rows([
        *_rows([("T", "c_a", 0, DOC["T1"]), ("TR", "c_a", 0, DOC["TR1"])]),
        {"type": "P", "label": "manual"},
    ])
    plan = env.plan(1, _body([("T", "c_a"), ("N", "c_n"), ("T", "c_b")]))
    candidate = _candidates(env, plan, "append")
    assert candidate["blockers"] == []
    codes = [n["code"] for n in candidate["notifications"]]
    assert "foreign_rows_before" in codes and "steps_already_done" in codes
    assert next(n for n in candidate["notifications"]
                if n["code"] == "steps_already_done")["items"] == [{"card_id": "c_a", "plan_key": "T#1"}]
    _save_candidates(env, candidate, "append")
    assert [(r["type"], r["source_wp_card_id"]) for r in _items(seq_id)] == [
        ("T", "c_a"), ("TR", "c_a"), ("P", None), ("N", "c_n"), ("NR", "c_n"),
        ("T", "c_b"), ("TR", "c_b"),
    ]
    _monotonic(seq_id)


def test_g3_replace_after_and_p1_do_not_pour_a_started_card_again(env):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")]))
    seq_id = _seed_rows(_rows([("T", "c_a", 0, DOC["T1"]), ("TR", "c_a", 0, None)]))
    before = _full(seq_id)
    plan = env.plan(1, _body([("T", "c_a"), ("T", "c_b")]))
    candidate = _candidates(env, plan, "replace_after")
    assert candidate["blockers"] == []
    assert [r["source_wp_card_id"] for r in candidate["rows"] if r.get("poured")] == ["c_b", "c_b"]
    assert next(n for n in candidate["notifications"]
                if n["code"] == "steps_already_done")["items"][0]["card_id"] == "c_a"
    # P1 (no tail) saves exactly that.
    result = wpseq.expand_final_work_plan(doc=env.doc(), plan=plan)
    assert result["status"] == "expanded"
    after = _items(seq_id)
    assert [_ident(r) for r in after[:2]] == [_ident(r) for r in before]
    assert _cards_of(seq_id) == [("T", "c_a"), ("TR", "c_a"), ("T", "c_b"), ("TR", "c_b")]
    assert result["result"]["started_card_ids"] == ["c_a"]


def test_p1_leaves_the_sequence_while_the_run_works_on_a_row_without_a_document(env, monkeypatch):
    from modules.flow_gate.services import ai_invoke_service

    env.plan(0, _body([("T", "c_a"), ("T", "c_b")]))
    seq_id = _seed_rows(_rows([("T", "c_a", 0, DOC["T1"]), ("TR", "c_a", 0, None)]))
    before = _full(seq_id)
    plan = env.plan(1, _body([("T", "c_a"), ("T", "c_b")]))
    run = {"hop_item_seq": before[1]["item_seq"], "status": "running"}
    monkeypatch.setattr(ai_invoke_service, "_active_run_for_group", lambda group_id: run)
    result = wpseq.expand_final_work_plan(doc=env.doc(), plan=plan)
    assert result["status"] == "needs_selection" and result["reason"] == "ai_run_on_pending_row"
    assert _full(seq_id) == before
    # The ordinary approval case: the run's hop is a row that has its document — expanded.
    run["hop_item_seq"] = before[0]["item_seq"]
    assert wpseq.expand_final_work_plan(doc=env.doc(), plan=plan)["status"] == "expanded"


def test_plan_order_violation_rolls_the_save_back(env):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")]))
    seq_id = _seed_rows(_rows([
        ("T", "c_a", 0, None), ("TR", "c_a", 0, None), ("T", "c_b", 0, None), ("TR", "c_b", 0, None),
    ]))
    before = _full(seq_id)
    plan = env.plan(1, _body([("T", "c_a"), ("T", "c_b")]))
    wrong = [
        {"type": "T", "label": "T", "source_doc_id": _WP, "source_revision_no": 1, "source_wp_card_id": "c_b"},
        {"type": "T", "label": "T", "source_doc_id": _WP, "source_revision_no": 1, "source_wp_card_id": "c_a"},
    ]
    with pytest.raises(order.PlanOrderViolation) as raised:
        wds.edit_workflow_pending(_ROOT, [dict(x) for x in wrong],
                                  expected_plan={"wp_doc_id": _WP, "wp_revision_no": 1})
    assert raised.value.detail["reason"] == "order"
    assert _full(seq_id) == before  # deleted and re-inserted rows rolled back

    twice = [wrong[1], dict(wrong[1]), wrong[0]]
    with pytest.raises(order.PlanOrderViolation):
        wds.edit_workflow_pending(_ROOT, [dict(x) for x in twice],
                                  expected_plan={"wp_doc_id": _WP, "wp_revision_no": 1})
    assert _full(seq_id) == before

    # An ordinary sequence edit (no expected_plan) is a person's own edit: not checked.
    wds.edit_workflow_pending(_ROOT, [dict(x) for x in wrong])
    assert [r["source_wp_card_id"] for r in _items(seq_id)] == ["c_b", "c_b", "c_a", "c_a"]
    # ... and the next apply shows and refuses keeping that order.
    assert _preview(env, plan)["apply_blockers"]["keep_workflow"] == "order_differs"


def test_pre_card_client_rows_get_their_cards_from_the_plan(env):
    """A pour dialog that predates card ids sends no source_wp_card_id: the save gives each
    row the card the candidate held at that place, and still checks the order."""
    env.plan(0, _body([("T", "c_a"), ("N", "c_n")]))
    seq_id = _seed_rows(_rows([
        ("T", "c_a", 0, None), ("TR", "c_a", 0, None), ("N", "c_n", 0, None), ("NR", "c_n", 0, None),
    ]))
    plan = env.plan(1, _body([("N", "c_n"), ("T", "c_a")]))
    candidate = _candidates(env, plan, "replace_after")
    old_client = [{k: v for k, v in row.items() if k in (
        "type", "label", "note", "source_doc_id", "source_revision_no")}
        for row in candidate["rows"]]
    _ = wds.edit_workflow_pending(_ROOT, old_client, expected_workflow_tag=candidate["workflow_tag"],
                                  expected_plan={"wp_doc_id": _WP, "wp_revision_no": 1,
                                                 "mode": "replace_after"})
    assert _cards_of(seq_id) == [("N", "c_n"), ("NR", "c_n"), ("T", "c_a"), ("TR", "c_a")]
    # The same old client reordering the cards in the dialog is refused.
    candidate = _candidates(env, plan, "replace_after")
    swapped = [{k: v for k, v in row.items() if k in (
        "type", "label", "note", "source_doc_id", "source_revision_no")}
        for row in candidate["rows"][2:] + candidate["rows"][:2]]
    with pytest.raises(order.PlanOrderViolation):
        wds.edit_workflow_pending(_ROOT, swapped, expected_workflow_tag=candidate["workflow_tag"],
                                  expected_plan={"wp_doc_id": _WP, "wp_revision_no": 1})


_CLIENT_FIELDS = (
    "type", "label", "note", "source_doc_id", "source_revision_no", "provider_id",
    "provider_display_name", "review_count", "reviewer_provider_id",
    "reviewer_provider_display_name", "pre_instruction_text", "pre_instruction_attachment",
)


def _client_rows(rows: list[dict], fields=_CLIENT_FIELDS) -> list[dict]:
    """What WorkflowDecisionModal.save() sends today: no card id, no item id."""
    return [{k: row.get(k) for k in fields} for row in rows if not row.get("protected")]


def _pour(env: Env, rows: list[dict], tag: str):
    return wds.edit_workflow_pending(
        _ROOT, rows, expected_workflow_tag=tag,
        expected_plan={"wp_doc_id": _WP, "wp_revision_no": env.revision, "mode": "replace_after"},
    )


def test_pre_card_client_same_type_swap_is_refused_not_relabelled(env):
    """Rows of the same type without card ids are placed by what they carry, not by where
    they stand: swapping two T cards in the dialog no longer hands the swapped rows the
    expected ids, so the save is refused and nothing is written."""
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")]))
    seq_id = _seed_rows(_rows([
        ("T", "c_a", 0, None), ("TR", "c_a", 0, None), ("T", "c_b", 0, None), ("TR", "c_b", 0, None),
    ]))
    plan = env.plan(1, _body([("T", "c_a"), ("T", "c_b"), ("T", "c_c")]))
    candidate = _candidates(env, plan, "replace_after")
    rows = _client_rows(candidate["rows"])
    assert [r["type"] for r in rows] == ["T", "TR"] * 3
    before = _full(seq_id)

    swapped = rows[2:4] + rows[0:2] + rows[4:]
    with pytest.raises(order.PlanOrderViolation) as raised:
        _pour(env, [dict(x) for x in swapped], candidate["workflow_tag"])
    assert raised.value.detail["reason"] == "order"
    assert [c["card_id"] for c in raised.value.detail["actual"]] == ["c_b", "c_a", "c_c"]
    assert _full(seq_id) == before

    # Rows that state too little to tell the cards apart are refused, not guessed.
    bare = _client_rows(candidate["rows"], ("type", "label", "source_doc_id", "source_revision_no"))
    with pytest.raises(order.PlanOrderViolation) as raised:
        _pour(env, bare, candidate["workflow_tag"])
    assert raised.value.detail["reason"] == "ambiguous_card"
    assert raised.value.detail["type"] == "T"
    assert _full(seq_id) == before

    # One row whose note a person edited still finds the one card left for it ...
    edited = [dict(x) for x in rows]
    edited[2]["note"] = "edited in the dialog"
    _pour(env, edited, candidate["workflow_tag"])
    stored = _items(seq_id)
    assert [(r["type"], r["source_wp_card_id"]) for r in stored] == [
        ("T", "c_a"), ("TR", "c_a"), ("T", "c_b"), ("TR", "c_b"), ("T", "c_c"), ("TR", "c_c"),
    ]
    assert [r["note"] for r in stored if r["type"] == "T"] == [
        "note c_a", "edited in the dialog", "note c_c",
    ]
    assert [r["provider_id"] for r in stored if r["type"] == "T"] == ["p1", "p2", "p2"]

    # ... and the rows saved as the dialog showed them carry each card's own settings.
    candidate = _candidates(env, plan, "replace_after")
    _pour(env, _client_rows(candidate["rows"]), candidate["workflow_tag"])
    stored = _items(seq_id)
    assert [r["source_wp_card_id"] for r in stored if r["type"] == "T"] == ["c_a", "c_b", "c_c"]
    assert [r["note"] for r in stored if r["type"] == "T"] == ["note c_a", "note c_b", "note c_c"]
    assert [r["note"] for r in stored if r["type"] == "TR"] == [
        "report c_a", "report c_b", "report c_c",
    ]


def test_pre_card_client_swap_of_cards_with_identical_settings_is_the_same_save(env):
    """Cards whose rows carry identical settings are interchangeable: either order stores
    the same rows, so the save goes through in plan order."""
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")], same_values=True))
    seq_id = _seed_rows(_rows([
        ("T", "c_a", 0, None), ("TR", "c_a", 0, None), ("T", "c_b", 0, None), ("TR", "c_b", 0, None),
    ]))
    plan = env.plan(1, _body([("T", "c_a"), ("T", "c_b")], same_values=True))
    candidate = _candidates(env, plan, "replace_after")
    rows = _client_rows(candidate["rows"])
    _pour(env, rows[2:4] + rows[0:2], candidate["workflow_tag"])
    assert _cards_of(seq_id) == [("T", "c_a"), ("TR", "c_a"), ("T", "c_b"), ("TR", "c_b")]


def test_row_of_another_cards_type_rolls_the_save_back(env):
    """A direct plan-reflecting save may name a card on a row of a different type: the
    card-id order alone would pass, the type check refuses it inside the transaction."""
    env.plan(0, _body([("T", "c_t"), ("N", "c_n")]))
    seq_id = _seed_rows(_rows([
        ("T", "c_t", 0, None), ("TR", "c_t", 0, None), ("N", "c_n", 0, None), ("NR", "c_n", 0, None),
    ]))
    plan = env.plan(1, _body([("T", "c_t"), ("N", "c_n")]))
    before = _full(seq_id)

    def row(code, card):
        return {"type": code, "label": code, "source_doc_id": _WP, "source_revision_no": 1,
                "source_wp_card_id": card}

    # N/NR carrying the T card's id, T/TR carrying the N card's: same card order as the plan.
    crossed = [row("N", "c_t"), row("NR", "c_t"), row("T", "c_n"), row("TR", "c_n")]
    with pytest.raises(order.PlanOrderViolation) as raised:
        wds.edit_workflow_pending(_ROOT, [dict(x) for x in crossed],
                                  expected_plan={"wp_doc_id": _WP, "wp_revision_no": 1})
    assert raised.value.detail["reason"] == "card_type_mismatch"
    assert raised.value.detail["card_id"] == "c_t"
    assert raised.value.detail["type"] == "N" and raised.value.detail["card_type"] == "T"
    assert _full(seq_id) == before  # deleted and re-inserted rows rolled back

    # The check itself, on stored rows: a report of the wrong type for its card.
    stored = [dict(r) for r in _items(seq_id)]
    stored[1]["type"] = "NR"
    with pytest.raises(order.PlanOrderViolation) as raised:
        order.assert_plan_order(stored, plan, _WP, [])
    assert raised.value.detail["reason"] == "report_type_mismatch"
    assert raised.value.detail["card_id"] == "c_t" and raised.value.detail["card_type"] == "T"
    order.assert_plan_order(_items(seq_id), plan, _WP, [])  # the untouched rows pass


# ══ 0112 — a paired design card is two rows: DS + its result (NR0003 §5.4) ══
#
# plan_to_rows pours a paired design card (card_stage_model "paired") as its DS instruction
# row and its L/P/DB result row, both of the card. The post-write check used to expect one
# row of the card's type per card, so the first DS row failed card_type_mismatch and the
# whole save rolled back. The body is flowgate.v02.0102.0005-WP rev 1, stored verbatim.

_PAIRED_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "work_plan_v02_0102_0005_rev1.json"
_PAIRED_SHA256 = "13d9d09ca8705f25cf193774af9602ef5d5edac76e2069790e3f55d9f9d3c47a"
_PAIRED_ROWS = [
    ("DS", "L#1"), ("L", "L#1"), ("DS", "L#2"), ("L", "L#2"), ("DS", "L#3"), ("L", "L#3"),
    ("DS", "DB#1"), ("DB", "DB#1"), ("DS", "P#1"), ("P", "P#1"),
    *[(code, f"T#{n}") for n in range(1, 6) for code in ("T", "TR")],
]


def _paired_body() -> dict:
    raw = _PAIRED_FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _PAIRED_SHA256  # neither shortened nor edited
    return json.loads(raw.decode("utf-8"))


def _paired(env: Env, body: dict | None = None):
    """The paired plan over a sequence whose first rows (another document's T/TR) ran."""
    seq_id = _seed_rows([
        {"type": "T", "label": "T", "result": DOC["T1"]},
        {"type": "TR", "label": "TR", "result": DOC["TR1"]},
    ])
    return seq_id, env.plan(1, body or _paired_body())


def _paired_saved(env: Env):
    seq_id, plan = _paired(env)
    _save_candidates(env, _candidates(env, plan, "replace_after"), "replace_after")
    return seq_id, plan


def _plan_rows(seq_id: int) -> list[dict]:
    return [r for r in _items(seq_id) if r["source_doc_id"] == _WP]


def _pairs(rows: list[dict]) -> list[tuple]:
    return [(r["type"], r["source_wp_card_id"]) for r in rows]


def _row_settings(row: dict) -> tuple:
    return (row["type"], row["source_wp_card_id"], row["provider_id"], int(row["review_count"] or 0),
            row["reviewer_provider_id"], row["note"], row["pre_instruction_text"])


def _step_settings(step: dict) -> tuple:
    return (step["type"], step["card_id"], step["provider_id"], int(step["review_count"] or 0),
            step["reviewer_provider_id"], wpseq.normalize_note(step["note"]),
            step["pre_instruction_text"])


def test_paired_plan_pours_and_saves_ds_and_result_rows_of_each_design_card(env):
    seq_id, plan = _paired(env)
    before = _full(seq_id)
    candidate = _candidates(env, plan, "replace_after")
    assert candidate["blockers"] == []
    assert candidate["plan_step_count"] == 15
    assert _pairs([r for r in candidate["rows"] if r.get("poured")]) == _PAIRED_ROWS

    _save_candidates(env, candidate, "replace_after")
    after = _items(seq_id)
    assert [_ident(r) for r in after[:2]] == [_ident(r) for r in before]  # the rows that ran
    stored = _plan_rows(seq_id)
    assert _pairs(stored) == _PAIRED_ROWS
    # Each row keeps its own step's AI, review, note and brief: a DS instruction and its
    # design result are two steps with two settings (body order is row order).
    assert [_row_settings(r) for r in stored] == [_step_settings(s) for s in plan["steps"]]
    assert {r["source_revision_no"] for r in stored} == {1}
    _monotonic(seq_id)
    order.assert_plan_order(_items(seq_id), plan, _WP, [])


def test_paired_plan_saved_or_poured_again_makes_no_duplicate(env):
    seq_id, plan = _paired_saved(env)
    first = [_row_settings(r) for r in _plan_rows(seq_id)]

    again = _candidates(env, plan, "replace_after")
    assert again["blockers"] == []
    assert _pairs([r for r in again["rows"] if r.get("poured")]) == _PAIRED_ROWS
    assert again["row_count_change"]["deleted"] == len(_PAIRED_ROWS)  # the poured rows it replaces
    _save_candidates(env, again, "replace_after")
    assert [_row_settings(r) for r in _plan_rows(seq_id)] == first
    assert len(_items(seq_id)) == 2 + len(_PAIRED_ROWS)
    # append on top of them stays refused: the plan's rows are still pending (G7).
    assert _candidates(env, plan, "append")["blockers"] == ["plan_rows_pending"]


def test_paired_plan_rows_sent_without_card_ids_find_their_cards(env):
    """A pre-card client: each DS row and each result row is placed by its own settings."""
    seq_id, plan = _paired(env)
    candidate = _candidates(env, plan, "replace_after")
    _pour(env, _client_rows(candidate["rows"]), candidate["workflow_tag"])
    assert _pairs(_plan_rows(seq_id)) == _PAIRED_ROWS

    # Rows stating nothing but their type cannot be told apart: refused, not guessed.
    before = _full(seq_id)
    candidate = _candidates(env, plan, "replace_after")
    bare = _client_rows(candidate["rows"], ("type", "label", "source_doc_id", "source_revision_no"))
    with pytest.raises(order.PlanOrderViolation) as raised:
        _pour(env, bare, candidate["workflow_tag"])
    assert raised.value.detail["reason"] == "ambiguous_card"
    assert _full(seq_id) == before


def _ds_of_another_design_card(rows):
    rows[0]["source_wp_card_id"] = "L#2"


def _ds_of_a_t_card(rows):
    rows[0]["source_wp_card_id"] = "T#1"


def _result_type_changed(rows):
    rows[1]["type"] = "P"


def _result_type_changed_in_the_dialog(rows):
    rows[1]["type"] = "P"  # the dialog drops the card of a row whose type a person changed
    rows[1].pop("source_wp_card_id")


def _ds_and_result_reversed(rows):
    rows[0], rows[1] = rows[1], rows[0]


def _result_missing(rows):
    del rows[1]


@pytest.mark.parametrize("break_rows, reason", [
    (_ds_of_another_design_card, "result_not_after_instruction"),
    (_ds_of_a_t_card, "card_type_mismatch"),
    (_result_type_changed, "card_type_mismatch"),
    (_result_type_changed_in_the_dialog, "row_without_card"),
    (_ds_and_result_reversed, "result_not_after_instruction"),
    (_result_missing, "result_count"),
])
def test_paired_rows_that_break_their_card_roll_the_save_back(env, break_rows, reason):
    seq_id, plan = _paired_saved(env)
    candidate = _candidates(env, plan, "replace_after")
    rows = _sent_rows(candidate)
    assert _pairs(rows[:2]) == [("DS", "L#1"), ("L", "L#1")]
    before = _full(seq_id)
    break_rows(rows)
    with pytest.raises(order.PlanOrderViolation) as raised:
        _pour(env, rows, candidate["workflow_tag"])
    assert raised.value.detail["reason"] == reason
    assert _full(seq_id) == before  # deleted and re-inserted rows rolled back


def test_paired_layout_and_started_rows_pair_the_result_row_with_its_ds_row(env):
    seq_id, plan = _paired_saved(env)
    stored = _plan_rows(seq_id)
    expected = {
        row["source_wp_card_id"]: (row["id"], second["id"])
        for row, second in zip(stored[0::2], stored[1::2])
    }
    items = _items(seq_id)
    classification = cards.classify_rows(wp_doc=env.doc(), plan=plan, items=items)
    layout = order.reorder_layout(plan, items, _WP, classification)
    assert (layout["removed"], layout["interleaved"], layout["order_differs"]) == ([], [], False)
    assert {
        card_id: (picked["row"]["id"], (picked["report"] or {}).get("id"))
        for card_id, picked in layout["cards"].items()
    } == expected

    db_wfseq.set_item_result_doc_id(stored[0]["id"], DOC["T2"])  # DS of L#1 ran
    items = _items(seq_id)
    classification = cards.classify_rows(wp_doc=env.doc(), plan=plan, items=items)
    found = order.started_card_rows(items, classification)
    assert {c: (x["row"]["id"], (x["report"] or {}).get("id")) for c, x in found.items()} == {
        "L#1": expected["L#1"],
    }


@pytest.mark.parametrize("ds_doc", [DOC["T2"], f"{_GROUP}.0007-T"], ids=["done", "in_progress"])
@pytest.mark.parametrize("result_started", [False, True], ids=["result_pending", "result_started"])
def test_paired_card_whose_ds_row_started_is_poured_again_without_card_refusal_or_lost_result(
    env, ds_doc, result_started,
):
    """No false card_identity_mismatch / order_conflicts_started for a started DS row. When
    the card's result ran too, the pour saves and keeps both rows; when it has not, the pour
    would drop the pending result row (nothing pours a started card again), so the save is
    refused and rolled back rather than saved without that step."""
    seq_id, plan = _paired_saved(env)
    stored = _plan_rows(seq_id)
    db_wfseq.set_item_result_doc_id(stored[0]["id"], ds_doc)
    if result_started:
        db_wfseq.set_item_result_doc_id(stored[1]["id"], DOC["T3"])
    before = _full(seq_id)
    started_rows = 2 + (2 if result_started else 1)  # the other document's T/TR + L#1's

    classification = cards.classify_rows(wp_doc=env.doc(), plan=plan, items=_items(seq_id))
    assert classification["started_card_ids"] == ["L#1"]
    assert order.check_started_prefix(plan, classification, _items(seq_id)) is None
    for mode in ("replace_after", "append"):
        codes = {n["code"] for n in _candidates(env, plan, mode)["notifications"]}
        assert not {"card_identity_mismatch", "order_conflicts_started"} & codes
    candidate = _candidates(env, plan, "replace_after")
    assert candidate["blockers"] == []
    assert _pairs([r for r in candidate["rows"] if r.get("poured")]) == _PAIRED_ROWS[2:]

    if not result_started:
        with pytest.raises(order.PlanOrderViolation) as raised:
            _save_candidates(env, candidate, "replace_after")
        assert raised.value.detail["reason"] == "result_count"
        assert (raised.value.detail["card_id"], raised.value.detail["started"]) == ("L#1", True)
        assert _full(seq_id) == before  # the pending result row and its settings are kept
        return

    _save_candidates(env, candidate, "replace_after")
    after = _items(seq_id)
    assert [_ident(r) for r in after[:started_rows]] == [_ident(r) for r in before[:started_rows]]
    assert _pairs(after[2:started_rows]) == [("DS", "L#1"), ("L", "L#1")]
    assert _pairs(after[started_rows:]) == _PAIRED_ROWS[2:]
    _monotonic(seq_id)


@pytest.mark.xfail(strict=True, raises=order.PlanOrderViolation, reason=(
    "0112 TR0006 Q529 (scope): a pending result row behind a started DS row is not protected "
    "(db/workflow_sequences.protected_row_ids keeps only an automatic report) and the pour "
    "skips started cards, so the save would drop it; assert_plan_order refuses that save "
    "(result_count) until the row is kept -- a fix outside work_plan_card_order.py"
))
def test_paired_result_row_behind_a_started_ds_row_survives_a_pour(env):
    seq_id, plan = _paired_saved(env)
    stored = _plan_rows(seq_id)
    db_wfseq.set_item_result_doc_id(stored[0]["id"], DOC["T2"])
    _save_candidates(env, _candidates(env, plan, "replace_after"), "replace_after")
    assert _pairs(_plan_rows(seq_id)) == _PAIRED_ROWS


# Another plan of the same root. Card ids are per plan: its L#1 is not this plan's L#1.
_WP_B = f"{_GROUP}.0009-WP"
_WP_B_SQL = (
    "INSERT OR IGNORE INTO documents(doc_id, project_id, module, group_id, type_code, seq, "
    "title, status, doc_review_status, created_at, updated_at) VALUES "
    f"('{_WP_B}', '{_PROJECT}', 'default', '{_GROUP}', 'WP', 9, 'plan b', 'open', 'approved', "
    "datetime('now'), datetime('now'))"
)


def _foreign_l1_behind_started_ds(env: Env):
    """This plan's DS of L#1 ran, and right behind it sits another plan's pending L row that
    names that plan's own card L#1 (this plan's L#1 result row is not there)."""
    seq_id, plan = _paired_saved(env)
    stored = _plan_rows(seq_id)
    db_wfseq.set_item_result_doc_id(stored[0]["id"], DOC["T2"])
    env.store._execute(_WP_B_SQL)
    env.store._execute(
        "UPDATE workflow_sequence_items SET source_doc_id = ?, source_revision_no = ?, "
        "note = ?, provider_id = ?, pre_instruction_text = ? WHERE id = ?",
        [_WP_B, 3, "plan b note", "p2", "plan b brief", stored[1]["id"]],
    )
    return seq_id, plan, stored[1]["id"]


def _foreign_state(seq_id: int) -> list[tuple]:
    return [
        (r["type"], r["source_doc_id"], r["source_revision_no"], r["source_wp_card_id"],
         r["note"], r["provider_id"], r["pre_instruction_text"], r["result_doc_id"])
        for r in _items(seq_id) if r["source_doc_id"] == _WP_B
    ]


def test_another_plans_row_with_the_same_card_id_is_not_a_started_ds_rows_result(env):
    seq_id, plan, foreign_id = _foreign_l1_behind_started_ds(env)
    items = _items(seq_id)
    classification = cards.classify_rows(wp_doc=env.doc(), plan=plan, items=items)
    assert classification["started_card_ids"] == ["L#1"]
    found = order.started_card_rows(items, classification)
    assert found["L#1"]["report"] is None
    layout = order.reorder_layout(plan, items, _WP, classification)
    linked = {
        (picked.get(slot) or {}).get("id")
        for picked in layout["cards"].values() for slot in ("row", "report")
    }
    assert foreign_id not in linked
    assert foreign_id not in {row["id"] for row in layout["removed"]}
    assert foreign_id in {e["row"]["id"] for e in layout["entries"] if e["kind"] == "foreign"}


@pytest.mark.parametrize("change", [False, True], ids=["keep", "change"])
def test_apply_leaves_another_plans_row_with_the_same_card_id_as_it_was(env, change):
    """keep / change map a plan step to the rows of its card and write the plan's values
    onto them: another plan's row must never be one of them, whatever apply answers."""
    seq_id, plan, _foreign_id = _foreign_l1_behind_started_ds(env)
    before = _foreign_state(seq_id)
    assert len(before) == 1
    try:
        _apply(env, plan, change=change)
    except (wpa.ApplyConflict, order.PlanOrderViolation, order.PlanOrderBlocked):
        pass
    assert _foreign_state(seq_id) == before


@pytest.mark.parametrize("mode", [None, "use", "omit"])
def test_paired_row_types_are_the_rows_plan_to_rows_makes_whatever_the_instruction_mode(env, mode):
    """plan_to_rows does not read instruction_mode: an omitted DS (or T) step is poured like
    any other, so the card's row_types keep it and the pour saves."""
    body = _paired_body()
    if mode is not None:
        for step in body["steps"]:
            if wp.instruction_mode_allowed(step):
                step["instruction_mode"] = mode
    seq_id, plan = _paired(env, body)
    rows, _dropped, _uid = wpseq.plan_to_rows(plan, _WP, 1)
    made: dict[str, list[str]] = {}
    for row in rows:
        made.setdefault(row["source_wp_card_id"], []).append(row["type"])
    assert made == {card["card_id"]: list(card["row_types"]) for card in order.plan_cards(plan)}
    assert {card["card_id"]: card["type"] for card in order.plan_cards(plan)}["L#1"] == "L"

    _save_candidates(env, _candidates(env, plan, "replace_after"), "replace_after")
    assert _pairs(_plan_rows(seq_id)) == _PAIRED_ROWS


# ══ O0 legacy history — G9 delete and re-add ════════════════════════════════

def _g9(env: Env, *, evidence: str, deleted: bool = True):
    env.revisions[0] = _legacy({"T": 2})
    env.revisions[1] = _legacy({"T": 1 if deleted else 2})
    plan = env.plan(2, _legacy({"T": 2}))  # r2: a new T#2 (deleted=True) or the same one
    seq_id = _seed_rows([
        {"type": "T", "source": _WP, "rev": 0, "result": DOC["T1"], "label": "T"},
        {"type": "TR", "source": _WP, "rev": 0, "result": DOC["TR1"], "label": "TR"},
        {"type": "T", "source": _WP, "rev": 0, "result": DOC["T2"], "label": "T"},
        {"type": "TR", "source": _WP, "rev": 0, "label": "TR"},
    ])
    if evidence == "E1":
        env.provenance[DOC["T1"]] = {"source_wp_doc_id": _WP, "source_wp_revision_no": 0,
                                     "source_wp_step_key": "T#1"}
        env.provenance[DOC["T2"]] = {"source_wp_doc_id": _WP, "source_wp_revision_no": 0,
                                     "source_wp_step_key": "T#2"}
    return seq_id, plan


@pytest.mark.parametrize("evidence", ["E1", "E3"])
@pytest.mark.parametrize("path", ["replace_after", "apply_change"])
def test_g9_deleted_and_re_added_card_is_poured_and_the_old_row_retired(env, evidence, path):
    seq_id, plan = _g9(env, evidence=evidence)
    before = _full(seq_id)
    classification = cards.classify_rows(wp_doc=env.doc(), plan=plan, items=_items(seq_id))
    assert classification["started_card_ids"] == ["T#1"]
    assert classification["rows"][before[2]["id"]]["value"] == "retired:r0:T#2"

    preview = _preview(env, plan)
    codes = _codes(preview["warnings"])
    assert "retired_plan_rows" in codes
    assert not {"started_card_removed", "order_conflicts_started"} & set(codes)
    candidate = _candidates(env, plan, "replace_after")
    assert candidate["blockers"] == []
    assert [x["card_id"] for x in next(
        n for n in candidate["notifications"] if n["code"] == "steps_already_done")["items"]] == ["T#1"]

    if path == "replace_after":
        _save_candidates(env, candidate, "replace_after")
    else:
        _apply(env, plan, change=True)
    after = _items(seq_id)
    # The old T#2 rows (started T + its protected TR) stay exactly where they were.
    assert [_ident(r) for r in after[:4]] == [_ident(r) for r in before]
    assert [r["source_wp_card_id"] for r in after[:4]] == [
        "T#1", "T#1", "retired:r0:T#2", "retired:r0:T#2",
    ]
    # The new T#2 is poured at its card position, after the started T#1.
    assert _cards_of(seq_id)[4:] == [("T", "T#2"), ("TR", "T#2")]
    _monotonic(seq_id)


def test_g9_control_without_deletion_links_the_started_row(env):
    seq_id, plan = _g9(env, evidence="E3", deleted=False)
    classification = cards.classify_rows(wp_doc=env.doc(), plan=plan, items=_items(seq_id))
    assert classification["started_card_ids"] == ["T#1", "T#2"]
    candidate = _candidates(env, plan, "replace_after")
    assert [r for r in candidate["rows"] if r.get("poured")] == []


def test_g9_moving_the_new_card_ahead_of_the_started_one_is_refused_behind_is_allowed(env):
    seq_id, _plan = _g9(env, evidence="E1")
    _save_candidates(env, _candidates(env, _plan, "replace_after"), "replace_after")
    snapshot = _full(seq_id)
    # r3 is the first new-format revision: the new T#2 (legacy id "T#2") moved to the front.
    ahead = env.plan(3, _body([("T", "T#2"), ("T", "T#1")], values=False))
    assert _preview(env, ahead)["apply_blockers"]["change_workflow"] == "order_conflicts_started"
    assert _candidates(env, ahead, "replace_after")["blockers"] == ["order_conflicts_started"]
    assert _full(seq_id) == snapshot
    # r4: a new N card between them — the new T#2 moves behind it, after the started T#1.
    behind = env.plan(4, _body([("T", "T#1"), ("N", "c_n"), ("T", "T#2")], values=False))
    assert _preview(env, behind)["apply_blockers"]["change_workflow"] is None
    _apply(env, behind, change=True)
    assert _cards_of(seq_id)[4:] == [("N", "c_n"), ("NR", "c_n"), ("T", "T#2"), ("TR", "T#2")]


# ══ O6 — the materializer's step key ════════════════════════════════════════

def _descriptor(monkeypatch, env: Env, items: list[dict], head: dict):
    from modules.flow_gate.documents.routers import documents as documents_router

    monkeypatch.setattr(documents_router.document_service, "get_document", lambda doc_id: {
        **env.doc(), "type_code": "WP",
    })
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda sequence_id: items)
    return documents_router._work_plan_instruction_descriptor(1, head)


def test_o6_step_key_follows_the_card_in_its_revision(env, monkeypatch):
    env.plan(0, _body([("T", "c_a"), ("T", "c_b")], values=False))
    env.plan(1, _body([("T", "c_a"), ("N", "c_n"), ("T", "c_b")], values=False))
    # G5: c_a (rev0) finished; c_b poured from rev1 is the only T row of rev1 — counting
    # same-revision rows would call it T#1. Its card says T#2.
    items = [
        {"id": 1, "item_seq": 1, "type": "T", "source_doc_id": _WP, "source_revision_no": 0,
         "source_wp_card_id": "c_a", "result_doc_id": DOC["T1"]},
        {"id": 2, "item_seq": 2, "type": "N", "source_doc_id": _WP, "source_revision_no": 1,
         "source_wp_card_id": "c_n"},
        {"id": 3, "item_seq": 3, "type": "T", "source_doc_id": _WP, "source_revision_no": 1,
         "source_wp_card_id": "c_b"},
    ]
    found = _descriptor(monkeypatch, env, items, items[2])
    assert found["source_wp_step_key"] == "T#2"
    assert found["idempotency_key"] == f"{_WP}:1:T#2"
    # A legacy row (no card id) keeps the old same-revision counting rule.
    legacy = {**items[2], "source_wp_card_id": None}
    assert _descriptor(monkeypatch, env, [items[0], items[1], legacy], legacy)[
        "source_wp_step_key"] == "T#1"


def test_o6_a_card_row_whose_key_cannot_be_read_gets_no_descriptor(env, monkeypatch):
    from modules.flow_gate.documents.routers import documents as documents_router

    env.plan(0, _body([("T", "c_a"), ("T", "c_b")], values=False))
    env.plan(1, _body([("T", "c_a"), ("N", "c_n"), ("T", "c_b")], values=False))
    other = {"id": 1, "item_seq": 1, "type": "T", "source_doc_id": _WP, "source_revision_no": 1,
             "source_wp_card_id": None}
    row = {"id": 3, "item_seq": 3, "type": "T", "source_doc_id": _WP, "source_revision_no": 1,
           "source_wp_card_id": "c_b"}

    def refused(head: dict) -> None:
        # Counting the same-revision T rows would name this row T#2 (another card's key);
        # a row that records its card never falls back to it (NR0003 O6).
        with pytest.raises(documents_router.NextApprovedError) as raised:
            _descriptor(monkeypatch, env, [other, head], head)
        assert raised.value.status_code == 409

    refused({**row, "source_revision_no": 7})  # no snapshot of that revision
    refused({**row, "source_wp_card_id": "c_gone"})  # card not in the snapshot
    refused({**row, "source_wp_card_id": "c_n"})  # the card is of another type
    refused({**row, "source_wp_card_id": "retired:unresolved:9"})
    refused({**row, "source_wp_card_id": "retired:r0:T#2"})  # marker of another revision

    def broken(doc):
        def load(revision_no):
            raise OSError("snapshot unreadable")
        return load

    monkeypatch.setattr(cards, "stored_revision_loader", broken)
    refused(row)
    # A retired row names its key in its own marker; no snapshot read is needed.
    retired = {**row, "source_wp_card_id": "retired:r1:T#2"}
    found = _descriptor(monkeypatch, env, [other, retired], retired)
    assert found["source_wp_step_key"] == "T#2"
    assert found["idempotency_key"] == f"{_WP}:1:T#2"
    # Only a row without a card id keeps the counting rule.
    legacy = {**row, "source_wp_card_id": None}
    assert _descriptor(monkeypatch, env, [other, legacy], legacy)["source_wp_step_key"] == "T#2"


def test_o6_legacy_snapshot_names_cards_by_key(env):
    env.revisions[0] = _legacy({"T": 2})
    assert cards.step_key_for_card(env.doc(), 0, "T#2", "T", revision_loader=env.revisions.get) == "T#2"
    assert cards.step_key_for_card(env.doc(), 0, "T#3", "T", revision_loader=env.revisions.get) is None
    assert cards.step_key_for_card(env.doc(), 0, "T#2", "N", revision_loader=env.revisions.get) is None
    assert cards.step_key_for_card(env.doc(), 0, "retired:r0:T#2", "T",
                                   revision_loader=env.revisions.get) is None


# ══ CAS / execution ═════════════════════════════════════════════════════════

def test_stale_tag_refuses_the_reorder_and_change_rebases_the_live_target(env, monkeypatch):
    from modules.flow_gate.services import ai_invoke_service

    seq_id = _abc(env)
    plan = env.plan(1, _body([("T", "c_a"), ("T", "c_c"), ("T", "c_b")]))
    before = _full(seq_id)
    stale = _tag(seq_id)
    db_wfseq.set_item_result_doc_id(before[2]["id"], DOC["T2"])  # the sequence moved on
    with pytest.raises(wpa.ApplyConflict) as raised:
        _apply(env, plan, change=True, tag=stale)
    assert raised.value.code == "workflow_changed"
    db_wfseq.set_item_result_doc_id(before[2]["id"], None)
    assert [_ident(r) for r in _items(seq_id)] == [_ident(r) for r in before]

    rebased = []
    monkeypatch.setattr(ai_invoke_service, "rebase_active_to_end",
                        lambda group_id, doc_ref: rebased.append((group_id, doc_ref)))
    _apply(env, plan, change=True)
    assert rebased == [(_GROUP, _ROOT)]


def test_shared_save_conflict_inside_change_apply_is_a_409_and_writes_nothing(env, monkeypatch):
    seq_id = _abc(env)
    before = _full(seq_id)
    plan = env.plan(1, _body([("T", "c_a"), ("T", "c_c"), ("T", "c_b")]))
    real = wds.edit_workflow_pending

    def racing(*args, **kwargs):
        # Somebody saved between the apply's own tag check and the shared save.
        raise wds.SequenceChanged(_ROOT, kwargs.get("expected_workflow_tag"), "other")

    monkeypatch.setattr(wds, "edit_workflow_pending", racing)
    with pytest.raises(wpa.ApplyConflict) as raised:
        _apply(env, plan, change=True)
    assert raised.value.code == "workflow_changed"
    assert _full(seq_id) == before
    monkeypatch.setattr(wds, "edit_workflow_pending", real)


# ══ route contracts ═════════════════════════════════════════════════════════

def test_patch_route_answers_order_refusals_with_409(env, monkeypatch):
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import workflow_decision_routes as routes
    from routers.main import app

    monkeypatch.setattr(routes, "verify_bearer", lambda request: {"_is_user_jwt": True, "issued_to": "usr"})
    monkeypatch.setattr(routes, "_active_ai_run_response_for_user", lambda d, a: None)
    seq_id, plan = _g4(env)
    client = TestClient(app, raise_server_exceptions=False)
    path = "/flowgate/api/v1/workflow/sequence"
    body = {"doc_id": _ROOT, "items": [{"type": "T", "label": "T", "source_doc_id": _WP,
                                        "source_revision_no": 1, "source_wp_card_id": "c_b"}],
            "expected_plan": {"wp_doc_id": _WP, "wp_revision_no": 1}}
    resp = client.patch(path, json=body)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "order_conflicts_started"
    assert [c["card_id"] for c in resp.json()["cards"]] == ["c_a"]

    env.plan(2, _body([("T", "c_a"), ("T", "c_b")]))
    body["expected_plan"] = {"wp_doc_id": _WP, "wp_revision_no": 2, "mode": "append"}
    resp = client.patch(path, json=body)
    assert resp.status_code == 409 and resp.json()["error"] == "plan_rows_pending"

    body["expected_plan"] = {"wp_doc_id": _WP, "wp_revision_no": 2}
    body["items"] = [{"type": "T", "label": "T", "source_doc_id": _WP, "source_revision_no": 2,
                      "source_wp_card_id": "c_a"}]  # a second row for the started card
    resp = client.patch(path, json=body)
    assert resp.status_code == 409 and resp.json()["error"] == "plan_order_violation"
    assert resp.json()["detail"]["reason"] in ("order", "row_without_card")


def test_apply_router_answers_order_refusals_with_the_preview_copy(monkeypatch):
    from modules.flow_gate.documents.routers import work_plan as router

    monkeypatch.setattr(router, "_load_doc", lambda doc_id: {"doc_id": doc_id, "target_id": _ROOT})
    monkeypatch.setattr(router, "_plan_path", lambda doc: "plan.json")
    monkeypatch.setattr(router.wp, "load_body", lambda *a, **k: {"steps": []})
    monkeypatch.setattr(router.db_docs, "get_by_id", lambda doc_id: {"doc_id": doc_id})
    monkeypatch.setattr(router, "_providers", lambda project_id: [])

    def refuse(**kwargs):
        raise wpa.ApplyConflict("order_conflicts_started", {
            "code": "order_conflicts_started", "cards": [{"card_id": "c_a", "key": "T#2"}],
        })

    monkeypatch.setattr(router.wpa, "apply", refuse)
    response = router._apply_sync(_WP, router.WorkPlanApply(
        change_workflow=True, workflow_tag="t", wp_revision_no=1,
    ), "en", "tester")
    assert response.status_code == 409
    payload = json.loads(response.body)
    assert payload["code"] == "order_conflicts_started"
    assert payload["message"] == wpa._COPY["en"]["order_conflicts_started"].format(count=1)
