"""(WP-3) — a person's approval on an idle run moves the run on to the next card, with no further
resume (the the earlier change the earlier implementation contract restored; the earlier implementation revised).

kept every propagation of a run that is not ``running`` waiting (``frontier.defers``: paused, idle) until the next
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
``no_targets``), so no command can be proven and only the revised ``defers`` moves it on. With the the earlier implementation ``defers`` restored, the
reported wait keeps the next card pending until a resume
(``test_with_the_0061_t2_rule_the_reported_wait_needed_a_resume``).

Rework (rej_01M5FKJYMETCGHGJ): a card whose attempt worked in a Git worktree is done only once its result is merged
into the Flow's branch (``result_merge.defer_done``). On the reported wait such an approval was held for the
merge before the run was woken, so the integration took the idle run's hold (``result_merge._wake``), applied the
approval's propagation as ``pending`` and left the run idle: the next card waited for a resume. Now the approval wakes
the run when it is held (``run_apply.apply_decision``), the merge runs on the running run and its end opens the next
card. These cases use a prepared worktree attempt (its ``input_commit`` and mode record as ``workspace.prepare`` writes
them) whose result is a real commit on its own branch, merged by the real ``result_merge._merge`` (``git merge
--no-ff``) into a real group repository; the group's Git lock, branch merge claim and worktree cleanup are stubbed.

(T-2): no person approves a card any more — the card approval route answers 409
``needs_intervention`` and the run waits ``needs_intervention`` (no longer ``waiting_approval``). The decision these cases
make on the idle run is now the card's automatic completion after its intervention — a re-review requested after a memo
that passed (``card_settings.legacy_adapter.approve``, the T-1 completion; local ``_approve``) — whose propagation the
idle run takes exactly as it took a person's approval (``frontier.defers``, ``command.wake_approval``). The test names
keep their 0083 wording. The ``reported`` wait is a run idle before T-2 too: its stored ``waiting_approval`` is read as
``needs_intervention`` (``settle.reason_as_read``), never converted.

On the card settings suites' real SQLite DB, routes and fixtures (``test_fg_card_settings``,
``test_fg_card_settings_review``, ``test_fg_card_approval_0056``, ``test_fg_wp_ai_run_0083``). A paused or stopped
run with a card in review cannot be reached through the routes alone (Stop / Pause need a running run, and a Stop takes
its open / review cards back to pending), so those two states are set on the run row, as the the earlier implementation suite does.
"""
from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    legacy_cards, passive_person_results, pre_feature_attempts,
    AI, FA, _h, _sql, db_path, decided, env,
)
from test_fg_card_settings_review import (
    _admin_and_reviewer, _executions_ended, _node, _result, _review_status, _run_ended,
)
from test_fg_card_approval_0056 import (  # noqa: F401 — card_outputs_port: registered as startup does
    LEE, _chain, _current, _human_result, _run_id, _v01_approve, card_outputs_port,
)
from test_fg_wp_ai_run_0083 import _cards, _register, _run_status, _start_ai_run
# (T): this suite's cards are cards made before the AI role feature (their legacy fields:
# assignee, run_ai, review_count / review_ai — L-1 §2.4.5); the stage assignment feature stays on, so each new attempt
# runs the assigned path (L-2) resolved from those fields. Pre-feature attempts are made explicitly
# (``pre_feature_attempts``) by the tests of the pre-feature contract.
# (rej_01M6DQW6YF0SCQ5D P1): the person cards here are passive vehicles (``passive_person_results``).
pytestmark = pytest.mark.usefixtures("legacy_cards", "passive_person_results")


# ── helpers ──────────────────────────────────────────────────────────────────

def _begin(env, cards, mode=None):
    """R -> the cards confirmed and its run started (``mode``: the start command, default full)."""
    keys = _chain(env, cards)
    body = {"revision_no": 1} if mode is None else {"revision_no": 1, "mode": mode}
    started = env["client"].post(f"/api/v1/flows/{FA}/runs", json=body, headers=_h())
    assert started.status_code == 201, started.text
    return keys


@pytest.fixture(autouse=True)
def no_document_gates(monkeypatch):
    """The automatic completion's read-only document gates (body / test gate) pass: the person's results here have no
    file."""
    from flowgate.card_settings import legacy_adapter

    monkeypatch.setattr(legacy_adapter, "completion_gates", lambda doc: None)


def _approve(env, key, body=None):
    """(T-2): the card's automatic completion of its result on the idle run (a re-review requested
    after a memo that passed), as the card stage makes it (T-1) — no person's card approval. -> ``{"run": …}`` read
    after it."""
    from flowgate.card_settings import legacy_adapter

    result = (body or {}).get("result_doc_id") or _node(env, key)[1]
    assert legacy_adapter.approve(FA, result, "u_kim", legacy_adapter.AUTOMATION, node_key=key) is True
    return {"run": _current(env)}


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
    assert _run_status(env) == ("idle", "needs_intervention")       # a person writes t1
    if wait == "reported":
        _reported(env)
    result = _human_result(env, keys[0])
    assert _run_status(env)[0] == "idle"
    return keys, result


WAITS = pytest.mark.parametrize("wait", ["command_chain", "reported"])


# ── a person's approval on an idle run moves on (revised) ───────────────────

# 0113 T#3 (T §6.4) pre-feature contract: a person card's result registered at its opening waits for a person's [승인] (0083 WP-3 / 0061 T#2 scenes); an assigned person RUN card writes its instruction first and its result is approved by the system (L-2 §2.7, T §6.4) — test_fg_ai_roles_l2_assignment.py::test_g2_*
@WAITS
def test_a_person_s_approval_on_an_idle_run_opens_the_next_ai_card_and_creates_its_job_revised_0061_t2(pre_feature_attempts, 
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
    # T-2: the automatic completion records its authority first (B-4), then the idle run is woken
    assert _run_events_after(env, seq) == [("idle", "idle", "auto_approval_authority", "system"),
                                           ("idle", "running", "work_resumed", "approval")]
    assert commands == []


# 0113 T#3 (T §6.4) pre-feature contract: a person card's result registered at its opening waits for a person's [승인] (0083 WP-3 / 0061 T#2 scenes); an assigned person RUN card writes its instruction first and its result is approved by the system (L-2 §2.7, T §6.4) — test_fg_ai_roles_l2_assignment.py::test_g2_*
@WAITS
def test_a_person_s_approval_on_an_idle_run_opens_the_next_person_s_card_and_the_run_waits_for_it_revised_0061_t2(pre_feature_attempts, 
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
    assert _run_status(env) == ("idle", "needs_intervention")             # woken, then idle again: nothing works
    assert _run_events_after(env, seq) == [("idle", "idle", "auto_approval_authority", "system"),     # T-2 (B-4)
                                           ("idle", "running", "work_resumed", "approval"),
                                           ("running", "idle", "run_idle", None)]
    assert commands == []


# 0113 T#3 (T §6.4) pre-feature contract: a person card's result registered at its opening waits for a person's [승인] (0083 WP-3 / 0061 T#2 scenes); an assigned person RUN card writes its instruction first and its result is approved by the system (L-2 §2.7, T §6.4) — test_fg_ai_roles_l2_assignment.py::test_g2_*
@WAITS
def test_a_person_s_v01_approval_on_an_idle_run_moves_on_as_the_card_approval_does_revised_0061_t2(pre_feature_attempts, 
        env, decided, monkeypatch, wait):
    commands = _no_command(monkeypatch)
    (t1, t2), result = _person_waits(env, [("T", LEE), ("T", AI)], wait=wait)
    _v01_approve(result)                                                # the v0.1 approval and its Graph contact
    assert (_node(env, t1), _node(env, t2)[0]) == (("done", result), "open")
    assert [j["node_key"] for j in env["jobs"]] == [t2] and _propagations(env, t1) == ["done"]
    assert commands == []


# 0113 T#3 (T §6.4) pre-feature contract: a person card's result registered at its opening waits for a person's [승인] (0083 WP-3 / 0061 T#2 scenes); an assigned person RUN card writes its instruction first and its result is approved by the system (L-2 §2.7, T §6.4) — test_fg_ai_roles_l2_assignment.py::test_g2_*
@WAITS
def test_the_last_card_s_approval_on_an_idle_run_completes_it_revised_0061_t2(pre_feature_attempts, env, decided, wait):
    (t1,), result = _person_waits(env, [("T", LEE)], wait=wait)
    _approve(env, t1, body={"result_doc_id": result})
    assert _node(env, t1)[0] == "done" and _propagations(env, t1) == ["done"]
    assert _run_status(env) == ("idle", "completed")


# 0113 T#3 (T §6.4) pre-feature contract: a person card's result registered at its opening waits for a person's [승인] (0083 WP-3 / 0061 T#2 scenes); an assigned person RUN card writes its instruction first and its result is approved by the system (L-2 §2.7, T §6.4) — test_fg_ai_roles_l2_assignment.py::test_g2_*


# ── kept: step, paused, stopped, Time Machine ────────────────────────────────

# 0113 T#3 (T §6.4) pre-feature contract: a person card's result registered at its opening waits for a person's [승인] (0083 WP-3 / 0061 T#2 scenes); an assigned person RUN card writes its instruction first and its result is approved by the system (L-2 §2.7, T §6.4) — test_fg_ai_roles_l2_assignment.py::test_g2_*


# 0113 T#3 (T §6.4) pre-feature contract: a person card's result registered at its opening waits for a person's [승인] (0083 WP-3 / 0061 T#2 scenes); an assigned person RUN card writes its instruction first and its result is approved by the system (L-2 §2.7, T §6.4) — test_fg_ai_roles_l2_assignment.py::test_g2_*






# ── regression: the ordinary flow approves by itself ─────────────────────────



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
    linked worktree of the group repository, recorded as the earlier implementation records it (``result_commit``, reused by
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
    (``token_consumed``: the result's authorship by the attempt, which the earlier implementation's ``commit_result`` checks)."""
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
    assert _run_status(env) == ("idle", "needs_intervention")
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


# 0113 T#3 (T §6.4) pre-feature contract: a person card's result registered at its opening waits for a person's [승인] (0083 WP-3 / 0061 T#2 scenes); an assigned person RUN card writes its instruction first and its result is approved by the system (L-2 §2.7, T §6.4) — test_fg_ai_roles_l2_assignment.py::test_g2_*


# 0113 T#3 (T §6.4) pre-feature contract: a person card's result registered at its opening waits for a person's [승인] (0083 WP-3 / 0061 T#2 scenes); an assigned person RUN card writes its instruction first and its result is approved by the system (L-2 §2.7, T §6.4) — test_fg_ai_roles_l2_assignment.py::test_g2_*


# 0113 T#3 (T §6.4) pre-feature contract: a person card's result registered at its opening waits for a person's [승인] (0083 WP-3 / 0061 T#2 scenes); an assigned person RUN card writes its instruction first and its result is approved by the system (L-2 §2.7, T §6.4) — test_fg_ai_roles_l2_assignment.py::test_g2_*
