"""flowgate.v02.0083 T#3 (WP-3) — a person's approval on an idle run moves the run on to the next card, with no further
resume (the flowgate.v02.0056 T#1 contract restored; 0061 T#2 개정, revised).

0061 T#2 kept every propagation of a run that is not ``running`` waiting (``frontier.defers``: paused, idle) until the next
command, so a person's approval on an idle run — the exception path of a card: a review that did not pass or spent its
count, a card a person writes — left the next card ``pending`` until someone pressed resume again. Revised here:

- an idle run a person's decision may take back to running (``RunState.approval_wakes``, ``command.approval_wakes``:
  the Flow's active run, not ``stopped``, no Time Machine operation unfinished, not inside an explicit command — the
  conditions of ``wake_graph_run``) propagates at once: the next card opens, and an AI card's job is created, the run
  woken to running first (``command.wake_approval``, cause ``work_resumed`` reason ``approval``);
- kept: a step opens only its targets, a paused run waits, a stopped run and a run under a Time Machine operation do not
  go on — their propagation waits for the next command, which drains it;
- the ordinary flow is not changed: an AI card still approves itself and opens the next card with no person's approval.

Each approval case runs on two idle waits: ``command_chain`` — the run's events prove the unfinished command it waits in,
so the existing ``command.resume_approval`` wakes it as well — and ``reported`` — the reported Flow's state, a run that
went idle before 0083 WP-2 judged a person's open card ``waiting_approval`` (its idle transition's event says
``no_targets``), so no command can be proven and only the revised ``defers`` moves it on. With the 0061 T#2 ``defers`` restored, the
reported wait keeps the next card pending until a resume
(``test_with_the_0061_t2_rule_the_reported_wait_needed_a_resume``).

Rework (rej_01M5FKJYMETCGHGJ): a card whose attempt worked in a Git worktree is done only once its result is merged
into the Flow's branch (0061 T#9 ``result_merge.defer_done``). On the reported wait such an approval was held for the
merge before the run was woken, so the integration took the idle run's hold (``result_merge._wake``), applied the
approval's propagation as ``pending`` and left the run idle: the next card waited for a resume. Now the approval wakes
the run when it is held (``run_apply.apply_decision``), the merge runs on the running run and its end opens the next
card. These cases use a prepared worktree attempt (its ``input_commit`` and mode record as ``workspace.prepare`` writes
them) whose result is a real commit on its own branch, merged by the real ``result_merge._merge`` (``git merge
--no-ff``) into a real group repository; the group's Git lock, branch merge claim and worktree cleanup are stubbed.

On the card settings suites' real SQLite DB, routes and fixtures (``test_fg_card_settings``,
``test_fg_card_settings_review``, ``test_fg_card_approval_0056``, ``test_fg_wp_ai_run_0083``). A paused or stopped
run with a card in review cannot be reached through the routes alone (Stop / Pause need a running run, and a Stop takes
its open / review cards back to pending), so those two states are set on the run row, as the 0083 T#2 suite does.
"""
from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    AI, FA, _h, _sql, db_path, decided, env,
)
from test_fg_card_settings_review import (
    _admin_and_reviewer, _executions_ended, _node, _result, _review_status, _run_ended,
)
from test_fg_card_approval_0056 import (  # noqa: F401 — card_approval_port: registered as startup does
    LEE, _approve, _chain, _current, _human_result, _run_id, _v01_approve, card_approval_port,
)
from test_fg_wp_ai_run_0083 import _cards, _register, _run_status, _start_ai_run


# ── helpers ──────────────────────────────────────────────────────────────────

def _begin(env, cards, mode=None):
    """R -> the cards confirmed and its run started (``mode``: the start command, default full)."""
    keys = _chain(env, cards)
    body = {"revision_no": 1} if mode is None else {"revision_no": 1, "mode": mode}
    started = env["client"].post(f"/api/v1/flows/{FA}/runs", json=body, headers=_h())
    assert started.status_code == 201, started.text
    return keys


def _seq(env):
    return _current(env)["last_event_seq"]


def _run_events_after(env, seq):
    return [(r["from_status"], r["to_status"], r["cause"], r["reason"]) for r in _sql(
        env["db"], "SELECT from_status, to_status, cause, reason FROM graph_run_events WHERE event_seq > ? "
                   "AND node_key IS NULL ORDER BY event_seq", (seq,))]


def _propagations(env, key):
    return [r["status"] for r in _sql(env["db"], "SELECT status FROM graph_propagations WHERE node_key = ?", (key,))]


def _no_command(monkeypatch):
    """Record every explicit command (resume / step / full) the test makes: none is expected."""
    from modules.flow_gate.graph_workflow.services import run_service

    commands = []
    real = run_service.go_on_command
    monkeypatch.setattr(run_service, "go_on_command",
                        lambda ctx, command, policies: commands.append(command) or real(ctx, command, policies))
    return commands


def _reported(env):
    """The reported Flow's wait: its run's idle transition was recorded as ``no_targets`` (before 0083 WP-2), so the
    events prove no unfinished command (``command._waiting_command``) though the run reads ``waiting_approval`` now."""
    import json

    row = _sql(env["db"], "SELECT event_seq, payload_json FROM graph_run_events WHERE node_key IS NULL "
                          "AND cause = 'run_idle' ORDER BY event_seq DESC LIMIT 1")[0]
    payload = {**json.loads(row["payload_json"]), "idle_reason": "no_targets"}
    _sql(env["db"], "UPDATE graph_run_events SET payload_json = ? WHERE event_seq = ? AND node_key IS NULL",
         (json.dumps(payload), row["event_seq"]))


def _person_waits(env, cards, mode=None, wait="command_chain"):
    """R -> t1 (a person's card) -> … with t1's result registered: the run idle, t1 awaiting its approval."""
    _admin_and_reviewer(env)
    keys = _begin(env, cards, mode)
    assert _run_status(env) == ("idle", "waiting_approval")       # 0083 T#2: a person writes t1
    if wait == "reported":
        _reported(env)
    result = _human_result(env, keys[0])
    assert _run_status(env)[0] == "idle"
    return keys, result


WAITS = pytest.mark.parametrize("wait", ["command_chain", "reported"])


# ── a person's approval on an idle run moves on (0061 T#2 개정) ───────────────────

@WAITS
def test_a_person_s_approval_on_an_idle_run_opens_the_next_ai_card_and_creates_its_job_revised_0061_t2(
        env, decided, monkeypatch, wait):
    commands = _no_command(monkeypatch)
    (t1, t2), result = _person_waits(env, [("T", LEE), ("T", AI)], wait=wait)
    seq = _seq(env)
    body = _approve(env, t1, body={"result_doc_id": result})
    nodes = {n["node_key"]: n["status"] for n in body["run"]["node_runs"]}
    assert (nodes[t1], nodes[t2]) == ("done", "open")                   # no resume: the next card opened
    assert _review_status(result) == "approved" and _propagations(env, t1) == ["done"]
    assert [j["node_key"] for j in env["jobs"]] == [t2]                 # the AI card's job is created
    assert _run_status(env) == ("running", None)                        # its execution keeps the run working
    assert _run_events_after(env, seq) == [("idle", "running", "work_resumed", "approval")]
    assert commands == []


@WAITS
def test_a_person_s_approval_on_an_idle_run_opens_the_next_person_s_card_and_the_run_waits_for_it_revised_0061_t2(
        env, decided, monkeypatch, wait):
    commands = _no_command(monkeypatch)
    (t1, t2), result = _person_waits(env, [("T", LEE), ("T", LEE)], wait=wait)
    seq = _seq(env)
    _approve(env, t1, body={"result_doc_id": result})
    assert (_node(env, t1)[0], _node(env, t2)[0], _propagations(env, t1)) == ("done", "open", ["done"])
    assert env["jobs"] == []                                            # a person writes t2: no job
    issued = _sql(env["db"], "SELECT t.issued_to, t.provider_id FROM tokens t JOIN graph_run_token_map m "
                             "ON m.token_id = t.token_id WHERE m.node_key = ?", (t2,))
    assert issued == [{"issued_to": "u_lee", "provider_id": None}]
    assert _run_status(env) == ("idle", "waiting_approval")             # woken, then idle again: nothing works
    assert _run_events_after(env, seq) == [("idle", "running", "work_resumed", "approval"),
                                           ("running", "idle", "run_idle", None)]
    assert commands == []


@WAITS
def test_a_person_s_v01_approval_on_an_idle_run_moves_on_as_the_card_approval_does_revised_0061_t2(
        env, decided, monkeypatch, wait):
    commands = _no_command(monkeypatch)
    (t1, t2), result = _person_waits(env, [("T", LEE), ("T", AI)], wait=wait)
    _v01_approve(result)                                                # the v0.1 approval and its Graph contact
    assert (_node(env, t1), _node(env, t2)[0]) == (("done", result), "open")
    assert [j["node_key"] for j in env["jobs"]] == [t2] and _propagations(env, t1) == ["done"]
    assert commands == []


@WAITS
def test_the_last_card_s_approval_on_an_idle_run_completes_it_revised_0061_t2(env, decided, wait):
    (t1,), result = _person_waits(env, [("T", LEE)], wait=wait)
    _approve(env, t1, body={"result_doc_id": result})
    assert _node(env, t1)[0] == "done" and _propagations(env, t1) == ["done"]
    assert _run_status(env) == ("idle", "completed")


@pytest.mark.parametrize("wait, before_resume", [("command_chain", "open"), ("reported", "pending")])
def test_with_the_0061_t2_rule_the_reported_wait_needed_a_resume(env, decided, monkeypatch, wait, before_resume):
    """What is revised: with the 0061 T#2 ``defers`` (every run not running waits) only a wait whose command its events
    prove went on (``command.resume_approval``, 0083 T#2); the reported wait left the next card pending after the
    approval, and only the next resume opened it — the reported "a person approves, then resumes"."""
    from modules.flow_gate.graph_workflow.domain import frontier

    monkeypatch.setattr(frontier, "defers", lambda s: s.status != "running")      # the rule before the revision
    (t1, t2), result = _person_waits(env, [("T", LEE), ("T", AI)], wait=wait)
    _approve(env, t1, body={"result_doc_id": result})
    assert (_node(env, t1)[0], _node(env, t2)[0]) == ("done", before_resume)
    assert [j["node_key"] for j in env["jobs"]] == ([t2] if before_resume == "open" else [])
    if before_resume == "pending":
        assert _propagations(env, t1) == ["pending"]
        resumed = env["client"].post(f"/api/v1/runs/{_run_id(env)}/resume", json={"mode": "full"}, headers=_h())
        assert resumed.status_code == 200, resumed.text
        assert (_node(env, t2)[0], [j["node_key"] for j in env["jobs"]]) == ("open", [t2])


# ── kept: step, paused, stopped, Time Machine ────────────────────────────────

@WAITS
def test_a_step_still_keeps_the_next_card_pending_after_a_person_s_approval(env, decided, monkeypatch, wait):
    (t1, t2), result = _person_waits(env, [("T", LEE), ("T", AI)], mode="step", wait=wait)
    _approve(env, t1, body={"result_doc_id": result})
    assert (_node(env, t1)[0], _node(env, t2)[0], _propagations(env, t1)) == ("done", "pending", ["pending"])
    assert env["jobs"] == [] and _run_status(env)[0] == "idle"         # the step is over: the next one waits
    resumed = env["client"].post(f"/api/v1/runs/{_run_id(env)}/resume", json={"mode": "step"}, headers=_h())
    assert resumed.status_code == 200, resumed.text                     # the next step opens it
    assert (_node(env, t2)[0], [j["node_key"] for j in env["jobs"]]) == ("open", [t2])


@WAITS
@pytest.mark.parametrize("state", ["paused", "stopped", "time_machine"])
def test_a_paused_stopped_or_time_machine_run_does_not_go_on_after_a_person_s_approval(
        env, decided, monkeypatch, state, wait):
    from flowgate.graph_time_machine import operation

    (t1, t2), result = _person_waits(env, [("T", LEE), ("T", AI)], wait=wait)
    if state == "paused":
        _sql(env["db"], "UPDATE graph_runs SET status = 'paused', idle_reason = NULL WHERE group_id = ?", (FA,))
    elif state == "stopped":
        _sql(env["db"], "UPDATE graph_runs SET idle_reason = 'stopped' WHERE group_id = ?", (FA,))
    else:
        monkeypatch.setattr(operation, "_unfinished", lambda flow: [{"operation_id": "tm_in_progress"}])
    before = _run_status(env)
    seq = _seq(env)
    _v01_approve(result)                                                # a person's approval reaches the Graph
    assert _node(env, t1) == ("done", result) and _review_status(result) == "approved"
    assert (_node(env, t2)[0], _propagations(env, t1)) == ("pending", ["pending"])
    assert env["jobs"] == [] and _run_status(env)[0] == before[0]       # the run does not go on
    if state != "time_machine":                                          # (a TM wait's idle reason is re-judged)
        assert _run_status(env) == before
    assert ("idle", "running", "work_resumed", "approval") not in _run_events_after(env, seq)
    if state == "time_machine":
        monkeypatch.setattr(operation, "_unfinished", lambda flow: [])  # the operation ends
    resumed = env["client"].post(f"/api/v1/runs/{_run_id(env)}/resume", json={"mode": "full"}, headers=_h())
    assert resumed.status_code == 200, resumed.text                     # the next command drains what waited
    assert (_node(env, t2)[0], _propagations(env, t1)) == ("open", ["done"])
    assert [j["node_key"] for j in env["jobs"]] == [t2]


def test_the_wake_judgement_follows_wake_graph_run_s_conditions(env, monkeypatch):
    from types import SimpleNamespace

    from flowgate.graph_run import command
    from flowgate.graph_time_machine import operation

    monkeypatch.setattr(operation, "_unfinished", lambda flow: [])
    idle = {"status": "idle", "idle_reason": "waiting_approval", "active_group_id": FA, "run_mode": "full",
            "step_node_keys": None}

    def wakes(command_pending=False, **run):
        return command.approval_wakes(SimpleNamespace(run={**idle, **run}, command_pending=command_pending, flow_id=FA))

    assert wakes() is True
    assert wakes(idle_reason="no_targets") is True and wakes(idle_reason="error") is True
    assert wakes(run_mode="step", step_node_keys=["gn_a"]) is True     # the step's own bound is ``in_command``
    assert [wakes(status=s) for s in ("running", "paused", "stopping")] == [False, False, False]
    assert wakes(idle_reason="stopped") is False
    assert wakes(active_group_id=None) is False                         # not the Flow's active run
    assert wakes(command_pending=True) is False                         # an explicit command drains it itself
    assert wakes(run_mode="step", step_node_keys=None) is False
    monkeypatch.setattr(operation, "_unfinished", lambda flow: [{"operation_id": "tm"}])
    assert wakes() is False


def test_defers_keeps_every_wait_but_the_wakeable_idle_run_revised_0061_t2():
    from modules.flow_gate.graph_workflow.domain.frontier import Decision, NodeRun, RunState, defers, virtual

    def state(status, wakes=False):
        return RunState(status, {"a": NodeRun("a", "review", "d")}, approval_wakes=wakes)

    assert defers(state("running")) is False
    assert defers(state("idle", wakes=True)) is False                   # 0061 T#2 개정
    assert defers(state("idle")) is True                                # stopped / Time Machine / a command
    assert defers(state("paused")) is True and defers(state("paused", wakes=True)) is True
    assert virtual(state("idle", wakes=True), Decision()).approval_wakes is True


# ── regression: the ordinary flow approves by itself ─────────────────────────

def test_r_ai_cards_still_approve_by_themselves_with_no_person_s_approval(env, decided, monkeypatch):
    from flowgate.card_approval import approval

    approvals = []
    real = approval.approve
    monkeypatch.setattr(approval, "approve", lambda *a, **kw: approvals.append(a) or real(*a, **kw))
    commands = _no_command(monkeypatch)
    _admin_and_reviewer(env)
    t1, t2 = _cards(env, [("T", AI), ("T", AI)], {0: (("instruction", "omit"),)})   # t1 writes its result at once
    started = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1}, headers=_h())
    assert started.status_code == 201, started.text
    [job] = env["jobs"]
    _heads, _call, [issued] = _start_ai_run(monkeypatch, job, run_id="air_t1")
    result = _result(81, "TR")
    _register(issued["token_id"], result, "TR")
    _executions_ended()
    _run_ended("air_t1")
    assert (_node(env, t1), _review_status(result)) == (("done", result), "approved")
    assert _node(env, t2)[0] == "open" and [j["node_key"] for j in env["jobs"]] == [t1, t2]
    assert approvals == [] and commands == []                           # no person, no resume
    assert _run_status(env)[0] == "running"


# ── a code result's merge: the approval's wake lasts until it is merged (rework rej_01M5FKJYMETCGHGJ) ────────

def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


@pytest.fixture
def group_tree(tmp_path, monkeypatch):
    """The Flow's group worktree: a real Git repository on the group branch ``flow`` (``result_merge._group_root``).
    Stubbed: the group's lock G (taken at once), no branch merge claim, and the ended attempt's worktree cleanup.
    ``merges`` records, at each real merge, the merged node's, the next node's and the run's status."""
    if not shutil.which("git"):
        pytest.skip("Git is not installed")
    from flowgate.graph_run import result_merge, workspace

    root = tmp_path / "group_tree"
    root.mkdir()
    _git(root, "init", "-b", "flow")
    _git(root, "config", "user.name", "Idle Approval Test")
    _git(root, "config", "user.email", "idle-approval@example.invalid")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "chore: base")
    monkeypatch.setattr(result_merge, "_group_root", lambda row: ("flowgate", root, "flow"))
    monkeypatch.setattr(result_merge.git_commit, "_acquire_group_lock", lambda *a, **kw: ("G",))
    monkeypatch.setattr(result_merge.git_commit, "_release_group_lock", lambda lock: None)
    monkeypatch.setattr(result_merge.git, "get_branch_merge_group_claim", lambda flow_id: None)
    cleaned, merges, seen = [], [], {}
    monkeypatch.setattr(workspace, "cleanup_ended", lambda attempt_id: cleaned.append(attempt_id) or True)
    real_merge = result_merge._merge

    def merge(row):
        env = seen["env"]
        merges.append((_node(env, row["node_key"])[0], _node(env, seen["next"])[0], _run_status(env)[0]))
        return real_merge(row)
    monkeypatch.setattr(result_merge, "_merge", merge)
    return SimpleNamespace(root=root, base=_git(root, "rev-parse", "HEAD"), tmp=tmp_path, cleaned=cleaned,
                           merges=merges, seen=seen)


def _prepared_worktree(env, tree, key):
    """``key``'s attempt as ``workspace.prepare`` records a Git worktree attempt (``input_commit`` from the run base,
    its mode record: the attempt's first workspace event). Its result is a real commit on the attempt's own branch in a
    linked worktree of the group repository, recorded as T#8 records it (``result_commit``, reused by
    ``result_commit.commit_result``)."""
    from flowgate.graph_run import workspace
    from modules.flow_gate.graph_workflow.store import run_store

    attempt_id = run_store.active_attempt_id(_run_id(env), key)
    _directory, _root, branch = workspace.location(workspace.attempt(attempt_id), "flowgate")
    linked = tree.tmp / "attempt_tree"
    _git(tree.root, "worktree", "add", "-b", branch, str(linked), tree.base)
    (linked / "result.txt").write_text("the card's result\n", encoding="utf-8")
    _git(linked, "add", ".")
    _git(linked, "commit", "-m", "feat: the card's result")
    result = _git(linked, "rev-parse", "HEAD")
    _sql(env["db"], "UPDATE graph_node_attempts SET input_commit = ?, input_source = 'run_base', "
                    "input_attempt_ids = '[]' WHERE attempt_id = ?", (tree.base, attempt_id))
    workspace._event(workspace.attempt(attempt_id), "worktree", input_commit=tree.base, input_source="run_base",
                     input_attempt_ids=[])
    _sql(env["db"], "UPDATE graph_node_attempts SET result_commit = ? WHERE attempt_id = ?", (result, attempt_id))
    return attempt_id, result


def _person_registers(env, key, seq=81, typ="T"):
    """As ``_human_result``, in the inbox's order: the Graph contact, then the node token's consume
    (``token_consumed``: the result's authorship by the attempt, which T#8's ``commit_result`` checks)."""
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import token_service

    token = _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ? ORDER BY rowid",
                 (key,))[0]["token_id"]
    result = _result(seq, typ)
    v01_contact.after_inbox_registered(FA, result, typ, token)
    assert token_service.consume(token, "flowgate", result)
    assert _node(env, key) == ("review", result)
    return result


def _person_waits_on_code(env, tree, cards, mode=None, wait="command_chain"):
    """As ``_person_waits``, t1's attempt a prepared worktree attempt with its result commit."""
    from flowgate.graph_run import result_merge, workspace

    _admin_and_reviewer(env)
    keys = _begin(env, cards, mode)
    assert _run_status(env) == ("idle", "waiting_approval")
    attempt_id, commit = _prepared_worktree(env, tree, keys[0])
    assert result_merge.needs_integration(workspace.attempt(attempt_id))     # its approval waits for the merge
    if wait == "reported":
        _reported(env)
    result = _person_registers(env, keys[0])
    assert _run_status(env)[0] == "idle"
    tree.seen.update(env=env, next=keys[-1])
    return keys, result, attempt_id, commit


def _integration(attempt_id):
    from flowgate.graph_run import workspace

    row = workspace.attempt(attempt_id)
    return row["status"], row["integration_status"], row.get("integrated_commit")


def _merged(tree, commit):
    """The result commit is a parent of a merge commit on the group branch (``--no-ff``)."""
    parents = _git(tree.root, "log", "--merges", "--format=%P", "flow").splitlines()
    return any(commit in line.split() for line in parents)


HOLD = ("integration_hold", "integration_hold_end")


@WAITS
@pytest.mark.parametrize("approve", ["card", "v01"])
def test_a_person_s_approval_held_for_its_merge_opens_the_next_ai_card_once_merged_revised_0061_t2(
        env, decided, monkeypatch, group_tree, wait, approve):
    commands = _no_command(monkeypatch)
    (t1, t2), result, attempt_id, commit = _person_waits_on_code(env, group_tree, [("T", LEE), ("T", AI)], wait=wait)
    seq = _seq(env)
    if approve == "card":
        _approve(env, t1, body={"result_doc_id": result})
    else:
        _v01_approve(result)
    assert group_tree.merges == [("review", "pending", "running")]     # nothing opened before the merge; run woken
    assert _merged(group_tree, commit)
    status, integration, merged_as = _integration(attempt_id)
    assert (status, integration) == ("ended", "integrated") and merged_as
    assert group_tree.cleaned == [attempt_id]
    assert (_node(env, t1), _node(env, t2)[0]) == (("done", result), "open")   # once merged, with no resume
    assert _review_status(result) == "approved" and _propagations(env, t1) == ["done"]
    assert [j["node_key"] for j in env["jobs"]] == [t2]                 # the AI card's job is created
    assert _run_status(env) == ("running", None)
    events = _run_events_after(env, seq)
    assert events.count(("idle", "running", "work_resumed", "approval")) == 1
    assert not [e for e in events if e[2] in HOLD]                      # no idle run's integration hold
    assert ("running", "idle", "run_idle", None) not in events
    assert commands == []                                              # no resume, step or full


@WAITS
def test_the_last_card_s_approval_held_for_its_merge_completes_the_run_once_merged_revised_0061_t2(
        env, decided, monkeypatch, group_tree, wait):
    commands = _no_command(monkeypatch)
    (t1,), result, attempt_id, commit = _person_waits_on_code(env, group_tree, [("T", LEE)], wait=wait)
    _approve(env, t1, body={"result_doc_id": result})
    assert _merged(group_tree, commit) and _integration(attempt_id)[1] == "integrated"
    assert _node(env, t1)[0] == "done" and _propagations(env, t1) == ["done"]
    assert _run_status(env) == ("idle", "completed") and commands == []


@WAITS
@pytest.mark.parametrize("state", ["step", "paused", "stopped", "time_machine"])
def test_a_step_paused_stopped_or_time_machine_run_still_waits_after_an_approval_held_for_its_merge(
        env, decided, monkeypatch, group_tree, state, wait):
    from flowgate.graph_time_machine import operation

    mode = "step" if state == "step" else None
    (t1, t2), result, attempt_id, commit = _person_waits_on_code(env, group_tree, [("T", LEE), ("T", AI)],
                                                                  mode=mode, wait=wait)
    if state == "paused":
        _sql(env["db"], "UPDATE graph_runs SET status = 'paused', idle_reason = NULL WHERE group_id = ?", (FA,))
    elif state == "stopped":
        _sql(env["db"], "UPDATE graph_runs SET idle_reason = 'stopped' WHERE group_id = ?", (FA,))
    elif state == "time_machine":
        monkeypatch.setattr(operation, "_unfinished", lambda flow: [{"operation_id": "tm_in_progress"}])
    seq = _seq(env)
    _v01_approve(result)
    assert _review_status(result) == "approved"
    assert _node(env, t2)[0] == "pending" and env["jobs"] == []         # the next card waits
    assert _propagations(env, t1) in ([], ["pending"])                  # (none yet while the merge waits)
    if state != "step":         # (a step's proven command is woken by ``resume_approval``, still bounded to its targets)
        assert ("idle", "running", "work_resumed", "approval") not in _run_events_after(env, seq)
    if state == "time_machine":
        assert group_tree.merges == [] and _node(env, t1)[0] == "review"   # no integration under the operation
        monkeypatch.setattr(operation, "_unfinished", lambda flow: [])
    else:
        assert _merged(group_tree, commit) and _node(env, t1)[0] == "done"
    resumed = env["client"].post(f"/api/v1/runs/{_run_id(env)}/resume", json={"mode": mode or "full"}, headers=_h())
    assert resumed.status_code == 200, resumed.text                     # the next command goes on
    assert _merged(group_tree, commit) and (_node(env, t1)[0], _node(env, t2)[0]) == ("done", "open")
    assert _propagations(env, t1) == ["done"] and [j["node_key"] for j in env["jobs"]] == [t2]
