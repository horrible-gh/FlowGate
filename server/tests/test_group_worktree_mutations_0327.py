"""flowgate.default.0327 T0004 — creating, uploading and downloading while a group
branch is selected in the file explorer.

B0001: "브랜치를 변경하면 우측 마우스 버튼이 거의 동작을 안한다 / 폴더·파일 생성이나
업로드 같은게 안된다". NR0003 traced it to the explorer equating "a group is selected"
with "read-only", even though the server already knows which groups own a live
worktree. These tests pin the server half of the fix:

  1. the group-slot payload says whether a slot is writable, so the client can stop
     guessing (NR0003 발견 3);
  2. create / upload / download / DELETE accept a group_id and resolve it to THAT
     group's worktree — fail-closed, never silently falling back to the base checkout,
     which would dirty base and block every other group's finalize (NR0003 권고 1·2·3);
  3. a folder created in a worktree is visible in the group tree even though git can
     express no empty directory — otherwise "새 폴더" appears to do nothing.

Delete (§4 below) supersedes NR0003 권고 4. That recommendation kept delete blocked in
every group context because it assumed delete meant "hard-delete in the BASE checkout",
entangling it with the finalize E3 base-contamination guard. Resolved against the
group's own worktree, base is never touched, so delete now follows exactly the same rule
as create/upload. The permission gate is unchanged and still runs first.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(args: list[str], cwd: Path) -> None:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A tiny real git repo with one committed file."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@example.com"], root)
    _git(["config", "user.name", "t"], root)
    (root / "kept.txt").write_text("kept", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "init"], root)
    return root


class _FakeGitDb:
    def __init__(self, cfg, state):
        self._cfg, self._state = cfg, state

    def get_config(self, _project_id):
        return self._cfg

    def get_state(self, _group_id):
        return self._state


def _worktree(monkeypatch, tmp_path: Path, *, registered: bool = True) -> Path:
    """Install a git_service whose group resolves to a real on-disk worktree."""
    from modules.flow_gate.services import git_service

    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    wt = tmp_path / "src" / "FlowGate Live" / "fg-0327"
    wt.mkdir(parents=True)
    # 0287 NR0004: a worktree is a directory WITH its .git link.
    (wt / ".git").write_text("gitdir: ../main/.git/worktrees/x", encoding="utf-8")
    monkeypatch.setattr(
        git_service,
        "db_git",
        _FakeGitDb(
            {"enabled": 1},
            {"worktree_registered": 1 if registered else 0, "branch": "fg-0327"},
        ),
    )
    monkeypatch.setattr(git_service, "_project_name", lambda _pid: "FlowGate Live")
    return wt.resolve()


# ── 1. empty folders survive the trip to the client ──────────────────────────


def test_build_tree_nodes_renders_a_folder_that_holds_no_file():
    from modules.flow_gate.services import git_service

    nodes = git_service._build_tree_nodes(["docs/a.md"], ["docs/brand_new", "top_new/deep"])
    by_path = {n["path"]: n for n in nodes}

    # The whole point of B0001: the folder the user just created is in the tree.
    assert by_path["docs/brand_new"]["type"] == "folder"
    assert by_path["top_new"]["type"] == "folder"
    assert by_path["top_new/deep"]["type"] == "folder"
    # …and it did not disturb the committed entries.
    assert by_path["docs/a.md"]["type"] == "file"
    assert by_path["docs"]["type"] == "folder"
    # Parent linkage still holds for the injected folders.
    assert by_path["top_new/deep"]["parent_id"] == by_path["top_new"]["id"]


def test_empty_dir_scan_lists_only_file_less_untracked_folders(repo: Path):
    from modules.flow_gate.services import git_service

    (repo / "empty_new").mkdir()
    (repo / "empty_new" / "nested").mkdir()
    (repo / "has_file").mkdir()
    (repo / "has_file" / "x.txt").write_text("x", encoding="utf-8")
    (repo / ".hidden_new").mkdir()

    found = git_service._group_empty_dirs_visible(repo)

    assert found == ["empty_new", "empty_new/nested"]
    # A folder holding an untracked file is already implied by that file's path;
    # listing it again here would double-register it.
    assert "has_file" not in found
    # Same exposure rule as the committed tree — dotfiles stay hidden.
    assert ".hidden_new" not in found


def test_empty_dir_scan_ignores_a_folder_that_is_already_tracked(repo: Path):
    from modules.flow_gate.services import git_service

    (repo / "tracked_dir").mkdir()
    (repo / "tracked_dir" / "f.txt").write_text("f", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "add tracked_dir"], repo)
    (repo / "tracked_dir" / "fresh_empty").mkdir()

    found = git_service._group_empty_dirs_visible(repo)

    # Only the new empty folder inside the tracked one, not the tracked one itself.
    assert found == ["tracked_dir/fresh_empty"]


# ── 2. the slot payload answers "may I write here?" ──────────────────────────


def test_group_worktree_writable_tracks_the_live_worktree(monkeypatch, tmp_path):
    from modules.flow_gate.services import git_service

    _worktree(monkeypatch, tmp_path)
    assert git_service.group_worktree_writable("flowgate", "flowgate.default.0327") is True

    # No group context at all is not a writable worktree either.
    assert git_service.group_worktree_writable("flowgate", None) is False


def test_group_worktree_writable_is_false_once_the_slot_is_released(monkeypatch, tmp_path):
    """NR0003 권고 5: a group whose worktree was released (merge/push cleanup) stays
    fully read-only — this signal is what keeps that promise."""
    from modules.flow_gate.services import git_service

    _worktree(monkeypatch, tmp_path, registered=False)

    assert git_service.group_worktree_writable("flowgate", "flowgate.default.0327") is False


# ── 3. create / upload / download are fail-closed on the group ───────────────


def test_create_lands_in_the_group_worktree(monkeypatch, tmp_path):
    from modules.flow_gate import process_service
    from modules.flow_gate.db import groups as db_groups

    wt = _worktree(monkeypatch, tmp_path)
    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: {"project_id": "flowgate"})

    assert process_service.create_storage_folder(
        "flowgate", "", "new_folder", group_id="flowgate.default.0327"
    ) == {"status": "success"}
    assert process_service.create_storage_file(
        "flowgate", "new_folder", "note.md", group_id="flowgate.default.0327"
    ) == {"status": "success"}

    assert (wt / "new_folder").is_dir()
    assert (wt / "new_folder" / "note.md").is_file()


def test_create_refuses_rather_than_writing_into_base(monkeypatch, tmp_path):
    """The whole hazard of routing writes by group: a missing worktree must NOT
    quietly become a base-checkout write (which dirties base and blocks finalize)."""
    from modules.flow_gate import process_service
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services import git_service

    _worktree(monkeypatch, tmp_path, registered=False)
    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: {"project_id": "flowgate"})
    base_calls: list = []
    monkeypatch.setattr(
        git_service, "base_src_root",
        lambda *a, **k: base_calls.append(a) or (tmp_path / "base"),
    )

    result = process_service.create_storage_folder(
        "flowgate", "", "new_folder", group_id="flowgate.default.0327"
    )

    assert result["status"] == "error"
    assert "worktree_unregistered" in result["message"]
    assert base_calls == [], "a group create must never resolve the base checkout"
    assert not (tmp_path / "base").exists()


def test_create_rejects_a_group_from_another_project(monkeypatch, tmp_path):
    from modules.flow_gate import process_service
    from modules.flow_gate.db import groups as db_groups

    _worktree(monkeypatch, tmp_path)
    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: {"project_id": "other-project"})

    result = process_service.create_storage_file(
        "flowgate", "", "x.md", group_id="flowgate.default.0327"
    )

    assert result["status"] == "error"
    assert "Group not found" in result["message"]


def test_create_rejects_a_parent_path_that_escapes_the_root(monkeypatch, tmp_path):
    from modules.flow_gate import process_service
    from modules.flow_gate.db import groups as db_groups

    _worktree(monkeypatch, tmp_path)
    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: {"project_id": "flowgate"})

    result = process_service.create_storage_folder(
        "flowgate", "../../escape", "evil", group_id="flowgate.default.0327"
    )

    assert result["status"] == "error"
    assert not (tmp_path / "escape").exists()


def test_upload_root_resolution_is_fail_closed_for_a_group(monkeypatch, tmp_path):
    from fastapi import HTTPException
    from modules.flow_gate.api.v1 import file_transfer_routes
    from modules.flow_gate.db import groups as db_groups

    wt = _worktree(monkeypatch, tmp_path)
    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: {"project_id": "flowgate"})

    assert file_transfer_routes._get_src_root("flowgate", "flowgate.default.0327") == wt

    # Worktree gone → 409, NOT a base-checkout upload (NR0003 권고 2 / the 0115
    # shared resolver would have fallen back here).
    _worktree(monkeypatch, tmp_path / "b", registered=False)
    with pytest.raises(HTTPException) as excinfo:
        file_transfer_routes._get_src_root("flowgate", "flowgate.default.0327")
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"]["code"] == "WORKTREE_UNAVAILABLE"


def test_download_reads_the_group_worktree_not_the_base_checkout(monkeypatch, tmp_path):
    """NR0003 권고 3: download is a read and is allowed in a group view — but it has
    to return the GROUP's bytes, or the user silently gets the base version."""
    from fastapi import HTTPException
    from modules.flow_gate.api.v1 import tree_routes
    from modules.flow_gate.db import groups as db_groups

    wt = _worktree(monkeypatch, tmp_path)
    (wt / "a.md").write_text("group copy", encoding="utf-8")
    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: {"project_id": "flowgate"})

    resolved = tree_routes._resolve_src_path("flowgate", "a.md", "flowgate.default.0327")
    assert resolved.read_text(encoding="utf-8") == "group copy"

    # Traversal is still refused inside the group root.
    with pytest.raises(HTTPException) as excinfo:
        tree_routes._resolve_src_path("flowgate", "../../outside.md", "flowgate.default.0327")
    assert excinfo.value.status_code == 403


# ── 4. delete is symmetric with create/upload (supersedes NR0003 권고 4) ───────

GID = "flowgate.default.0327"
DEL_URL = "/flowgate/api/v1/projects/flowgate/files"


def _delete_client(monkeypatch, tmp_path, *, registered=True, can_delete=True,
                   owner_project="flowgate"):
    """TestClient wired to a group worktree, with a real base checkout beside it."""
    from fastapi.testclient import TestClient
    from app import app
    from modules.flow_gate.api.v1 import tree_routes
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services import git_service

    wt = _worktree(monkeypatch, tmp_path, registered=registered)
    base = tmp_path / "base_checkout"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(db_groups, "get_by_id", lambda _gid: {"project_id": owner_project})
    monkeypatch.setattr(git_service, "base_src_root", lambda *a, **k: base)
    monkeypatch.setattr(git_service, "base_checkout_dirty_status", lambda _pid: {"dirty": False, "files": []})
    monkeypatch.setattr(tree_routes, "verify_bearer", lambda _r: {"issued_to": "u1", "_is_user_jwt": True})
    monkeypatch.setattr(tree_routes, "has_permission", lambda _u, _p, perm: can_delete or perm != "perm_document_delete")
    return TestClient(app), wt, base


def test_group_delete_removes_the_group_copy_and_leaves_base_alone(monkeypatch, tmp_path):
    """The claim that replaces 권고 4: a group delete is a working-tree change on that
    group's branch. The base checkout — whose dirtiness is what E3 guards — is intact."""
    client, wt, base = _delete_client(monkeypatch, tmp_path)
    (wt / "docs").mkdir(parents=True, exist_ok=True)
    (wt / "docs" / "note.md").write_text("group copy", encoding="utf-8")
    (base / "docs").mkdir(parents=True, exist_ok=True)
    (base / "docs" / "note.md").write_text("base copy", encoding="utf-8")

    res = client.request(
        "DELETE", DEL_URL, json={"path": "docs/note.md", "type": "file", "group_id": GID},
    )

    assert res.status_code == 200
    assert res.json()["deleted"] == "docs/note.md"
    assert not (wt / "docs" / "note.md").exists()
    assert (base / "docs" / "note.md").read_text(encoding="utf-8") == "base copy"


def test_group_delete_removes_a_folder_recursively(monkeypatch, tmp_path):
    client, wt, _base = _delete_client(monkeypatch, tmp_path)
    (wt / "full" / "sub").mkdir(parents=True, exist_ok=True)
    (wt / "full" / "sub" / "a.md").write_text("a", encoding="utf-8")

    res = client.request(
        "DELETE", DEL_URL, json={"path": "full", "type": "folder", "group_id": GID},
    )

    assert res.status_code == 200
    assert not (wt / "full").exists()


def test_group_delete_without_a_worktree_is_409_not_a_base_delete(monkeypatch, tmp_path):
    client, _wt, base = _delete_client(monkeypatch, tmp_path, registered=False)
    (base / "keep.md").write_text("k", encoding="utf-8")

    res = client.request(
        "DELETE", DEL_URL, json={"path": "keep.md", "type": "file", "group_id": GID},
    )

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "WORKTREE_UNAVAILABLE"
    assert (base / "keep.md").exists()


def test_group_delete_for_another_projects_group_is_404(monkeypatch, tmp_path):
    client, wt, _base = _delete_client(monkeypatch, tmp_path, owner_project="other-project")
    (wt / "keep.md").write_text("k", encoding="utf-8")

    res = client.request(
        "DELETE", DEL_URL, json={"path": "keep.md", "type": "file", "group_id": GID},
    )

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "GROUP_NOT_FOUND"
    assert (wt / "keep.md").exists()


def test_group_delete_still_requires_the_delete_permission(monkeypatch, tmp_path):
    """Opening group deletes must not loosen the gate — perm_document_delete first."""
    client, wt, _base = _delete_client(monkeypatch, tmp_path, can_delete=False)
    (wt / "keep.md").write_text("k", encoding="utf-8")

    res = client.request(
        "DELETE", DEL_URL, json={"path": "keep.md", "type": "file", "group_id": GID},
    )

    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"
    assert (wt / "keep.md").exists()


@pytest.mark.parametrize("bad_path", ["../escape.md", "/etc/passwd", "C:/Windows", "a/../../b", "."])
def test_group_delete_path_guard_is_anchored_at_the_group_root(monkeypatch, tmp_path, bad_path):
    client, _wt, _base = _delete_client(monkeypatch, tmp_path)
    outside = tmp_path / "escape.md"
    outside.write_text("v", encoding="utf-8")

    res = client.request(
        "DELETE", DEL_URL, json={"path": bad_path, "type": "file", "group_id": GID},
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_PATH"
    assert outside.exists()


def test_group_delete_rejects_a_symlink_component(monkeypatch, tmp_path):
    import os

    client, wt, _base = _delete_client(monkeypatch, tmp_path)
    victim = tmp_path / "victim_dir"
    shutil.rmtree(victim, ignore_errors=True)
    victim.mkdir(parents=True, exist_ok=True)
    (victim / "secret.md").write_text("s", encoding="utf-8")
    try:
        os.symlink(victim, wt / "linked")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")

    res = client.request(
        "DELETE", DEL_URL, json={"path": "linked/secret.md", "type": "file", "group_id": GID},
    )

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_PATH"
    assert (victim / "secret.md").exists()
