"""flowgate.default.0448 T0005 — the provider a continuous hop actually runs.

B0001 ("연계는 쳐 되어있지도 않고") and NR0003 §4: an ordinary provider pick in the browser
used to arrive as `provider_pinned=true`, and `start_run()` applies a pin BEFORE the sequence's
stored provider — so a work plan's per-step provider was cancelled by a plain default choice.
0448 fixes the client so an ordinary pick sends `provider_id` alone; the server's tier order was
already right (T0005 §5-3/§5-4 "이미 맞는 경계는 불필요하게 재설계하지 말고 실제 endpoint 회귀의
앵커로 사용하라"), and this file is that anchor.

Tiers asserted here, in order:

    item_seq override > explicit force-all > active stored sequence provider
        > unpinned request selection > mode-aware doc-type assignment > project default chain

Covers T0005 §7 rows 1, 3, 5, 6, 7, 8 and 9 on the server side. Row 2 (the ten selection
surfaces) and rows 4 (refresh/re-entry) are client-side and live in
client/tests/main/providerSelectionSurfaces.0448.spec.ts and aiProviderStore.spec.ts.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("ALLOWED_ORIGIN", "")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import ai_invoke_routes  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402

GROUP = "flowgate.default.0448"
ROOT = "flowgate.default.0448.0001-B"
API_BASE = "http://127.0.0.1:1/flowgate/api/v1"

# N/NR and T/TR each store a provider of their own — the shape §7-1 asks about.
ITEMS = [
    {"id": 1, "item_seq": 1, "type": "N", "label": "조사지시",
     "provider_id": "aip_n", "provider_display_name": "N Provider",
     "result_doc_id": None, "result_doc_review_status": None},
    {"id": 2, "item_seq": 2, "type": "NR", "label": "조사레포트",
     "provider_id": "aip_nr", "provider_display_name": "NR Provider",
     "result_doc_id": None, "result_doc_review_status": None},
    {"id": 3, "item_seq": 3, "type": "T", "label": "작업지시",
     "provider_id": "aip_t", "provider_display_name": "T Provider",
     "result_doc_id": None, "result_doc_review_status": None},
    {"id": 4, "item_seq": 4, "type": "TR", "label": "작업레포트",
     "provider_id": "aip_tr", "provider_display_name": "TR Provider",
     "result_doc_id": None, "result_doc_review_status": None},
]

ALL_PROVIDER_IDS = ["aip_header", "aip_n", "aip_nr", "aip_t", "aip_tr", "aip_step", "aip_opus"]


def provider(pid, enabled=True):
    return {
        "id": pid, "name": pid.replace("aip_", "").upper(), "exec_type": "cli", "kind": "claude",
        "enabled": enabled, "cli_command": "unused", "api_base_url": None,
        "api_model": None, "api_key_set": False, "api_key_hint": None,
    }


class FakeWfseq:
    """Sequence rows plus which row is the current head — the two facts every provider tier
    in start_run() reads (get_sequence_for_member_doc / get_effective_head /
    get_sequence_items)."""

    def __init__(self, items, head_item_seq=1):
        self.items = [dict(row) for row in items]
        self.head_item_seq = head_item_seq

    def get_sequence_for_member_doc(self, doc_id):
        return {"id": 448}

    def get_sequence_by_doc_id(self, doc_id):
        return {"id": 448}

    def get_sequence_items(self, seq_id):
        return [dict(row) for row in self.items]

    def get_effective_head(self, seq_id):
        return next((dict(r) for r in self.items if r["item_seq"] == self.head_item_seq), None)


@pytest.fixture
def env(monkeypatch, tmp_path):
    wfseq = FakeWfseq(ITEMS)
    chain = {"providers": [provider(pid) for pid in ALL_PROVIDER_IDS],
             "source": "system", "registered_count": len(ALL_PROVIDER_IDS)}
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda group_id: 4)
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", lambda group_id: [])
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: {"doc_id": doc_id, "branch": "main"})
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_name": "flowgate"})
    monkeypatch.setattr(svc.db_git, "get_config", lambda pid: None)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", wfseq.get_sequence_for_member_doc)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", wfseq.get_sequence_by_doc_id)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", wfseq.get_sequence_items)
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", wfseq.get_effective_head)
    monkeypatch.setattr(svc.ai_settings_service, "resolve_effective", lambda pid: {"ok": True, **chain})
    monkeypatch.setattr(svc.token_service, "issue", lambda **kw: {
        "raw_token": "tok_0448", "token_id": "tok_0448",
        "expires_at": "2026-09-01T00:00:00+00:00", "scratch_dir": str(tmp_path / "scratch"),
    })
    src_root = tmp_path / "src"
    src_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(svc.storage_paths, "resolve_project_src_root",
                        lambda pid, branch, *, group_id: src_root)
    monkeypatch.setattr(svc, "_create_scratch", lambda pid, run_id: src_root)
    monkeypatch.setattr(svc, "_git_status_paths", lambda root: set())
    monkeypatch.setattr(svc, "_cleanup_retained_scratches", lambda pid: None)
    # Capture the finalized execution chain without starting a worker process.
    worker_chains = []
    monkeypatch.setattr(
        svc, "_worker",
        lambda run, chain_, prompt: worker_chains.append([p["id"] for p in chain_]),
    )
    monkeypatch.setattr(svc, "_broadcast", lambda run, event_type, payload: None)
    monkeypatch.setattr(svc, "_runs", {})
    return {"wfseq": wfseq, "chain": chain, "worker_chains": worker_chains}


def start(env, **over):
    """One continuous hop. Defaults mirror a ContinuousWorkDialog run with an ordinary
    (unpinned) header selection of aip_header."""
    values = dict(
        project_id="flowgate", module="default", group_id=GROUP, doc_ref=ROOT,
        action_scope="new", mode="continuous", continuation_target_seq=4,
        continuation_review_mode=False, continuation_instruction_mode="auto_approved",
        continuation_locale="ko", issued_to="usr_admin", api_base_url=API_BASE,
        mention_builder=lambda raw, scratch: "work",
        provider_id="aip_header",
    )
    values.update(over)
    result = svc.start_run(**values)
    _release(result["run_id"])
    return result


def _release(run_id):
    """Free the group lease so the next start in the same test can be admitted."""
    run = svc.get_run_record(run_id)
    if run:
        run["status"] = "finished"
    try:
        svc.db_group_ai_leases.release(GROUP, run_id)
    except Exception:  # noqa: BLE001 — lease bookkeeping is not what these tests assert
        pass


# ── §7-1 / §7-5: an ordinary (unpinned) selection never displaces the stored provider ──

def test_unpinned_selection_loses_to_the_stored_step_provider(env):
    """§7-5: plan value == sequence value, so no override map exists — the stored provider
    still has to be the one that runs."""
    result = start(env, continuation_provider_overrides=None)
    assert result["provider"]["id"] == "aip_nr"


def test_empty_override_map_is_the_same_as_none(env):
    """§5-2: the client omits an empty map; an older caller sending `{}` must not be read as
    'the user cleared the stored provider'."""
    assert start(env, continuation_provider_overrides={})["provider"]["id"] == "aip_nr"


@pytest.mark.parametrize("head_item_seq,mode,expected", [
    # auto_approved folds an instruction head onto its paired report — the worker is NR/TR.
    (1, "auto_approved", "aip_nr"),
    (3, "auto_approved", "aip_tr"),
    # ai_direct runs the instruction itself, so the N/T row's own provider is the worker's.
    (1, "ai_direct", "aip_n"),
    (3, "ai_direct", "aip_t"),
])
def test_each_hop_runs_its_own_stored_provider(env, head_item_seq, mode, expected):
    """§7-1: N/NR and T/TR store different providers, and each worker gets its own — the
    ordinary header selection (aip_header) is the default for rows that stored nothing, not a
    replacement for rows that did."""
    env["wfseq"].head_item_seq = head_item_seq
    result = start(env, continuation_instruction_mode=mode, continuation_provider_overrides=None)
    assert result["provider"]["id"] == expected


def test_selection_is_used_only_where_no_provider_was_stored(env):
    """The other half of the same contract: strip the stored values and the unpinned selection
    becomes the hop's provider. Without this, 'stored wins' could be passing because the
    request's provider_id is ignored outright."""
    env["wfseq"].items = [dict(row, provider_id=None, provider_display_name=None) for row in ITEMS]
    assert start(env, continuation_provider_overrides=None)["provider"]["id"] == "aip_header"


# ── §7-3: force-all is explicit, and a step override still outranks it ────────

def test_explicit_force_all_outranks_the_stored_provider(env):
    """The tier 0448 keeps for pause/resume and non-browser callers. Reaching it now requires
    provider_pinned, which only aiProviderStore.forceProviderForAllSteps can produce."""
    result = start(env, provider_id="aip_header", provider_pinned=True,
                   continuation_provider_overrides=None)
    assert result["provider"]["id"] == "aip_header"
    assert result["selected_provider_source"] == "force_all"
    assert result["fallback_allowed"] is False
    assert env["worker_chains"][-1] == ["aip_header"]


def test_step_override_outranks_an_explicit_force_all(env):
    """§5-5: '단계 override는 명시 force-all보다도 우선해야 한다.' The head is N(1); under
    auto_approved the worker row is NR(2), so the map is keyed by 2."""
    result = start(env, provider_id="aip_header", provider_pinned=True,
                   continuation_provider_overrides={"2": "aip_step"})
    assert result["provider"]["id"] == "aip_step"
    assert result["selected_provider_source"] == "step_override"
    assert result["fallback_allowed"] is False
    assert env["worker_chains"][-1] == ["aip_step"]


def test_step_override_outranks_the_stored_provider_and_the_selection(env):
    """§7-6: plan value != sequence value becomes this run's explicit item_seq override."""
    result = start(env, continuation_provider_overrides={"2": "aip_step"})
    assert result["provider"]["id"] == "aip_step"
    assert result["selected_provider_source"] == "step_override"
    assert result["fallback_allowed"] is False
    assert env["worker_chains"][-1] == ["aip_step"]


def test_an_override_for_another_hop_is_ignored(env):
    """The map is per item_seq, not 'any entry wins' — a value written for a later step must
    leave this hop on its own stored provider."""
    result = start(env, continuation_provider_overrides={"4": "aip_step"})
    assert result["provider"]["id"] == "aip_nr"


# ── §7-7: an unregistered / inactive stored provider ──────────────────────────

def test_inactive_stored_provider_falls_back_and_warns_once(env, caplog):
    env["chain"]["providers"] = [provider("aip_header")]
    with caplog.at_level(logging.WARNING):
        result = start(env, continuation_provider_overrides=None)
    expected = (
        f"continuation hop provider fallback: aip_nr not active for {ROOT} "
        "item_seq 2, falling back to aip_header"
    )
    # The request's own (unpinned) selection is what it degrades to, and the snapshot id is
    # named in the log so the screen's "지금 쓸 수 없음" badge and the server agree on WHY.
    assert result["provider"]["id"] == "aip_header"
    assert result["selected_provider_source"] == "request"
    assert result["fallback_allowed"] is False
    assert svc.get_run_record(result["run_id"])["fallback_history"] == []
    assert env["worker_chains"][-1] == ["aip_header"]
    assert caplog.messages.count(expected) == 1


def test_inactive_document_type_assignment_uses_project_default_without_execution_fallback(
        env, monkeypatch):
    env["wfseq"].items = [
        dict(row, provider_id=None, provider_display_name=None) for row in ITEMS
    ]
    env["chain"]["providers"] = [provider("aip_header"), provider("aip_nr")]
    monkeypatch.setattr(svc, "_resolve_continuation_hop_provider", lambda *a, **k: "aip_t")

    result = start(env, provider_id=None, continuation_provider_overrides=None)
    run = svc.get_run_record(result["run_id"])

    assert result["provider"]["id"] == "aip_header"
    assert result["selected_provider_source"] == "project_default"
    assert result["fallback_allowed"] is True
    assert run["fallback_history"] == []
    assert env["worker_chains"][-1] == ["aip_header", "aip_nr"]


def test_active_document_type_assignment_is_a_single_audited_chain(env, monkeypatch):
    env["wfseq"].items = [
        dict(row, provider_id=None, provider_display_name=None) for row in ITEMS
    ]
    monkeypatch.setattr(svc, "_resolve_continuation_hop_provider", lambda *a, **k: "aip_t")

    result = start(env, provider_id=None, continuation_provider_overrides=None)

    assert result["provider"]["id"] == "aip_t"
    assert result["selected_provider_source"] == "document_type"
    assert result["fallback_allowed"] is False
    assert env["worker_chains"][-1] == ["aip_t"]


def test_document_review_loop_provider_is_a_single_audited_chain(env, monkeypatch):
    loop = {
        "review_count": 1,
        "reviewer_provider_id": "aip_step",
        "review_criteria": "document_type_default",
        "rework_provider_id": "aip_t",
        "rework_timeout_sec": 1800,
        "rework_message": "fix findings",
        "failure_restart_max_attempts": 0,
        "total_timeout_sec": 3600,
    }
    monkeypatch.setattr(svc, "compute_review_baseline", lambda _doc_ref: {
        "review_baseline_id": 1, "baseline_revision_no": 1, "starts_with_rework": False,
    })
    monkeypatch.setattr(svc, "_insert_document_review_loop", lambda _run: None)
    monkeypatch.setattr(svc, "document_review_loop_payload", lambda _run: None)

    result = start(
        env,
        action_scope="review",
        mode="single",
        continuation_target_seq=None,
        continuation_review_mode=False,
        provider_id=None,
        continuation_provider_overrides=None,
        document_review_loop=loop,
    )

    assert result["provider"]["id"] == "aip_step"
    assert result["selected_provider_source"] == "review_loop"
    assert result["fallback_allowed"] is False
    assert env["worker_chains"][-1] == ["aip_step"]


@pytest.mark.parametrize("classification", ["fast_fail", "spawn_failed"])
def test_explicit_stored_selection_start_failure_never_invokes_trailing_provider(
        env, monkeypatch, classification):
    result = start(env, continuation_provider_overrides=None)
    run = svc.get_run_record(result["run_id"])
    # Both start-failure classifications must retain the stored-selection audit contract.
    assert result["selected_provider_source"] == "stored_sequence"
    assert result["fallback_allowed"] is False
    assert run["selected_provider_source"] == "stored_sequence"
    assert run["fallback_allowed"] is False
    invoked = []

    def _fail(provider_, prompt, run_):
        invoked.append(provider_["id"])
        return classification, f"{classification}: simulated"

    monkeypatch.setattr(svc, "_cli_execute", _fail)
    selected_chain = [provider(pid) for pid in env["worker_chains"][-1]]
    assert selected_chain[0]["id"] == "aip_nr"
    assert "aip_opus" not in [p["id"] for p in selected_chain]

    started = svc._execute_provider_chain(run, selected_chain, "work")
    svc._classify_end_reason(run, started)

    assert started is False
    assert invoked == ["aip_nr"]
    assert run["attempt_no"] == 1
    assert [item["provider_id"] for item in run["fallback_history"]] == ["aip_nr"]
    assert run["fallback_history"][0]["reason"] == classification
    assert run["end_reason"] == "all_providers_failed"


def test_a_disabled_step_override_falls_through_instead_of_failing(env):
    """A map entry naming a provider that is no longer in the effective chain drops to the next
    tier — the stored provider — rather than pinning the run to something unrunnable."""
    env["chain"]["providers"] = [provider(pid) for pid in ALL_PROVIDER_IDS if pid != "aip_step"]
    assert start(env, continuation_provider_overrides={"2": "aip_step"})["provider"]["id"] == "aip_nr"


# ── §7-9: what the next hop, a pause/resume and a no-output retry replay ──────

def test_the_resolved_provider_and_override_map_ride_the_run_forward(env):
    """The handoff bundle is what _spawn_auto_resume and resume_chain read back. All three of
    the request states have to survive it, or a resumed hop silently re-resolves differently."""
    result = start(env, continuation_provider_overrides={"4": "aip_step"})
    run = svc.get_run_record(result["run_id"])

    assert run["continuation_selected_provider_id"] == "aip_nr"
    assert run["continuation_provider_overrides"] == {"4": "aip_step"}
    assert run["continuation_base_provider_id"] == "aip_header"
    assert run["continuation_provider_pinned"] is False

    bundle = svc._handoff_bundle({"doc_ref": ROOT, "target_seq": 4, "issued_to": "usr_admin",
                                  "api_base_url": API_BASE}, run)
    assert bundle["provider_overrides"] == {"4": "aip_step"}
    assert bundle["base_provider_id"] == "aip_header"
    assert bundle["provider_pinned"] is False


def test_a_resume_replays_the_bundle_onto_the_same_provider(env):
    """Feed the bundle back the way _spawn_auto_resume does: same tiers, same answer. An
    unpinned resume must NOT drop to the project default chain (the 0365 defect) and must not
    come back forced either."""
    first = start(env, continuation_provider_overrides={"4": "aip_step"})
    run = svc.get_run_record(first["run_id"])
    bundle = svc._handoff_bundle({"doc_ref": ROOT, "target_seq": 4, "issued_to": "usr_admin",
                                  "api_base_url": API_BASE}, run)

    resumed = start(
        env,
        provider_id=bundle["base_provider_id"],
        provider_pinned=bundle["provider_pinned"],
        continuation_provider_overrides=bundle["provider_overrides"],
    )
    assert resumed["provider"]["id"] == first["provider"]["id"] == "aip_nr"
    assert svc.get_run_record(resumed["run_id"])["continuation_provider_pinned"] is False


def test_a_no_output_retry_reuses_the_finalized_head_not_the_tiers(env):
    """0435 T0004's contract, re-checked against the new priority: the retry chain is exactly
    the provider this hop settled on — the stored one — and never re-walks the tiers."""
    result = start(env, continuation_provider_overrides=None)
    run = svc.get_run_record(result["run_id"])
    run["attempts_used"] = 0
    run["attempts_max"] = 2
    chain = svc._retry_provider_chain(run)
    assert [p["id"] for p in chain] == ["aip_nr"]


# ── §5-3 / §7-8: POST /api/v1/ai-invoke/start → start_run → final worker provider ──

@pytest.fixture
def client(monkeypatch, env):
    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    monkeypatch.setattr(
        ai_invoke_routes, "verify_bearer",
        lambda request: {"_is_user_jwt": True, "issued_to": "usr_admin", "is_admin": True},
    )
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda pid: {"project_id": pid})
    from modules.flow_gate.db import workflow_sequences as route_wfseq
    monkeypatch.setattr(route_wfseq, "get_sequence_for_member_doc",
                        env["wfseq"].get_sequence_for_member_doc)
    monkeypatch.setattr(route_wfseq, "get_sequence_items", env["wfseq"].get_sequence_items)
    from modules.flow_gate.api import token_routes
    monkeypatch.setattr(token_routes, "_build_mention_for_token", lambda **kw: "work")
    return TestClient(app, raise_server_exceptions=False)


def start_body(**over):
    body = {
        "project": "flowgate", "module": "default", "group": "0448",
        "doc_ref": ROOT, "action_scope": "new", "mode": "continuous",
        "continuation_target_seq": 4,
        # review_mode keeps this on the direct-issue path: the first-hop advance_workflow
        # builder is a different concern (0226) and would drag document creation into a
        # provider-priority test.
        "continuation_review_mode": True,
        "continuation_instruction_mode": "auto_approved",
    }
    body.update(over)
    return body


def post_start(client, **over):
    return client.post("/api/v1/ai-invoke/start", json=start_body(**over),
                       headers={"Authorization": "Bearer tok"})


@pytest.mark.parametrize("body,expected", [
    # An ordinary selection: provider_id only.
    ({"provider_id": "aip_header"},
     {"provider_id": "aip_header", "provider_pinned": None, "continuation_provider_overrides": None}),
    # An explicit force-all.
    ({"provider_id": "aip_header", "provider_pinned": True},
     {"provider_id": "aip_header", "provider_pinned": True, "continuation_provider_overrides": None}),
    # A per-step override, independent of the two above.
    ({"provider_id": "aip_header", "continuation_provider_overrides": {"2": "aip_step"}},
     {"provider_id": "aip_header", "provider_pinned": None,
      "continuation_provider_overrides": {"2": "aip_step"}}),
])
def test_route_forwards_the_three_request_states_separately(client, monkeypatch, body, expected):
    """§5-3: ai_invoke_routes.py:54,71,584-587 — three fields, three meanings, no collapsing."""
    captured = {}

    def _fake_start_run(**kw):
        captured.update(kw)
        return {"run_id": "aiv_route", "status": "running"}

    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", _fake_start_run)
    assert post_start(client, **body).status_code == 200
    for key, value in expected.items():
        assert captured[key] == value


@pytest.mark.parametrize("body,expected_provider", [
    ({"provider_id": "aip_header"}, "aip_nr"),
    ({"provider_id": "aip_header", "provider_pinned": True}, "aip_header"),
    ({"provider_id": "aip_header", "continuation_provider_overrides": {"2": "aip_step"}}, "aip_step"),
    ({"provider_id": "aip_header", "provider_pinned": True,
      "continuation_provider_overrides": {"2": "aip_step"}}, "aip_step"),
])
def test_route_request_reaches_the_right_final_worker_provider(client, body, expected_provider):
    """§7-8: the same three states again, but through the REAL start_run this time — a request
    unit test and a service unit test can both stay green while the wire between them is
    broken, which is exactly what NR0003 §7-3 warned about."""
    response = post_start(client, **body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["provider"]["id"] == expected_provider
    _release(payload["run_id"])
