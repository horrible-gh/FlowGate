"""flowgate.v02.0113 T#1 (0030-M §4, 0002-D 개정 §3.11 표 교체 · §3.13 신설) — a card that actually runs vs one that waits.

One judgement, ``card_settings.legacy_adapter.node_running``, is the card's *actual running*: an AI execution of the node
is requested or running, or an AI review loop works on its result (also a result taken as written and sent straight to
review). The card settings lock (``locked``, 409 ``node_settings_locked`` — its AI roles included), the title save lock
(409 ``node_title_locked``) and the run read's ``node_running`` all follow it. A node merely open — a person's writing
(test3.test.0004: ``POST runs`` 201, run idle, 0 executions, ``open`` / ``manual_write_wait``), an AI setup wait, an
unavailable AI, a question, an approval — or done, or in error, is not running and takes its settings and title, all
three roles ``not_used`` too; a running run alone locks nothing. The step / full commands keep refusing a second
command while the run runs (409 ``run_already_active``), apart from the card lock.

On the real SQLite DB and routes of ``test_fg_card_settings`` with the stage assignment harness of
``test_fg_ai_roles_l2_assignment`` (the AI start and the review loop admission are recorders: nothing calls an AI).
"""
from __future__ import annotations

import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    FA, X, _sql, command, db_path, decided, env, flow, ledger, line_doc, node_run, roles, save_instruction, start,
    starts, wired, write_line,
)
from test_fg_card_settings import _h, get_settings, setting
from test_fg_card_settings_review import _executions_ended, _result

REPORT = "## 결과\n\n사람이 쓴 레포트\n"


# ── helpers ──────────────────────────────────────────────────────────────────

def _current(env):
    res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["run"]


def _wire(run, key):
    return next(n for n in run["node_runs"] if n["node_key"] == key)


def put_title(env, key, title, expect):
    res = env["client"].put(f"/api/v1/flows/{FA}/graph/nodes/{key}/title", json={"title": title}, headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def _lock(env, key):
    """(the card lock itself, the settings read's ``locked``) — no run read, so nothing is reconciled or settled."""
    from flowgate.card_settings import legacy_adapter

    return legacy_adapter.node_running(FA, key), get_settings(env, FA, key)["locked"]


def _takes_changes(env, key, title):
    """A card that is not running takes a card setting, its AI roles and its title."""
    assert setting(env, FA, key, "restart", 1)["locked"] is False
    assert setting(env, FA, key, "execution_role", {"kind": "assigned", "provider_id": X})["locked"] is False
    assert setting(env, FA, key, "execution_role", {"kind": "not_used"})["locked"] is False
    assert put_title(env, key, title, 200)["title_state"]["title"] == title


def _refuses_changes(env, key):
    """A running card refuses its settings, its AI roles and its title (nothing is written)."""
    before = get_settings(env, FA, key)["settings_version"]
    assert setting(env, FA, key, "restart", 2, expect=409)["error"]["code"] == "node_settings_locked"
    assert setting(env, FA, key, "review_role", {"kind": "not_used"}, expect=409)["error"]["code"] == "node_settings_locked"
    assert put_title(env, key, "실행 중 저장", 409)["error"]["code"] == "node_title_locked"
    assert get_settings(env, FA, key)["settings_version"] == before


def _person_wait(env):
    """A new Flow (R -> 작업 card), its three roles ``not_used``, its documents empty, its run started: the card waits for
    a person to write its instruction (the test3.test.0004 shape)."""
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    return key


# ── the reported shape: a person's manual write wait (test3.test.0004) ───────

def test_a_manual_write_wait_like_test3_0004_is_not_running_and_takes_its_settings_roles_and_title(env, decided, starts):
    key = _person_wait(env)
    run = _current(env)
    wire = _wire(run, key)
    # POST runs 201 took the command; nothing runs: the run is idle with no execution, the node open and waiting
    assert (run["status"], run.get("running_execution_count", 0), run.get("requested_execution_count", 0)) == ("idle", 0, 0)
    assert (wire["status"], wire["wait_reason"]["code"], wire["wait_reason"]["role"]) == ("open", "manual_write_wait",
                                                                                          "instruction")
    assert ledger(env) == [] and starts == [] and env["jobs"] == []
    assert wire["node_running"] is False                                   # no 🔒 실행 중, no ◌
    assert _lock(env, key) == (False, False)
    _takes_changes(env, key, "지시서 작성 대기")
    assert _wire(_current(env), key)["node_running"] is False


@pytest.mark.parametrize("run_status", ["running", "paused", "idle"])
def test_a_running_run_alone_locks_no_waiting_card(env, decided, starts, run_status):
    key = _person_wait(env)
    _sql(env["db"], "UPDATE graph_runs SET status = ? WHERE group_id = ?", (run_status, FA))
    assert _lock(env, key) == (False, False)
    assert setting(env, FA, key, "restart", 1)["locked"] is False
    assert setting(env, FA, key, "review_role", {"kind": "assigned", "provider_id": X})["locked"] is False
    assert put_title(env, key, "실행 흐름만으로는 잠그지 않음", 200)["title_state"]["version"] >= 1


# ── actually running: an AI execution requested / running, an AI review ───────

@pytest.mark.parametrize("status", ["requested", "running"])
def test_a_card_whose_ai_execution_is_requested_or_running_is_running_and_locked(env, decided, starts, status):
    from flowgate.card_settings import legacy_adapter

    key = flow(env)
    roles(key, X, "not_used", "not_used")                                  # its instruction AI starts at once
    start(env)
    [row] = ledger(env)
    assert row["status"] == "requested"
    if status == "running":
        _sql(env["db"], "UPDATE graph_attempt_runs SET status = 'running' WHERE exec_id = ?", (row["exec_id"],))
    assert _lock(env, key) == (True, True)
    assert _wire(_current(env), key)["node_running"] is True
    _refuses_changes(env, key)
    _sql(env["db"], "UPDATE graph_attempt_runs SET status = 'requested' WHERE exec_id = ?", (row["exec_id"],))
    _executions_ended()                                                     # the execution is over: nothing works on it
    assert legacy_adapter.node_running(FA, key) is False
    assert get_settings(env, FA, key)["locked"] is False


@pytest.mark.parametrize("node_status", ["review", "open"])
def test_a_card_whose_ai_review_loop_works_on_its_result_is_running_until_the_loop_ends(
        env, decided, starts, monkeypatch, node_status):
    """``open`` with a result: the card took a document already written and sent it straight to its review."""
    from flowgate.card_settings import legacy_adapter

    key = _person_wait(env)
    result = _result(71, "TR")
    _sql(env["db"], "UPDATE graph_node_runs SET status = ?, result_doc_id = ? WHERE node_key = ?",
         (node_status, result, key))
    monkeypatch.setattr(legacy_adapter, "review_loop_active", lambda doc_id: doc_id == result)
    assert _lock(env, key) == (True, True)
    _refuses_changes(env, key)
    monkeypatch.setattr(legacy_adapter, "review_loop_active", lambda doc_id: False)
    assert _lock(env, key) == (False, False)
    _takes_changes(env, key, "검수 끝")


# ── waiting, done and error cards are never running ──────────────────────────

def _setup_wait(env, monkeypatch):
    key = flow(env)
    roles(key, init="pending_init")
    command(env, "step")
    assert _wire(_current(env), key)["wait_reason"]["code"] == "ai_setup_required"
    return key


def _unavailable(env, monkeypatch):
    key = flow(env)
    roles(key, X, X, "not_used")
    _sql(env["db"], "UPDATE ai_providers SET enabled = 0 WHERE provider_id = ?", (X,))
    start(env)
    assert _wire(_current(env), key)["wait_reason"]["code"] == "ai_unavailable"
    return key


def _question(env, monkeypatch):
    from modules.flow_gate.db import questions as db_questions

    key = _person_wait(env)
    instruction = line_doc(env, key, "instruction")
    _sql(env["db"], "UPDATE graph_node_runs SET instruction_doc_id = ? WHERE node_key = ?", (instruction, key))
    monkeypatch.setattr(db_questions, "list_open_doc_ids_by_group", lambda group_id: [instruction])
    assert _wire(_current(env), key)["question"] is True
    return key


def _awaiting_approval(env, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    key = _person_wait(env)
    _sql(env["db"], "UPDATE graph_node_runs SET status = 'review', result_doc_id = ? WHERE node_key = ?",
         (_result(72, "TR"), key))
    monkeypatch.setattr(legacy_adapter, "review_loop_active", lambda doc_id: False)
    return key


def _done(env, monkeypatch):
    key = _person_wait(env)
    save_instruction(env, key)
    write_line(env, key, "report", REPORT)
    assert node_run(env, key)["status"] == "done"
    return key


def _error(env, monkeypatch):
    key = _person_wait(env)
    _sql(env["db"], "UPDATE graph_node_runs SET dispatch_error_code = 'no_result' WHERE node_key = ?", (key,))
    return key


@pytest.mark.parametrize("make", [_setup_wait, _unavailable, _question, _awaiting_approval, _done, _error],
                         ids=["ai_setup_required", "ai_unavailable", "question", "awaiting_approval", "done", "error"])
def test_waiting_done_and_error_cards_are_not_running_and_take_their_settings(env, decided, starts, monkeypatch, make):
    key = make(env, monkeypatch)
    assert _lock(env, key) == (False, False)
    _takes_changes(env, key, "잠기지 않는 카드")


# ── the run commands keep their own duplicate refusal ────────────────────────

def test_a_second_command_while_the_run_runs_is_refused_apart_from_the_card_lock(env, decided, starts):
    key = flow(env)
    roles(key, X, "not_used", "not_used")
    start(env)
    assert _current(env)["status"] == "running"
    for mode in ("step", "full"):
        res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": None, "mode": mode}, headers=_h())
        assert res.status_code == 409, res.text
        assert res.json()["error"]["code"] == "run_already_active"
    assert len(ledger(env)) == 1                                           # nothing more was requested


def test_an_idle_run_s_waiting_card_lets_the_next_command_go_on(env, decided, starts):
    """POST runs 201 is the command taken: on the idle run of a waiting card the next command goes on (no 409)."""
    key = _person_wait(env)
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": None, "mode": "step"}, headers=_h())
    assert res.status_code in (200, 201), res.text
    assert _wire(res.json()["run"], key)["node_running"] is False
