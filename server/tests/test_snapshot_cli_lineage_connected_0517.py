"""flowgate.default.0517 T#2 rework (TR0021 rev2): successor-lineage connected regression.

The prior TR0021 revision proved chain-lineage capability succession only by hand-typing
the same ``chain_id`` string into two independently-built fixture dicts — a predecessor
"run" dict and a "successor" dict that never came from any FlowGate continuation code.
The human rejection for that revision named this out explicitly: the successor must be
produced by the REAL continuation/handoff/resume path, and an unrelated run produced the
same real way must still be denied.

This file drives the actual production sequence: a real continuous run (admission.py
``start_run``) requests a snapshot through the genuine CLI worker-token HTTP boundary,
is durably paused (``chain.pause_run``, which copies the run's OWN chain_id into
``ai_invoke_paused_chains``), and a real ``chain.resume_chain`` call consumes that row and
spawns the successor run — inheriting chain_id via production code, not test code. A
second, wholly independent real run (different chain) is then denied against the same
snapshot.
"""
from __future__ import annotations

import sys
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate.api.v1 import snapshot_routes
from modules.flow_gate.services import snapshot_access_service as access
from modules.flow_gate.services import snapshot_materialization_service as materialize
from modules.flow_gate.services import snapshot_request_service

from test_ai_invoke_pause_resume_0252 import (  # noqa: F401  (fixtures/helpers reused as-is)
    DOC_REF,
    GROUP,
    PY,
    _patch_advance,
    _provider,
    _slow_cmd,
    _start,
    _wait_finished,
    fake_env,
    svc,
)
from test_snapshot_materialization_0517 import _Store, snapshot_env  # noqa: F401


def _wait_provider_assigned(run_id: str, timeout: float = 5.0) -> dict:
    """admission.start_run spawns the worker thread that fills in provider_id; the CLI
    snapshot request needs a non-empty provider_id (validate_request requires it), so
    give the just-started run a brief moment to reach that point."""
    deadline = time.monotonic() + timeout
    run = svc.get_run_record(run_id)
    while run is not None and not run.get("provider_id") and time.monotonic() < deadline:
        time.sleep(0.02)
        run = svc.get_run_record(run_id)
    return run


def _token_for(run: dict, issued_to: str) -> dict:
    return {
        "token_id": run["token_id"], "ai_run_id": run["run_id"],
        "project": run["project_id"], "group_id": run["group_id"],
        "action_scope": run["action_scope"], "doc_ref": run["doc_ref"],
        "issued_to": issued_to,
    }


def test_real_resume_chain_successor_inherits_capability_unrelated_run_denied(
    fake_env, snapshot_env, monkeypatch,
):
    monkeypatch.setattr(
        snapshot_request_service, "validate_request_authority",
        lambda candidate, active: candidate,
    )
    monkeypatch.setattr(snapshot_request_service, "get_store", lambda: _Store())
    monkeypatch.setattr(
        snapshot_request_service.workflow_events, "create", lambda event: event,
    )
    monkeypatch.setattr(snapshot_request_service, "_notify", lambda *args: None)
    monkeypatch.setattr(access.usage_db, "record", lambda data: dict(data))
    monkeypatch.setattr(access.workflow_events, "create", lambda data: data)
    # snapshot_env's own db_groups/git_service mocks assume project_id "project" — this
    # test's snapshot rides the REAL run's project/group ("flowgate"/GROUP) instead.
    monkeypatch.setattr(
        materialize.db_groups, "get_by_id",
        lambda group_id: {"group_id": group_id, "project_id": "flowgate"},
    )
    monkeypatch.setattr(
        materialize.git_service, "effective_src_root_ex",
        lambda project_id, group_id: (snapshot_env.worktree, "registered"),
    )

    def create(data):
        snapshot_env.row.update(data)
        snapshot_env.row.update(
            snapshot_id="snap_lineage", status="requested",
            requested_at="2026-09-24T00:00:00+00:00",
        )
        return dict(snapshot_env.row)

    def transition(snapshot_id, decision, actor):
        if snapshot_env.row["status"] != "requested":
            return dict(snapshot_env.row), False
        snapshot_env.row["status"] = decision
        snapshot_env.row["approved_by" if decision == "approved" else "rejected_by"] = actor
        return dict(snapshot_env.row), True

    monkeypatch.setattr(snapshot_request_service.db, "create", create)
    monkeypatch.setattr(snapshot_request_service.db, "transition", transition)
    snapshot_env.row.update(
        status="requested", stale=False, stale_detected_at=None,
        created_at=None, source_revision=None, source_fingerprint=None,
    )
    (snapshot_env.worktree / "lineage.txt").write_text("lineage", encoding="utf-8")

    app = FastAPI()
    app.include_router(snapshot_routes.router)
    from modules.flow_gate.auth.middleware import get_current_user
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_admin"}
    client = TestClient(app)

    # ---- Run A: a real continuous run requests the snapshot through the real CLI
    # worker-token boundary while it is genuinely alive. ----
    res_a = _start(fake_env, mode="continuous", target=3, cmd=_slow_cmd(3))
    run_a_id = res_a["run_id"]
    run_a = _wait_provider_assigned(run_a_id)
    assert run_a is not None and run_a["provider_id"]
    assert run_a["mode"] == "continuous"
    # A fresh chain's first hop carries its own run_id as chain_id (admission.py).
    assert run_a["chain_id"] == run_a_id

    monkeypatch.setattr(snapshot_routes.token_service, "verify", lambda raw: _token_for(run_a, "worker-a"))
    try:
        requested = client.post(
            "/api/v1/snapshots/cli/request",
            headers={"Authorization": "Bearer raw-a"},
            json={
                "reason": "run A needs a real tree", "scope": "single_file",
                "requested_paths": ["lineage.txt"], "purpose": "run tests",
                "source_kind": "current_worktree",
            },
        )
        assert requested.status_code == 200, requested.text
        snapshot_id = requested.json()["request"]["snapshot_id"]
        # The chain_id on the durable snapshot request is run A's OWN chain_id — read
        # straight off the live run object, never hand-typed by this test.
        assert snapshot_env.row["chain_id"] == run_a["chain_id"] == run_a_id
        assert snapshot_env.row["run_id"] == run_a_id
        assert "locator" not in requested.text and "snapshot_path" not in requested.text

        # Human decision only transitions requested -> approved here (no auto-materialize),
        # so the CLI materialize call below is the thing that actually publishes the
        # snapshot to disk -- exercising the real CLI boundary the rejection named, not
        # the separate human /approve route (which is covered by
        # test_snapshot_materialization_0517.py and is allowed to show operators the
        # real path).
        snapshot_request_service.decide(snapshot_id, "approved", "usr_admin")
        assert snapshot_env.row["status"] == "approved"

        materialized = client.post(
            f"/api/v1/snapshots/cli/{snapshot_id}/materialize",
            headers={"Authorization": "Bearer raw-a"},
        )
        assert materialized.status_code == 200, materialized.text
        assert snapshot_env.row["status"] == "created"
        real_path = str(snapshot_env.final())
        assert materialized.json()["snapshot"]["status"] == "active"
        assert "locator" not in materialized.text
        assert "snapshot_path" not in materialized.text
        assert real_path not in materialized.text
    finally:
        # ---- Run A must be OVER before it can be durably paused/resumed. A genuine user
        # pause (chain.pause_run copies run["chain_id"] into the durable row — production
        # code, not this test) while the run is still alive, followed by letting the short
        # `_slow_cmd` finish ON ITS OWN: a *cancelled* finish is not resumable and would
        # have finalize.py delete the very row resume_chain needs (verified experimentally
        # — cancelling here reproduces exactly that and turns resume into 409 resume_conflict).
        svc.pause_run(run_a_id, "usr_admin")
        _wait_finished(run_a_id)

    # ---- Successor: the REAL resume_chain/admission path consumes the paused row and
    # spawns run B, inheriting chain_id by production code. ----
    calls: list[dict] = []
    _patch_advance(monkeypatch, fake_env["tmp"], calls)
    fake_env["chain"]["providers"] = [_provider(f'"{PY}" -c "import sys; sys.stdin.read()"')]
    res_b = svc.resume_chain(
        group_id=GROUP, user_id="usr_admin", api_base_url="http://127.0.0.1:1/flowgate/api/v1",
    )
    assert res_b["ok"] is True
    assert res_b["chain_id"] == run_a_id  # inherited by resume_chain, not asserted by fiat
    run_b_id = res_b["run_id"]
    assert run_b_id != run_a_id
    run_b = svc.get_run_record(run_b_id)
    assert run_b["chain_id"] == run_a_id

    monkeypatch.setattr(snapshot_routes.token_service, "verify", lambda raw: _token_for(run_b, "worker-b"))
    for operation, extra in (
        ("read", {"path": "lineage.txt"}),
        ("search", {"pattern": "lineage", "glob": "*.txt"}),
        ("glob", {"pattern": "*.txt"}),
        ("stat", {"path": "lineage.txt"}),
    ):
        response = client.post(
            f"/api/v1/snapshots/cli/{snapshot_id}/access",
            headers={"Authorization": "Bearer raw-b"},
            json={"operation": operation, **extra},
        )
        assert response.status_code == 200, response.text
        assert "locator" not in response.text
        assert str(snapshot_env.final()) not in response.text
    read_back = client.post(
        f"/api/v1/snapshots/cli/{snapshot_id}/access",
        headers={"Authorization": "Bearer raw-b"},
        json={"operation": "read", "path": "lineage.txt"},
    )
    assert read_back.json()["content"] == "lineage"

    # The successor executes a real process that reports the runtime paths FlowGate
    # injected. This proves lineage authorization and response redaction on the same
    # production CLI /run boundary instead of hiding the output behind a Popen mock.
    print_runtime_paths = (
        f'"{sys.executable}" -c "import os,sys;print(os.getcwd());'
        "print(os.environ.get('TEMP'));print(os.environ.get('TMP'),file=sys.stderr)\""
    )
    executed = client.post(
        f"/api/v1/snapshots/cli/{snapshot_id}/run",
        headers={"Authorization": "Bearer raw-b"},
        json={"task_kind": "test", "command": print_runtime_paths},
    )
    assert executed.status_code == 200, executed.text
    executed_body = executed.json()
    assert executed_body["exit_code"] == 0
    assert access.SNAPSHOT_PATH_REDACTION in executed_body["stdout"]
    assert access.SNAPSHOT_PATH_REDACTION in executed_body["stderr"]
    combined_output = executed_body["stdout"] + executed_body["stderr"]
    for raw_path in (
        snapshot_env.scratch.resolve(),
        snapshot_env.final().resolve(),
        (snapshot_env.final() / "source").resolve(),
        (snapshot_env.final() / ".flowgate-tmp").resolve(),
    ):
        raw = str(raw_path)
        assert raw not in combined_output
        assert raw.replace("\\", "/") not in combined_output
        assert raw.replace("/", "\\") not in combined_output
    assert "locator" not in executed.text
    assert "snapshot_path" not in executed.text
    assert str(snapshot_env.final()) not in executed.text
    _wait_finished(run_b_id)

    # ---- Unrelated: a wholly separate real run (its own chain, no lineage to run A/B)
    # must be denied against the SAME snapshot. ----
    res_c = _start(fake_env, mode="single", cmd=f'"{PY}" -c "print(1)"')
    run_c_id = res_c["run_id"]
    _wait_finished(run_c_id)
    run_c = svc.get_run_record(run_c_id)
    assert run_c["chain_id"] == run_c_id
    assert run_c["chain_id"] != run_a_id

    monkeypatch.setattr(snapshot_routes.token_service, "verify", lambda raw: _token_for(run_c, "worker-c"))
    denied = client.get(
        f"/api/v1/snapshots/cli/{snapshot_id}/status",
        headers={"Authorization": "Bearer raw-c"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["error"]["code"] == "snapshot_forbidden"
