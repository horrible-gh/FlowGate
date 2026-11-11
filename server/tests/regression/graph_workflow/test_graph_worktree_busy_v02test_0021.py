"""v02test.0021: a transient refusal of the group worktree is ``git_busy``, not ``group_input_missing``.

Before, ``ensure_worktree`` answered 'failed' both for a broken slot and for a refusal that passes (another provision
job queued / running, a branch merge holding the group), and the attempt preparation turned every 'failed' into
``group_input_missing``: the attempt was invalidated and the run idled with ``error`` though running it again went
through. Now the caller reads the failure reason (``take_worktree_failure_reason``): a transient one keeps the attempt
and its request (``git_busy``, retried by a later refill); a lasting one fails as before, its reason kept in the run
event, the log and the 409 (``cause``). A failed Git command's stderr is kept with ``worktree_add_failed``.
"""
import json
from types import SimpleNamespace

import pytest

from flowgate.graph_run import execution_store as ledger, workspace
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import git_service, graph_source_hooks as port
from modules.flow_gate.services.git import base_slot, worktree, worktree_provision
from test_graph_attempt_workspaces_0061 import harness, repo, prepare, PROJECT  # noqa: F401 (fixtures)


@pytest.fixture(autouse=True)
def _write_gate(monkeypatch):
    """The owner marker write goes through the document write gate the server startup registers. Not under test here
    (these SQLite fixtures lack its document tables): a pass-through for each test, restored after."""
    from contextlib import nullcontext
    from modules.flow_gate.graph_workflow.services import card_document_ports as ports
    monkeypatch.setitem(ports._handlers, ports.DOCUMENT_WRITE_GATE, lambda *_a, **_k: nullcontext())


def _missing_group(repo, monkeypatch, ensure):
    """The group slot is absent at the first check, present once ``ensure`` answered 'ok'."""
    from flowgate.gate_decisions import cleanup as gate_cleanup
    root, state = repo.group, {"ready": False}
    monkeypatch.setattr(gate_cleanup, "restore_point", lambda *_: (None, None))
    monkeypatch.setattr(workspace.git, "effective_src_root", lambda *_: root if state["ready"] else None)

    def ensure_worktree(*_a, **_k):
        answer = ensure()
        state["ready"] = answer == "ok"
        return answer
    monkeypatch.setattr(workspace.git, "ensure_worktree", ensure_worktree)


def _refusing(reason):
    def ensure():
        worktree._note_worktree_failure(reason)
        return "failed"
    return ensure


def _events():
    return [json.loads(e["payload_json"]) for e in run_store.events("run")]


@pytest.mark.parametrize("reason", ["git_busy", "branch_merge_claim_active"])
def test_transient_refusal_keeps_attempt_and_request(repo, monkeypatch, reason):
    _missing_group(repo, monkeypatch, _refusing(reason))
    item = repo.new()
    with pytest.raises(port.GraphSourceError) as refused:
        prepare(item)
    assert refused.value.detail["code"] == "graph_worktree_unavailable"
    assert refused.value.detail["reason"] == "git_busy"            # execution.start_failed keeps the request
    assert refused.value.detail["provision_error"] == reason
    assert workspace.attempt(item.attempt)["status"] == "active"
    assert ledger.get(item.execution)["status"] == "requested"
    assert not any(e.get("mode") == "worktree_failed" for e in _events())
    assert run_store.run_by_id("run")["status"] == "running"


def test_busy_retry(repo, monkeypatch):
    """A later refill retries the same request once the refusal passed (short name: Git on Windows refuses a long
    ``$GIT_DIR`` and pytest names the temporary directory after the test)."""
    answers = iter([_refusing("git_busy"), lambda: "ok"])
    _missing_group(repo, monkeypatch, lambda: next(answers)())
    item = repo.new()
    with pytest.raises(port.GraphSourceError):
        prepare(item)
    root = prepare(item)                                            # the refill's retry of the same request
    assert root is not None and (root / "value.txt").read_text() == "initial"
    assert workspace.attempt(item.attempt)["status"] == "active"


def test_lasting_failure_still_invalidates_with_its_reason(repo, monkeypatch):
    _missing_group(repo, monkeypatch, _refusing("git_unavailable"))
    item = repo.new()
    with pytest.raises(port.GraphSourceError) as refused:
        prepare(item)
    assert refused.value.detail["reason"] == "worktree_failed"
    assert refused.value.detail["cause"] == "group_input_missing"
    row = workspace.attempt(item.attempt)
    assert row["status"] == "invalidated" and row["invalidated_reason"] == "worktree_failed"
    event = _events()[-1]
    assert event["reason"] == "group_input_missing" and event["provision_error"] == "git_unavailable"


def test_reason_left_by_an_earlier_call_is_not_taken(repo, monkeypatch):
    worktree._note_worktree_failure("git_busy")                     # an earlier call nobody read
    _missing_group(repo, monkeypatch, lambda: "failed")             # this one notes nothing
    item = repo.new()
    with pytest.raises(port.GraphSourceError) as refused:
        prepare(item)
    assert refused.value.detail["cause"] == "group_input_missing"
    assert "provision_error" not in _events()[-1]


def test_worktree_add_failure_keeps_git_stderr(repo, monkeypatch):
    original = workspace.git._run_git

    def fail_add(args, **kw):
        if args[:2] == ["worktree", "add"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="fatal: injected\n")
        return original(args, **kw)
    monkeypatch.setattr(workspace.git, "_run_git", fail_add)
    item = repo.new()
    with pytest.raises(port.GraphSourceError) as refused:
        prepare(item)
    assert refused.value.detail["cause"] == "worktree_add_failed"
    event = _events()[-1]
    assert event["reason"] == "worktree_add_failed" and event["git_stderr"] == "fatal: injected"


# -- the reason ensure_worktree leaves for its caller ---------------------------------------------------------------

@pytest.fixture
def quiet(monkeypatch):
    monkeypatch.setattr(git_service.db_git, "upsert_provision_failure", lambda *_a, **_k: None)
    monkeypatch.setattr(git_service, "_emit", lambda *_a, **_k: None)
    monkeypatch.setattr(base_slot, "_record_attempt", lambda *_a, **_k: None)
    monkeypatch.setattr(worktree, "_record_attempt", lambda *_a, **_k: None)
    worktree.take_worktree_failure_reason()


def test_fail_worktree_notes_reason_and_take_clears_it(quiet):
    git_service._fail_worktree("p", "p.default.1", "b", "git_unavailable")
    assert git_service.take_worktree_failure_reason() == "git_unavailable"
    assert git_service.take_worktree_failure_reason() is None


def test_ensure_worktree_starts_without_a_reason(quiet, monkeypatch):
    worktree._note_worktree_failure("git_busy")
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _: None)
    assert git_service.ensure_worktree("p", "default", "p.default.1") == "skipped"
    assert git_service.take_worktree_failure_reason() is None


def test_ensure_worktree_notes_branch_merge_claim(quiet, monkeypatch):
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _: {"enabled": True})
    monkeypatch.setattr(git_service, "_project_name", lambda _: "p")
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(git_service, "get_branch_merge_group_claim", lambda _: {"session": "s"})
    assert git_service.ensure_worktree("p", "default", "p.default.1") == "failed"
    assert git_service.take_worktree_failure_reason() == "branch_merge_claim_active"


@pytest.mark.parametrize("job, reason", [
    ({"status": "blocked", "last_error_code": "busy"}, "git_busy"),
    ({"status": "pending"}, "git_busy"),
    ({"status": "failed", "last_error_code": "branch_merge_claim_active"}, "branch_merge_claim_active"),
    ({"status": "failed", "last_error_code": "base_checkout_missing"}, "base_checkout_missing"),
])
def test_provision_report_notes_reason(quiet, job, reason):
    job = {"project_id": "p", "group_id": "p.default.1", "payload": json.dumps({"branch": "b"}), **job}
    assert worktree_provision._report(job) == worktree_provision.FAILED
    assert git_service.take_worktree_failure_reason() == reason
    assert (reason in git_service.TRANSIENT_WORKTREE_FAILURES) == (reason != "base_checkout_missing")


# -- a kept git_busy request asks for its own settle, and fails past the last retry ----------------------------------

def test_busy_start_retries_then_fails(repo, monkeypatch):
    from flowgate.graph_run import execution
    timers, settled = [], []

    class Timer:
        def __init__(self, delay, fn):
            self.delay, self.fn, self.daemon = delay, fn, False
            timers.append(self)

        def start(self):
            pass
    monkeypatch.setattr(execution.threading, "Timer", Timer)
    monkeypatch.setattr(execution, "settle", settled.append)
    monkeypatch.setattr(execution, "_busy_retries", {})
    _missing_group(repo, monkeypatch, _refusing("git_busy"))
    item = repo.new()
    for _ in execution.BUSY_RETRY_DELAYS:
        with pytest.raises(port.GraphSourceError) as refused:
            prepare(item)
        assert execution.start_failed(item.execution, refused.value) is True
        timers[-1].fn()                                            # the delayed settle replays the request
    assert [t.delay for t in timers] == list(execution.BUSY_RETRY_DELAYS)
    assert all(t.daemon for t in timers)
    assert len(settled) == len(execution.BUSY_RETRY_DELAYS) and set(settled) == {"p.default.1"}
    assert workspace.attempt(item.attempt)["status"] == "active"   # still kept while it retries
    with pytest.raises(port.GraphSourceError) as refused:
        prepare(item)
    assert execution.start_failed(item.execution, refused.value) is True
    assert timers[-1].delay == 0.0
    timers[-1].fn()                                                # past the last retry: fails as before
    row = workspace.attempt(item.attempt)
    assert row["status"] == "invalidated" and row["invalidated_reason"] == "worktree_failed"
    event = _events()[-1]
    assert event["reason"] == "git_busy_exhausted" and event["provision_error"] == "git_busy"
    assert len(settled) == len(execution.BUSY_RETRY_DELAYS) and execution._busy_retries == {}


def test_busy_retry_skips_an_admitted_request(repo, monkeypatch):
    from flowgate.graph_run import execution
    timers, settled = [], []
    monkeypatch.setattr(execution.threading, "Timer",
                        lambda delay, fn: timers.append(fn) or SimpleNamespace(daemon=False, start=lambda: None))
    monkeypatch.setattr(execution, "settle", settled.append)
    monkeypatch.setattr(execution, "_busy_retries", {})
    item = repo.new()
    execution._retry_busy(item.execution, "git_busy")
    ledger.acquire(item.execution, "ai")                           # admitted by another refill meanwhile
    timers[-1]()
    assert settled == [] and execution._busy_retries == {}
