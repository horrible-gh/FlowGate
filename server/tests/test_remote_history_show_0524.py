"""HEAD-side history (`log(side=...)`) and commit inspection (`show`) — group 0524 T0004.

Covers NR0003 §11/§16 and T0004 §2.1: `log` gains a `side` selector symmetric with the
existing target-side behaviour, and `show` is a new bounded, read-only commit-inspection
op. Both stay inside the existing read-only git-inspection boundary (T0004 §1): fixed
argv only, no arbitrary git/shell execution, no HEAD/index/working-tree mutation.

Git-level tests use a local subprocess fixture (mirrors test_remote_diff_log_0478.py's
`diverged_repo`); HTTP-pipeline tests reuse the DB-backed harness from
test_remote_tool_0003_T0012 (`RAW_TOKEN` / `_call` / `env`).
"""
from __future__ import annotations

import subprocess

import pytest

from modules.flow_gate.services import remote_tool_service as svc
from test_remote_tool_0003_T0012 import RAW_TOKEN, _call, env  # noqa: F401  (env is a fixture)


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def diverged_repo(tmp_path):
    """base -> target (origin/main) and base -> worker (HEAD), diverging both ways.

    `worker`'s HEAD commit touches two files (a new one and a modified one) so
    `show`'s numstat/files list has more than one row to check.
    """
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "worker@example.com")
    _git(root, "config", "user.name", "Worker")
    (root / "shared.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base history")
    base = _git(root, "rev-parse", "HEAD")

    _git(root, "checkout", "-b", "target")
    (root / "shared.txt").write_text("base\ntarget-only\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "target intent")
    target = _git(root, "rev-parse", "HEAD")

    _git(root, "checkout", "-b", "worker", base)
    (root / "shared.txt").write_text("base\nhead-only\n", encoding="utf-8")
    (root / "head.txt").write_text("head file\nsecond line\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "head intent")
    head_commit = _git(root, "rev-parse", "HEAD")

    _git(root, "update-ref", "refs/remotes/origin/main", target)
    return root, base, target, head_commit


# ── log(side=...) ───────────────────────────────────────────────────────────

def test_log_side_head_returns_only_the_head_only_commit(diverged_repo):
    root, base, target, head_commit = diverged_repo
    result, _ = svc._exec_log({"side": "head"}, root)
    assert result["side"] == "head"
    assert result["merge_base"] == base
    assert [c["subject"] for c in result["commits"]] == ["head intent"]
    assert result["commits"][0]["sha"] == head_commit
    assert "target intent" not in str(result)


def test_log_default_and_explicit_side_target_are_identical_and_unchanged(diverged_repo):
    root, base, target, _head_commit = diverged_repo
    default_result, _ = svc._exec_log({}, root)
    explicit_result, _ = svc._exec_log({"side": "target"}, root)
    assert default_result["side"] == "target"
    assert explicit_result == default_result
    assert [c["subject"] for c in default_result["commits"]] == ["target intent"]
    assert "head intent" not in str(default_result)


def test_log_invalid_side_is_422_invalid_side():
    with pytest.raises(svc._OpError) as exc:
        svc._validate_required("log", {"side": "branch"})
    assert exc.value.status == 422
    assert exc.value.details == {"reason": "invalid_side"}


def test_log_side_is_not_accepted_by_diff():
    """T0004 §2.1(a): `side` is log-only — diff keeps its existing field set."""
    assert "side" not in svc._ALLOWED_FIELDS["diff"]
    with pytest.raises(svc._OpError) as exc:
        svc._validate_allowed_fields("diff", {"side": "head"})
    assert exc.value.status == 422
    assert exc.value.details == {"reason": "unknown_field", "fields": ["side"]}


# ── show ──────────────────────────────────────────────────────────────────────

def test_show_returns_metadata_files_and_patch_matching_git(diverged_repo):
    root, base, _target, head_commit = diverged_repo
    result, nbytes = svc._exec_show({"sha": head_commit}, root)

    assert result["sha"] == head_commit
    assert result["parents"] == [base]
    assert result["author_name"] == "Worker"
    assert result["author_email"] == "worker@example.com"
    assert result["subject"] == "head intent"
    assert result["truncated"] is False
    assert nbytes == len(result["patch"].encode("utf-8"))

    files_by_path = {f["path"]: f for f in result["files"]}
    assert files_by_path.keys() == {"head.txt", "shared.txt"}
    assert files_by_path["head.txt"] == {"path": "head.txt", "insertions": 2, "deletions": 0}
    assert files_by_path["shared.txt"] == {"path": "shared.txt", "insertions": 1, "deletions": 0}

    assert "head-only" in result["patch"] and "head file" in result["patch"]
    assert "target-only" not in result["patch"]


def test_show_root_commit_returns_nonempty_files_and_patch(diverged_repo):
    """Rejection fix: `git diff-tree` needs `--root` or a parentless commit gets
    treated as having nothing to diff against, so `files`/`patch` come back empty
    even though `show` accepts the sha and must return its stat/patch."""
    root, base, _target, _head_commit = diverged_repo
    result, nbytes = svc._exec_show({"sha": base}, root)

    assert result["sha"] == base
    assert result["parents"] == []
    assert result["subject"] == "base history"
    assert result["files"] == [{"path": "shared.txt", "insertions": 1, "deletions": 0}]
    assert "base" in result["patch"]
    assert result["patch"] != ""
    assert nbytes == len(result["patch"].encode("utf-8"))


def test_show_unknown_sha_404(diverged_repo):
    root, _base, _target, _head = diverged_repo
    with pytest.raises(svc._OpError) as exc:
        svc._exec_show({"sha": "0" * 40}, root)
    assert exc.value.status == 404
    assert exc.value.details["reason"] == "not_found"


@pytest.mark.parametrize("bad_sha", [
    "--upload-pack=evil", "-x", "HEAD~1", "HEAD", "abc/../secret", "", "abc", None, 123,
])
def test_show_malformed_sha_is_422_invalid_sha(bad_sha):
    with pytest.raises(svc._OpError) as exc:
        svc._validate_required("show", {"sha": bad_sha})
    assert exc.value.status == 422
    assert exc.value.details == {"reason": "invalid_sha"}


def test_show_malformed_sha_never_reaches_git(env, monkeypatch):
    """Pipeline-level proof that an option-shaped sha never becomes a git argv
    element (T0004 §2.4 injection check) -- ④ rejects it before ⑤ executes."""
    env.make_grant(["read"])
    from modules.flow_gate.services import git_service

    def _forbidden(*_args, **_kwargs):
        pytest.fail("a malformed sha must never reach git_service._run_git")

    monkeypatch.setattr(git_service, "_run_git", _forbidden)
    status, payload = _call("show", {"sha": "--upload-pack=evil"})
    assert status == 422
    assert payload["error"]["details"]["reason"] == "invalid_sha"


def test_show_truncates_large_patch(diverged_repo, monkeypatch):
    root, _base, _target, _head = diverged_repo
    (root / "big.txt").write_text("x" * 5000 + "\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "big commit")
    big_sha = _git(root, "rev-parse", "HEAD")

    monkeypatch.setattr(svc, "_MAX_DIFF_BYTES", 20)
    result, _ = svc._exec_show({"sha": big_sha}, root)
    assert result["truncated"] is True
    assert result["returned_bytes"] <= 20


# ── read-only guarantee ────────────────────────────────────────────────────────

def test_show_and_log_side_head_never_change_head_index_or_worktree(diverged_repo):
    root, _base, _target, head_commit = diverged_repo
    before_head = _git(root, "rev-parse", "HEAD")
    before_status = _git(root, "status", "--porcelain")

    svc._exec_log({"side": "head"}, root)
    svc._exec_show({"sha": head_commit}, root)

    after_head = _git(root, "rev-parse", "HEAD")
    after_status = _git(root, "status", "--porcelain")
    assert after_head == before_head
    assert after_status == before_status == ""


# ── HTTP pipeline: scope / 403 ──────────────────────────────────────────────────

@pytest.mark.parametrize("operation,body", [("show", {"sha": "0" * 40}), ("log", {"side": "head"})])
def test_http_without_read_scope_is_403(monkeypatch, operation, body):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1.remote_routes import router

    monkeypatch.setattr(svc, "_authenticate", lambda token: {"grant_id": "g-none"})
    monkeypatch.setattr(svc.db_grants, "get_scopes", lambda grant_id: set())
    monkeypatch.setattr(svc.db_oplog, "insert", lambda **row: None)
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        f"/api/v1/remote/{operation}", json=body, headers={"Authorization": "Bearer ok"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_http_show_200_is_logged_and_never_gets_a_continuation(diverged_repo, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1.remote_routes import router

    root, _base, _target, head_commit = diverged_repo
    grant = {"grant_id": "g-read", "project": "p", "module": "default"}
    recorded = []
    monkeypatch.setattr(svc, "_authenticate", lambda token: grant if token == "ok" else None)
    monkeypatch.setattr(svc.db_grants, "get_scopes", lambda grant_id: {"read"})
    monkeypatch.setattr(svc, "_resolve_src_root", lambda _grant, _op: root)
    monkeypatch.setattr(svc.db_oplog, "insert", lambda **row: recorded.append(row))
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post(
        "/api/v1/remote/show", json={"sha": head_commit}, headers={"Authorization": "Bearer ok"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["sha"] == head_commit
    assert "continuation" not in payload  # show is not a mutating op (L0006 §6.1)
    assert len(recorded) == 1
    assert recorded[0]["op"] == "show" and recorded[0]["result"] == "success"


# ── Help/registry SSOT parity (mirrors test_remote_tool_read_lines_0507.py) ─────

def test_help_log_and_show_request_fields_match_the_allowed_field_set():
    from modules.flow_gate.services import tool_registry

    for op in ("log", "show"):
        for locale in ("ko", "ja", "en"):
            fields = tool_registry._request_fields(op, locale)
            names = {f["name"] for f in fields}
            assert names == svc._ALLOWED_FIELDS[op]


def test_show_is_a_read_scope_non_mutating_op():
    assert svc.OP_SCOPE["show"] == "read"
    assert "show" not in svc._MUTATING_OPS
