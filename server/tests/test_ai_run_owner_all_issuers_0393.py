"""Every token issuer that start_run hands ai_run_id to must actually accept it (0393 T0005).

B0001 reported "검수요청 3연속 실패" — the review issuer never declared the ai_run_id
keyword, so ai_invoke_service._call_issue_builder (which inspects the signature) called it
bare, the minted token's ai_run_id column stayed NULL, and the reviewing worker's own
submission was rejected by the lease its own run had just acquired
(mutation_policy.assert_group_mutation_allowed: GROUP_AI_RUN_OWNER_MISMATCH). NR0003 §6
found the same structural gap in three more issuers that had simply not been hit yet:
workflow_sequence_edit, test_run, and workflow_decide — plus a fifth spot, the TSR branch
inside advance_workflow, which drops ai_run_id on the floor even though the ordinary path
two dozen lines below it already forwards the same parameter (0359 L0007 §2.9).

Per T0005 §2-5 this suite does NOT hand-pick the scopes it checks — it walks
`ai_invoke_routes._ALLOWED_SCOPES`, the real module-level constant `start_ai_invoke` itself
validates against, so a newly added action_scope is covered automatically the moment it is
declared. For each scope it drives the real `start_ai_invoke` route function (mocking only
the DB/auth edges), captures the `issue_builder` `ai_invoke_service.start_run` was actually
handed, and asserts the builder declares `ai_run_id` whenever one exists — exactly the
signature `_call_issue_builder` inspects.

The review path also gets one real-gate proof (§2-5 item 4): the issued token is fed through
the REAL `mutation_policy.assert_group_mutation_allowed`, not a monkeypatched judgment
function — only the lease row it reads is substituted (same technique as
test_q_answer_lease_owner_0389.py). No `skipif` anywhere in this file.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api import token_routes as _token_routes_mod  # noqa: E402
from modules.flow_gate.api.v1 import ai_invoke_routes as routes  # noqa: E402
from modules.flow_gate.db import tokens as db_tokens  # noqa: E402
from modules.flow_gate.db import workflow_sequences as db_wfseq  # noqa: E402
from modules.flow_gate.services import ai_invoke_service  # noqa: E402
from modules.flow_gate.services import mutation_policy as policy  # noqa: E402
from modules.flow_gate.services import test_run_service  # noqa: E402
from modules.flow_gate.services import workflow_decision_service as wfd  # noqa: E402

RUN_ID = "aiv_test_0393_000001"
USER = "4d96c7c2-c0be-4f4e-8594-bd65d2a8fa39"
PROJECT = "flowgate"
MODULE = "default"
GROUP_SHORT = "0393"
GROUP_ID = f"{PROJECT}.{MODULE}.{GROUP_SHORT}"
DOC_REF = f"{GROUP_ID}.0001-B"
API_BASE = "http://127.0.0.1:8089/flowgate/api/v1"


class _FakeRequest:
    """Only what start_ai_invoke touches directly: headers.get(...) for locale."""

    def __init__(self):
        self.headers: dict = {}


def _body(action_scope: str, **overrides) -> "routes.AiInvokeStartRequest":
    kwargs = dict(
        project=PROJECT, module=MODULE, group=GROUP_SHORT,
        doc_ref=DOC_REF, action_scope=action_scope, mode="single",
    )
    if action_scope == "resolve_conflict":
        kwargs["doc_ref"] = None
        kwargs["merge_id"] = 1
    kwargs.update(overrides)
    return routes.AiInvokeStartRequest(**kwargs)


@pytest.fixture(autouse=True)
def _route_edges(monkeypatch):
    """Stub only the auth/DB edges start_ai_invoke crosses before building issue_builder —
    everything downstream of `issue_builder=` runs for real in the tests that need it."""
    monkeypatch.setattr(routes, "_require_user", lambda _request: {"issued_to": USER, "is_admin": True})
    monkeypatch.setattr(routes.db_projects, "get_by_id", lambda _pid: {"project_id": PROJECT})
    monkeypatch.setattr(routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(routes, "_continuation_target_error", lambda *_a, **_k: None)
    monkeypatch.setattr(_token_routes_mod, "_build_api_base", lambda _request: API_BASE)


@pytest.fixture
def captured_start_run(monkeypatch):
    """Replace ai_invoke_service.start_run with a recorder that hands the issue_builder to
    the REAL `_call_issue_builder` — the exact helper whose `inspect.signature` call is the
    thing a bare `def builder():` silently defeats (ai_invoke_service.py:1309)."""
    calls: list = []

    def _fake_start_run(**kwargs):
        calls.append(kwargs)
        issue_builder = kwargs.get("issue_builder")
        issued = (
            ai_invoke_service._call_issue_builder(issue_builder, RUN_ID)
            if issue_builder is not None else None
        )
        return {"run_id": RUN_ID, "status": "running", "provider": "p", "_issued": issued}

    monkeypatch.setattr(routes.ai_invoke_service, "start_run", _fake_start_run)
    return calls


# ── §2-5 item 1+2: harvest every action_scope from the real constant, not a hand list ──

def test_allowed_scopes_constant_is_not_empty():
    """Guards against the harvesting loop below silently iterating zero scopes."""
    assert len(routes._ALLOWED_SCOPES) >= 9


@pytest.mark.parametrize("action_scope", list(routes._ALLOWED_SCOPES))
def test_issue_builder_declares_ai_run_id_when_one_is_used(
    action_scope, captured_start_run, monkeypatch,
):
    """Drive the real route for every declared scope. Scopes without a custom issue_builder
    (issue_builder=None) fall through to ai_invoke_service.start_run's own inline
    `token_service.issue(..., ai_run_id=run_id, ...)` call (line ~1032) — safe by
    construction, nothing to inspect. Scopes WITH a custom builder must declare the keyword.
    """
    # The four custom-builder scopes call out to service functions that would otherwise hit
    # a real DB; stub each at its own module so the builder closure still runs for real.
    monkeypatch.setattr(
        wfd, "request_review",
        lambda **kw: {"token": "t", "token_id": "tok", "scratch_dir": "/s", "mention": "m",
                       "_kwargs": kw},
    )
    monkeypatch.setattr(
        wfd, "request_sequence_edit",
        lambda **kw: {"raw_token": "t", "token_id": "tok", "scratch_dir": "/s", "mention": "m",
                       "_kwargs": kw},
    )
    monkeypatch.setattr(
        wfd, "request_workflow_decision",
        lambda **kw: {"doc_ref": DOC_REF, "action_scope": "workflow_decide", "group_id": GROUP_ID,
                       "token": "t", "token_id": "tok", "expires_at": None, "scratch_dir": "/s",
                       "mention": "m", "_kwargs": kw},
    )
    monkeypatch.setattr(
        test_run_service, "issue_test_run_request",
        lambda **kw: {"token": "t", "token_id": "tok", "scratch_dir": "/s", "mention": "m",
                       "_kwargs": kw},
    )
    # work_plan_fill joined _ALLOWED_SCOPES after this harvesting test was written. Keep the
    # generic issuer-signature check isolated from its DB just like the four issuers above.
    monkeypatch.setattr(
        routes.work_plan_service, "request_work_plan_fill",
        lambda **kw: {"raw_token": "t", "token_id": "tok", "scratch_dir": "/s", "mention": "m",
                       "_kwargs": kw},
    )
    # workflow_decide's continuous-new sibling, _issue_first_hop, calls advance_workflow.
    monkeypatch.setattr(
        wfd, "advance_workflow",
        lambda **kw: {"token": "t", "token_id": "tok", "scratch_dir": "/s", "mention": "m",
                       "_kwargs": kw},
    )

    body = _body(action_scope)
    request = _FakeRequest()
    response = routes.start_ai_invoke(body, request)
    assert getattr(response, "status_code", 200) == 200, getattr(response, "body", response)

    assert len(captured_start_run) == 1
    issue_builder = captured_start_run[0].get("issue_builder")
    if issue_builder is None:
        # Direct token_service.issue path inside start_run itself — nothing to assert here;
        # covered structurally, see the docstring above.
        return
    params = inspect.signature(issue_builder).parameters
    assert "ai_run_id" in params, (
        f"{action_scope}'s issue_builder does not accept ai_run_id — a worker on this scope "
        "would mint a token with an empty ai_run_id and get locked out of its own submission "
        "(B0001)."
    )


def test_continuous_new_first_hop_declares_ai_run_id(captured_start_run, monkeypatch):
    """action_scope=new only gets a CUSTOM builder (_issue_first_hop) when mode=continuous —
    the parametrized sweep above only ever sees the single-mode (issue_builder=None) shape
    for "new", so this is the one deliberately-added extra scenario, not a replacement for
    the harvesting loop (§2-5 item 1 is about not hand-listing the SCOPE SET, not about
    banning every mode combination)."""
    monkeypatch.setattr(
        wfd, "advance_workflow",
        lambda **kw: {"token": "t", "token_id": "tok", "scratch_dir": "/s", "mention": "m",
                       "_kwargs": kw},
    )
    body = _body("new", mode="continuous", continuation_target_seq=1)
    response = routes.start_ai_invoke(body, _FakeRequest())
    assert getattr(response, "status_code", 200) == 200, getattr(response, "body", response)
    issue_builder = captured_start_run[0]["issue_builder"]
    assert issue_builder is not None
    assert "ai_run_id" in inspect.signature(issue_builder).parameters


# ── §2-5 item 2 (deeper): the value actually reaches token_service.issue ──────────────

@pytest.mark.parametrize(
    "scope,module,func,expected_action_scope",
    [
        ("review", wfd, "request_review", "review"),
        ("workflow_sequence_edit", wfd, "request_sequence_edit", "workflow_sequence_edit"),
        ("test_run", test_run_service, "issue_test_run_request", "test_run"),
        ("workflow_decide", wfd, "request_workflow_decision", "workflow_decide"),
    ],
)
def test_ai_run_id_and_scope_reach_the_service_function(
    scope, module, func, expected_action_scope, captured_start_run, monkeypatch,
):
    """§2-4: the action_scope the token is issued under must equal the scope start_run
    leased with (`_TOKEN_SCOPE.get(action_scope, action_scope)` — identity fallthrough for
    all four of these). Pin it with an assertion instead of trusting a code read."""
    recorded: dict = {}

    def _fake(**kw):
        recorded.update(kw)
        return {"token": "t", "raw_token": "t", "token_id": "tok", "scratch_dir": "/s",
                "mention": "m", "doc_ref": DOC_REF, "action_scope": expected_action_scope,
                "group_id": GROUP_ID, "expires_at": None}

    monkeypatch.setattr(module, func, _fake)
    body = _body(scope)
    response = routes.start_ai_invoke(body, _FakeRequest())
    assert getattr(response, "status_code", 200) == 200, getattr(response, "body", response)

    assert recorded.get("ai_run_id") == RUN_ID
    token_scope_sent_to_start_run = captured_start_run[0]["action_scope"]
    assert token_scope_sent_to_start_run == expected_action_scope


# ── §2-4 + §2-5 item 3: the REAL token_service.issue call of each remaining path ───────
# The parametrized test above stubs the service functions, so it proves the route hands the
# run id over; these drive the service functions themselves with only token_service.issue
# captured, so they prove the value survives to the row that is written — and that the scope
# the token is minted under is the same one start_run leased with (`_TOKEN_SCOPE` identity
# fallthrough). §2-4 warns that a scope mismatch fails exactly like a missing run id does,
# so it is asserted rather than read off the source.

def _capture_issue(monkeypatch, module):
    captured: dict = {}

    def _fake_issue(**kwargs):
        captured.update(kwargs)
        return {"raw_token": "raw", "token_id": "tok_x", "scratch_dir": "/s",
                "expires_at": "2999-01-01T00:00:00+00:00"}

    monkeypatch.setattr(module.token_service, "issue", _fake_issue)
    return captured


def _doc(**overrides) -> dict:
    doc = {"doc_id": DOC_REF, "group_id": GROUP_ID, "project_id": PROJECT, "seq": 1,
           "type_code": "B", "title": "검수요청 3연속 실패", "module": MODULE}
    doc.update(overrides)
    return doc


def test_sequence_edit_token_carries_the_run_id_and_its_own_scope(monkeypatch):
    captured = _capture_issue(monkeypatch, wfd)
    monkeypatch.setattr(wfd.db_documents, "get_by_id", lambda _did: _doc(type_code="R"))
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda _did: {"id": 7})
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda _sid: [
        {"type": "T", "label": "작업지시", "status": "completed"},
    ])
    monkeypatch.setattr(wfd.mention_service, "build_sequence_edit_mention", lambda **_kw: "m")

    wfd.request_sequence_edit(
        doc_id=DOC_REF, issued_to=USER, api_base_url=API_BASE, ai_run_id=RUN_ID,
    )

    assert captured["ai_run_id"] == RUN_ID
    assert captured["action_scope"] == "workflow_sequence_edit"
    assert routes._TOKEN_SCOPE.get("workflow_sequence_edit", "workflow_sequence_edit") == (
        captured["action_scope"]
    )


def test_workflow_decision_token_carries_the_run_id_and_its_own_scope(monkeypatch):
    captured = _capture_issue(monkeypatch, wfd)
    monkeypatch.setattr(wfd.db_documents, "get_by_id", lambda _did: _doc(type_code="R"))
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda _did: None)
    monkeypatch.setattr(wfd.db_documents, "get_group_max_seq", lambda _gid: 1)
    monkeypatch.setattr(wfd.db_documents, "fetch_recent_group_docs", lambda **_kw: [])
    monkeypatch.setattr(wfd.mention_service, "build_workflow_decision_mention", lambda **_kw: "m")

    wfd.request_workflow_decision(
        doc_id=DOC_REF, issued_to=USER, api_base_url=API_BASE, ai_run_id=RUN_ID,
    )

    assert captured["ai_run_id"] == RUN_ID
    assert captured["action_scope"] == "workflow_decide"
    assert routes._TOKEN_SCOPE.get("workflow_decide") == captured["action_scope"]


def test_test_run_token_carries_the_run_id_and_its_own_scope(monkeypatch):
    captured = _capture_issue(monkeypatch, test_run_service)
    monkeypatch.setattr(test_run_service.db_docs, "get_by_id", lambda _did: _doc(type_code="TS"))
    monkeypatch.setattr(test_run_service, "_build_test_run_mention", lambda **_kw: "m")

    test_run_service.issue_test_run_request(
        doc_id=DOC_REF, issued_to=USER, api_base_url=API_BASE, ai_run_id=RUN_ID,
    )

    assert captured["ai_run_id"] == RUN_ID
    assert captured["action_scope"] == "test_run"
    assert routes._TOKEN_SCOPE.get("test_run", "test_run") == captured["action_scope"]


# ── §2-3: the TSR branch inside advance_workflow ───────────────────────────────────────

def test_continuous_tsr_head_forwards_ai_run_id_to_test_run_issuer(monkeypatch):
    """advance_workflow's TSR early-return branch (workflow_decision_service.py ~L567) used
    to build the test_run request without ai_run_id even though the ordinary token issued a
    few lines further down already carried it — so a continuous chain landing on a TSR head
    died the same self-lock way review did."""
    seq = {"id": 1}
    head = {"id": 2, "type": "TSR", "item_seq": 5, "result_doc_id": None,
            "result_doc_review_status": None}
    pred_doc = {"doc_id": "flowgate.default.0393.0002-TS", "type_code": "TS",
                "doc_review_status": "approved"}
    doc = {"doc_id": DOC_REF, "group_id": GROUP_ID, "project_id": PROJECT, "seq": 1}

    monkeypatch.setattr(wfd.db_documents, "get_by_id",
                        lambda did: pred_doc if did == pred_doc["doc_id"] else doc)
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda _did: seq)
    monkeypatch.setattr(db_wfseq, "get_effective_head", lambda _sid: head)
    monkeypatch.setattr(db_wfseq, "get_predecessor_result_doc_id",
                        lambda _sid, _hid: pred_doc["doc_id"])
    # advance_workflow does `from modules.flow_gate.db import tokens as _db_tokens` inline,
    # which resolves to this same cached module object — patching it here is enough.
    monkeypatch.setattr(db_tokens, "get_unconsumed_by_doc_ref", lambda _doc_ref: None)

    recorded: dict = {}

    def _fake_issue_test_run_request(**kw):
        recorded.update(kw)
        return {"doc_ref": pred_doc["doc_id"], "group_id": GROUP_ID, "token": "t",
                "token_id": "tok", "expires_at": None, "scratch_dir": "/s", "mention": "m"}

    monkeypatch.setattr(test_run_service, "issue_test_run_request", _fake_issue_test_run_request)

    result = wfd.advance_workflow(
        doc_id=DOC_REF, issued_to=USER, api_base_url=API_BASE, continuous=True,
        continuation_target_seq=10, ai_run_id=RUN_ID,
    )
    assert result["action_scope"] == "test_run"
    assert recorded.get("ai_run_id") == RUN_ID


# ── §2-5 item 4: the review path through the REAL gate, not a monkeypatched judgment ────

def test_review_token_passes_the_real_group_lease_gate(monkeypatch):
    """Drives policy.assert_group_mutation_allowed for real; only the lease ROW is
    substituted (test_q_answer_lease_owner_0389.py's technique)."""
    doc = {"doc_id": DOC_REF, "group_id": GROUP_ID, "project_id": PROJECT, "seq": 1,
           "type_code": "B", "title": "검수요청 3연속 실패"}
    monkeypatch.setattr(wfd.db_documents, "get_by_id", lambda _did: doc)
    monkeypatch.setattr(wfd.db_documents, "fetch_recent_group_docs", lambda **_kw: [])
    monkeypatch.setattr(wfd.mention_service, "build_review_mention", lambda **_kw: "mention")

    issued_kwargs: dict = {}

    def _fake_token_issue(**kw):
        issued_kwargs.update(kw)
        return {"raw_token": "raw-review-token", "token_id": "tok_review",
                "scratch_dir": "/s", "expires_at": "2999-01-01T00:00:00+00:00"}

    monkeypatch.setattr(wfd.token_service, "issue", _fake_token_issue)

    issued = wfd.request_review(
        doc_id=DOC_REF, issued_to=USER, api_base_url=API_BASE, ai_run_id=RUN_ID,
    )
    assert issued["token"] == "raw-review-token"
    assert issued_kwargs["ai_run_id"] == RUN_ID
    assert issued_kwargs["action_scope"] == "review"

    token_row = {
        "token_id": issued_kwargs.get("token_id") or "tok_review", "group_id": GROUP_ID,
        "doc_ref": DOC_REF, "action_scope": "review", "issued_to": USER, "ai_run_id": RUN_ID,
    }
    lease = {
        "group_id": GROUP_ID, "project_id": PROJECT, "run_id": RUN_ID, "chain_id": RUN_ID,
        "token_id": token_row["token_id"], "action_scope": "review", "worker_identity": USER,
        "state": "active", "generation": 1, "expires_at": "2999-01-01T00:00:00+00:00",
    }
    beats: list = []
    monkeypatch.setattr(policy.db_leases, "get_active", lambda gid: dict(lease) if gid == GROUP_ID else None)
    monkeypatch.setattr(policy.db_leases, "heartbeat",
                        lambda gid, rid: beats.append((gid, rid)) or True)

    principal = policy.worker_principal(token_row)
    granted = policy.assert_group_mutation_allowed(
        GROUP_ID, principal, f"POST {API_BASE}/inbox",
    )
    assert granted["run_id"] == RUN_ID
    assert beats == [(GROUP_ID, RUN_ID)]


def test_review_token_without_run_id_is_the_403_b0001_reported(monkeypatch):
    """The pre-fix shape, pinned so the regression cannot silently come back."""
    lease = {
        "group_id": GROUP_ID, "project_id": PROJECT, "run_id": RUN_ID, "chain_id": RUN_ID,
        "token_id": "tok_review", "action_scope": "review", "worker_identity": USER,
        "state": "active", "generation": 1, "expires_at": "2999-01-01T00:00:00+00:00",
    }
    monkeypatch.setattr(policy.db_leases, "get_active", lambda gid: dict(lease) if gid == GROUP_ID else None)
    token_row = {"token_id": "tok_review", "group_id": GROUP_ID, "doc_ref": DOC_REF,
                 "action_scope": "review", "issued_to": USER, "ai_run_id": None}
    principal = policy.worker_principal(token_row)
    with pytest.raises(policy.MutationPolicyError) as exc_info:
        policy.assert_group_mutation_allowed(GROUP_ID, principal, "POST /flowgate/api/v1/inbox")
    assert exc_info.value.status_code == 403
    assert exc_info.value.error["code"] == "GROUP_AI_RUN_OWNER_MISMATCH"
