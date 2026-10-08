"""Per-group work-base update regression (0626 T0004)."""
import subprocess
from pathlib import Path

import pytest

from modules.flow_gate.services import ai_invoke_service, git_service
from modules.flow_gate.services.git import finalize
from modules.flow_gate.db import group_ai_leases


GROUP = "demo.default.0001"


from group_lock_stub import group_store, spy_acquire, stub_work_base_floor  # noqa: F401
import pytest as _pytest_locks

# Group/base/remote work takes domain locks from the real lock manager (0669): these tests
# run on the real SQLite lock/job store instead of stubbing the removed project mutex.
pytestmark = _pytest_locks.mark.usefixtures("group_store")


@_pytest_locks.fixture(autouse=True)
def _verified_work_base_floor(monkeypatch):
    stub_work_base_floor(monkeypatch)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


def repo_pair(tmp_path: Path) -> tuple[Path, Path, Path]:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    base = tmp_path / "base"
    subprocess.run(["git", "clone", str(origin), str(base)], check=True, capture_output=True)
    git(base, "config", "user.name", "FlowGate Test")
    git(base, "config", "user.email", "flowgate@example.invalid")
    (base / "seed.txt").write_text("seed\n", encoding="utf-8")
    git(base, "add", ".")
    git(base, "commit", "-m", "seed")
    git(base, "push", "origin", "main")
    git(base, "branch", "v0.2")
    git(base, "switch", "v0.2")
    (base / "v1.txt").write_text("V1\n", encoding="utf-8")
    git(base, "add", ".")
    git(base, "commit", "-m", "V1")
    git(base, "push", "-u", "origin", "v0.2")
    git(base, "switch", "main")
    group = tmp_path / "group"
    git(base, "worktree", "add", "-b", "group/test", str(group), "v0.2")
    git(group, "config", "user.name", "FlowGate Test")
    git(group, "config", "user.email", "flowgate@example.invalid")
    return origin, base, group


def setup_update(monkeypatch, base: Path, group: Path, *, work_base: str = "v0.2") -> None:
    cfg = {"enabled": True, "base_branch": "main", "repo_url": str(base.parent / "origin.git")}
    state = {"branch": "group/test", "status": "waiting", "worktree_registered": 1}
    monkeypatch.setattr(git_service, "_finalize_context", lambda _gid: (cfg, state, "demo", base, group))
    monkeypatch.setattr(git_service, "resolve_group_work_base_ref", lambda *_a, **_k: work_base)
    monkeypatch.setattr(git_service.db_git, "get_open_session_by_group", lambda _gid: None)
    monkeypatch.setattr(git_service, "guard_base_free", lambda _pid: None)
    monkeypatch.setattr(finalize, "_guard_group_update_ai_idle", lambda _gid: None)
    monkeypatch.setattr(ai_invoke_service, "has_active_run", lambda _gid: False)


def test_nondefault_update_keeps_main_unmerged_and_does_not_push(tmp_path, monkeypatch):
    origin, base, group = repo_pair(tmp_path)
    setup_update(monkeypatch, base, group)
    git(base, "switch", "v0.2")
    (base / "v2.txt").write_text("V2\n", encoding="utf-8")
    git(base, "add", ".")
    git(base, "commit", "-m", "V2 local only")
    v2 = git(base, "rev-parse", "v0.2")
    remote_before = git(base, "rev-parse", "origin/v0.2")
    git(base, "switch", "main")
    (base / "main_only.txt").write_text("main\n", encoding="utf-8")
    git(base, "add", ".")
    git(base, "commit", "-m", "main only")

    result = git_service.update_from_base(GROUP)

    assert result["result"]["status"] == "updated"
    assert git(group, "merge-base", "--is-ancestor", v2, "HEAD") == ""
    assert (group / "v2.txt").read_text(encoding="utf-8") == "V2\n"
    assert not (group / "main_only.txt").exists()
    assert git(base, "rev-parse", "origin/v0.2") == remote_before
    assert git(base, "ls-remote", "--heads", str(origin), "group/test") == ""


def test_dirty_group_is_absorbed_before_work_base_merge(tmp_path, monkeypatch):
    _, base, group = repo_pair(tmp_path)
    setup_update(monkeypatch, base, group)
    (group / "worker.txt").write_text("preserved\n", encoding="utf-8")
    git(base, "switch", "v0.2")
    (base / "next.txt").write_text("next\n", encoding="utf-8")
    git(base, "add", ".")
    git(base, "commit", "-m", "next")
    git(base, "switch", "main")

    result = git_service.update_from_base(GROUP)

    assert result["result"]["status"] == "updated"
    assert (group / "worker.txt").read_text(encoding="utf-8") == "preserved\n"
    assert (group / "next.txt").read_text(encoding="utf-8") == "next\n"
    assert git(group, "status", "--porcelain") == ""
    assert "preserve" in git(group, "log", "--format=%s", "-3")


@pytest.mark.parametrize("active_run,active_lease,code", [
    (True, False, "run_already_active"),
    (False, True, "group_lease_active"),
])
def test_active_ai_rejects_before_absorb_or_merge(
    tmp_path, monkeypatch, active_run, active_lease, code,
):
    _, base, group = repo_pair(tmp_path)
    real_guard = finalize._guard_group_update_ai_idle
    setup_update(monkeypatch, base, group)
    monkeypatch.setattr(finalize, "_guard_group_update_ai_idle", real_guard)
    monkeypatch.setattr(ai_invoke_service, "has_active_run", lambda _gid: active_run)
    monkeypatch.setattr(group_ai_leases, "get_active",
                        lambda _gid: {"run_id": "run"} if active_lease else None)
    acquired = spy_acquire(monkeypatch)
    (group / "worker.txt").write_text("in progress\n", encoding="utf-8")
    absorbed = []
    monkeypatch.setattr(finalize, "_absorb_worker_edits", lambda *_a, **_k: absorbed.append(True))
    with pytest.raises(git_service.GitServiceError) as exc:
        git_service.update_from_base(GROUP)
    assert exc.value.status == 409
    assert exc.value.code == code
    assert absorbed == []
    assert acquired == [], "Git update took a lock while AI was active"
    assert (group / "worker.txt").read_text(encoding="utf-8") == "in progress\n"
    assert git(group, "status", "--porcelain")


def test_nondefault_no_work_and_counts_use_work_base(tmp_path, monkeypatch):
    _, base, group = repo_pair(tmp_path)
    cfg = {"enabled": True, "base_branch": "main"}
    state = {"branch": "group/test", "status": "waiting", "worktree_registered": 1}
    monkeypatch.setattr(git_service, "resolve_group_work_base_ref", lambda *_a, **_k: "v0.2")
    monkeypatch.setattr(git_service, "_project_of_group", lambda _gid: "demo")
    monkeypatch.setattr(git_service, "_project_name", lambda _pid: "demo")
    monkeypatch.setattr(git_service, "src_root",
                        lambda _name, branch: group if branch == "group/test" else base)
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _pid: cfg)
    monkeypatch.setattr(git_service.db_git, "get_state", lambda _gid: state)
    monkeypatch.setattr(git_service.db_git, "get_open_session_by_group", lambda _gid: None)
    monkeypatch.setattr(git_service, "resolve_commit_message", lambda _gid: ("subject", "fallback"))
    monkeypatch.setattr(finalize.approval_intent, "group_is_final_approval_bound", lambda _gid: False)
    assert finalize._group_has_changes(cfg, state, "demo", GROUP) is False
    status = finalize._finalize_state(GROUP)["state"]
    assert status["work_base_ref"] == "v0.2"
    assert (status["ahead_count"], status["behind_count"]) == (0, 0)

    git(base, "switch", "v0.2")
    (base / "next.txt").write_text("next\n", encoding="utf-8")
    git(base, "add", ".")
    git(base, "commit", "-m", "next")
    git(base, "switch", "main")
    status = finalize._finalize_state(GROUP)["state"]
    assert (status["ahead_count"], status["behind_count"]) == (0, 1)


def test_legacy_group_updates_from_project_base(tmp_path, monkeypatch):
    _, base, group = repo_pair(tmp_path)
    setup_update(monkeypatch, base, group, work_base="main")
    (base / "main_next.txt").write_text("next\n", encoding="utf-8")
    git(base, "add", ".")
    git(base, "commit", "-m", "main next")
    assert git_service.update_from_base(GROUP)["result"]["status"] == "updated"
    assert (group / "main_next.txt").read_text(encoding="utf-8") == "next\n"


def test_nondefault_no_change_does_not_push(tmp_path, monkeypatch):
    _, base, group = repo_pair(tmp_path)
    setup_update(monkeypatch, base, group)
    remote_before = git(base, "rev-parse", "origin/v0.2")
    assert git_service.update_from_base(GROUP)["result"]["status"] == "no_change"
    assert git(base, "rev-parse", "origin/v0.2") == remote_before


def test_nondefault_conflict_uses_group_update_session(tmp_path, monkeypatch):
    _, base, group = repo_pair(tmp_path)
    setup_update(monkeypatch, base, group)
    (group / "v1.txt").write_text("group\n", encoding="utf-8")
    git(group, "commit", "-am", "group edit")
    git(base, "switch", "v0.2")
    (base / "v1.txt").write_text("work base\n", encoding="utf-8")
    git(base, "commit", "-am", "work base edit")
    git(base, "switch", "main")
    captured = {}
    def create_session(gid, files, *, kind, context):
        captured.update(gid=gid, files=files, kind=kind, context=context)
        return 42
    monkeypatch.setattr(git_service.db_git, "create_session", create_session)
    monkeypatch.setattr(git_service, "apply_eol_separation", lambda *_a: None)
    result = git_service.update_from_base(GROUP)["result"]
    assert {k: result[k] for k in ("status", "merge_id", "conflict_files")} == {
        "status": "conflict", "merge_id": 42, "conflict_files": ["v1.txt"]}
    assert result["source_ref"] == "v0.2" and result["source_sha"]   # 0665 T0004 pinned source
    assert captured["kind"] == git_service.db_git.SESSION_KIND_GROUP_UPDATE
    assert captured["gid"] == GROUP
    assert git(group, "diff", "--name-only", "--diff-filter=U") == "v1.txt"
    git(group, "merge", "--abort")


def test_ai_cannot_acquire_between_idle_check_and_absorb(tmp_path, monkeypatch):
    _, base, group = repo_pair(tmp_path)
    setup_update(monkeypatch, base, group)
    git(base, "switch", "v0.2")
    (base / "next.txt").write_text("next\n", encoding="utf-8")
    git(base, "add", ".")
    git(base, "commit", "-m", "next")
    git(base, "switch", "main")
    real_absorb = finalize._absorb_worker_edits
    attempts = []

    def absorb_with_racing_ai(*args, **kwargs):
        # This is the old TOCTOU window: the final idle probe has returned,
        # but the update has not yet absorbed or merged the worktree.
        assert group_ai_leases.get_active(GROUP)["action_scope"] == "group_update"
        blocker = group_ai_leases.acquire(
            group_id=GROUP, project_id="demo", run_id="ai-race",
            chain_id="ai-race", action_scope="edit", worker_identity="test",
        )
        attempts.append(blocker)
        assert blocker is not None
        assert blocker["run_id"].startswith("group-update:")
        return real_absorb(*args, **kwargs)

    monkeypatch.setattr(finalize, "_absorb_worker_edits", absorb_with_racing_ai)
    result = git_service.update_from_base(GROUP)
    assert result["result"]["status"] == "updated"
    assert len(attempts) == 1
    assert (group / "next.txt").read_text(encoding="utf-8") == "next\n"
    assert group_ai_leases.get_active(GROUP) is None


def test_ai_lease_winning_after_idle_check_prevents_update(tmp_path, monkeypatch):
    _, base, group = repo_pair(tmp_path)
    setup_update(monkeypatch, base, group)
    real_acquire = group_ai_leases.acquire
    absorbed = []
    monkeypatch.setattr(finalize, "_absorb_worker_edits", lambda *_a, **_k: absorbed.append(True))

    def ai_wins_before_update_reservation(**kwargs):
        real_acquire(
            group_id=GROUP, project_id="demo", run_id="ai-race",
            chain_id="ai-race", action_scope="edit", worker_identity="test",
        )
        return real_acquire(**kwargs)

    monkeypatch.setattr(group_ai_leases, "acquire", ai_wins_before_update_reservation)
    try:
        with pytest.raises(git_service.GitServiceError) as exc:
            git_service.update_from_base(GROUP)
        assert exc.value.status == 409
        assert exc.value.code == "group_lease_active"
        assert absorbed == []
        assert group_ai_leases.get_active(GROUP)["run_id"] == "ai-race"
    finally:
        group_ai_leases.release(GROUP, "ai-race")