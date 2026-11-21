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

def test_03_04_05_conflict_creates_persistent_attempt_and_keeps_workspace(proj):
    from modules.flow_gate.services import git_service as svc
    p = proj
    out, before = p.conflict(push=False)
    assert out["status"] == "conflict" and out["ok"] is True
    merge_id = out["merge_id"]
    assert out["conflict_files"] == ["same.txt"] and out["push"] is False
    session = _session(merge_id)
    assert session["status"] == "open" and session["group_id"] is None
    assert session["kind"] == "branch_merge" and session["project_id"] == p.pid
    ctx = _ctx(merge_id)
    assert ctx["attempt_state"] == "conflict"
    assert ctx["auto_authority"] is False and ctx["ai"]["auto_authority"] is False
    assert ctx["branch_merge"]["source_branch"] == "feature" and ctx["branch_merge"]["push"] is False
    assert ctx["resolver_baseline"]["base_head"] == before
    # 4: the managed workspace is still there, owned by this attempt
    tree = p.workspace("develop") / "tree"
    assert tree.is_dir()
    from modules.flow_gate.services.git import merge_target
    assert merge_target.read_owner_marker(p.workspace("develop"))["merge_id"] == merge_id
    # 5: no merge --abort — MERGE_HEAD and the markers are alive
    assert _git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=tree).strip()
    assert "<<<<<<<" in (tree / "same.txt").read_text(encoding="utf-8")
    # nothing moved
    assert p.sha("refs/heads/develop") == before
    assert svc.list_conflicts(None, merge_id, project_id=p.pid)["files"][0]["conflict_count"] == 1
    assert _view(p, merge_id)["state"] == "conflict"
def test_06_multi_file_partial_resolve_keeps_the_rest_open(proj):
    from modules.flow_gate.services import git_service as svc
    p = proj
    out, _before = p.conflict(files=("same.txt", "other.txt"))
    merge_id = out["merge_id"]
    assert sorted(out["conflict_files"]) == ["other.txt", "same.txt"]
    first = svc.resolve_conflicts(
        None, merge_id, [{"path": "same.txt", "content": "merged same\n"}], False,
        project_id=p.pid,
    )["result"]
    assert first["status"] == "conflict" and first["remaining_conflicts"] == ["other.txt"]
    view = _view(p, merge_id)
    assert view["state"] == "conflict_remaining"
    assert view["resolved_count"] == 1 and view["unresolved"] == ["other.txt"]
    assert view["resolver_type"] == "human"
def test_07_route_conflict_does_not_auto_start_resolver(proj, monkeypatch):
    """T#3: conflict route must NOT auto-start the AI resolver. The attempt is left
    open in 'conflict' state with ai.status=not_started and auto_start=False."""
    from modules.flow_gate.api.v1 import git_routes
    p = proj
    _prepare_route_conflict(p)
    calls = []

    def fake_start(**kwargs):
        calls.append(kwargs)
        return "run-should-never-fire"

    monkeypatch.setattr(git_routes, "_start_branch_merge_resolve_run", fake_start)
    resp = _client(monkeypatch).post(
        f"/api/v1/projects/{p.pid}/git/branches/merge",
        json={"source_branch": "feature", "target_branch": "develop", "push": False,
              "provider_id": "prov-x"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "conflict" and body["merge_id"]
    # T#3 key assertions: resolver was NOT started automatically
    assert calls == [], "auto-start must not fire on conflict (T#3)"
    ai = body["ai"]
    assert ai["status"] == "not_started", f"expected not_started, got {ai['status']}"
    assert ai["auto_start"] is False, "auto_start must be False (T#3)"
    assert ai["auto_authority"] is False
    assert ai.get("run_id") is None
    # Conflict event is recorded; AI-started event must NOT be present
    events = [e["type"] for e in _ctx(body["merge_id"])["attempt_events"]]
    assert "git_branch_merge_conflict" in events
    assert "git_branch_merge_ai_started" not in events, "ai_started must not appear (T#3)"
    # the merge's domain locks (target W/B and R) are released (nothing blocking)
    from modules.flow_gate.db import git_concurrency as gc
    assert gc.list_locks_in_scope(p.pid) == []
def test_08_explicit_ai_resolve_route_starts_resolver_and_duplicate_is_guarded(proj, monkeypatch):
    """T#3: /ai-resolve is the ONLY way to start the resolver.
    First call creates a run; duplicate call returns already_running (single live run)."""
    from modules.flow_gate.api.v1 import git_routes
    p = proj
    _prepare_route_conflict(p)
    calls = []

    def fake_start(**kwargs):
        calls.append(kwargs)
        return "run-explicit-1"

    monkeypatch.setattr(git_routes, "_start_branch_merge_resolve_run", fake_start)
    client = _client(monkeypatch)
    # Step 1: trigger conflict — no auto-start
    resp = client.post(
        f"/api/v1/projects/{p.pid}/git/branches/merge",
        json={"source_branch": "feature", "target_branch": "develop", "push": False},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    merge_id = body["merge_id"]
    assert body["ai"]["status"] == "not_started"    # T#3: no run yet
    assert calls == []                              # T#3: starter not called

    # Step 2: user opens dialog and clicks [AI 호출] → /ai-resolve
    from modules.flow_gate.services.git import branch_merge as bm_mod
    from modules.flow_gate.services.git.branch_merge import AI_RUNNING
    monkeypatch.setattr(bm_mod, "_ai_run_status", lambda run_id: AI_RUNNING)
    resp2 = client.post(
        f"/api/v1/projects/{p.pid}/git/merge/{merge_id}/ai-resolve",
        json={"provider_id": "prov-x"},
    )
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["result"]["status"] == "running"
    assert body2["result"]["run_id"] == "run-explicit-1"
    assert len(calls) == 1                          # exactly one run created
    assert calls[0]["provider_id"] == "prov-x"     # provider from dialog

    # Step 3: duplicate click — same live run returned, no second start
    resp3 = client.post(
        f"/api/v1/projects/{p.pid}/git/merge/{merge_id}/ai-resolve",
        json={"provider_id": "prov-x"},
    )
    assert resp3.status_code == 200, resp3.text
    body3 = resp3.json()
    assert body3["result"]["status"] == "already_running"
    assert len(calls) == 1                          # still only one run

    # Step 4: manual resolve still works after explicit AI path
    from modules.flow_gate.services import git_service as svc
    monkeypatch.setattr(bm_mod, "_ai_run_status", lambda run_id: "finished")
    svc.resolve_conflicts(
        None, merge_id, [{"path": "same.txt", "content": "manual\n"}], True,
        project_id=p.pid,
    )
    view = _view(p, merge_id)
    assert view["state"] == "resolved_pending_review"
    # /ai-resolve after review is blocked
    again = client.post(f"/api/v1/projects/{p.pid}/git/merge/{merge_id}/ai-resolve", json={})
    assert again.status_code == 409     # already in review: use the review screen
