"""flowgate.v02test.0023 -- a chat that changes source leaves a commit (0023-CH0002 rule).

1. a chat names a commit message (``commit_message`` on /ai-invoke/start);
2. its run end commits the flow's worktree with it (the TR commit point, ``create_tr_commit``);
3. without a message, a chat is refused while the worktree holds uncommitted changes
   (409 ``chat_commit_message_required``). A group without Git is outside the rule.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from modules.flow_gate.api.v1 import ai_invoke_routes  # noqa: E402
from modules.flow_gate.services import chat_commit_service as svc  # noqa: E402
from modules.flow_gate.services import chat_start_requests  # noqa: E402
from modules.flow_gate.services import git_service  # noqa: E402

GROUP = "flowgate.v02test.0023"
CHAT = "flowgate.v02test.0023.0002-CH"


@pytest.fixture(autouse=True)
def preserve_runtime_environment():
    # The route's lazy config import can publish this value. Restore its exact prior state.
    key = "FLOWGATE_AGENT_PEPPER_ID"
    previous = os.environ.get(key)
    yield
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "wt"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    (root / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    (root / "a.py").write_text("a = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture()
def with_git(monkeypatch, repo):
    monkeypatch.setattr(svc, "group_worktree", lambda group_id: repo)
    return repo


@pytest.fixture()
def without_git(monkeypatch):
    monkeypatch.setattr(svc, "group_worktree", lambda group_id: None)


# -- message -------------------------------------------------------------------

def test_message_is_one_line_and_blank_is_none():
    assert svc.validate_message("  fix:   chat\n commit  ") == "fix: chat commit"
    assert svc.validate_message("   ") is None
    assert svc.validate_message(None) is None


def test_message_longer_than_a_subject_is_refused():
    with pytest.raises(ValueError):
        svc.validate_message("x" * (git_service.COMMIT_SUBJECT_MAX + 1))


def test_subject_keeps_a_conventional_message_and_types_a_plain_one(monkeypatch):
    monkeypatch.setattr(git_service, "derive_commit_type", lambda group_id: "fix")
    assert svc.commit_subject(GROUP, "feat(chat): commit point") == "feat(chat): commit point"
    assert svc.commit_subject(GROUP, "채팅 커밋 추가") == "fix: 채팅 커밋 추가"


# -- pending changes -----------------------------------------------------------

def test_clean_worktree_has_no_pending_paths(repo):
    assert svc.pending_paths(repo) == []


def test_pending_paths_take_edits_deletions_and_new_files_not_ignored_ones(repo):
    (repo / "a.py").write_text("a = 2\n", encoding="utf-8")
    (repo / "new.py").write_text("n = 1\n", encoding="utf-8")
    (repo / "ignored.log").write_text("x\n", encoding="utf-8")
    assert svc.pending_paths(repo) == ["a.py", "new.py"]
    (repo / "a.py").unlink()
    assert "a.py" in svc.pending_paths(repo)


def test_pending_check_failure_reads_as_no_rule(monkeypatch):
    def boom(group_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "group_worktree", boom)
    assert svc.pending_changes(GROUP) is None


# -- refusal (rule 3) ----------------------------------------------------------

def test_dirty_worktree_without_message_is_refused(with_git):
    (with_git / "a.py").write_text("a = 3\n", encoding="utf-8")
    refused = svc.refusal(GROUP, None)
    assert refused["code"] == svc.ERR_COMMIT_MESSAGE_REQUIRED
    assert refused["file_count"] == 1 and refused["files"] == ["a.py"]
    assert "a.py" in refused["message"]


def test_dirty_worktree_with_message_starts(with_git):
    (with_git / "a.py").write_text("a = 3\n", encoding="utf-8")
    assert svc.refusal(GROUP, "fix: leftover") is None


def test_clean_worktree_or_no_git_never_refuses(with_git, monkeypatch):
    assert svc.refusal(GROUP, None) is None
    monkeypatch.setattr(svc, "group_worktree", lambda group_id: None)
    (with_git / "a.py").write_text("a = 3\n", encoding="utf-8")
    assert svc.refusal(GROUP, None) is None


# -- the run end (rules 1 and 2) -----------------------------------------------

def _run(message=None):
    return {"action_scope": "chat", "run_id": "aiv2_x", "group_id": GROUP, "chat_commit_message": message}


def test_run_end_commits_with_the_message(with_git, monkeypatch):
    calls = []

    def commit(group_id, subject):
        calls.append((group_id, subject))
        return {"committed": True, "commit": "abc1234", "commit_sha": "abc1234" + "0" * 33,
                "subject": subject, "skipped_reason": None, "committed_paths": ["a.py"]}

    monkeypatch.setattr(git_service, "create_tr_commit", commit)
    run = _run("feat(chat): commit point")
    result = svc.commit_on_finalize(run)
    assert calls == [(GROUP, "feat(chat): commit point")]
    assert result["committed"] and result["commit"] == "abc1234" and result["files"] == ["a.py"]
    assert run["chat_commit"] is result


def test_run_end_with_nothing_to_commit_records_nothing(with_git, monkeypatch):
    for reason in ("no_changes", "artifacts_only"):
        monkeypatch.setattr(git_service, "create_tr_commit", lambda g, s, r=reason: {
            "committed": False, "skipped_reason": r, "committed_paths": []})
        run = _run("fix: x")
        assert svc.commit_on_finalize(run) is None and "chat_commit" not in run


def test_run_end_skip_is_shown(with_git, monkeypatch):
    monkeypatch.setattr(git_service, "create_tr_commit", lambda g, s: {
        "committed": False, "skipped_reason": "git_busy", "committed_paths": []})
    run = _run("fix: x")
    assert svc.commit_on_finalize(run)["skipped_reason"] == "git_busy"


def test_run_end_without_message_reports_the_leftover(with_git, monkeypatch):
    monkeypatch.setattr(git_service, "create_tr_commit", lambda g, s: pytest.fail("must not commit"))
    (with_git / "b.py").write_text("b = 1\n", encoding="utf-8")
    result = svc.commit_on_finalize(_run())
    assert result == {"committed": False, "commit": None, "commit_sha": None, "subject": None,
                      "skipped_reason": svc.SKIP_MESSAGE_MISSING, "file_count": 1, "files": ["b.py"]}


def test_run_end_without_message_and_clean_tree_records_nothing(with_git):
    assert svc.commit_on_finalize(_run()) is None


def test_run_end_without_git_or_outside_chat_does_nothing(without_git, monkeypatch):
    monkeypatch.setattr(git_service, "create_tr_commit", lambda g, s: pytest.fail("must not commit"))
    assert svc.commit_on_finalize(_run("fix: x")) is None
    assert svc.commit_on_finalize({**_run("fix: x"), "action_scope": "work"}) is None


def test_run_end_never_raises(with_git, monkeypatch):
    def boom(g, s):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(git_service, "create_tr_commit", boom)
    assert svc.commit_on_finalize(_run("fix: x"))["skipped_reason"] == "commit_failed"


def test_real_commit_through_the_tr_commit_point(with_git, monkeypatch):
    """create_tr_commit's own staging and commit, with only its DB gates stood in for."""
    from modules.flow_gate.services.git import commit as git_commit

    monkeypatch.setattr(git_service, "create_tr_commit",
                        lambda group_id, subject: git_commit._commit_worktree(with_git, subject, {}, group_id))
    (with_git / "a.py").write_text("a = 9\n", encoding="utf-8")
    (with_git / "c.py").write_text("c = 1\n", encoding="utf-8")
    result = svc.commit_on_finalize(_run("feat(chat): real commit"))
    assert result["committed"], result
    assert _git(with_git, "log", "-1", "--format=%s").strip() == "feat(chat): real commit"
    assert svc.pending_paths(with_git) == []
    assert sorted(result["files"]) == ["a.py", "c.py"]


# -- the /ai-invoke/start route ------------------------------------------------

@pytest.fixture()
def route(monkeypatch):
    chat_start_requests.clear()
    monkeypatch.setattr(ai_invoke_routes, "verify_bearer",
                        lambda request: {"_is_user_jwt": True, "issued_to": "u_kim", "is_admin": True})
    monkeypatch.setattr(ai_invoke_routes, "db_projects", SimpleNamespace(get_by_id=lambda project: {"id": project}))
    calls: list[dict] = []

    def start_run(**kw):
        calls.append(kw)
        return {"ok": True, "run_id": f"aiv2_20261112_{len(calls):06d}", "status": "running"}

    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", start_run)
    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    client = TestClient(app, raise_server_exceptions=False)

    def post(**extra):
        body = {"project": "flowgate", "module": "v02test", "group": "0023", "doc_ref": CHAT,
                "action_scope": "chat", "mode": "single", "provider_id": "aip_opus", **extra}
        return client.post("/api/v1/ai-invoke/start", json=body, headers={"Authorization": "Bearer tok"})

    yield SimpleNamespace(post=post, calls=calls)
    chat_start_requests.clear()


def test_route_refuses_a_chat_on_a_dirty_worktree_and_starts_nothing(route, with_git):
    (with_git / "a.py").write_text("a = 4\n", encoding="utf-8")
    res = route.post(client_request_id="run-0023-commit-1")
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "chat_commit_message_required" and res.json()["files"] == ["a.py"]
    assert route.calls == []
    # The refused press is forgotten: the same press with a message is admitted afresh.
    res = route.post(client_request_id="run-0023-commit-1", commit_message="fix: leftover edits")
    assert res.status_code == 200, res.text
    assert route.calls[0]["chat_commit_message"] == "fix: leftover edits"


def test_route_passes_the_message_on_a_clean_worktree(route, with_git):
    assert route.post(commit_message="  feat: x  ").status_code == 200
    assert route.post().status_code == 200
    assert [c["chat_commit_message"] for c in route.calls] == ["feat: x", None]


def test_route_validates_the_message(route, without_git):
    res = route.post(commit_message="x" * (git_service.COMMIT_SUBJECT_MAX + 1))
    assert res.status_code == 422
    res = route.post(action_scope="edit", commit_message="fix: x")
    assert res.status_code == 422
    assert route.calls == []
