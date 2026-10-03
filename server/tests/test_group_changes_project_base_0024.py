"""flowgate.v02.0024 TR0004 rev2 — ``/changes`` and ``/diff`` against an explicit Base.

The Designer File Explorer's ``변경됨`` filter is defined against the PROJECT Base
branch (0024-D §5.2/§12: "whatever the Flow branch/worktree, compare with the project
Base"). ``_group_diff_context`` resolves a group's own pinned ``work_base_ref`` first,
so a group forked from a rolling branch (this very group: project Base ``main``,
work base ``v0.2``) could not be listed against the project Base at all. These tests
build that two-Base shape in a real repository and check that an explicit ``base``
selects the comparison branch while the default stays the group's own work base.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from modules.flow_gate.api.v1 import git_routes
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.services import git_service
from modules.flow_gate.services.git.credentials import GitServiceError


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False
    )


def _commit(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    assert _git(repo, "add", name).returncode == 0
    assert _git(repo, "commit", "-m", message).returncode == 0


@pytest.fixture
def two_base_group(tmp_path, monkeypatch):
    """Project Base ``main``; the group is pinned to ``v0.2`` and forked from it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(repo, "init", "-b", "main").returncode == 0
    assert _git(repo, "config", "user.name", "Test").returncode == 0
    assert _git(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _git(repo, "config", "core.autocrlf", "false").returncode == 0
    _commit(repo, "README.md", "base\n", "base")

    # v0.2: the rolling integration branch already carrying a sibling group's work.
    assert _git(repo, "switch", "-c", "v0.2").returncode == 0
    _commit(repo, "sibling.py", "sibling = True\n", "sibling group merged into v0.2")

    # The Flow's own branch: one commit, one uncommitted edit, one untracked file.
    assert _git(repo, "switch", "-c", "flow-0024").returncode == 0
    _commit(repo, "mine.py", "mine = True\n", "this flow's committed work")
    (repo / "README.md").write_text("base\nedited in the worktree\n", encoding="utf-8")
    (repo / "draft.txt").write_text("draft\n", encoding="utf-8")

    cfg = {"enabled": 1, "base_branch": "main"}
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _pid: cfg)
    monkeypatch.setattr(
        git_service.db_git, "get_state",
        lambda _gid: {"branch": "flow-0024", "worktree_registered": 1, "project_id": "p"},
    )
    monkeypatch.setattr(git_service, "_base_root_of", lambda _pid: repo)
    monkeypatch.setattr(git_service, "_project_name", lambda _pid: "Proj")
    monkeypatch.setattr(git_service, "src_root", lambda _name, _branch: repo)
    monkeypatch.setattr(git_service, "_group_untracked_safe", lambda *_a: ["draft.txt"])
    monkeypatch.setattr(db_groups, "get_by_id", lambda _gid: {"work_base_ref": "v0.2"})
    return repo


def _statuses(payload: dict) -> dict[str, str]:
    return {change["path"]: change["status"] for change in payload["data"]["changes"]}


def test_default_still_compares_against_the_pinned_group_work_base(two_base_group):
    payload = git_service.read_group_changes("p", "g")
    assert payload["data"]["base_branch"] == "v0.2"
    assert _statuses(payload) == {"mine.py": "A", "README.md": "M", "draft.txt": "?"}


def test_explicit_project_base_lists_commits_and_worktree_against_that_base(two_base_group):
    expected_merge_base = _git(two_base_group, "merge-base", "main", "flow-0024").stdout.strip()

    payload = git_service.read_group_changes("p", "g", base_ref="main")

    assert payload["data"]["base_branch"] == "main"
    # Everything the Flow carries relative to the project Base: the v0.2 file it forked
    # with, its own commit, its uncommitted edit and its untracked file.
    assert _statuses(payload) == {
        "sibling.py": "A", "mine.py": "A", "README.md": "M", "draft.txt": "?",
    }
    base_branch, _branch, _commit_sha, _wt, merge_base = git_service._group_diff_context(
        "p", "g", base_ref="main"
    )
    assert (base_branch, merge_base) == ("main", expected_merge_base)


def test_file_diff_uses_the_same_explicit_base(two_base_group):
    pinned = git_service.read_group_file_diff("p", "g", "sibling.py")["data"]
    assert pinned["base_branch"] == "v0.2"
    assert pinned["old"]["exists"] is True

    project = git_service.read_group_file_diff("p", "g", "sibling.py", base_ref="main")["data"]
    assert project["base_branch"] == "main"
    assert project["old"]["exists"] is False
    assert project["status"] == "A"


@pytest.mark.parametrize(
    ("base", "status", "code"),
    [
        ("", 422, "invalid_base"),
        (" main", 422, "invalid_base"),
        ("HEAD", 422, "invalid_base"),
        ("-main", 422, "invalid_base"),
        ("bad..name", 422, "invalid_base"),
        ("main~1", 422, "invalid_base"),
        ("no-such-branch", 404, "base_not_found"),
    ],
)
def test_explicit_base_must_be_an_existing_local_branch(two_base_group, base, status, code):
    with pytest.raises(GitServiceError) as caught:
        git_service.read_group_changes("p", "g", base_ref=base)
    assert (caught.value.status, caught.value.code) == (status, code)


def test_routes_pass_the_base_query_through(two_base_group):
    changes = git_routes.get_group_branch_changes("p", "g", base="main", user=None)
    assert changes["data"]["base_branch"] == "main"
    assert "sibling.py" in _statuses(changes)

    default = git_routes.get_group_branch_changes("p", "g", user=None)
    assert default["data"]["base_branch"] == "v0.2"

    diff = git_routes.get_group_file_diff("p", "g", path="sibling.py", base="main", user=None)
    assert diff["data"]["status"] == "A"

    refused = git_routes.get_group_branch_changes("p", "g", base="no-such-branch", user=None)
    assert refused.status_code == 404
