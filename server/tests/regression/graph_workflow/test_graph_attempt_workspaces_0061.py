"""0061 T#5: attempt workspaces and fail-closed source routing.

TR runs py_compile only. These SQLite/Git fixtures and mocked provider/lock
boundaries are for the separate test WP/TSR; no AI or network is required.
"""
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from flowgate.graph_run import execution, execution_store as ledger, workspace
from flowgate.conversation import commands as conversation
from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services import run_apply
from modules.flow_gate.services import graph_source_hooks as port
from modules.flow_gate.services import chat_command_service as chat, remote_tool_service as remote
from modules.flow_gate.services import api_server_tools, tr_self_check_service
from modules.flow_gate.services.ai_invoke import admission, provider_cli
from modules.flow_gate.services import graph_execution_hooks
from test_graph_attempt_runs_0061 import Store, MIGRATIONS, STAMP, FUTURE

PROJECT, GROUP = "p", "p.default.1"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    db = Store()
    db.db.executescript("""
        CREATE TABLE graph_runs(run_id TEXT PRIMARY KEY, group_id TEXT, status TEXT,
            last_event_seq INTEGER DEFAULT 0, updated_at TEXT, stopped_at TEXT, finished_at TEXT);
        CREATE TABLE graph_run_token_map(token_id TEXT PRIMARY KEY, run_id TEXT,
            node_key TEXT, stage TEXT, mapped_at TEXT);
        CREATE TABLE graph_node_runs(run_id TEXT, node_key TEXT, status TEXT, result_doc_id TEXT,
            opened_at TEXT, finished_at TEXT, dispatch_token_issued INTEGER, dispatch_issued_to TEXT,
            dispatch_mention_id TEXT, dispatch_error_code TEXT, stage TEXT, instruction_doc_id TEXT,
            result_event_floor INTEGER, updated_at TEXT);
        CREATE TABLE graph_run_events(run_id TEXT, event_seq INTEGER, event_type TEXT,
            node_key TEXT, from_status TEXT, to_status TEXT, cause TEXT, result_doc_id TEXT,
            canvas_state TEXT, actor_user_id TEXT, reason TEXT, payload_json TEXT, created_at TEXT,
            PRIMARY KEY(run_id, event_seq));
    """)
    # 0110 T#2: execution_store.request and workspace.prepare read/write the T#8 result columns
    # (result_commit, integration_status, input_source, ...) added by 153a, which also alters
    # tr_commit_ledger (086d) — the same chain test_graph_attempt_results_0061 loads, on the
    # same parent stubs (086d references groups/documents and alters group_git_state).
    db.db.executescript("""
        CREATE TABLE groups(group_id TEXT PRIMARY KEY);
        CREATE TABLE documents(doc_id TEXT PRIMARY KEY, group_id TEXT, type_code TEXT,
                               seq INTEGER, commit_message TEXT);
        CREATE TABLE group_git_state(group_id TEXT PRIMARY KEY);
    """)
    for name in ("151_graph_run_state_attempts.sql", "152_graph_attempt_runs.sql",
                 "086d_tr_commit_ledger.sql", "153a_graph_attempt_results.sql"):
        db.db.executescript((MIGRATIONS / "sqlite" / name).read_text(encoding="utf-8"))
    db._execute("INSERT INTO graph_runs(run_id,group_id,status) VALUES('run',?,'running')", [GROUP])
    monkeypatch.setattr(run_store, "get_store", lambda: db)
    monkeypatch.setattr(port, "get_store", lambda: db)
    monkeypatch.setattr(execution, "get_store", lambda: db)
    # 0110 T#2: T#10 inputs (definition_store.revision) and 0085 gate reservations (settle) read
    # these stores; on this DB their tables are absent -> no definition edges, no reservations.
    from flowgate.gate_decisions import repository as gate_repository
    monkeypatch.setattr(workspace.definition_store, "get_store", lambda: db)
    monkeypatch.setattr(gate_repository, "get_store", lambda: db)
    monkeypatch.setattr(run_store, "run_by_id", lambda rid: db._fetch_one(
        "SELECT * FROM graph_runs WHERE run_id=?", [rid]))
    monkeypatch.setattr(run_store, "node_runs", lambda rid: db._fetch_all(
        "SELECT * FROM graph_node_runs WHERE run_id=?", [rid]))
    monkeypatch.setattr(ledger, "now", lambda: STAMP)
    monkeypatch.setattr(ledger, "deadline", lambda: FUTURE)
    state = SimpleNamespace(db=db, enabled=True, tokens={}, held=[], lock_calls=[])
    def guard(_group, body):
        assert not state.held, "Git locks must be released before a Flow transaction"
        with db.transaction():
            return body()
    monkeypatch.setattr(workspace, "with_flow_guard", guard)
    monkeypatch.setattr(execution, "with_flow_guard", guard)
    def acquire(domain, project, **kw):
        assert not db.db.in_transaction
        key = (domain, kw.get("target_key"))
        state.held.append(key)
        state.lock_calls.append(key)
        return SimpleNamespace(ok=True, lock_key=key)
    monkeypatch.setattr(workspace.locks, "new_context", lambda *_: object())
    monkeypatch.setattr(workspace.locks, "acquire", acquire)
    monkeypatch.setattr(workspace.locks, "release", lambda _ctx, key: state.held.remove(key))
    monkeypatch.setattr(workspace.git, "get_storage_root", lambda: tmp_path / "storage")
    monkeypatch.setattr(workspace, "project_dir_name", lambda project: project)
    monkeypatch.setattr(workspace.git.db_git, "get_config", lambda _: {"enabled": state.enabled})
    monkeypatch.setattr(workspace.db_groups, "get_by_id", lambda _: {"project_id": PROJECT})
    monkeypatch.setattr(workspace, "_record_attempt", Mock())
    monkeypatch.setattr(port, "_handler", workspace.source_root)
    monkeypatch.setattr(port, "_mutation_handler", workspace.mutation)
    monkeypatch.setattr(graph_execution_hooks, "_handler",
                        lambda action, *a, **kw: execution._ACTIONS[action](*a, **kw))
    monkeypatch.setattr(db_tokens, "get_by_id", lambda key: state.tokens.get(key))
    monkeypatch.setattr(run_apply, "load_context", lambda run, *_:
                        SimpleNamespace(run_id=run["run_id"], run=run))
    monkeypatch.setattr(run_apply, "emit_node_state", Mock())
    monkeypatch.setattr(run_apply, "emit_run_status", Mock())
    monkeypatch.setattr(execution.ai_invoke_runs, "get", Mock(return_value=None))
    from modules.flow_gate.services import ai_invoke_service
    monkeypatch.setattr(ai_invoke_service, "_reclaim_orphan_lease_token", Mock())

    def new(node="a", kind="write"):
        db._execute("INSERT INTO graph_node_runs(run_id,node_key,status) VALUES('run',?,'open')", [node])
        aid = run_store.open_attempt("run", node, 1)
        eid = ledger.request(aid, "run", kind, {"type": "write", "job": {}})
        tid = "token-" + node
        db._execute("INSERT INTO graph_run_token_map(token_id,run_id,node_key,attempt_id) VALUES(?,'run',?,?)",
                    [tid, node, aid])
        state.tokens[tid] = {"token_id": tid, "project": PROJECT, "group_id": GROUP,
                             "action_scope": "new", "continuation_locale": "en"}
        return SimpleNamespace(attempt=aid, execution=eid, token=state.tokens[tid])
    state.new = new
    yield state
    db.db.close()


@pytest.fixture
def repo(harness, tmp_path, monkeypatch):
    if not workspace.git.git_available():
        pytest.skip("Git executable is required for linked-worktree fixtures")
    root = tmp_path / "group"
    root.mkdir()
    def git(*args):
        proc = workspace.git._run_git(list(args), cwd=root)
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()
    git("init", "-b", "group")
    (root / "value.txt").write_text("initial", encoding="utf-8")
    git("add", "value.txt")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "initial")
    monkeypatch.setattr(workspace.git, "effective_src_root", lambda *_: root)
    monkeypatch.setattr(workspace.git, "ensure_worktree", Mock(side_effect=AssertionError("group already exists")))
    harness.group, harness.git = root, git
    return harness


def prepare(item):
    return workspace.prepare(item.execution, PROJECT, GROUP)


def test_two_attempts_isolate_dirty_files_and_share_only_input_commit(repo):
    left, right = repo.new("left"), repo.new("right")
    left_root, right_root = prepare(left), prepare(right)
    assert left_root != right_root and repo.group not in (left_root, right_root)
    assert workspace.attempt(left.attempt)["input_commit"] == repo.git("rev-parse", "HEAD")
    assert workspace.attempt(right.attempt)["input_commit"] == repo.git("rev-parse", "HEAD")
    remote._execute("write", {"path": "value.txt", "content": "left"}, port.source_root(left.token))
    remote._execute("write", {"path": "value.txt", "content": "right"}, port.source_root(right.token))
    assert (left_root / "value.txt").read_text() == "left"
    assert (right_root / "value.txt").read_text() == "right"
    assert (repo.group / "value.txt").read_text() == "initial"
    assert not repo.held


def test_review_and_rework_reuse_attempt_branch_and_dirty_tree(repo):
    item = repo.new()
    root = prepare(item)
    (root / "value.txt").write_text("work in progress", encoding="utf-8")
    for kind in ("review", "rework", "review"):
        owner = ledger.acquire(item.execution, "ai")
        ledger.end(item.execution, owner)
        item.execution = ledger.request(item.attempt, "run", kind, {})
        assert prepare(item) == root
        assert (root / "value.txt").read_text() == "work in progress"
    assert len(run_store.events("run")) == 1


def test_recover_after_git_before_journal_pins_original_group_head(repo):
    item = repo.new()
    original = workspace._materialize(workspace.attempt(item.attempt), PROJECT)
    assert workspace.attempt(item.attempt)["input_commit"] is None
    (repo.group / "value.txt").write_text("later", encoding="utf-8")
    repo.git("add", "value.txt")
    repo.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "later")
    root = prepare(item)
    assert workspace.attempt(item.attempt)["input_commit"] == original
    assert (root / "value.txt").read_text() == "initial"


def test_document_only_admission_and_source_refusal(harness, monkeypatch):
    harness.enabled = False
    item = harness.new()
    monkeypatch.setattr(workspace.git, "ensure_worktree", Mock(side_effect=AssertionError("must not provision")))
    assert admission._require_group_worktree(PROJECT, "default", GROUP, "main",
                                            graph_exec_id=item.execution) is None
    assert workspace.attempt(item.attempt)["input_commit"] is None
    assert json.loads(run_store.events("run")[0]["payload_json"])["mode"] == "document_only"
    harness.enabled = True  # mode remains pinned for this attempt
    assert prepare(item) is None
    with pytest.raises(port.GraphSourceError) as refused:
        port.source_root(item.token)
    assert refused.value.status_code == 409 and refused.value.code == "graph_git_required"


@pytest.mark.parametrize("other_running", [False, True])
def test_creation_failure_invalidates_only_its_attempt(repo, monkeypatch, other_running):
    item = repo.new()
    other = repo.new("other") if other_running else None
    if other:
        prepare(other)
        ledger.acquire(other.execution, "other-ai")
    original = workspace.git._run_git
    def fail_add(args, **kw):
        if args[:2] == ["worktree", "add"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="injected")
        return original(args, **kw)
    monkeypatch.setattr(workspace.git, "_run_git", fail_add)
    with pytest.raises(port.GraphSourceError):
        prepare(item)
    row = workspace.attempt(item.attempt)
    assert row["status"] == "invalidated" and row["invalidated_reason"] == "worktree_failed"
    assert ledger.get(item.execution)["status"] == "cancelled"
    assert next(n for n in run_store.node_runs("run") if n["node_key"] == "a")["status"] == "pending"
    assert run_store.run_by_id("run")["status"] == ("running" if other else "idle")
    directory, _root, branch = workspace.location(row, PROJECT)
    assert not directory.exists()
    assert not workspace.git._ref_exists(repo.group, "refs/heads/" + branch)
    assert json.loads(run_store.events("run")[-1]["payload_json"])["reason"] == "worktree_add_failed"


def test_launch_revalidates_before_acquire(repo, monkeypatch):
    item = repo.new()
    root = prepare(item)
    repo.git("worktree", "remove", "--force", str(root))
    acquired = Mock(side_effect=AssertionError("must not acquire without a worktree"))
    monkeypatch.setattr(ledger, "acquire", acquired)
    import threading
    run = {"graph_exec_id": item.execution, "run_id": "ai", "cancel_event": threading.Event()}
    assert execution.launch(run) is False
    acquired.assert_not_called()
    assert workspace.attempt(item.attempt)["status"] == "invalidated"
    assert ledger.get(item.execution)["status"] == "cancelled"


def test_owner_mismatch_never_deletes_foreign_workspace(repo):
    item = repo.new()
    root = prepare(item)
    marker_path = root.parent / workspace.merge_target.OWNER_MARKER
    marker = json.loads(marker_path.read_text())
    marker["attempt_id"] = "foreign"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(port.GraphSourceError):
        port.source_root(item.token)
    ledger.cancel(item.execution, None, "requested")
    repo.db._execute("UPDATE graph_node_attempts SET status='invalidated' WHERE attempt_id=?", [item.attempt])
    assert workspace.cleanup(item.attempt) is False
    assert root.exists()


def test_cleanup_keeps_ended_or_live_attempts_and_removes_invalidated(repo):
    item = repo.new()
    root = prepare(item)
    repo.db._execute("UPDATE graph_node_attempts SET status='invalidated' WHERE attempt_id=?", [item.attempt])
    assert not workspace.cleanup(item.attempt)  # requested execution may still launch
    ledger.cancel(item.execution, None, "requested")
    repo.db._execute("UPDATE graph_node_attempts SET status='ended' WHERE attempt_id=?", [item.attempt])
    assert not workspace.cleanup(item.attempt) and root.exists()
    repo.db._execute("UPDATE graph_node_attempts SET status='invalidated' WHERE attempt_id=?", [item.attempt])
    assert workspace.cleanup(item.attempt)
    assert not root.parent.exists()


@pytest.fixture
def remote_pipeline(harness, monkeypatch):
    def grant(raw):
        token = harness.tokens[raw]
        return {"grant_id": remote._WORKER_GRANT_PREFIX + raw, "project": PROJECT, "group_id": GROUP}
    monkeypatch.setattr(remote, "_authenticate", grant)
    monkeypatch.setattr(remote.db_grants, "get_scopes", lambda _: {"read", "grep", "write", "remove"})
    monkeypatch.setattr(remote, "_log", Mock())
    monkeypatch.setattr(remote, "_emit_explorer_refresh", Mock())
    return lambda item, op, body: remote.handle(op, item.token["token_id"], body)


OPERATIONS = [
    ("read", {"path": "value.txt"}), ("grep", {"pattern": "initial"}),
    ("glob", {"pattern": "*.txt"}), ("stat", {"path": "value.txt"}),
    ("diff", {"target_ref": "group"}), ("log", {"target_ref": "group"}),
    ("show", {"sha": "abcd"}), ("merge_preview", {"target_ref": "group"}),
    ("write", {"path": "new.txt", "content": "new"}),
    ("patch", {"path": "value.txt", "old_string": "initial", "new_string": "changed"}),
    ("remove", {"path": "value.txt"}),
]


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


# 0110 T#3 (contract change, NR0003 §3 T-B item 6): "self_check" left this list -- a bound token's Self-check now runs
# in its attempt root (test_mapped_token_self_check_runs_in_attempt_root below). The other group-only paths still refuse.
@pytest.mark.parametrize("operation", ["test_run", "file_transfer", "attachment_copy", "api_group_source"])
def test_group_only_source_paths_refuse_bound_tokens(repo, operation):
    item = repo.new()
    prepare(item)
    with pytest.raises(port.GraphSourceError) as refused:
        port.reject_group_source(item.token, operation)
    assert refused.value.code == "graph_attempt_source_unsupported"
    assert refused.value.details["operation"] == operation


def test_api_group_source_tool_still_refuses_bound_tokens(repo):
    """0110 T#3 replaced the Self-check half of the former
    test_selfcheck_service_and_api_tool_do_not_reach_group_lookup; the API tool's group root keeps refusing.
    ``ToolError`` carries the code as ``reason`` (the former test read a ``code`` attribute it does not have)."""
    item = repo.new()
    root = prepare(item)
    with pytest.raises(api_server_tools.ToolError) as refused:
        api_server_tools.require_group_root({"project_id": PROJECT, "group_id": GROUP,
            "source_root": str(root), "token_id": item.token["token_id"]})
    assert refused.value.status == 409 and refused.value.reason == "graph_attempt_source_unsupported"


@pytest.fixture
def selfcheck(repo, monkeypatch):
    """0110 T#3: tr_self_check_service._start on these attempt fixtures, up to its worker thread. The run store and
    the worker are recorded instead of executed; the Group / attempt locks are the harness's recording fakes. The
    worker keeps ``_finish``'s spawn contract: it "spawns" (``before_spawn`` may hold it there, ``order`` records it),
    then sets ``spawned``. The real ``_finish`` and locks: test_tr_self_check_attempt_w_0110."""
    sc = tr_self_check_service
    from modules.flow_gate.services.git import instance_registry
    state = SimpleNamespace(created=[], finished=[], done=threading.Event(), before_spawn=None, order=[])
    state.group_lookup = Mock(side_effect=AssertionError("an attempt token's Self-check must not use the group worktree"))
    monkeypatch.setattr(sc.git_service, "effective_src_root_ex", state.group_lookup)
    monkeypatch.setattr(sc.db_projects, "tr_self_check_enabled", lambda _p: True)
    monkeypatch.setattr(sc.db_runs, "has_group_recovery_incomplete", lambda *_: False)
    monkeypatch.setattr(sc.db_runs, "get_active", lambda *_: None)
    monkeypatch.setattr(sc.lock_manager, "new_context", lambda kind="req", ident=None, **_: SimpleNamespace(
        ctx_id=f"{kind}:{ident}", on_lock_lost=None, lock_lost=False, find_held=lambda _key: None))
    monkeypatch.setattr(sc.policy, "resolve_command", lambda program, *_a, **_kw: SimpleNamespace(
        executable=program, argv_prefix=[], name=program, origin="path"))
    monkeypatch.setattr(instance_registry, "current_instance_id", lambda: "inst-0110")
    monkeypatch.setattr(sc, "_emit", Mock())

    def create_pending(project_id, group_id, tr_doc_id, *_args, **kw):
        state.created.append({"project_id": project_id, "group_id": group_id, "tr_doc_id": tr_doc_id, **kw})
        return {"self_check_run_id": kw["run_id"], "project_id": project_id, "group_id": group_id,
                "tr_doc_id": tr_doc_id, "status": "pending", "attempt_id": kw.get("attempt_id"),
                "input_commit": kw.get("input_commit")}
    monkeypatch.setattr(sc.db_runs, "create_pending", create_pending)

    def finish(run_id, ctx, lock_key, root, before, runtime, command, args, cwd, env, timeout, keeper, attempt_lock,
               spawned=None):
        if state.before_spawn is not None:
            state.before_spawn()
        state.order.append("spawned")
        if spawned is not None:
            spawned.set()
        state.finished.append(SimpleNamespace(root=root, cwd=cwd, before=before, lock_key=lock_key,
                                              attempt_lock=attempt_lock, spawned=spawned))
        keeper.stop()
        sc._cleanup_runtime(runtime, run_id, tracked=False)
        state.done.set()
    monkeypatch.setattr(sc, "_finish", finish)
    monkeypatch.setattr(port, "_fence_handler", Mock())
    return state


def _draft_token(item, monkeypatch):
    """The attempt's token as a TR(new) worker token, admitted by draft_target."""
    from modules.flow_gate.services import remote_tool_service
    sc = tr_self_check_service
    token = {**item.token, "doc_ref": "seq-doc", "action_scope": "new", "issued_to": "worker"}
    monkeypatch.setattr(sc.db_documents, "get_by_id", lambda _doc: {"project_id": PROJECT, "group_id": GROUP})
    monkeypatch.setattr(remote_tool_service, "_worker_token_step_type_result", lambda _token: ("TR", False))
    return token


@pytest.mark.parametrize("entry", ["edit", "draft"])
def test_mapped_token_self_check_runs_in_attempt_root(repo, selfcheck, monkeypatch, entry):
    """0110 T#3 (NR0003 §3 T-B): a mapped token's Self-check is admitted (no graph_attempt_source_unsupported), runs
    with its attempt root as worktree and cwd, holds the Group's G and the attempt's W, and records attempt_id /
    input_commit / the attempt tree's HEAD on the run row. The group worktree is never resolved."""
    sc = tr_self_check_service
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
    assert finished.before["branch"] == workspace.location(workspace.attempt(item.attempt), PROJECT)[2]
    branch = finished.before["branch"]
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
    """0110 T#3: an attempt that is not prepared, lost its tree, was invalidated or whose run is stopping is 409 --
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


def test_attempt_tree_lost_after_admission_is_selfcheck_worktree_unavailable(repo, selfcheck):
    """0110 T#3: ``_worktree(target)`` of an admitted attempt target re-resolves the attempt; a lost tree is
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


@pytest.fixture
def fenced_selfcheck(selfcheck, monkeypatch):
    """0110 T#3 rework: the real Stop gate judgement (``fencing.require_token``) in front of the Self-check start, on
    the harness's real graph rows; its refusal records are captured."""
    from flowgate.graph_run import fencing
    recorded = []
    monkeypatch.setattr(fencing, "record", lambda row, operation, why, **extra: recorded.append((operation, why)))
    monkeypatch.setattr(port, "_fence_handler", fencing.require_token)
    selfcheck.recorded, selfcheck.fencing = recorded, fencing
    return selfcheck


@pytest.mark.parametrize("commit", ["stop", "invalidation"])
def test_a_stop_or_invalidation_committed_before_the_judgement_spawns_nothing(repo, fenced_selfcheck, monkeypatch,
                                                                             commit):
    """0110 T#3 rework: the start is held at a barrier before its judgement (``_document``); meanwhile a Stop commits
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
    """0110 T#3 rework: the start's worker is held at a barrier right before its spawn -- G, W and the run row taken.
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
    """0110 T#3: the TR-bound route no longer answers 409 graph_attempt_source_unsupported for a mapped token; the
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
    """0110 T#3: help_catalog's tr_self_check item matches the service -- a mapped token is told its run executes in
    its attempt tree (and the 409 codes it may get); an unmapped token's content is unchanged."""
    from modules.flow_gate.services import help_catalog
    item = repo.new()
    prepare(item)
    repo.tokens["ordinary"] = {"token_id": "ordinary", "project": PROJECT, "group_id": GROUP}

    def content(token, locale="en"):
        ctx = {"principal_kind": "worker", "action_scope": "edit", "doc_type": "TR", "locale": locale,
               "base_url": "/flowgate/api/v1", "source_mode": "remote", "tool_kind": "read_write",
               "registry": None, "token_rec": token}
        assert help_catalog.decide_visibility("tr_self_check", ctx).visible
        return help_catalog.build_item("tr_self_check", ctx)["content"]

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
    # 0110 T#3: an unmapped token's Self-check keeps the group worktree and adds no attempt fields.
    assert tr_self_check_service._attempt_target(token, PROJECT, GROUP) == {}
    assert tr_self_check_service._worktree({"project_id": PROJECT, "group_id": GROUP}) == repo.group.resolve()
    assert remote._resolve_src_root(grant) == repo.group
    assert chat.worktree_root(PROJECT, GROUP, str(repo.group), token_id="ordinary") == repo.group
    # 0110 T#2: the TR scope check keeps the group basis for an unmapped token.
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


def _commit_in(root, *args):
    proc = workspace.git._run_git(
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args], cwd=root)
    assert proc.returncode == 0, proc.stderr


def test_mapped_token_tr_scope_measures_attempt_tree_from_input_commit(repo, monkeypatch):
    """0110 T#2 (NR0003 §2 T-A): a mapped token's TR scope is measured on its attempt tree from its
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

