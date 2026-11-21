"""0066 E7: real immutable Git objects, captured Provider inputs and HTTP failures."""
from __future__ import annotations

import json
import sys
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from modules.flow_gate.db import group_ai_leases
from modules.flow_gate.services.git import commit_suggest as s
from modules.flow_gate.services.git.credentials import GitServiceError
from modules.flow_gate.services.ai_invoke import provider_cli, runtime
from tests.integration.group_worktree.test_group_worktree_stage_0066 import repo, git, put, index, G, SECRET, URL, gs, m, policy, git_routes

ENDPOINT = URL + "/commit-message/suggest"
REAL_EXECUTE = s.connection.execute_prompt


@pytest.fixture
def provider(repo, monkeypatch):
    captures = []
    subject = s.connection.ConnectionSubject(
        "p_0669b", "test-provider", "project", "Test", "api", "openai",
        None, "http://unused", "test", "fake-key", "", 1)
    def resolve(project, provider_id, **kwargs):
        assert project == "p_0669b" and provider_id == "test-provider"
        assert kwargs == {"fingerprint": False}
        return subject
    def execute(subject, prompt, handle, timeout, **kwargs):
        assert timeout == s.connection.TIMEOUT_SEC
        assert kwargs == {"max_tokens": s.MAX_OUTPUT_TOKENS}
        captures.append(prompt)
        return runtime.ProbeOutcome(runtime.PROBE_OK, "fix(git): summarize staged changes")
    monkeypatch.setattr(s.connection, "resolve_subject", resolve)
    monkeypatch.setattr(s.connection, "execute_prompt", execute)
    return SimpleNamespace(captures=captures, subject=subject)


def stage(repo, path="a.py", text="staged-content\n"):
    put(repo.root, path, text)
    git(repo.root, "add", "--", path)


def call(repo, status=200, body=None):
    response = repo.client.post(ENDPOINT, json={"provider_id": "test-provider"} if body is None else body)
    assert response.status_code == status, response.text
    return response.json()["result" if status == 200 else "error"]


def records(prompt):
    return [json.loads(line) for line in prompt.splitlines()[1:]]


def test_staged_only_with_stats_and_no_mutations(repo, provider):
    stage(repo)
    put(repo.root, "a.py", "UNSTAGED_SECRET\n")
    put(repo.root, "b.py", "UNSTAGED_OTHER\n")
    put(repo.root, "new.py", "UNTRACKED_SECRET\n")
    before = git(repo.root, "rev-parse", "HEAD"), index(repo), (repo.root / "a.py").read_bytes()
    snapshot = m.read_index_snapshot(G)
    result = call(repo)
    assert result == {"suggested": "fix(git): summarize staged changes", "source": "ai",
                      "provider_id": "test-provider", "staged_count": 1, "input_truncated": False,
                      "head": snapshot.head, "index_token": snapshot.index_token}
    prompt = provider.captures[0]
    assert "staged-content" in prompt and "original" in prompt
    assert all(x not in prompt for x in ("UNSTAGED_SECRET", "UNSTAGED_OTHER", "UNTRACKED_SECRET"))
    row, = records(prompt)
    assert row["insertions"] == row["deletions"] == 1
    assert before == (git(repo.root, "rev-parse", "HEAD"), index(repo), (repo.root / "a.py").read_bytes())
    assert repo.calls == []


@pytest.mark.parametrize("path,owner,key", [
    (SECRET, False, "hidden"), (SECRET, True, "hidden"),
    ("server/.env-한글", False, "hidden"), ("server/.env-한글", True, "hidden"),
    ("managed.py", True, "managed"), ("new.log", False, "excluded_new"),
    ("new.log", True, "managed"),
])
def test_protected_paths_before_any_blob_or_provider(repo, provider, monkeypatch, path, owner, key):
    repo.owner.discard(path)
    if owner:
        repo.owner.add(path)
    stage(repo, path, "NEVER_SEND_THIS\n")
    def forbidden(*args, **kwargs):
        pytest.fail("protected input reached blob or Provider resolution")
    monkeypatch.setattr(s, "_blob", forbidden)
    monkeypatch.setattr(s.connection, "resolve_subject", forbidden)
    err = call(repo, 409)
    assert err["code"] == "staged_protected_paths" and err["details"]["counts"][key] == 1
    assert "NEVER_SEND_THIS" not in json.dumps(err)
    if key == "hidden":
        assert path not in json.dumps(err, ensure_ascii=False) and err["details"]["paths"] == []
    assert provider.captures == []


def test_hidden_rename_is_protected_deletion(repo, provider):
    git(repo.root, "mv", SECRET, "visible.py")
    err = call(repo, 409)
    assert err["code"] == "staged_protected_paths"
    assert err["details"]["counts"]["hidden"] == 1
    assert SECRET not in json.dumps(err) and not provider.captures


def test_excluded_stats_binary_path_and_add_delete(repo, provider):
    stage(repo, "old.log", "EXCLUDED_PRIVATE_ONE\nEXCLUDED_PRIVATE_TWO\n")
    (repo.root / "binary.dat").write_bytes(b"\0BINARY_PRIVATE\xff")
    git(repo.root, "add", "binary.dat")
    stage(repo, "new.py", "new-line\n")
    git(repo.root, "rm", "b.py")
    result = call(repo)
    assert result["staged_count"] == 4
    prompt = provider.captures[0]
    assert "EXCLUDED_PRIVATE" not in prompt and "BINARY_PRIVATE" not in prompt
    rows = {r["path"]: r for r in records(prompt)}
    assert rows["binary.dat"] == {"path": "binary.dat"}
    assert rows["old.log"] == {"path": "old.log", "status": "M", "insertions": 2, "deletions": 1}
    assert rows["new.py"]["deletions"] == 0 and rows["b.py"]["insertions"] == 0


@pytest.mark.parametrize("reason,code", [
    ("ownership_invariant", "ownership_unavailable"),
    ("ownership_read_failed", "ownership_unavailable"),
    ("tr_history_recovery_required", "tr_history_recovery_required"),
])
def test_ownership_failure_is_closed(repo, provider, monkeypatch, reason, code):
    stage(repo)
    monkeypatch.setattr(policy, "read_ownership", lambda _: policy.Ownership(None, reason))
    assert call(repo, 409)["code"] == code
    assert not provider.captures


def test_empty_index(repo, provider):
    assert call(repo, 422)["code"] == "nothing_staged"
    assert not provider.captures


@pytest.mark.parametrize("kind", ["unmerged", "type_change"])
def test_unsupported_index(repo, provider, monkeypatch, kind):
    snap = m.read_index_snapshot(G)
    if kind == "unmerged":
        entries = tuple((p, mode, oid, 1 if p == SECRET else st) for p, mode, oid, st in snap.entries)
    else:
        entries = tuple((p, "120000" if p == SECRET else mode, oid, st) for p, mode, oid, st in snap.entries)
    monkeypatch.setattr(m, "read_index_snapshot", lambda *a, **kw: replace(snap, entries=entries))
    err = call(repo, 409)
    assert err["code"] == "index_entry_unsupported"
    assert err["details"]["counts"][kind] == 1 and SECRET not in json.dumps(err)
