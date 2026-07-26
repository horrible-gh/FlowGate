"""flowgate.default.0267 TR0005 — required tests for the file/folder delete API.

Covers the NR0003 '필수 테스트' items that touch the server contract:
  - file delete success (+ base_git returned so the explorer can refresh base-dirty)
  - empty / non-empty folder recursive delete
  - non-existent path (404 NOT_FOUND) and type mismatch (409 TYPE_MISMATCH)
  - root / absolute / '..' / drive-prefix / symlink rejection (400 INVALID_PATH)
  - unauthorized user and other-project token rejection (403 FORBIDDEN)
  - group-scoped delete resolves that group's worktree — 0327 TR0005 (see below)
  - delete failure surfaces 500 DELETE_FAILED without corrupting the response contract

0327 TR0005 supersedes NR0003 권고 4/권고 5 for the group case. Those recommendations
assumed a group delete would hard-delete inside the BASE checkout, which is why they
tied it to the finalize E3 base-contamination guard and refused every group delete with
403. Group context is now resolved to that group's OWN worktree (fail-closed — never a
base fallback), so base is not touched and the E3 reasoning no longer applies; the
contract asserted here is the new one. The rest of this file (permission gate, path
validation, 404/409, DELETE_FAILED) is unchanged.

Auth is stubbed on the tree_routes module (verify_bearer / has_permission are imported
there at module scope), the src root is redirected to an on-disk scratch dir, and the
base-git status probe is stubbed so no real git runs.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.storage import paths as storage_paths
from modules.flow_gate.services import git_service
from modules.flow_gate.api.v1 import tree_routes as tr

PID = "proj-del-tr0005"
ROOT = Path(__file__).resolve().parent / "_scratch_tr0005_delete_src"
BASE_GIT_SENTINEL = {"dirty": True, "files": ["docs/gone.md"]}


def _url() -> str:
    return f"/flowgate/api/v1/projects/{PID}/files"


def _client(monkeypatch, *, is_user_jwt=True, can_delete=True) -> TestClient:
    shutil.rmtree(ROOT, ignore_errors=True)
    ROOT.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        db_projects, "get_by_id",
        lambda pid: {"project_name": "del-project"} if pid == PID else None,
    )
    monkeypatch.setattr(db_projects, "get_settings", lambda _pid: {"branch": "main"})
    monkeypatch.setattr(storage_paths, "src_root", lambda _name, _branch: ROOT)
    rec = {"issued_to": "u1"}
    if is_user_jwt:
        rec["_is_user_jwt"] = True
    monkeypatch.setattr(tr, "verify_bearer", lambda _r: rec)
    monkeypatch.setattr(tr, "has_permission", lambda _u, _p, _perm: can_delete)
    monkeypatch.setattr(git_service, "base_checkout_dirty_status", lambda _pid: BASE_GIT_SENTINEL)
    return TestClient(app)


def teardown_module(_module) -> None:
    shutil.rmtree(ROOT, ignore_errors=True)


# ── success paths ────────────────────────────────────────────────────────────

def test_delete_file_success_returns_base_git(monkeypatch):
    client = _client(monkeypatch)
    f = ROOT / "docs" / "gone.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x", encoding="utf-8")
    res = client.request("DELETE", _url(), json={"path": "docs/gone.md", "type": "file"})
    assert res.status_code == 200
    body = res.json()
    assert body["deleted"] == "docs/gone.md"
    assert body["type"] == "file"
    # NR0003 권장 8: base-git status is returned so the explorer refreshes base-dirty/finalize.
    assert body["base_git"] == BASE_GIT_SENTINEL
    assert not f.exists()


def test_delete_empty_folder_success(monkeypatch):
    client = _client(monkeypatch)
    d = ROOT / "emptydir"
    d.mkdir(parents=True, exist_ok=True)
    res = client.request("DELETE", _url(), json={"path": "emptydir", "type": "folder"})
    assert res.status_code == 200
    assert res.json()["type"] == "folder"
    assert not d.exists()


def test_delete_non_empty_folder_is_recursive(monkeypatch):
    client = _client(monkeypatch)
    d = ROOT / "full"
    (d / "sub").mkdir(parents=True, exist_ok=True)
    (d / "sub" / "a.md").write_text("a", encoding="utf-8")
    (d / "b.md").write_text("b", encoding="utf-8")
    res = client.request("DELETE", _url(), json={"path": "full", "type": "folder"})
    assert res.status_code == 200
    assert not d.exists()


# ── not found / type mismatch ────────────────────────────────────────────────

def test_delete_missing_path_returns_404(monkeypatch):
    client = _client(monkeypatch)
    res = client.request("DELETE", _url(), json={"path": "docs/nope.md", "type": "file"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_delete_type_mismatch_on_disk_returns_409(monkeypatch):
    client = _client(monkeypatch)
    (ROOT / "realdir").mkdir(parents=True, exist_ok=True)
    res = client.request("DELETE", _url(), json={"path": "realdir", "type": "file"})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "TYPE_MISMATCH"
    assert (ROOT / "realdir").exists()  # nothing deleted on mismatch


def test_delete_invalid_type_value_returns_400(monkeypatch):
    client = _client(monkeypatch)
    res = client.request("DELETE", _url(), json={"path": "docs/x.md", "type": "symlink"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "TYPE_MISMATCH"


# ── path rejection ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_path", ["", ".", "/etc/passwd", "../secret", "C:/Windows", "a/../../b"])
def test_delete_rejects_unsafe_paths(monkeypatch, bad_path):
    client = _client(monkeypatch)
    res = client.request("DELETE", _url(), json={"path": bad_path, "type": "file"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_PATH"


def test_delete_rejects_symlink_component(monkeypatch):
    client = _client(monkeypatch)
    outside = ROOT.parent / "_scratch_tr0005_outside"
    shutil.rmtree(outside, ignore_errors=True)
    (outside / "sub").mkdir(parents=True, exist_ok=True)
    (outside / "sub" / "victim.md").write_text("v", encoding="utf-8")
    link = ROOT / "linkdir"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    try:
        res = client.request("DELETE", _url(), json={"path": "linkdir/sub/victim.md", "type": "file"})
        assert res.status_code == 400
        assert res.json()["error"]["code"] == "INVALID_PATH"
        # the real file behind the symlink must remain untouched
        assert (outside / "sub" / "victim.md").exists()
    finally:
        shutil.rmtree(outside, ignore_errors=True)


# ── authorization ────────────────────────────────────────────────────────────

def test_delete_forbidden_without_delete_permission(monkeypatch):
    # NR0003 권장 4: a user JWT without perm_document_delete is rejected.
    client = _client(monkeypatch, is_user_jwt=True, can_delete=False)
    (ROOT / "keep.md").write_text("k", encoding="utf-8")
    res = client.request("DELETE", _url(), json={"path": "keep.md", "type": "file"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"
    assert (ROOT / "keep.md").exists()


def test_delete_worker_token_without_delete_permission_is_rejected(monkeypatch):
    # NR0003 권장 4: worker/outbound tokens (and cross-project tokens) must ALSO be gated
    # by perm_document_delete — not just perm_document_read. is_user_jwt=False models a
    # worker token; can_delete=False models a token whose issued_to user lacks delete on
    # THIS project (e.g. a token bound to a different project).
    client = _client(monkeypatch, is_user_jwt=False, can_delete=False)
    (ROOT / "keep.md").write_text("k", encoding="utf-8")
    res = client.request("DELETE", _url(), json={"path": "keep.md", "type": "file"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"
    assert (ROOT / "keep.md").exists()


# ── group-scoped delete (0327 TR0005 — supersedes NR0003 권고 4/권고 5) ─────────

GID = "flowgate.default.0267"


def _stub_group(monkeypatch, *, worktree: Path | None, exists: bool = True) -> None:
    """Point the group lookup + worktree resolver at a controlled fixture."""
    from modules.flow_gate.db import groups as db_groups

    monkeypatch.setattr(
        db_groups, "get_by_id",
        lambda gid: {"group_id": gid, "project_id": PID} if (exists and gid == GID) else None,
    )
    monkeypatch.setattr(
        git_service, "effective_src_root_ex",
        lambda _pid, _gid: (worktree, "worktree" if worktree else "dir_missing"),
    )


def test_delete_on_group_resolves_group_worktree(monkeypatch):
    """0327 TR0005: a group delete removes the file from THAT group's worktree and
    leaves the base checkout's same-named file alone (so finalize's E3 base guard is
    never involved — the reason the old 403 blanket rule existed)."""
    client = _client(monkeypatch)
    wt = ROOT.parent / "_scratch_tr0005_group_wt"
    shutil.rmtree(wt, ignore_errors=True)
    (wt / "docs").mkdir(parents=True, exist_ok=True)
    (wt / "docs" / "gone.md").write_text("group copy", encoding="utf-8")
    (ROOT / "docs").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs" / "gone.md").write_text("base copy", encoding="utf-8")
    _stub_group(monkeypatch, worktree=wt)
    try:
        res = client.request(
            "DELETE", _url(),
            json={"path": "docs/gone.md", "type": "file", "group_id": GID},
        )
        assert res.status_code == 200
        assert res.json()["deleted"] == "docs/gone.md"
        assert not (wt / "docs" / "gone.md").exists()
        assert (ROOT / "docs" / "gone.md").read_text(encoding="utf-8") == "base copy"
    finally:
        shutil.rmtree(wt, ignore_errors=True)


def test_delete_on_group_without_worktree_is_409_and_spares_base(monkeypatch):
    """Fail-closed: an unresolvable worktree must NOT fall back to the base checkout.
    (The pre-0327 version of this test passed a group_id that does not exist at all;
    that case is now asserted separately below as a 404.)"""
    client = _client(monkeypatch)
    (ROOT / "keep.md").write_text("k", encoding="utf-8")
    _stub_group(monkeypatch, worktree=None)
    res = client.request(
        "DELETE", _url(),
        json={"path": "keep.md", "type": "file", "group_id": GID},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "WORKTREE_UNAVAILABLE"
    assert (ROOT / "keep.md").exists()  # base checkout untouched


def test_delete_on_unknown_group_is_404(monkeypatch):
    client = _client(monkeypatch)
    (ROOT / "keep.md").write_text("k", encoding="utf-8")
    _stub_group(monkeypatch, worktree=None, exists=False)
    res = client.request(
        "DELETE", _url(),
        json={"path": "keep.md", "type": "file", "group_id": "flowgate.default.9999"},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "GROUP_NOT_FOUND"
    assert (ROOT / "keep.md").exists()


# ── delete failure ───────────────────────────────────────────────────────────

def test_delete_failure_surfaces_delete_failed(monkeypatch):
    import pathlib

    client = _client(monkeypatch)
    (ROOT / "boom.md").write_text("b", encoding="utf-8")

    def _raise(self, *args, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(pathlib.Path, "unlink", _raise)
    res = client.request("DELETE", _url(), json={"path": "boom.md", "type": "file"})
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "DELETE_FAILED"
