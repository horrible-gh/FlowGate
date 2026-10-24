from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from modules.flow_gate.services.ai_invoke import chain


DOC = "flowgate.default.0415.0001-R"
GROUP = "flowgate.default.0415"


class _FakeRequest:
    headers = {"x-locale": "ko"}
    base_url = "http://flowgate.test/"


def _items(last=10, head=2):
    return [
        {"item_seq": n, "result_doc_id": "done" if n < head else None,
         "result_doc_review_status": "approved" if n < head else None}
        for n in range(1, last + 1)
    ]


def _sequence(monkeypatch, items):
    monkeypatch.setattr(chain.db_wfseq, "get_sequence_for_member_doc", lambda _doc: {"id": 1})
    monkeypatch.setattr(chain.db_wfseq, "get_sequence_items", lambda _id: items)


def test_g1_g2_to_end_resolves_latest_sequence_on_each_hop(monkeypatch):
    _sequence(monkeypatch, _items(last=10, head=2))
    assert chain._resolve_continuation_target(DOC, 1, to_end=True) == 10

    # Human/worker edits use the same sequence SSOT; changing its current rows is enough.
    _sequence(monkeypatch, _items(last=14, head=2))
    assert chain._resolve_continuation_target(DOC, 10, to_end=True) == 14


def test_g3_explicit_target_never_rebases(monkeypatch):
    monkeypatch.setattr(
        chain.db_wfseq, "get_sequence_for_member_doc",
        lambda _doc: pytest.fail("explicit target must not query the latest max"),
    )
    assert chain._resolve_continuation_target(DOC, 3, to_end=False) == 3


def test_g4_rebase_before_head_is_rejected(monkeypatch):
    _sequence(monkeypatch, _items(last=3, head=4))
    monkeypatch.setattr(chain, "_next_incomplete_item_seq", lambda _doc: 4)
    with pytest.raises(HTTPException) as exc:
        chain._resolve_continuation_target(DOC, None, to_end=True)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "sequence_changed"


def test_g5_g6_paused_and_handoff_keep_lazy_to_end_marker(monkeypatch):
    run = {
        "doc_ref": DOC, "target_to_end": True, "continuation_target_seq": 3,
        "mode": "continuous", "issued_to": "user", "api_base_url": "http://api",
    }
    bundle = chain._handoff_bundle({"doc_ref": DOC, "target_seq": 3}, run)
    assert bundle["to_end"] is True
    assert bundle["target_seq"] is None


def test_g6_g9_spawn_rechecks_latest_and_carries_intent(monkeypatch):
    """Unit-level: _spawn_auto_resume's OWN contract given an already-to_end-tagged pending
    dict. This does not prove the real hop-to-hop wiring ever produces that dict — see
    test_engine_handoff_carries_to_end_from_the_live_run below for that: it drives
    inbox_routes._continuation_self_chain end to end and lets the real is_active_run_to_end
    (not a hand-fed key) decide whether the queued payload gets a to_end at all."""
    _sequence(monkeypatch, _items(last=10, head=2))
    advanced = {}
    started = {}

    def advance_workflow(**kwargs):
        advanced.update(kwargs)
        return {
            "token": "raw", "token_id": "tid", "scratch_dir": "scratch",
            "mention": "mention",
        }

    def start_run(**kwargs):
        started.update(kwargs)
        kwargs["issue_builder"]("run-id")
        return {"run_id": "run-id"}

    from modules.flow_gate.services import workflow_decision_service
    monkeypatch.setattr(workflow_decision_service, "advance_workflow", advance_workflow)
    monkeypatch.setattr(chain, "_svc", lambda: SimpleNamespace(start_run=start_run))

    chain._spawn_auto_resume(GROUP, {
        "doc_ref": DOC, "target_seq": None, "to_end": True,
        "review_mode": False, "instruction_mode": "auto_approved",
        "locale": "ko", "issued_to": "user", "api_base_url": "http://api",
    })

    assert advanced["continuation_target_seq"] == 10
    assert started["continuation_target_seq"] == 10
    assert started["continuation_to_end"] is True


def test_g7_eager_rebase_is_idempotent(monkeypatch):
    _sequence(monkeypatch, _items(last=10, head=2))
    run = {
        "mode": "continuous", "target_to_end": True, "doc_ref": DOC,
        "continuation_target_seq": 1,
    }
    monkeypatch.setattr(chain, "_svc", lambda: SimpleNamespace(
        _active_run_for_group=lambda _group: run,
    ))
    assert chain.rebase_active_to_end(GROUP, DOC) == 10
    assert chain.rebase_active_to_end(GROUP, DOC) == 10
    assert run["continuation_target_seq"] == 10


def test_g3_eager_rebase_ignores_explicit_chain(monkeypatch):
    run = {"mode": "continuous", "target_to_end": False, "doc_ref": DOC}
    monkeypatch.setattr(chain, "_svc", lambda: SimpleNamespace(
        _active_run_for_group=lambda _group: run,
    ))
    assert chain.rebase_active_to_end(GROUP, DOC) is None


def test_engine_handoff_carries_to_end_from_the_live_run(monkeypatch):
    """The rejection's exact finding: the ORDINARY hop-to-hop path (inbox_routes
    ._continuation_self_chain -> _hand_off_to_engine -> request_auto_resume), driven
    end to end, with nobody hand-feeding a to_end key anywhere in the test's own inputs.

    The consumed token carries only a concrete (stale) continuation_target_seq=3, exactly
    like a real per-hop token always does. The only source of "this is a run-to-end chain"
    is the live active run's target_to_end, discovered through the real is_active_run_to_end
    (backed by the same chain._svc() the run registry itself uses) — not a pre-filled dict.
    The sequence has since grown to 10 items (a WP/human/worker expansion), so a correct
    fix must both (a) queue the NEXT hop with to_end=True, so the re-spawned hop keeps
    re-resolving, and (b) NOT treat the just-completed item_seq 3 as "target reached" just
    because it matches the token's stale number.
    """
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.db import ai_invoke_paused_chains
    from modules.flow_gate.db import workflow_sequences
    from modules.flow_gate.services import ai_invoke_service

    _sequence(monkeypatch, _items(last=10, head=4))
    run = {
        "run_id": "airun-hop-3", "group_id": GROUP, "doc_ref": DOC,
        "mode": "continuous", "target_to_end": True,
    }
    queue_resume = MagicMock()

    monkeypatch.setattr(chain, "_svc", lambda: SimpleNamespace(
        _active_run_for_group=lambda _group: run,
    ))
    monkeypatch.setattr(
        workflow_sequences, "get_item_by_result_doc_id",
        lambda _doc_id: {"item_seq": 3, "type": "M"},
    )
    monkeypatch.setattr(ai_invoke_paused_chains, "get_by_group", lambda _group_id: None)
    monkeypatch.setattr(
        ai_invoke_service, "get_active_status",
        lambda _group_id: {"run_id": "airun-hop-3"},
    )
    monkeypatch.setattr(ai_invoke_service, "request_auto_resume", queue_resume)
    monkeypatch.setattr(
        inbox_routes, "_inbox_api_base", lambda _request: "http://flowgate.test/api",
    )

    envelope = inbox_routes._continuation_self_chain(
        request=_FakeRequest(),
        token_rec={
            "doc_ref": DOC,
            "issued_to": "usr-admin",
            "group_id": GROUP,
            "continuation_target_seq": 3,
            "continuation_review_mode": 0,
            "continuation_locale": "ko",
        },
        project="flowgate",
        canonical_doc_id="flowgate.default.0415.0009-M",
        # M is auto-completed: keeps this test on the hop-boundary/target-rebase logic
        # rather than the independent document-approval permission path.
        doc_type="M",
    )

    assert envelope["continuation_pending"] is True
    assert "continuation_done" not in envelope
    assert envelope["continuation_target_seq"] == 10

    queue_resume.assert_called_once()
    payload = queue_resume.call_args.args[1]
    assert payload["to_end"] is True
    assert payload["target_seq"] == 10
