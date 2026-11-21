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

@pytest.mark.parametrize("op,body", OPERATIONS)
def test_every_remote_operation_uses_attempt_root(repo, remote_pipeline, monkeypatch, op, body):
    item = repo.new()
    root = prepare(item)
    executed = Mock(return_value=({}, 0))
    monkeypatch.setattr(remote, "_execute", executed)
    status, _ = remote_pipeline(item, op, body)
    assert status == 200
    assert executed.call_args.args[2] == root
@pytest.mark.parametrize("op,body", OPERATIONS)
def test_document_only_remote_returns_git_required_even_without_scopes(
        harness, remote_pipeline, monkeypatch, op, body):
    harness.enabled = False
    item = harness.new()
    prepare(item)
    monkeypatch.setattr(remote.db_grants, "get_scopes", lambda _: set())
    executed = Mock(side_effect=AssertionError("must not reach filesystem"))
    monkeypatch.setattr(remote, "_execute", executed)
    status, response = remote_pipeline(item, op, body)
    assert status == 409 and response["error"]["code"] == "graph_git_required"
    executed.assert_not_called()
def test_bound_resolvers_never_fall_back_if_tree_or_handler_missing(repo, monkeypatch):
    item = repo.new()
    root = prepare(item)
    grant = {"grant_id": remote._WORKER_GRANT_PREFIX + item.token["token_id"], "project": PROJECT, "group_id": GROUP}
    fallback = Mock(side_effect=AssertionError("base fallback forbidden"))
    monkeypatch.setattr(remote, "_fallback_project_root", fallback)
    for handler in (None, lambda *_a, **_kw: None):
        monkeypatch.setattr(port, "_handler", handler)
        for resolver in (remote._resolve_src_root, remote._resolve_root_for_mutation):
            with pytest.raises(remote._OpError) as refused:
                resolver(grant, "write")
            assert refused.value.details["code"] == "graph_worktree_unavailable"
    monkeypatch.setattr(port, "_handler", workspace.source_root)
    repo.git("worktree", "remove", "--force", str(root))
    with pytest.raises(remote._OpError):
        remote._resolve_src_root(grant)
    fallback.assert_not_called()
@pytest.mark.parametrize("service", [chat, conversation])
def test_chat_root_and_expected_root_are_attempt_scoped(repo, service):
    item = repo.new()
    root = prepare(item)
    assert service.worktree_root(PROJECT, GROUP, str(root), token_id=item.token["token_id"]) == root
    error = chat.ChatCommandError if service is chat else conversation.c.ConversationError
    with pytest.raises(error) as refused:
        service.worktree_root(PROJECT, GROUP, str(repo.group), token_id=item.token["token_id"])
    assert refused.value.code == "chat_command_worktree_mismatch"
@pytest.mark.parametrize("service", [chat, conversation])
def test_document_only_chat_refuses_before_chat_capability(harness, service):
    harness.enabled = False
    item = harness.new()
    prepare(item)
    run = {"project_id": PROJECT, "group_id": GROUP, "token_id": item.token["token_id"], "action_scope": "new"}
    error = chat.ChatCommandError if service is chat else conversation.c.ConversationError
    with pytest.raises(error) as refused:
        service.create_request(run, {"program": "pytest"})
    assert refused.value.code == "graph_git_required"
@pytest.mark.parametrize("operation", ["test_run", "file_transfer", "attachment_copy", "api_group_source"])
def test_group_only_source_paths_refuse_bound_tokens(repo, operation):
    item = repo.new()
    prepare(item)
    with pytest.raises(port.GraphSourceError) as refused:
        port.reject_group_source(item.token, operation)
    assert refused.value.code == "graph_attempt_source_unsupported"
    assert refused.value.details["operation"] == operation
def test_api_group_source_tool_still_refuses_bound_tokens(repo):
    """replaced the Self-check half of the former
    test_selfcheck_service_and_api_tool_do_not_reach_group_lookup; the API tool's group root keeps refusing.
    ``ToolError`` carries the code as ``reason`` (the former test read a ``code`` attribute it does not have)."""
    item = repo.new()
    root = prepare(item)
    with pytest.raises(api_server_tools.ToolError) as refused:
        api_server_tools.require_group_root({"project_id": PROJECT, "group_id": GROUP,
            "source_root": str(root), "token_id": item.token["token_id"]})
    assert refused.value.status == 409 and refused.value.reason == "graph_attempt_source_unsupported"
@pytest.mark.parametrize("layout", [workspace.REF_LAYOUT, workspace.BRANCH_LAYOUT])
@pytest.mark.parametrize("entry", ["edit", "draft"])
def test_mapped_token_self_check_runs_in_attempt_root(repo, selfcheck, monkeypatch, entry, layout):
    """a mapped token's Self-check is admitted (no graph_attempt_source_unsupported), runs
    with its attempt root as worktree and cwd, holds the Group's G and the attempt's W, and records attempt_id /
    input_commit / the attempt tree's HEAD on the run row. The group worktree is never resolved. the earlier implementation: the same
    in both layouts; a ref-layout tree's HEAD is detached (no branch), the W key is the attempt's either way."""
    sc = tr_self_check_service
    monkeypatch.setattr(workspace, "NEW_LAYOUT", layout)
    item = repo.new()
    root = prepare(item)
    (root / "value.txt").write_text("attempt edit", encoding="utf-8")
    calls_before = len(repo.lock_calls)
    request = {"program": "pytest", "args": ["-q"], "cwd": "."}
    if entry == "edit":
        monkeypatch.setattr(sc, "_document", lambda _doc: {"project_id": PROJECT, "group_id": GROUP})
        row = sc.start("tr-doc", request, "worker", item.token)
    else:
        row = sc.start_draft(_draft_token(item, monkeypatch), request)
    assert selfcheck.done.wait(10)

    input_commit = workspace.attempt(item.attempt)["input_commit"]
    assert row["attempt_id"] == item.attempt and row["input_commit"] == input_commit
    created = selfcheck.created[0]
    assert (created["attempt_id"], created["input_commit"]) == (item.attempt, input_commit)
    assert created["tr_doc_id"] == ("tr-doc" if entry == "edit" else None)
    finished = selfcheck.finished[0]
    assert finished.root == root.resolve() and finished.cwd == root.resolve()
    assert finished.before["head"] == input_commit
    branch = workspace.location(workspace.attempt(item.attempt), PROJECT)[2]
    assert finished.before["branch"] == (None if layout == workspace.REF_LAYOUT else branch)
    assert repo.lock_calls[calls_before:] == [("G", None), ("W", branch)]
    assert finished.attempt_lock == ("W", branch)
    assert finished.spawned is not None and finished.spawned.is_set()     # the request waited for the spawn
    port._fence_handler.assert_called_once()
    assert port._fence_handler.call_args.args[1] == "self_check"
    assert port._fence_handler.call_args.kwargs["keep"] is not None      # judged inside the write session
    selfcheck.group_lookup.assert_not_called()
    assert (repo.group / "value.txt").read_text() == "initial"
@pytest.mark.parametrize("broken,code,reason", [
    ("not_prepared", "graph_worktree_unavailable", "not_prepared"),
    ("worktree_removed", "graph_worktree_unavailable", "worktree_missing"),
    ("invalidated", "graph_worktree_unavailable", "attempt_not_active"),
    ("stopping", "graph_attempt_inactive", "run_stopping"),
])
@pytest.mark.parametrize("entry", ["edit", "draft"])
def test_mapped_token_self_check_refuses_unready_or_inactive_attempt_with_409(
        repo, selfcheck, monkeypatch, entry, broken, code, reason):
    """an attempt that is not prepared, lost its tree, was invalidated or whose run is stopping is 409 --
    no run row, no lock, no group worktree in its place."""
    sc = tr_self_check_service
    item = repo.new()
    if broken != "not_prepared":
        root = prepare(item)
    if broken == "worktree_removed":
        repo.git("worktree", "remove", "--force", str(root))
    elif broken == "invalidated":
        repo.db._execute("UPDATE graph_node_attempts SET status='invalidated' WHERE attempt_id=?", [item.attempt])
    elif broken == "stopping":
        port._fence_handler.side_effect = port.GraphAttemptInactive(
            reason="run_stopping", attempt_id=item.attempt, operation="self_check")
    calls_before = len(repo.lock_calls)
    with pytest.raises(sc.SelfCheckError) as refused:
        if entry == "edit":
            monkeypatch.setattr(sc, "_document", lambda _doc: {"project_id": PROJECT, "group_id": GROUP})
            sc.start("tr-doc", {"program": "pytest"}, "worker", item.token)
        else:
            sc.start_draft(_draft_token(item, monkeypatch), {"program": "pytest"})
    assert refused.value.status == 409 and refused.value.code == code
    assert refused.value.details["reason"] == reason
    assert refused.value.code != "graph_attempt_source_unsupported"
    assert selfcheck.created == [] and selfcheck.finished == []
    assert repo.lock_calls[calls_before:] == []
    selfcheck.group_lookup.assert_not_called()
