"""0478 T0010 — real Git regression coverage for conflict-token read roots."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.services import git_service
from modules.flow_gate.services import remote_tool_service as remote


GROUP_ID = "flowgate.default.0478"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture()
def conflict_repos(tmp_path: Path) -> dict[str, Path]:
    """Two real checkout roots share a committed tree but have distinct dirty values."""
    base = tmp_path / "base-release"
    base.mkdir()
    _git(base, "init")
    _git(base, "checkout", "-b", "release")
    _git(base, "config", "user.email", "test@example.invalid")
    _git(base, "config", "user.name", "FlowGate test")
    (base / "tracked.txt").write_text("committed value\n", encoding="utf-8", newline="\n")
    _git(base, "add", "tracked.txt")
    _git(base, "commit", "-m", "seed")
    _git(base, "update-ref", "refs/remotes/origin/main", "HEAD")

    group = tmp_path / "group-worktree"
    _git(tmp_path, "clone", str(base), str(group))
    _git(group, "config", "user.email", "test@example.invalid")
    _git(group, "config", "user.name", "FlowGate test")
    _git(group, "update-ref", "refs/remotes/origin/main", "HEAD")
    (base / "tracked.txt").write_text("finalize working value\n", encoding="utf-8", newline="\n")
    (group / "tracked.txt").write_text("tr working value\n", encoding="utf-8", newline="\n")
    return {"release": base, "group-branch": group}


def _wire_real_session(monkeypatch, repos: dict[str, Path], session_kind: str) -> dict:
    """Use the production resolver; only DB storage is replaced by an in-memory session."""
    session = {"merge_id": 31, "group_id": GROUP_ID, "status": "open", "kind": session_kind}
    monkeypatch.setattr(db_git, "get_session", lambda merge_id: session if merge_id == 31 else None)
    monkeypatch.setattr(db_git, "session_kind", lambda value: value["kind"])
    monkeypatch.setattr(db_git, "get_config", lambda project_id: {"enabled": True, "base_branch": "release"})
    monkeypatch.setattr(db_git, "get_state", lambda group_id: {"worktree_registered": True, "branch": "group-branch"})
    monkeypatch.setattr(git_service, "_project_of_group", lambda group_id: "flowgate")
    monkeypatch.setattr(git_service, "_project_name", lambda project_id: "flowgate")
    monkeypatch.setattr(git_service, "src_root", lambda project_name, branch: repos[branch])
    # Settings intentionally disagree. Conflict finalize resolution must use base_branch=release.
    monkeypatch.setattr(remote.db_projects, "get_settings", lambda project_id: {"branch": "wrong-settings-branch"})
    token = {"action_scope": "resolve_conflict", "merge_id": 31, "group_id": GROUP_ID}
    monkeypatch.setattr(remote, "_worker_token_for_grant", lambda grant: token)
    return {"project": "flowgate", "module": "default", "group_id": GROUP_ID}


def _four_working_tree_results(root: Path, expected: str) -> None:
    read, _ = remote._execute("read", {"path": "tracked.txt"}, root)
    grep, _ = remote._execute("grep", {"pattern": expected, "path": ""}, root)
    glob, _ = remote._execute("glob", {"pattern": "*.txt", "path": ""}, root)
    stat, _ = remote._execute("stat", {"path": "tracked.txt"}, root)
    assert read["content"] == expected + "\n"
    assert grep["matches"] == [{"file": "tracked.txt", "line": 1, "text": expected}]
    assert glob["paths"] == ["tracked.txt"]
    assert stat["exists"] is True and stat["type"] == "file"
    assert stat["size"] == len((expected + "\n").encode())


@pytest.mark.parametrize(
    ("session_kind", "expected_root", "expected_value"),
    [("tr_revert", "group-branch", "tr working value"), ("finalize", "release", "finalize working value")],
)
def test_real_conflict_session_root_makes_all_four_tools_read_its_dirty_checkout(
    monkeypatch, conflict_repos, session_kind, expected_root, expected_value
):
    """Regression: finalize is base checkout, whereas TR is the group worktree."""
    grant = _wire_real_session(monkeypatch, conflict_repos, session_kind)
    root = remote._resolve_src_root(grant, "read")
    assert root == conflict_repos[expected_root]
    _four_working_tree_results(root, expected_value)


def test_finalize_resolver_uses_git_base_branch_not_project_settings(monkeypatch, conflict_repos):
    grant = _wire_real_session(monkeypatch, conflict_repos, "finalize")
    assert git_service.resolve_conflict_src_root(GROUP_ID, 31) == conflict_repos["release"]
    assert remote._resolve_src_root(grant, "stat") == conflict_repos["release"]


@pytest.mark.parametrize("ref", ["HEAD", "origin/main"])
def test_explicit_ref_makes_all_four_tools_read_committed_tree(monkeypatch, conflict_repos, ref):
    grant = _wire_real_session(monkeypatch, conflict_repos, "finalize")
    root = remote._resolve_src_root(grant, "read")
    # First prove the working tree differs from the committed object.
    _four_working_tree_results(root, "finalize working value")
    read, _ = remote._execute("read", {"path": "tracked.txt", "ref": ref}, root)
    grep, _ = remote._execute("grep", {"pattern": "committed value", "path": "", "ref": ref}, root)
    glob, _ = remote._execute("glob", {"pattern": "*.txt", "path": "", "ref": ref}, root)
    stat, _ = remote._execute("stat", {"path": "tracked.txt", "ref": ref}, root)
    assert read["content"] == "committed value\n" and read["mode"] == "committed_tree"
    assert grep["matches"] == [{"file": "tracked.txt", "line": 1, "text": "committed value"}]
    assert glob["paths"] == ["tracked.txt"]
    assert stat["exists"] is True and stat["type"] == "file" and stat["size"] == len(b"committed value\n")


def test_ref_grep_treats_dash_prefixed_pattern_as_a_literal_expression(conflict_repos):
    result, _ = remote._exec_ref_grep(
        {"pattern": "--totally-bogus-flag-xyz", "ref": "HEAD"}, conflict_repos["release"]
    )
    assert result["matches"] == []


def test_working_tree_mode_never_invokes_git(monkeypatch, conflict_repos):
    monkeypatch.setattr(remote, "_git_ref_output", lambda *args, **kwargs: pytest.fail("unexpected git call"))
    _four_working_tree_results(conflict_repos["group-branch"], "tr working value")