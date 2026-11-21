"""Independently selectable scenarios; shared fixtures remain in test_branch_merge_attempt_0630.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_branch_merge_attempt_0630 import (
    Path,
    Proj,
    _COUNTER,
    _Crash,
    _GIT,
    _MockDB,
    _MockTxn,
    _OTHER_GROUP,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _approve,
    _busy,
    _client,
    _crashed_branch_merge,
    _ctx,
    _end_attempt_job,
    _git,
    _plan_finalize,
    _prepare_route_conflict,
    _resolve_all,
    _review_pending,
    _session,
    _view,
    base64,
    contextmanager,
    copy,
    itertools,
    mock_db,
    os,
    proj,
    pytest,
    pytestmark,
    shutil,
    sqlite3,
    storage_dir,
    subprocess,
    sys,
    tempfile,
    uuid,
)

def test_08b_concurrent_explicit_ai_resolve_requests_start_single_live_run(proj, monkeypatch):
    """Concurrent explicit /ai-resolve requests are guarded by an atomic per-attempt lock.
    Even when multiple requests arrive while start_run is in-flight, exactly ONE live run is created."""
    import threading
    import time
    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.services.git import branch_merge as bm_mod
    from modules.flow_gate.services.git.branch_merge import AI_RUNNING

    p = proj
    _prepare_route_conflict(p)
    calls = []
    start_entered = threading.Event()
    release_starter = threading.Event()

    def fake_start(**kwargs):
        calls.append(kwargs)
        start_entered.set()
        # Hold in start_run briefly to simulate in-flight execution and let concurrent requests arrive
        release_starter.wait(timeout=5)
        return "run-concurrent-1"

    monkeypatch.setattr(git_routes, "_start_branch_merge_resolve_run", fake_start)
    monkeypatch.setattr(bm_mod, "_ai_run_status", lambda run_id: AI_RUNNING)

    client = _client(monkeypatch)
    resp = client.post(
        f"/api/v1/projects/{p.pid}/git/branches/merge",
        json={"source_branch": "feature", "target_branch": "develop", "push": False},
    )
    assert resp.status_code == 202, resp.text
    merge_id = resp.json()["merge_id"]

    results = []
    results_lock = threading.Lock()

    def invoke():
        r = client.post(
            f"/api/v1/projects/{p.pid}/git/merge/{merge_id}/ai-resolve",
            json={"provider_id": "prov-x"},
        )
        with results_lock:
            results.append((r.status_code, r.json()))

    # Thread 1 starts the resolver and waits inside fake_start
    t1 = threading.Thread(target=invoke)
    t1.start()

    # Wait until Thread 1 has actually entered fake_start (meaning status=starting is written and run_id is absent)
    assert start_entered.wait(timeout=5), "starter was never entered"

    # Thread 2 and Thread 3 fire concurrently WHILE Thread 1 is still inside fake_start!
    threads = [threading.Thread(target=invoke) for _ in range(2)]
    for t in threads:
        t.start()

    # Give them a moment to hit the atomic guard and wait on the lock
    time.sleep(0.05)

    # Now release Thread 1 to finish start_run and record run_id
    release_starter.set()

    t1.join(timeout=5)
    for t in threads:
        t.join(timeout=5)

    assert len(results) == 3
    # Exactly one live run was started
    assert len(calls) == 1, f"expected 1 starter call, got {len(calls)}"

    statuses = [res[1]["result"]["status"] for res in results]
    assert statuses.count("running") == 1, f"expected 1 running, got {statuses}"
    assert statuses.count("already_running") == 2, f"expected 2 already_running, got {statuses}"

    run_ids = [res[1]["result"]["run_id"] for res in results]
    assert set(run_ids) == {"run-concurrent-1"}
def test_08c_start_resolver_claims_ownership_through_db_cas(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services.git import branch_merge as bm_mod

    p = proj
    out, _before = p.conflict()
    merge_id = out["merge_id"]
    real_cas = db_git.cas_session_context
    cas_calls = []

    def spy_cas(mid, expected_raw, context):
        # a snapshot: start_resolver goes on mutating the same dict (ai.status -> running)
        cas_calls.append((mid, expected_raw, copy.deepcopy(context)))
        return real_cas(mid, expected_raw, context)

    monkeypatch.setattr(db_git, "cas_session_context", spy_cas)
    result = bm_mod.start_resolver(
        p.pid, merge_id,
        start_run=lambda _provider, _messages: "run-cas-owner",
        provider_id="prov-cas",
    )
    assert result["result"]["status"] == "running"
    assert result["result"]["run_id"] == "run-cas-owner"
    assert len(cas_calls) == 1
    assert cas_calls[0][0] == merge_id
    assert cas_calls[0][2]["ai"]["status"] == "starting"
    assert cas_calls[0][2]["ai"]["provider_id"] == "prov-cas"
def test_08d_session_context_cas_rejects_stale_parallel_start_claim(proj):
    from modules.flow_gate.db import git_integration as db_git

    p = proj
    out, _before = p.conflict()
    merge_id = out["merge_id"]
    session = db_git.get_session(merge_id)
    raw = session["context"]
    original = db_git.session_context(session)

    first = dict(original)
    first_ai = dict(first.get("ai") or {})
    first_ai.update({
        "status": "starting",
        "provider_id": "winner",
        "requested_at": "2026-09-28T00:00:00+00:00",
    })
    first["ai"] = first_ai

    stale = dict(original)
    stale_ai = dict(stale.get("ai") or {})
    stale_ai.update({
        "status": "starting",
        "provider_id": "loser",
        "requested_at": "2026-09-28T00:00:00+00:00",
    })
    stale["ai"] = stale_ai

    assert db_git.cas_session_context(merge_id, raw, first) is True
    assert db_git.cas_session_context(merge_id, raw, stale) is False
    latest = db_git.session_context(db_git.get_session(merge_id))
    assert latest["ai"]["provider_id"] == "winner"
def test_09_10_ai_resolution_stops_at_review_and_moves_nothing(proj):
    p = proj
    out, before = p.conflict(push=True, publish=True)
    merge_id = out["merge_id"]
    remote_before = p.remote_sha("develop")
    result = _resolve_all(p, merge_id, run_id="run-ai-9")
    assert result["status"] == "resolved_pending_review" and result["merge_commit"] is None
    ctx = _ctx(merge_id)
    assert ctx["review_state"] == "resolved_pending_review"
    assert ctx["resolver_run_id"] == "run-ai-9" and ctx["review_fingerprint"]
    assert ctx["conflict_origins"]
    view = _view(p, merge_id)
    assert view["state"] == "resolved_pending_review" and view["resolver_type"] == "ai"
    # 10: target ref and remote untouched before approval
    assert p.sha("refs/heads/develop") == before
    assert p.remote_sha("develop") == remote_before
    events = [e["type"] for e in ctx["attempt_events"]]
    assert "git_branch_merge_resolved" in events and "git_branch_merge_review_pending" in events
