"""Read-only 3-way merge preview (`merge_preview`) — group 0524 T0006.

T0004/TR0005 explicitly deferred a merge-conflict preview to this T2 (T0004 §1,
TR0005 §4). `merge_preview` simulates merging the authorized group worktree's HEAD
onto target_ref via `git merge-tree --write-tree` (git 2.38+), without ever
touching HEAD, the index or the working tree, and reports whether the merge is
clean and which files would conflict.

Git-level tests reuse the `diverged_repo`/`_git` fixtures from
test_remote_history_show_0524.py (same base -> target / base -> worker(HEAD)
divergence, where `shared.txt` is modified differently on each side and
therefore conflicts). HTTP-pipeline tests reuse the DB-backed harness from
test_remote_tool_0003_T0012 (`RAW_TOKEN` / `_call` / `env`).
"""
from __future__ import annotations

import re

import pytest

from modules.flow_gate.services import remote_tool_service as svc
from test_remote_history_show_0524 import _git, diverged_repo  # noqa: F401  (fixture)


@pytest.fixture
def disjoint_repo(tmp_path):
    """base -> target adds b.txt, base -> worker(HEAD) adds a.txt: no overlapping
    hunks anywhere, so the 3-way merge is clean."""
    root = tmp_path / "repo-disjoint"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "worker@example.com")
    _git(root, "config", "user.name", "Worker")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base history")
    base = _git(root, "rev-parse", "HEAD")

    _git(root, "checkout", "-b", "target")
    (root / "b.txt").write_text("b\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "target adds b")
    target = _git(root, "rev-parse", "HEAD")

    _git(root, "checkout", "-b", "worker", base)
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "worker adds a")
    head_commit = _git(root, "rev-parse", "HEAD")

    _git(root, "update-ref", "refs/remotes/origin/main", target)
    return root, base, target, head_commit


@pytest.fixture
def multi_conflict_repo(tmp_path):
    """base -> target and base -> worker(HEAD) each rewrite the SAME three files
    differently, so all three conflict — used to exercise _MAX_CONFLICT_FILES."""
    root = tmp_path / "repo-multi-conflict"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "worker@example.com")
    _git(root, "config", "user.name", "Worker")
    for name in ("a.txt", "b.txt", "c.txt"):
        (root / name).write_text("base\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base history")
    base = _git(root, "rev-parse", "HEAD")

    _git(root, "checkout", "-b", "target")
    for name in ("a.txt", "b.txt", "c.txt"):
        (root / name).write_text("base\ntarget-only\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "target changes all three")
    target = _git(root, "rev-parse", "HEAD")

    _git(root, "checkout", "-b", "worker", base)
    for name in ("a.txt", "b.txt", "c.txt"):
        (root / name).write_text("base\nhead-only\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "worker changes all three")
    head_commit = _git(root, "rev-parse", "HEAD")

    _git(root, "update-ref", "refs/remotes/origin/main", target)
    return root, base, target, head_commit


# ── conflict / clean judgement ────────────────────────────────────────────────

def test_merge_preview_conflict_case_reports_the_one_conflicted_file(diverged_repo):
    root, base, target, head_commit = diverged_repo
    result, _ = svc._exec_merge_preview({}, root)

    assert result["clean"] is False
    assert result["merge_base"] == base
    assert result["head"] == head_commit
    assert result["target_ref"] == "origin/main"
    assert result["target_sha"] == target
    assert re.fullmatch(r"[0-9a-f]{40,64}", result["merge_tree"])
    # head.txt is a head-only new file -- it does not conflict with target.
    assert result["conflicts"] == ["shared.txt"]
    assert result["truncated"] is False


def test_merge_preview_clean_case_reports_no_conflicts(disjoint_repo):
    root, base, target, head_commit = disjoint_repo
    result, _ = svc._exec_merge_preview({}, root)

    assert result["clean"] is True
    assert result["conflicts"] == []
    assert result["merge_base"] == base
    assert result["head"] == head_commit
    assert result["target_sha"] == target
    assert re.fullmatch(r"[0-9a-f]{40,64}", result["merge_tree"])
    assert result["truncated"] is False


def test_merge_preview_merge_tree_oid_is_directly_readable_via_read_op(diverged_repo):
    root, _base, _target, _head_commit = diverged_repo
    preview, _ = svc._exec_merge_preview({}, root)

    result, _ = svc._execute("read", {"path": "shared.txt", "ref": preview["merge_tree"]}, root)

    assert "<<<<<<<" in result["content"]
    assert "=======" in result["content"]
    assert ">>>>>>>" in result["content"]


# ── read-only guarantee ────────────────────────────────────────────────────────

def test_merge_preview_never_changes_head_index_or_worktree(diverged_repo):
    root, _base, _target, _head_commit = diverged_repo
    before_head = _git(root, "rev-parse", "HEAD")
    before_status = _git(root, "status", "--porcelain")

    svc._exec_merge_preview({}, root)

    after_head = _git(root, "rev-parse", "HEAD")
    after_status = _git(root, "status", "--porcelain")
    assert after_head == before_head
    assert after_status == before_status == ""


# ── truncation ────────────────────────────────────────────────────────────────

def test_merge_preview_truncates_conflicts_at_the_cap(multi_conflict_repo, monkeypatch):
    root, _base, _target, _head = multi_conflict_repo
    monkeypatch.setattr(svc, "_MAX_CONFLICT_FILES", 2)

    result, _ = svc._exec_merge_preview({}, root)
    assert result["clean"] is False
    assert result["truncated"] is True
    assert len(result["conflicts"]) == 2


# ── request validation ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_ref", ["--upload-pack=evil", "-x", "a..b", "HEAD@{0}", ""])
def test_merge_preview_invalid_target_ref_is_422(bad_ref):
    with pytest.raises(svc._OpError) as exc:
        svc._validate_required("merge_preview", {"target_ref": bad_ref})
    assert exc.value.status == 422


def test_merge_preview_unknown_field_is_422():
    with pytest.raises(svc._OpError) as exc:
        svc._validate_allowed_fields("merge_preview", {"path": "x"})
    assert exc.value.status == 422
    assert exc.value.details == {"reason": "unknown_field", "fields": ["path"]}


def test_merge_preview_is_a_read_scope_non_mutating_op():
    assert svc.OP_SCOPE["merge_preview"] == "read"
    assert "merge_preview" not in svc._MUTATING_OPS
    assert "merge_preview" not in svc._PATH_VALIDATE_SINGLE_FIELD_OPS


# ── HTTP pipeline: success, logging, no continuation ────────────────────────────

def test_http_merge_preview_200_is_logged_and_never_gets_a_continuation(diverged_repo, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1.remote_routes import router

    root, _base, target, _head_commit = diverged_repo
    grant = {"grant_id": "g-read", "project": "p", "module": "default"}
    recorded = []
    monkeypatch.setattr(svc, "_authenticate", lambda token: grant if token == "ok" else None)
    monkeypatch.setattr(svc.db_grants, "get_scopes", lambda grant_id: {"read"})
    monkeypatch.setattr(svc, "_resolve_src_root", lambda _grant, _op: root)
    monkeypatch.setattr(svc.db_oplog, "insert", lambda **row: recorded.append(row))
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        "/api/v1/remote/merge_preview", json={}, headers={"Authorization": "Bearer ok"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["clean"] is False
    assert payload["target_sha"] == target
    assert "continuation" not in payload  # merge_preview is not a mutating op (L0006 §6.1)
    assert len(recorded) == 1
    assert recorded[0]["op"] == "merge_preview" and recorded[0]["result"] == "success"


# ── Help/registry SSOT parity (mirrors test_remote_history_show_0524.py) ─────────

def test_help_merge_preview_request_fields_match_the_allowed_field_set():
    from modules.flow_gate.services import tool_registry

    for locale in ("ko", "ja", "en"):
        fields = tool_registry._request_fields("merge_preview", locale)
        names = {f["name"] for f in fields}
        assert names == svc._ALLOWED_FIELDS["merge_preview"]
