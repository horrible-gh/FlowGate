"""Independently selectable scenarios; shared fixtures remain in test_commit_message_suggest_0066.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_commit_message_suggest_0066 import (
    ENDPOINT,
    G,
    GitServiceError,
    REAL_EXECUTE,
    SECRET,
    SimpleNamespace,
    URL,
    call,
    git,
    git_routes,
    group_ai_leases,
    gs,
    index,
    json,
    m,
    policy,
    provider,
    provider_cli,
    put,
    pytest,
    records,
    replace,
    repo,
    runtime,
    s,
    stage,
    sys,
    threading,
)

def test_permission_and_strict_body(repo, provider, monkeypatch):
    stage(repo)
    monkeypatch.setattr(git_routes, "_has_permission", lambda *args: args[1] == "project.git.read")
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
