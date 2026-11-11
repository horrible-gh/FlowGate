"""flowgate.v02.0158 — one check card candidate lookup reads the open merge attempts once.

Before: every listed non-base name's ``inspect_workspace`` (and the base name's slot check) called
``open_merge_attempts``, which re-reads every open session of the server, so a project with many local branches and
open sessions spent the view's one CHECK_TARGET_BUDGET_SEC budget on the same read again and again. Now the first read
is kept for the rest of that one ``_compute_snapshot``; saving / confirming / running (``judge_check_target`` outside a
view) and every merge path still read afresh, and a failed read is never kept.

No Git and no database: the basis, the shared reads, the session table and the workspace marker are replaced in the
module; ``inspect_workspace`` and the base slot check run as they are.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")
os.environ.setdefault("FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode())

_SERVER_DIR = Path(__file__).resolve().parents[3]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

REFS = {name: (c * 40, "commit") for name, c in
        (("develop", "b"), ("feature-a", "d"), ("feature-b", "e"), ("main", "a"), ("release", "c"))}


def _session(merge_id: int, project: str, branch: str, *, is_base: bool = False) -> dict:
    record = {"branch": branch, "is_project_base": is_base, "owner": f"group:{project}.g9", "started_at": "t"}
    return {"merge_id": merge_id, "group_id": f"{project}.g9", "status": "open", "kind": "merge",
            "project": project, "context": json.dumps({"merge_target": record})}


@pytest.fixture
def env(monkeypatch, tmp_path):
    from modules.flow_gate.services import git_service
    from modules.flow_gate.services.git import group_work_base, lock
    from modules.flow_gate.services.git import merge_target as mt

    basis = mt.CheckTargetBasis(project_id="p1", group_id="g1", cfg={}, base_root=Path("."),
                                base_branch="main", work_base_ref="main", source_branch="g1_branch")
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 30.0)
    monkeypatch.setattr(mt, "check_target_basis", lambda *_a, **_k: basis)
    monkeypatch.setattr(mt, "raise_if_base_checkout_off_branch", lambda *_a: None)
    monkeypatch.setattr(group_work_base, "local_branch_refs", lambda _root: dict(REFS))
    monkeypatch.setattr(mt, "check_scope_floor", lambda _basis: "f" * 40)
    monkeypatch.setattr(mt, "_base_dirty", lambda _root: False)
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda _pid: [])
    monkeypatch.setattr(git_service, "resolve_group_work_base_ref", lambda *_a, **_k: "main")
    # inspect_workspace's own reads other than the sessions: no marker, no tree, no blocker lookup
    monkeypatch.setattr(mt, "workspace_dir", lambda _pid, branch: tmp_path / branch)
    monkeypatch.setattr(mt, "read_owner_marker", lambda _wdir: None)
    monkeypatch.setattr(mt, "_blocker_details", lambda *_a: {})
    monkeypatch.setattr(git_service, "_session_project", lambda session: session["project"])
    # the base slot check's lock reads (they keep their own session reads; not this change)
    monkeypatch.setattr(lock, "guard_base_free", lambda _pid: None)
    monkeypatch.setattr(lock, "base_merge_in_progress", lambda _pid: None)

    state = {"calls": 0, "fail": 0,
             "sessions": [_session(7, "p1", "release"), _session(8, "p2", "develop")]}

    def list_open_sessions():
        state["calls"] += 1
        if state["fail"]:
            state["fail"] -= 1
            raise RuntimeError("session table unavailable")
        return [dict(s) for s in state["sessions"]]

    monkeypatch.setattr(git_service.db_git, "list_open_sessions", list_open_sessions)
    mt.reset_check_targets()
    yield mt, state
    assert mt.settle_check_targets(10)
    mt.reset_check_targets()


def test_0158_one_view_reads_the_open_attempts_once_and_judges_each_name_from_it(env):
    mt, state = env
    view = mt.check_target_view("p1", "g1", None, refresh=True)
    assert view["merge_target_state"]["status"] == "ok"
    # 4 non-base names + the base: one session read in all (before: one per name)
    assert state["calls"] == 1
    # the kept read still tells which workspace is claimed in THIS project only: release (p1) is refused, develop
    # (claimed in p2) is not
    assert view["merge_target_options"] == ["develop", "feature-a", "feature-b", "main"]
    verdicts = next(iter(mt._check_cache.values()))["verdicts"]
    assert verdicts["release"]["error"] == "merge_target_owner_mismatch"
    assert mt._attempts_memo.get() is None                 # nothing outlives the computation


def test_0158_a_base_attempt_seen_through_the_kept_read_still_refuses_the_base(env):
    mt, state = env
    state["sessions"].append(_session(9, "p1", "main", is_base=True))
    view = mt.check_target_view("p1", "g1", None, refresh=True)
    assert view["merge_target_state"]["status"] == "ok"
    assert state["calls"] == 1
    assert view["merge_target_options"] == ["develop", "feature-a", "feature-b"]
    assert view["merge_target_state"]["default"]["error"] == "merge_target_busy"


def test_0158_each_view_reads_afresh(env):
    """A refreshed view never reuses an earlier view's attempts: an attempt opened in between is seen."""
    mt, state = env
    assert "develop" in mt.check_target_view("p1", "g1", None, refresh=True)["merge_target_options"]
    state["sessions"].append(_session(10, "p1", "develop"))
    view = mt.check_target_view("p1", "g1", None, refresh=True)
    assert state["calls"] == 2
    assert "develop" not in view["merge_target_options"]


def test_0158_outside_a_view_every_call_reads_afresh(env):
    """Saving, confirming and running (and every merge path) are not changed: each call reads the sessions."""
    mt, state = env
    assert mt.inspect_workspace("p1", "develop")[0] == "free"
    assert mt.inspect_workspace("p1", "release")[0] == "mismatch"
    assert len(mt.open_merge_attempts("p1")) == 1
    assert state["calls"] == 3


def test_0158_a_failed_read_is_not_kept(env):
    mt, state = env
    token = mt._attempts_memo.set({})
    try:
        state["fail"] = 1
        with pytest.raises(RuntimeError):
            mt.open_merge_attempts("p1")
        assert [s["merge_id"] for s in mt.open_merge_attempts("p1")] == [7]
        assert [s["merge_id"] for s in mt.open_merge_attempts("p1")] == [7]
    finally:
        mt._attempts_memo.reset(token)
    assert state["calls"] == 2


def test_0158_a_failed_read_inside_a_view_fails_the_list_and_names_the_read(env, caplog):
    mt, state = env
    caplog.set_level(logging.WARNING, logger=mt.__name__)
    state["fail"] = 1
    view = mt.check_target_view("p1", "g1", None, refresh=True)
    st = view["merge_target_state"]
    assert (st["status"], st["code"], st["reason"]) == ("failed", "merge_target_options_unavailable", "probe_failed")
    assert view["merge_target_options"] == []
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("check target lookup")]
    assert len(lines) == 1 and "failed_read=attempts:RuntimeError" in lines[0], lines
    assert mt._attempts_memo.get() is None
