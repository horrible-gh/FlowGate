"""Independently selectable scenarios; shared fixtures remain in test_graph_attempt_workspaces_0061.py."""
from __future__ import annotations

from tests.regression.graph_workflow.test_graph_attempt_workspaces_0061 import (
    FUTURE,
    GROUP,
    MIGRATIONS,
    Mock,
    OPERATIONS,
    PROJECT,
    Path,
    STAMP,
    SimpleNamespace,
    Store,
    _commit_in,
    _draft_token,
    admission,
    api_server_tools,
    chat,
    conversation,
    db_tokens,
    execution,
    fenced_selfcheck,
    graph_execution_hooks,
    harness,
    json,
    ledger,
    port,
    prepare,
    provider_cli,
    pytest,
    remote,
    remote_pipeline,
    repo,
    run_apply,
    run_store,
    selfcheck,
    threading,
    tr_self_check_service,
    workspace,
)

def test_attempt_tree_lost_after_admission_is_selfcheck_worktree_unavailable(repo, selfcheck):
    """``_worktree(target)`` of an admitted attempt target re-resolves the attempt; a lost tree is
    selfcheck_worktree_unavailable with its reason, never the group worktree. ``_start`` with that target judges the
    attempt again with its token (rework: no attempt target runs unjudged) and refuses it the same way."""
    sc = tr_self_check_service
    item = repo.new()
    root = prepare(item)
    target = {"project_id": PROJECT, "group_id": GROUP, "tr_doc_id": "tr-doc",
              **sc._attempt_target(item.token, PROJECT, GROUP)}
    assert target["attempt_id"] == item.attempt
    assert sc._worktree(target) == root.resolve()
    repo.git("worktree", "remove", "--force", str(root))
    with pytest.raises(sc.SelfCheckError) as refused:
        sc._worktree(target)
    assert (refused.value.status, refused.value.code) == (409, "selfcheck_worktree_unavailable")
    assert refused.value.details == {"attempt_id": item.attempt, "reason": "worktree_missing"}
    calls_before = len(repo.lock_calls)
    with pytest.raises(sc.SelfCheckError) as refused:
        sc._start(target, {"program": "pytest"}, "worker")
    assert (refused.value.status, refused.value.code) == (409, "graph_worktree_unavailable")
    assert refused.value.details["reason"] == "worktree_missing"
    assert selfcheck.created == [] and selfcheck.finished == []
    assert repo.lock_calls[calls_before:] == []
    selfcheck.group_lookup.assert_not_called()
@pytest.mark.parametrize("commit", ["stop", "invalidation"])
def test_a_stop_or_invalidation_committed_before_the_judgement_spawns_nothing(repo, fenced_selfcheck, monkeypatch,
                                                                             commit):
    """the start is held at a barrier before its judgement (``_document``); meanwhile a Stop commits
    ``stopping`` under the gate's exclusive hold, or the runtime's invalidation (``workspace._failed``, its exclusive
    hold) commits. Released, the start is refused 409 graph_attempt_inactive -- no G/W, no run row, no spawn."""
    sc, fencing, state = tr_self_check_service, fenced_selfcheck.fencing, fenced_selfcheck
    item = repo.new()
    prepare(item)
    other = repo.new("other")                     # keeps the run running once item's attempt is invalidated
    prepare(other)
    ledger.acquire(other.execution, "other-ai")
    paused, resume, outcome = threading.Event(), threading.Event(), {}

    def document(_doc):
        paused.set()
        assert resume.wait(10)
        return {"project_id": PROJECT, "group_id": GROUP}

    def late_start():
        try:
            outcome["row"] = sc.start("tr-doc", {"program": "pytest"}, "worker", item.token)
        except Exception as exc:   # noqa: BLE001 -- asserted below
            outcome["error"] = exc

    monkeypatch.setattr(sc, "_document", document)
    starter = threading.Thread(target=late_start)
    starter.start()
    assert paused.wait(10)
    if commit == "stop":
        with fencing.exclusive(GROUP):
            repo.db._execute("UPDATE graph_runs SET status='stopping' WHERE run_id='run'")
        reason = "run_stopping"
    else:
        workspace._failed(item.execution, "worktree_failed")
        assert workspace.attempt(item.attempt)["status"] == "invalidated"
        reason = "attempt_invalidated"
    calls_before = len(repo.lock_calls)          # after the invalidation's own worktree cleanup (its Git locks)
    resume.set()
    starter.join(10)
    assert not starter.is_alive()

    error = outcome.get("error")
    assert isinstance(error, sc.SelfCheckError), outcome
    assert (error.status, error.code, error.details["reason"]) == (409, "graph_attempt_inactive", reason)
    assert state.recorded == [("self_check", reason)]
    assert state.created == [] and state.finished == [] and state.order == []
    assert repo.lock_calls[calls_before:] == []
    assert fencing._gate(GROUP).readers == 0
    state.group_lookup.assert_not_called()
def test_a_stop_waits_for_an_admitted_self_check_until_its_spawn(repo, fenced_selfcheck, monkeypatch):
    """the start's worker is held at a barrier right before its spawn -- G, W and the run row taken.
    The request still holds the gate's shared hold, so a Stop cannot commit ``stopping`` in that window: it commits
    after the spawn. A start after the Stop is judged under the gate and refused; nothing more spawns."""
    sc, fencing, state = tr_self_check_service, fenced_selfcheck.fencing, fenced_selfcheck
    item = repo.new()
    prepare(item)
    monkeypatch.setattr(sc, "_document", lambda _doc: {"project_id": PROJECT, "group_id": GROUP})
    at_spawn, resume, outcome = threading.Event(), threading.Event(), {}

    def hold_spawn():
        at_spawn.set()
        assert resume.wait(10)

    state.before_spawn = hold_spawn
    starter = threading.Thread(target=lambda: outcome.setdefault(
        "row", sc.start("tr-doc", {"program": "pytest"}, "worker", item.token)))
    starter.start()
    assert at_spawn.wait(10)
    assert len(state.created) == 1 and ("W", workspace.location(workspace.attempt(item.attempt), PROJECT)[2]) \
        in repo.lock_calls
    assert fencing._gate(GROUP).readers == 1                 # the request keeps the Stop gate until the spawn

    def stopper():
        with fencing.exclusive(GROUP):
            repo.db._execute("UPDATE graph_runs SET status='stopping' WHERE run_id='run'")
            state.order.append("stopping_committed")

    stop_thread = threading.Thread(target=stopper)
    stop_thread.start()
    stop_thread.join(0.3)
    assert stop_thread.is_alive() and state.order == []     # no stopping commit between judgement and spawn
    resume.set()
    starter.join(10)
    stop_thread.join(10)
    assert not starter.is_alive() and not stop_thread.is_alive()
    assert state.order == ["spawned", "stopping_committed"]
    assert outcome["row"]["attempt_id"] == item.attempt

    calls_before, created = len(repo.lock_calls), len(state.created)
    with pytest.raises(sc.SelfCheckError) as refused:
        sc.start("tr-doc", {"program": "pytest"}, "worker", item.token)
    assert (refused.value.status, refused.value.code, refused.value.details["reason"]) == (
        409, "graph_attempt_inactive", "run_stopping")
    assert len(state.created) == created and repo.lock_calls[calls_before:] == []
    assert state.order == ["spawned", "stopping_committed"]
    assert fencing._gate(GROUP).readers == 0
def test_self_check_route_admits_attempt_token_and_passes_it_on(repo, monkeypatch):
    """the TR-bound route no longer answers 409 graph_attempt_source_unsupported for a mapped token; the
    token reaches the service, which picks the attempt root."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1 import self_check_routes as routes
    item = repo.new()
    prepare(item)
    token = {**item.token, "action_scope": "edit", "doc_ref": "tr-doc", "issued_to": "worker"}
    monkeypatch.setattr(routes, "verify_bearer", lambda _request: token)
    monkeypatch.setattr(routes.selfcheck, "_document", lambda _doc: {"project_id": PROJECT, "group_id": GROUP})
    seen = []
    monkeypatch.setattr(routes.selfcheck, "start", lambda *args: seen.append(args) or {"self_check_run_id": "scr_1"})
    app = FastAPI()
    app.include_router(routes.router)
    response = TestClient(app).post("/api/v1/documents/tr-doc/self-check/runs", json={"program": "pytest"})
    assert response.status_code == 202, response.json()
    assert seen == [("tr-doc", {"program": "pytest"}, "worker", token)]
def test_help_describes_attempt_execution_only_for_mapped_tokens(repo, monkeypatch):
    """help_catalog's tr_self_check item matches the service -- a mapped token is told its run executes in
    its attempt tree (and the 409 codes it may get); an unmapped token's content is unchanged."""
    from modules.flow_gate.services import help_catalog
    item = repo.new()
    prepare(item)
    repo.tokens["ordinary"] = {"token_id": "ordinary", "project": PROJECT, "group_id": GROUP}

    def content(token, locale="en"):
        ctx = {"principal_kind": "worker", "action_scope": "edit", "doc_type": "TR", "locale": locale,
               "base_url": "/flowgate/api/v1", "source_mode": "remote", "tool_kind": "read_write",
               "registry": None, "token_rec": token}
        assert help_catalog.decide_visibility("submit/self_check", ctx).visible
        return help_catalog._CONTENT_SUPPLIERS["tr_self_check"](ctx)

    for locale in ("ko", "en", "ja"):
        block = content(item.token, locale)["graph_attempt"]
        assert block["attempt_id"] == item.attempt and block["execution_root"] == "graph_attempt_worktree"
        assert {row["code"] for row in block["errors"]} == {"graph_attempt_inactive", "graph_worktree_unavailable"}
        assert {"attempt_id", "input_commit"} <= set(block["run_fields"])
        assert set(block["run_fields"]) <= set(tr_self_check_service.public({}))
    assert "graph_attempt_source_unsupported" not in json.dumps(content(item.token), ensure_ascii=False)
    assert "graph_attempt" not in content(repo.tokens["ordinary"])
def test_unmapped_tokens_keep_group_routing(repo, monkeypatch):
    token = {"token_id": "ordinary", "project": PROJECT, "group_id": GROUP}
    repo.tokens["ordinary"] = token
    grant = {"grant_id": remote._WORKER_GRANT_PREFIX + "ordinary", "project": PROJECT, "group_id": GROUP}
    monkeypatch.setattr(workspace.git, "effective_src_root_ex", lambda *_: (repo.group, "worktree"))
    assert port.source_root(token) is None
    assert port.reject_group_source(token, "self_check") is None
    # an unmapped token's Self-check keeps the group worktree and adds no attempt fields.
    assert tr_self_check_service._attempt_target(token, PROJECT, GROUP) == {}
    assert tr_self_check_service._worktree({"project_id": PROJECT, "group_id": GROUP}) == repo.group.resolve()
    assert remote._resolve_src_root(grant) == repo.group
    assert chat.worktree_root(PROJECT, GROUP, str(repo.group), token_id="ordinary") == repo.group
    # the TR scope check keeps the group basis for an unmapped token.
    from modules.flow_gate.services import tr_scope_service as trs
    assert port.attempt_scope(token) is None
    assert trs.resolve_attempt_scope(token, PROJECT, GROUP) is None
    group_measure = Mock(return_value={"available": True, "reason": "worktree", "worktree": str(repo.group),
                                       "branch": "group", "paths": ["value.txt"], "entries": []})
    monkeypatch.setattr(trs.git_service, "collect_scope_changes", group_measure)
    monkeypatch.setattr(trs.git_service, "collect_scope_changes_at",
                        Mock(side_effect=AssertionError("v0.1 token must not use an attempt basis")))
    monkeypatch.setattr(trs, "resolve_stage", lambda _project: trs.STAGE_ENFORCE)
    result = trs.evaluate(PROJECT, GROUP, "## 변경 파일\n\n- value.txt\n",
                          attempt_scope=trs.resolve_attempt_scope(token, PROJECT, GROUP))
    group_measure.assert_called_once_with(PROJECT, GROUP)
    assert result["scope_source"] == "group" and result["attempt_id"] is None
    assert result["detected"] == ["value.txt"] and result["codes"] == []
def test_mapped_token_tr_scope_measures_attempt_tree_from_input_commit(repo, monkeypatch):
    """a mapped token's TR scope is measured on its attempt tree from its
    input_commit, so changes that have not reached the group branch are detected (no TRV-003)."""
    from modules.flow_gate.services import tr_scope_service as trs
    item = repo.new()
    root = prepare(item)
    (root / "value.txt").write_text("attempt edit", encoding="utf-8")
    _commit_in(root, "commit", "-qam", "attempt result")
    (root / "added.txt").write_text("new", encoding="utf-8")
    (root / "value.txt").write_text("attempt edit, then dirty", encoding="utf-8")
    group_measure = Mock(side_effect=AssertionError("group worktree must not be measured"))
    monkeypatch.setattr(trs.git_service, "collect_scope_changes", group_measure)
    monkeypatch.setattr(trs.git_service, "effective_src_root_ex", group_measure)
    monkeypatch.setattr(trs, "resolve_stage", lambda _project: trs.STAGE_ENFORCE)

    scope = trs.resolve_attempt_scope(item.token, PROJECT, GROUP)
    input_commit = workspace.attempt(item.attempt)["input_commit"]
    assert scope == {"attempt_id": item.attempt, "root": root, "input_commit": input_commit,
                     "input_source": "run_base"}
    assert input_commit == repo.git("rev-parse", "HEAD")

    result = trs.evaluate(PROJECT, GROUP, "## 변경 파일\n\n- value.txt\n- added.txt\n", attempt_scope=scope)
    assert result["detected"] == ["added.txt", "value.txt"]
    assert result["unconfirmed"] == [] and result["unreported"] == [] and result["codes"] == []
    assert result["scope_source"] == "graph_attempt"
    assert result["attempt_id"] == item.attempt and result["input_commit"] == input_commit
    assert result["scope_base_sha"] == input_commit
    assert result["worktree"] == str(root)
    assert {row["path"]: row["actual_status"] for row in result["file_manifest"]} == {
        "added.txt": "A", "value.txt": "M"}
    group_measure.assert_not_called()
    assert (repo.group / "value.txt").read_text() == "initial"
    assert not (repo.group / "added.txt").exists()
    assert not repo.held
@pytest.mark.parametrize("broken,reason", [
    ("worktree_removed", "worktree_missing"),
    ("invalidated", "attempt_not_active"),
    ("marker_input_mismatch", "input_mismatch"),
])
def test_mapped_token_unresolvable_attempt_is_trv006_never_group(repo, monkeypatch, broken, reason):
    from modules.flow_gate.services import tr_scope_service as trs
    item = repo.new()
    root = prepare(item)
    if broken == "worktree_removed":
        repo.git("worktree", "remove", "--force", str(root))
    elif broken == "invalidated":
        repo.db._execute("UPDATE graph_node_attempts SET status='invalidated' WHERE attempt_id=?", [item.attempt])
    else:
        marker_path = root.parent / workspace.merge_target.OWNER_MARKER
        marker = json.loads(marker_path.read_text())
        marker["input_commit"] = "0" * 40
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
    group_measure = Mock(side_effect=AssertionError("group fallback forbidden"))
    monkeypatch.setattr(trs.git_service, "collect_scope_changes", group_measure)
    monkeypatch.setattr(trs.git_service, "effective_src_root_ex", group_measure)
    monkeypatch.setattr(trs.git_service, "collect_scope_changes_at", group_measure)
    monkeypatch.setattr(trs, "resolve_stage", lambda _project: trs.STAGE_ENFORCE)

    scope = trs.resolve_attempt_scope(item.token, PROJECT, GROUP)
    assert scope["attempt_id"] == item.attempt and scope["root"] is None
    assert scope["error"] == {"code": "graph_worktree_unavailable", "reason": reason}
    result = trs.evaluate(PROJECT, GROUP, "## 변경 파일\n\n- value.txt\n", attempt_scope=scope)
    assert result["codes"] == [trs.TRV_NO_SCOPE] and result["verdict"] == trs.VERDICT_WARN
    assert result["unconfirmed"] == [] and result["detected"] == []
    assert result["scope_source"] == "graph_attempt" and result["attempt_id"] == item.attempt
    assert result["input_commit"] is None
    group_measure.assert_not_called()
def test_cli_launch_still_uses_run_scratch(repo, tmp_path, monkeypatch):
    item = repo.new()
    root = prepare(item)
    scratch = tmp_path / "run_scratch"
    scratch.mkdir()
    monkeypatch.setattr(provider_cli, "_validate_scratch_manifest", lambda *_: ({}, None))
    service = SimpleNamespace(_is_reparse_or_symlink=lambda _: False, _shell_kind=lambda: "test")
    monkeypatch.setattr(provider_cli, "_svc", lambda: service)
    monkeypatch.setattr(provider_cli.process_runner, "unc_safe_shell", lambda command, cwd: (command, cwd))
    decision, reason = provider_cli._resolve_cli_launch(
        {"kind": "codex"}, {"project_id": PROJECT, "run_id": "ai", "scratch_dir": str(scratch),
                            "source_root": str(root)}, "cli")
    assert reason == "valid"
    assert Path(decision["agent_cwd"]) == scratch.resolve()
    assert decision["cwd_source"] == "run_scratch"
