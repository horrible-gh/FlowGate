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


def test_snapshot_retries_and_uses_new_objects(repo, provider, monkeypatch):
    stage(repo, text="first\n")
    read = m._read_index_once
    calls = []
    def race(root):
        snap = read(root)
        calls.append(snap)
        if len(calls) == 1:
            stage(repo, text="second-snapshot\n")
        return snap
    monkeypatch.setattr(m, "_read_index_once", race)
    result = call(repo)
    assert len(calls) == 4
    assert result["index_token"] == calls[-1].index_token
    assert "second-snapshot" in provider.captures[0] and 'first' not in provider.captures[0]


def test_snapshot_exhaustion(repo, provider, monkeypatch):
    stage(repo)
    read = m._read_index_once
    calls = []
    def race(root):
        snap = read(root)
        calls.append(snap)
        stage(repo, text=str(len(calls)) + "\n")
        return snap
    monkeypatch.setattr(m, "_read_index_once", race)
    assert call(repo, 409)["code"] == "worktree_changed"
    assert len(calls) == 2 * s.SNAPSHOT_ATTEMPTS and not provider.captures


@pytest.mark.parametrize("change", ["protect", "unstage", "commit", "merge", "cleanup"])
def test_after_snapshot_changes_never_reopen_index_or_worktree(repo, provider, monkeypatch, tmp_path, change):
    # A real linked worktree proves the common objects survive slot removal.
    common = repo.root
    linked = tmp_path / "linked"
    git(common, "branch", "base")
    git(common, "checkout", "base")
    git(common, "worktree", "add", str(linked), "group")
    repo.root = linked
    monkeypatch.setattr(gs, "effective_src_root_ex", lambda *a: (linked, "worktree"))
    stage(repo)
    snap = m.read_index_snapshot(G)
    expected = s._prompt(snap, *s._staged(snap, policy.Ownership(frozenset(repo.owner))))
    read = m.read_index_snapshot
    def freeze(*a, **kw):
        frozen = read(*a, **kw)
        if change == "protect":
            stage(repo, SECRET, "LATE_SECRET\n")
        elif change == "unstage":
            git(linked, "reset", "HEAD", "--", "a.py")
        elif change == "commit":
            git(linked, "commit", "-m", "concurrent commit")
        elif change == "merge":
            put(common, "base-new.py", "base\n")
            git(common, "add", ".")
            git(common, "commit", "-m", "base change")
            git(linked, "commit", "-m", "staged commit")
            git(common, "merge", "--no-ff", "group", "-m", "manual merge")
        else:
            git(common, "worktree", "remove", "--force", str(linked))
            repo.state["worktree_registered"] = 0
        return frozen
    monkeypatch.setattr(m, "read_index_snapshot", freeze)
    result = call(repo)
    assert result["index_token"] == snap.index_token and result["head"] == snap.head
    assert (provider.captures[0], result["input_truncated"]) == expected
    assert "LATE_SECRET" not in provider.captures[0]


def test_slot_lost_during_snapshot(repo, provider, monkeypatch):
    stage(repo)
    read = m._read_index_once
    def disappear(root):
        snap = read(root)
        repo.state["worktree_registered"] = 0
        return snap
    monkeypatch.setattr(m, "_read_index_once", disappear)
    err = call(repo, 409)
    assert err["code"] == "invalid_state" and err["details"]["reason"] == "worktree_unavailable"


@pytest.mark.parametrize("changed", [False, True])
def test_suggested_token_commits_only_same_index(repo, provider, monkeypatch, changed):
    stage(repo)
    result = call(repo)
    if changed:
        stage(repo, "b.py", "later\n")
    monkeypatch.setattr(m.tr2.db_recovery, "has_unresolved", lambda _: False)
    r = repo.client.post(URL + "/commit", json={"message": result["suggested"], "index_token": result["index_token"]})
    assert r.status_code == (409 if changed else 200), r.text
    if changed:
        assert r.json()["error"]["code"] == "worktree_changed"
    else:
        assert r.json()["result"]["committed_paths"] == ["a.py"]


def test_active_lease_is_untouched_no_run_token_or_git_lock(repo, provider, monkeypatch):
    stage(repo)
    lease = {"run_id": "existing", "group_id": G}
    monkeypatch.setattr(group_ai_leases, "get_active", lambda _: lease)
    def forbidden(*a, **kw):
        pytest.fail("suggestion attempted a mutation or AI admission")
    monkeypatch.setattr(m.finalize, "_guard_group_update_ai_idle", forbidden)
    monkeypatch.setattr(m.lock_manager, "acquire_group", forbidden)
    monkeypatch.setattr(git_routes.token_service, "issue", forbidden, raising=False)
    before = dict(runtime._svc()._runs)
    call(repo)
    assert group_ai_leases.get_active(G) is lease and runtime._svc()._runs == before
    assert not repo.calls


@pytest.mark.parametrize("state", ["conflict", "merging", "merged", "pushed", "discarded"])
def test_state_gate(repo, provider, state):
    stage(repo)
    repo.state["status"] = state
    assert call(repo, 409)["code"] == "invalid_state"
    assert not provider.captures


@pytest.mark.parametrize("body", [{}, {"provider_id": None}, {"provider_id": "  "}])
def test_provider_required(repo, provider, body):
    assert call(repo, 422, body)["code"] == "provider_required"


def test_permission_and_strict_body(repo, provider, monkeypatch):
    stage(repo)
    monkeypatch.setattr(git_routes, "_has_permission", lambda *args: args[1] == "project.settings.read")
    assert call(repo, 403)["code"] == "forbidden"
    assert not provider.captures
    monkeypatch.setattr(git_routes, "_has_permission", lambda *args: True)
    for body in ({"provider_id": 7}, {"provider_id": "test-provider", "prompt": "override"}):
        assert repo.client.post(ENDPOINT, json=body).status_code == 422
    assert repo.client.post(ENDPOINT, content=b"x" * (1024 * 1024 + 1)).status_code == 413


@pytest.mark.parametrize("code,status", [("provider_not_found", 404), ("provider_disabled", 409), ("provider_unavailable", 409)])
def test_provider_resolution_errors(repo, provider, monkeypatch, code, status):
    stage(repo)
    def unavailable(*a, **kw):
        raise s.connection.ConnectionTestError(code, status)
    monkeypatch.setattr(s.connection, "resolve_subject", unavailable)
    assert call(repo, status)["code"] == code
    assert not provider.captures and G not in s._ACTIVE


@pytest.mark.parametrize("kind,status,code,reason", [
    (runtime.PROBE_TIMEOUT, 504, "ai_timeout", None),
    (runtime.PROBE_TRANSPORT_FAILED, 502, "ai_failed", "transport_failed"),
    (runtime.PROBE_LAUNCH_FAILED, 502, "ai_failed", "provider_launch_failed"),
    (runtime.PROBE_EXECUTION_FAILED, 502, "ai_failed", "execution_failed"),
    (runtime.PROBE_CANCELLED, 502, "ai_failed", "internal_error"),
])
def test_provider_failures(repo, provider, monkeypatch, kind, status, code, reason):
    stage(repo)
    monkeypatch.setattr(s.connection, "execute_prompt", lambda *a, **kw: runtime.ProbeOutcome(kind, "PRIVATE_DIAGNOSTIC"))
    err = call(repo, status)
    assert err["code"] == code and "PRIVATE_DIAGNOSTIC" not in json.dumps(err)
    if reason:
        assert err["details"]["reason_code"] == reason
    assert G not in s._ACTIVE


@pytest.mark.parametrize("answer,status", [("\n fix:  one\tline \r\n", 200), (" ", 502),
                                           ("x" * (gs.COMMIT_SUBJECT_MAX + 1), 502), ("a\0b", 502)])
def test_normalized_response(repo, provider, monkeypatch, answer, status):
    stage(repo)
    monkeypatch.setattr(s.connection, "execute_prompt", lambda *a, **kw: runtime.ProbeOutcome(runtime.PROBE_OK, answer))
    result = call(repo, status)
    assert result["suggested"] == "fix: one line" if status == 200 else result["code"] == "ai_invalid_response"


def test_exception_cancels_handle_and_releases_request(repo, provider, monkeypatch):
    stage(repo)
    cancelled = []
    def explode(subject, prompt, handle, *a, **kw):
        handle.bind(lambda: cancelled.append(True))
        raise RuntimeError("PRIVATE_DIAGNOSTIC")
    monkeypatch.setattr(s.connection, "execute_prompt", explode)
    err = call(repo, 502)
    assert err["code"] == "ai_failed" and "PRIVATE_DIAGNOSTIC" not in json.dumps(err)
    assert cancelled == [True] and G not in s._ACTIVE


def test_overlapping_suggestions_and_finally_release(repo, provider, monkeypatch):
    stage(repo)
    entered, release = threading.Event(), threading.Event()
    results = []
    def wait(*args, **kw):
        entered.set()
        assert release.wait(10)
        return runtime.ProbeOutcome(runtime.PROBE_OK, "fix: concurrent")
    monkeypatch.setattr(s.connection, "execute_prompt", wait)
    worker = threading.Thread(target=lambda: results.append(s.suggest(G, provider_id="test-provider")))
    worker.start()
    try:
        assert entered.wait(10)
        assert call(repo, 409)["code"] == "suggestion_in_progress"
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and len(results) == 1 and G not in s._ACTIVE
    call(repo)


@pytest.mark.parametrize("limit", ["prompt", "blob", "lines"])
def test_input_limits_still_check_all_paths(repo, provider, monkeypatch, limit):
    stage(repo, text="data\n" * 20)
    if limit == "prompt":
        monkeypatch.setattr(s, "MAX_INPUT_CHARS", len(s._INSTRUCTIONS) + 110)
    elif limit == "blob":
        monkeypatch.setattr(s, "MAX_BLOB_BYTES", 30)
    else:
        monkeypatch.setattr(s, "MAX_DIFF_LINES", 5)
    result = call(repo)
    assert result["input_truncated"] and len(provider.captures[0]) <= s.MAX_INPUT_CHARS
    records(provider.captures[0])  # complete JSON even when a diff is truncated
    stage(repo, SECRET, "LATE_PROTECTED\n")
    assert call(repo, 409)["code"] == "staged_protected_paths"
    assert len(provider.captures) == 1


def test_real_cli_uses_isolated_scratch_and_empty_tokens(repo, provider, monkeypatch, tmp_path):
    stage(repo)
    script = tmp_path / "provider.py"
    output = tmp_path / "observed.json"
    script.write_text("import os,json,pathlib,sys\n"
        "prompt=sys.stdin.read()\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'cwd':os.getcwd(), 'prompt':prompt,"
        "'env':{k:os.environ.get(k) for k in ['FLOWGATE_TOKEN','FLOWGATE_API_BASE','FLOWGATE_BUNDLE_API']}}))\n"
        "print('fix: isolated cli')\n", encoding="utf-8")
    scratch_root = tmp_path / "scratch" / "commit-suggest"
    monkeypatch.setattr(runtime, "_project_scratch_root", lambda _: scratch_root)
    monkeypatch.setattr(runtime.storage_paths, "get_storage_root", lambda *_a, **_k: tmp_path)
    monkeypatch.setenv("FLOWGATE_TOKEN", "DO_NOT_INHERIT")
    subject = replace(provider.subject, exec_type="cli", kind="custom",
                      cli_command=f'"{sys.executable}" "{script}" "{output}"')
    monkeypatch.setattr(s.connection, "resolve_subject", lambda *a, **kw: subject)
    monkeypatch.setattr(s.connection, "execute_prompt", REAL_EXECUTE)
    assert call(repo)["suggested"] == "fix: isolated cli"
    seen = json.loads(output.read_text())
    from pathlib import Path
    assert Path(seen["cwd"]).is_relative_to(scratch_root)
    assert seen["env"] == dict.fromkeys(("FLOWGATE_TOKEN", "FLOWGATE_API_BASE", "FLOWGATE_BUNDLE_API"), "")
    assert "staged-content" in seen["prompt"] and "DO_NOT_INHERIT" not in seen["prompt"]
    assert list(scratch_root.iterdir()) == []
