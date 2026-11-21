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
def harness(tmp_path_factory, monkeypatch):
    # Attempt worktrees also need short paths for Git for Windows.
    tmp_path = tmp_path_factory.mktemp("gw")
    # Import consumers before patching run_apply: a first import during prepare()
    # otherwise caches our temporary load_context/emit_* functions in run_service
    # after monkeypatch restores run_apply, breaking later chat-control tests.
    from modules.flow_gate.graph_workflow.services import run_service  # noqa: F401
    # Owner markers use the startup document-write port; this isolated Git/ledger fixture has no documents.
    from contextlib import nullcontext
    from modules.flow_gate.graph_workflow.services import card_document_ports as ports
    monkeypatch.setitem(ports._handlers, ports.DOCUMENT_WRITE_GATE, lambda *_a, **_k: nullcontext())
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
def repo(harness, tmp_path_factory, monkeypatch):
    if not workspace.git.git_available():
        pytest.skip("Git executable is required for linked-worktree fixtures")
    # Keep Git's common directory short on Windows ($GIT_DIR too big), while
    # retaining pytest's configured scratch root and temporary-directory cleanup.
    root = tmp_path_factory.mktemp("gw")
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
    # 0111 T#8: the ref a new attempt took before its failed worktree add goes with it.
    assert not workspace.git._ref_exists(repo.group, workspace.ATTEMPT_REF_PREFIX + directory.name)
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












# 0110 T#3 (contract change, NR0003 §3 T-B item 6): "self_check" left this list -- a bound token's Self-check now runs
# in its attempt root (test_mapped_token_self_check_runs_in_attempt_root below). The other group-only paths still refuse.




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












def _commit_in(root, *args):
    proc = workspace.git._run_git(
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args], cwd=root)
    assert proc.returncode == 0, proc.stderr
