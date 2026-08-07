"""A refused group mutation must leave its reason on the run that refused it (0393 T0005 §2-6).

B0001's three dead reviews are the whole point of this file. NR0003 §3 read the operating
database and found that every one of them ended with an EMPTY error list and exit code 0:
the gate said 403 on the wire and nowhere else, so the only account of what happened was a
sentence each worker happened to volunteer in its last message. The reporter's words were
"원인도 모르고" — and they were literally right, because the server had recorded nothing.

What is pinned here:

* `ai_invoke_service.mark_group_lease_denied` writes the refusal into the same
  `register_errors` list a failed inbox POST lands in, plus the `lease_denied_*` fields the
  stop classifier reads.
* `_resolve_stop_code` then names it `group_lease_denied` — for a mode="single" run, which
  is exactly the shape B0001 hit and the shape that used to fall through to `return None`.
* `_stop_reason_text` turns it into a sentence carrying the refusal code and the blocked
  operation, and `is_resumable` says no (T0005 §2-6: "재개 가능 목록에는 넣지 않는다").
* `mutation_policy._record_denied_mutation` does the write OFF the event loop
  (`anyio.to_thread.run_sync`) and swallows its own failures, so observability can never
  break the gate it is observing.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import os
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as ais  # noqa: E402
from modules.flow_gate.services import mutation_policy as policy  # noqa: E402

RUN_ID = "aiv_test_0393_denial"
GROUP_ID = "flowgate.default.0393"
OPERATION = "POST /flowgate/api/v1/inbox"
CODE = "GROUP_AI_RUN_OWNER_MISMATCH"


@pytest.fixture
def live_run():
    """A run in this process's registry, shaped like B0001's: single mode, nothing produced."""
    run = {
        "run_id": RUN_ID,
        "group_id": GROUP_ID,
        "status": "running",
        "mode": "single",
        "action_scope": "review",
        "docs_target": 1,
        "docs_reached": 0,
        "outcome": "none",
        "end_reason": "exited",
        "register_errors": [],
    }
    with ais._runs_lock:
        ais._runs[RUN_ID] = run
    try:
        yield run
    finally:
        with ais._runs_lock:
            ais._runs.pop(RUN_ID, None)


def _request(method: str = "POST", path: str = "/flowgate/api/v1/inbox"):
    """Only what _record_denied_mutation reads off the request."""
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


def _denial(**overrides) -> policy.MutationPolicyError:
    fields = {"group_id": GROUP_ID, "run_id": RUN_ID, "operation": OPERATION}
    fields.update(overrides)
    return policy.MutationPolicyError(
        403, CODE, "This worker token does not own the active group lease.", **fields
    )


def _worker():
    """The principal `principal_from_request` builds from a worker's Bearer token."""
    return policy.worker_principal({
        "token_id": "tok_review_0393", "group_id": GROUP_ID, "doc_ref": f"{GROUP_ID}.0001-B",
        "action_scope": "review", "issued_to": "user-1", "ai_run_id": None,
    })


# ── the record itself ─────────────────────────────────────────────────────────────────

def test_the_refusal_lands_in_the_runs_error_list(live_run):
    """NR0003 §3 measured "오류 목록은 빈 칸" on all three failures. It is not blank now."""
    assert ais.mark_group_lease_denied(
        group_id=GROUP_ID, run_id=RUN_ID, code=CODE, operation=OPERATION,
    ) is True
    assert live_run["register_errors"] == [
        {"status": 403, "reason": f"{CODE}: {OPERATION}", "turn": None},
    ]
    assert live_run["lease_denied_code"] == CODE
    assert live_run["lease_denied_operation"] == OPERATION
    assert live_run["lease_denied_count"] == 1


def test_a_run_this_process_does_not_hold_is_a_no_op():
    """A lease left behind by a run from before a restart must not raise, just report False."""
    assert ais.mark_group_lease_denied(
        group_id="flowgate.default.9999", run_id="aiv_not_here",
        code=CODE, operation=OPERATION,
    ) is False


def test_repeated_refusals_are_bounded(live_run):
    for index in range(ais.LEASE_DENIAL_RECORD_LIMIT + 5):
        ais.mark_group_lease_denied(
            group_id=GROUP_ID, run_id=RUN_ID, code=CODE, operation=f"{OPERATION}?{index}",
        )
    assert len(live_run["register_errors"]) == ais.LEASE_DENIAL_RECORD_LIMIT
    # The FIRST refusal is the informative one and is never evicted.
    assert live_run["register_errors"][0]["reason"] == f"{CODE}: {OPERATION}?0"
    assert live_run["lease_denied_count"] == ais.LEASE_DENIAL_RECORD_LIMIT + 5


# ── the name and the sentence ─────────────────────────────────────────────────────────

def test_the_run_ends_named_and_explained(live_run):
    ais.mark_group_lease_denied(
        group_id=GROUP_ID, run_id=RUN_ID, code=CODE, operation=OPERATION,
    )
    stop_code = ais._resolve_stop_code(live_run, False)
    assert stop_code == "group_lease_denied"

    sentence = ais._stop_reason_text(stop_code, live_run)
    assert sentence, "a stop code with no sentence is the cipher B0001 complained about"
    assert CODE in sentence
    assert OPERATION in sentence

    # §2-6: deliberately NOT resumable — re-running changes nothing until a human clears it.
    assert ais.is_resumable(stop_code) is False
    assert "group_lease_denied" not in ais.RESUMABLE_STOP_CODES


def test_an_undenied_single_run_still_has_no_stop_code(live_run):
    """The pre-fix shape, kept as the control: the new branch must not claim every run."""
    assert ais._resolve_stop_code(live_run, False) is None


def test_a_run_that_produced_its_document_keeps_its_ordinary_ending(live_run):
    """A stray refusal after the work landed must not relabel a successful hop."""
    live_run["docs_reached"] = 1
    ais.mark_group_lease_denied(
        group_id=GROUP_ID, run_id=RUN_ID, code=CODE, operation=OPERATION,
    )
    assert ais._resolve_stop_code(live_run, False) is None
    # …but the refusal is still on the record.
    assert live_run["register_errors"]


# ── the middleware hop ────────────────────────────────────────────────────────────────

def test_the_middleware_denial_path_reaches_the_run(live_run):
    """Drives the real async recorder, so the anyio offload is exercised, not just read."""
    asyncio.run(policy._record_denied_mutation(_denial(), _request(), _worker()))
    assert live_run["lease_denied_code"] == CODE
    assert live_run["lease_denied_operation"] == OPERATION


def test_a_denial_that_names_no_run_is_ignored(live_run):
    """A refusal that cannot name a run has nowhere to be filed; it must not raise either."""
    exc = policy.MutationPolicyError(403, CODE, "no run", group_id=GROUP_ID)
    asyncio.run(policy._record_denied_mutation(exc, _request(), _worker()))
    assert "lease_denied_code" not in live_run


def test_the_gate_survives_a_broken_recorder(monkeypatch, live_run):
    """Observability must never become a second failure mode for the gate (§2-6)."""
    def _boom(**_kwargs):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(ais, "mark_group_lease_denied", _boom)
    # No exception escapes — dispatch goes straight on to return the original 403.
    asyncio.run(policy._record_denied_mutation(_denial(), _request(), _worker()))


def test_a_person_held_off_by_the_lease_is_recorded_but_is_not_the_runs_error(live_run):
    """§2-6 names both refusal codes, so GROUP_AI_RUN_LOCKED is recorded too — but in the
    other bucket. `assert_group_mutation_allowed` only ever raises 423 for a NON-worker, so
    this is a person being told "an AI is working here": routine, and it happens on almost
    every run. Filing it as the run's own error would put a row on nearly every execution
    and would eventually end a healthy run with `group_lease_denied`."""
    exc = policy.MutationPolicyError(
        423, "GROUP_AI_RUN_LOCKED", "locked", group_id=GROUP_ID, run_id=RUN_ID,
    )
    asyncio.run(policy._record_denied_mutation(
        exc, _request("PATCH", "/flowgate/api/v1/workflow/sequence"), policy.human_principal(),
    ))

    assert live_run["lease_blocked_others"] == [{
        "status": 423, "code": "GROUP_AI_RUN_LOCKED",
        "operation": "PATCH /flowgate/api/v1/workflow/sequence",
    }]
    assert live_run["register_errors"] == []
    assert "lease_denied_code" not in live_run
    assert ais._resolve_stop_code(live_run, False) is None
    # …and it still reaches a reader, on the finished payload.
    live_run.update({
        "outcome": "none", "docs_target": 1, "reached_doc_ids": [], "exit_code": 0,
        "last_message_received": False, "last_message": None, "provider_id": "p",
        "attempt_no": 1, "fallback_history": [], "source_dirty": False, "duration_ms": 1,
        "scratch_retained": False,
    })
    assert ais.finished_payload(live_run)["lease_blocked_others"] == live_run["lease_blocked_others"]


def test_dispatch_tells_the_recorder_who_was_refused():
    """The split above only holds if `dispatch` actually forwards the principal."""
    source = inspect.getsource(policy.GroupMutationPolicyMiddleware.dispatch)
    assert "_record_denied_mutation(exc, request, principal)" in source


# ── the event loop stays free ─────────────────────────────────────────────────────────

def _dotted(node) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def test_the_record_is_offloaded_to_a_worker_thread():
    """`dispatch` is async: a synchronous registry/DB write inline would block every other
    request, which is the exact defect test_event_loop_blocking_0279 exists to prevent.
    Wrapping it in a helper does not launder it, so the offload is asserted structurally."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(policy._record_denied_mutation)))
    offloads = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and _dotted(node.value.func) == "anyio.to_thread.run_sync"
    ]
    assert offloads, "the denial record must go through anyio.to_thread.run_sync"


def test_dispatch_records_before_it_answers():
    """The 403 body is built from the same exception the recorder just consumed, so the
    ordering here is what guarantees a refused request is never answered silently."""
    source = inspect.getsource(policy.GroupMutationPolicyMiddleware.dispatch)
    record_at = source.index("_record_denied_mutation")
    respond_at = source.index("return mutation_error_response(exc)")
    assert record_at < respond_at
