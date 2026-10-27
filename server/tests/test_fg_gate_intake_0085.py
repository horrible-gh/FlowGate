"""flowgate.v02.0085 T#2 (D0007 §3.1~§3.3, §7.1, §7.2; §8-1, 2, 3 (reject part), 5, 14 (decision / GET), 16) — the
product gate rule ``human_confirm_v1`` and the gate decision intake.

On the real SQLite DB and routes of ``test_fg_card_settings`` with the product policies registered as ``startup``
does (``flowgate.graph_policies.integration``, isolated per test): a confirm card is confirmed and run with the real
rule, waits idle for its person, is confirmed or rejected through ``POST .../gate-decision`` (request key, expected
round / settings version, 200 / 202 / 403 / 409 / 422), its history is read through ``GET .../gate-decisions`` and the
run read tells each gate's decision summary. The Git work of an accepted confirm is a later card's: a confirm that
needs it is accepted (202, a ``gate_publish`` job that nothing runs here); the group's Git slot is a test double of
``legacy_adapter.git_slot`` where a test needs one.

A run whose stored snapshot is ``undecided`` still shows its gates (legacy history, ``can_decide`` false) and is still
refused; a ``limit`` that is no plain ASCII number in range is 422; and §8-5 is also checked with requests that really
race: each from its own thread, TestClient and SQLite connections, released together at a barrier just before the Flow
guard (``_race``).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from test_fg_card_settings import (  # noqa: E402,F401  (fixtures are used by name)
    FA, _doc, _h, _sql, db_path, edge, env, node, put_draft, setting)

LEE = {"kind": "human", "user_id": "u_lee", "provider_id": None}
SLOT = {"ok": True, "group_branch": "fg/flowgate.v02.0091", "group_head": "a" * 40, "work_base": "main",
        "work_base_sha": "b" * 40, "origin": True}


@pytest.fixture()
def product(env, monkeypatch):
    """The product policies, registered as startup does, in registries restored after the test; the history route
    mounted as ``routers/main.py`` does."""
    from flowgate.gate_decisions import routes
    from flowgate.graph_policies import integration
    from modules.flow_gate.graph_workflow.domain import policies, run_policies, run_scope_ports

    monkeypatch.setattr(policies, "_registered", dict(policies._registered))
    monkeypatch.setattr(policies, "_system_default", dict(policies._system_default))
    monkeypatch.setattr(run_policies, "_implementations", dict(run_policies._implementations))
    monkeypatch.setattr(run_scope_ports, "_handler", run_scope_ports._handler)
    integration.register()
    env["client"].app.include_router(routes.router)
    return env


@pytest.fixture()
def slot(monkeypatch):
    """The group's Git slot as the confirm's checks read it (a test double: no repository here)."""
    from flowgate.gate_decisions import legacy_adapter

    state = dict(SLOT)
    from modules.flow_gate.services.git import merge_target as mt
    from modules.flow_gate.services.git.credentials import GitServiceError
    def judge(project_id, group_id, branch, **kw):
        if branch == state["group_branch"]:
            raise GitServiceError(409, "merge_target_same_branch", "same branch")
        return {"target_branch": branch, "target_sha": state["work_base_sha"], "project_base_branch": "main",
                "work_base_ref": state["work_base"], "source_branch": state["group_branch"],
                "is_project_base": branch == "main", "unmerge_supported": branch == "main",
                "scope_floor_sha": None, "incoming_work_base_commits": None, "warnings": []}
    monkeypatch.setattr(mt, "judge_check_target", judge)
    monkeypatch.setattr(legacy_adapter, "git_slot", lambda project_id, group_id: dict(state))
    return state


@pytest.fixture()
def allowed(env, monkeypatch):
    """Group permission the test can withdraw (the harness grants every permission)."""
    from modules.flow_gate.graph_workflow.api import envelope

    state = {"allow": True}
    monkeypatch.setattr(envelope, "has_permission", lambda u, p, perm: state["allow"])
    return state


# ── helpers ──────────────────────────────────────────────────────────────────

def _confirmed(env, nodes, edges):
    body = put_draft(env, FA, 0, nodes, edges)
    res = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert res.status_code == 201, res.text
    return body["key_map"]


def _linear(env, after=("T", LEE)):
    """R -> G (gate) -> T (u_lee): (gate key, card key)."""
    keys = _confirmed(env, [node("tmp_r", "R"), node("tmp_g", None, "gate"), node("tmp_t", after[0], assignee=after[1])],
                      [edge("tmp_e1", "tmp_r", "tmp_g"), edge("tmp_e2", "tmp_g", "tmp_t")])
    return keys["tmp_g"], keys["tmp_t"]


def _start(env, mode=None):
    body = {"revision_no": None, **({"mode": mode} if mode else {})}
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json=body, headers=_h())
    assert res.status_code == 201, res.text
    return res.json()["run"]


def _decide(env, run_id, key, body, expect=200):
    res = env["client"].post(f"/api/v1/runs/{run_id}/nodes/{key}/gate-decision", json=body, headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def _current(env):
    res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["run"]


def _nodes(run):
    return {n["node_key"]: n for n in run["node_runs"]}


def _states(env):
    return {r["node_key"]: r["status"] for r in _sql(env["db"], "SELECT node_key, status FROM graph_node_runs")}


def _run_row(env):
    return _sql(env["db"], "SELECT run_id, status, idle_reason, last_event_seq FROM graph_runs WHERE group_id = ?",
                (FA,))[0]


def _set_run(env, status, idle_reason=None):
    _sql(env["db"], "UPDATE graph_runs SET status = ?, idle_reason = ? WHERE group_id = ?", (status, idle_reason, FA))


def _decisions(env):
    return _sql(env["db"], "SELECT * FROM gate_decisions ORDER BY accepted_at, decision_id")


def _jobs(env):
    return _sql(env["db"], "SELECT job_id, kind, status, request_key, phase_note, result FROM operation_job "
                           "WHERE kind = 'gate_publish' ORDER BY created_at")


def _gate_events(env, key):
    return _sql(env["db"], "SELECT event_seq, cause, actor_user_id, reason, payload_json FROM graph_run_events "
                           "WHERE node_key = ? AND cause IN ('gate_confirmed', 'gate_rejected') ORDER BY event_seq",
                (key,))


def _attempt(env, run_id, key):
    rows = _sql(env["db"], "SELECT attempt_id, status FROM graph_node_attempts WHERE run_id = ? AND node_key = ? "
                           "ORDER BY created_at", (run_id, key))
    return rows[-1] if rows else None


def _token(env, key):
    return _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ? ORDER BY rowid DESC",
                (key,))[0]["token_id"]


def _finish(env, key, seq, typ="T"):
    """The card's result registered with its token and approved (the v0.1 contacts, as the inbox and review do)."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.api import v01_contact

    result = _doc(FA, seq, typ)
    v01_contact.after_inbox_registered(FA, result, typ, _token(env, key))
    db_documents.update(result, {"doc_review_status": "approved"})
    v01_contact.after_review_decided(FA, result, "approve")
    return result


def _confirm_next(after: str, new_key: str) -> None:
    """Revision 2 = revision 1 plus a card after ``after`` (confirmed as P0008 S4 does)."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.services import confirm_service
    from modules.flow_gate.graph_workflow.store import definition_store

    record = definition_store.revision(FA, 1)
    fields = ("node_key", "node_type", "doc_type", "label", "assignee_kind", "assignee_user_id", "assignee_provider_id")
    nodes = [{k: n.get(k) for k in fields} for n in record["nodes"]]
    edges = [{"edge_id": e["edge_id"], "source": e["source"], "target": e["target"]} for e in record["edges"]]
    nodes.append({"node_key": new_key, "node_type": "document", "doc_type": "T", "label": "T", "assignee_kind": "human",
                  "assignee_user_id": "u_lee", "assignee_provider_id": None})
    edges.append({"edge_id": "ge_extra", "source": after, "target": new_key})
    with get_store().transaction():
        definition_store.create_draft(FA, 1, "u_kim")
        definition_store.replace_draft_content(FA, nodes, edges)
    version = definition_store.draft(FA)["draft_version"]
    confirm_service.confirm_revision({"group_id": FA, "project_id": "flowgate"}, "u_kim", version, 1, None)
    assert definition_store.latest_revision_no(FA) == 2


def _nothing_written(env, before):
    assert (len(_decisions(env)), len(_jobs(env)), _run_row(env)["last_event_seq"]) == before


def _marks(env):
    return len(_decisions(env)), len(_jobs(env)), _run_row(env)["last_event_seq"]


# ── §8-1: the real rule, Start -> gate -> what follows / completed ───────────

def test_the_product_rule_is_registered_for_the_validator_and_the_run(product):
    from flowgate.graph_policies import gate
    from modules.flow_gate.graph_workflow.domain import policies, run_policies

    entries = policies.system_default_set()["entries"]
    assert entries["gate_node"] == {"value": "human_confirm_v1", "impl_version": "1"}
    assert run_policies.implementation("gate_node", "human_confirm_v1", "1") is gate.HUMAN_CONFIRM_V1
    assert policies.current()["gate_node"] is gate.HUMAN_CONFIRM_V1                 # validator: no rule_undecided
    assert gate.HUMAN_CONFIRM_V1.judge("g", "confirm", None) == "confirmed"
    assert gate.HUMAN_CONFIRM_V1.judge("g", "reject", "x") == "rejected"
    with pytest.raises(ValueError):
        gate.HUMAN_CONFIRM_V1.judge("g", "maybe", None)


def test_start_waits_idle_at_the_gate_and_its_confirm_opens_what_follows_up_to_completed(product):
    g, t = _linear(product)
    run = _start(product)
    run_id = run["run_id"]
    entries = _sql(product["db"], "SELECT policy_value, impl_version FROM graph_run_policy_entries "
                                  "WHERE run_id = ? AND policy_key = 'gate_node'", (run_id,))
    assert entries == [{"policy_value": "human_confirm_v1", "impl_version": "1"}]
    current = _current(product)
    assert (current["status"], current["idle_reason"]) == ("idle", "waiting_approval")
    nodes = _nodes(current)
    assert (nodes[g]["status"], nodes[t]["status"]) == ("open", "pending")
    wire = nodes[g]["gate"]
    assert (wire["can_decide"], wire["decision"], wire["decision_in_progress"], wire["settings_locked"],
            wire["archived"]) == (True, None, False, False, False)
    assert "gate" not in nodes[t]
    # A card approval never stands in for the person's confirm: the gate is no card in review.
    approval = product["client"].post(f"/api/v1/runs/{run_id}/nodes/{g}/approval", json={}, headers=_h())
    assert approval.status_code == 409, approval.text
    assert _states(product)[g] == "open" and _states(product)[t] == "pending"

    attempt = _attempt(product, run_id, g)["attempt_id"]
    body = _decide(product, run_id, g, {"decision": "confirm", "comment": "OK", "request_id": "req-1",
                                        "expected_attempt_id": attempt, "expected_settings_version": 0})
    nodes = _nodes(body["run"])
    assert nodes[g]["status"] == "done" and nodes[t]["status"] == "open"      # the waiting command went on
    assert body["decision"]["status"] == "succeeded" and body["decision"]["result"] == "confirmed"
    assert body["job"] is None and nodes[g]["gate"]["decision"]["decision_id"] == body["decision"]["decision_id"]
    assert nodes[g]["gate"]["can_decide"] is False and nodes[g]["gate"]["settings_locked"] is True
    stored = _decisions(product)
    assert len(stored) == 1 and (stored[0]["status"], stored[0]["actor_user_id"], stored[0]["attempt_id"],
                                 stored[0]["policy_version"], stored[0]["job_id"]) == (
        "succeeded", "u_kim", attempt, "human_confirm_v1@1", None)
    assert _attempt(product, run_id, g)["status"] == "ended" and _jobs(product) == []
    events = _gate_events(product, g)
    assert [(e["cause"], e["actor_user_id"], e["reason"]) for e in events] == [("gate_confirmed", "u_kim", "OK")]
    assert json.loads(events[0]["payload_json"])["gate_decision_id"] == stored[0]["decision_id"]
    _finish(product, t, 90)
    final = _current(product)
    assert _nodes(final)[t]["status"] == "done"
    assert (final["status"], final["idle_reason"]) == ("idle", "completed")


def test_a_step_command_stops_at_its_targets_after_the_confirm(product):
    g, t = _linear(product)
    run = _start(product, mode="step")
    assert _states(product)[g] == "open"
    body = _decide(product, run["run_id"], g, {"decision": "confirm", "request_id": "step-1"})
    assert _nodes(body["run"])[g]["status"] == "done"
    assert _states(product)[t] == "pending"                                  # outside the step's targets
    assert g in _current(product)["pending_node_keys"]
    nxt = product["client"].post(f"/api/v1/runs/{run['run_id']}/resume", json={"mode": "step"}, headers=_h())
    assert nxt.status_code == 200, nxt.text
    assert _states(product)[t] == "open"


def test_a_snapshot_whose_gate_rule_is_undecided_is_still_refused(product):
    """Defence: an injected ``undecided`` gate rule in a stored snapshot is refused as before."""
    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    _sql(product["db"], "UPDATE graph_run_policy_entries SET policy_value = 'undecided', impl_version = 'undecided.1' "
                        "WHERE policy_key = 'gate_node'")
    before = _marks(product)
    refused = _decide(product, run_id, g, {"decision": "confirm", "request_id": "u-1"}, 409)
    assert refused["error"]["code"] == "run_policy_undecided"
    assert refused["error"]["params"]["undecided_policies"] == [{"policy": "gate_node", "rule_ref": "Q-11"}]
    _nothing_written(product, before)
    # Registering the rules again never rewrites a stored snapshot.
    from flowgate.graph_policies import integration
    integration.register()
    assert _sql(product["db"], "SELECT policy_value FROM graph_run_policy_entries WHERE policy_key = 'gate_node'") == [
        {"policy_value": "undecided"}]


# ── §8-2: which run states take a new decision ───────────────────────────────

@pytest.mark.parametrize("status", ["running", "idle"])
def test_a_running_or_idle_run_takes_the_decision(product, status):
    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    _set_run(product, status, None if status == "running" else "waiting_approval")
    body = _decide(product, run_id, g, {"decision": "reject", "request_id": f"rs-{status}"})
    assert _nodes(body["run"])[g]["status"] == "rejected"


@pytest.mark.parametrize("status", ["paused", "stopping"])
def test_a_paused_or_stopping_run_takes_no_decision_and_nothing_changes(product, status):
    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    _set_run(product, status)
    before = _marks(product)
    for body in ({"decision": "confirm", "request_id": "p-1"}, {"decision": "reject"}):
        refused = _decide(product, run_id, g, body, 409)
        assert refused["error"]["code"] == "run_not_running"
        assert refused["error"]["params"] == {"run_id": run_id, "status": status}
    _nothing_written(product, before)
    assert _states(product)[g] == "open"


def test_a_stopped_runs_pending_gate_waits_for_the_next_command(product):
    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    _set_run(product, "running")
    stopped = product["client"].post(f"/api/v1/runs/{run_id}/stop", json={}, headers=_h())
    assert stopped.status_code == 200, stopped.text
    assert (_run_row(product)["status"], _run_row(product)["idle_reason"]) == ("idle", "stopped")
    assert _states(product)[g] == "pending"
    before = _marks(product)
    refused = _decide(product, run_id, g, {"decision": "confirm", "request_id": "s-1"}, 409)
    assert refused["error"]["code"] == "node_not_open" and refused["error"]["params"]["status"] == "pending"
    _nothing_written(product, before)
    assert _nodes(_current(product))[g]["gate"]["can_decide"] is False


def test_a_stopped_run_with_its_gate_left_open_holds_the_propagation(product):
    """The existing defence: the decision is taken, what follows and the completion wait for the next command."""
    g, t = _linear(product)
    run_id = _start(product)["run_id"]
    _set_run(product, "idle", "stopped")
    body = _decide(product, run_id, g, {"decision": "confirm", "request_id": "o-1"})
    assert _nodes(body["run"])[g]["status"] == "done" and _states(product)[t] == "pending"
    assert (_run_row(product)["status"], _run_row(product)["idle_reason"]) == ("idle", "stopped")
    assert _sql(product["db"], "SELECT status FROM graph_propagations WHERE node_key = ?", (g,)) == [
        {"status": "pending"}]
    resumed = product["client"].post(f"/api/v1/runs/{run_id}/resume", json={"mode": "full"}, headers=_h())
    assert resumed.status_code == 200, resumed.text
    assert _states(product)[t] == "open"


def test_an_accepted_confirm_keeps_the_run_working_then_pause_and_stop_follow_d7_3(product, slot):
    g, t = _linear(product)
    run_id = _start(product)["run_id"]
    assert setting(product, FA, g, "merge_mode", "commit")["settings_version"] == 1
    body = _decide(product, run_id, g, {"decision": "confirm", "request_id": "a-1", "expected_settings_version": 1},
                   202)
    decision, job = body["decision"], body["job"]
    assert decision["status"] == "accepted" and decision["result"] == "in_progress"
    assert (decision["target_branch"], decision["target_sha"], decision["target_remote"]) == (
        SLOT["group_branch"], SLOT["group_head"], None)
    assert job["kind"] == "gate_publish" and job["status"] == "pending"
    jobs = _jobs(product)
    assert [(j["job_id"], j["request_key"]) for j in jobs] == [(job["job_id"], "gpb:" + decision["decision_id"])]
    assert _decisions(product)[0]["job_id"] == job["job_id"]
    wire = _nodes(body["run"])[g]["gate"]
    assert (wire["decision_in_progress"], wire["can_decide"], wire["settings_locked"], wire["settings_lock_reason"],
            wire["job"]["job_id"]) == (True, False, True, "decision_reserved", job["job_id"])
    # The gate stays open and held: nothing follows, the run works on (never completed, never a person's wait).
    assert _states(product)[g] == "open" and _states(product)[t] == "pending" and _gate_events(product, g) == []
    current = _current(product)
    assert current["status"] == "running" and current["idle_reason"] is None
    assert setting(product, FA, g, "archive", True, expect=409)["error"]["code"] == "gate_settings_locked"
    # Pause after the acceptance: the intent stays, no new decision on a paused run.
    paused = product["client"].post(f"/api/v1/runs/{run_id}/pause", json={}, headers=_h())
    assert paused.status_code == 200, paused.text
    assert _decide(product, run_id, g, {"decision": "reject", "request_id": "a-2"}, 409)["error"]["code"] == \
        "run_not_running"
    assert _decisions(product)[0]["status"] == "accepted"
    # Stop: the job was never claimed (no effect outside the DB) - the intent is canceled, the gate back to pending.
    stopped = product["client"].post(f"/api/v1/runs/{run_id}/stop", json={}, headers=_h())
    assert stopped.status_code == 200, stopped.text
    assert (_run_row(product)["status"], _run_row(product)["idle_reason"]) == ("idle", "stopped")
    assert _states(product)[g] == "pending"
    assert _decisions(product)[0]["status"] == "canceled" and _decisions(product)[0]["valid_attempt_key"] is None
    assert [(j["status"], j["phase_note"]) for j in _jobs(product)] == [("cancelled", "no_effect_canceled")]
    stop_event = _sql(product["db"], "SELECT payload_json FROM graph_run_events WHERE node_key = ? AND cause = 'user_stop'",
                      (g,))
    assert json.loads(stop_event[0]["payload_json"])["gate_decision_id"] == decision["decision_id"]


def test_an_idle_run_whose_command_did_not_wait_keeps_idle_with_the_publish_reason(product, slot):
    from flowgate.graph_run import settle
    from modules.flow_gate.graph_workflow.services import run_apply

    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    _set_run(product, "idle", "no_targets")                       # no command waits for this gate
    setting(product, FA, g, "archive", True)
    _decide(product, run_id, g, {"decision": "confirm", "request_id": "i-1"}, 202)
    row = _run_row(product)
    assert (row["status"], row["idle_reason"]) == ("idle", "gate_publish")
    from modules.flow_gate.graph_workflow.store import run_store
    ctx = run_apply.load_context(run_store.run_by_id(run_id), "flowgate", None)
    assert settle.idle_reason(ctx, run_store.node_runs(run_id), run_apply.read_run_state(ctx)) == "gate_publish"


# ── §8-3 (reject): the round ends, history stays, other branches go on, no adoption ──

def test_a_reject_ends_the_round_keeps_other_branches_and_blocks_a_revision_adoption(product):
    keys = _confirmed(product, [node("tmp_r", "R"), node("tmp_g", None, "gate"), node("tmp_a", "T", assignee=LEE),
                                node("tmp_b", "T", assignee=LEE)],
                      [edge("tmp_e1", "tmp_r", "tmp_g"), edge("tmp_e2", "tmp_g", "tmp_a"),
                       edge("tmp_e3", "tmp_r", "tmp_b")])
    g, a, b = keys["tmp_g"], keys["tmp_a"], keys["tmp_b"]
    run_id = _start(product)["run_id"]
    assert _states(product)[g] == "open" and _states(product)[b] == "open"
    body = _decide(product, run_id, g, {"decision": "reject", "comment": "rework", "request_id": "rj-1"})
    assert body["decision"]["status"] == "rejected" and body["job"] is None
    assert _states(product)[g] == "rejected" and _states(product)[a] == "pending"
    assert _attempt(product, run_id, g)["status"] == "ended"
    assert _jobs(product) == []
    events = _gate_events(product, g)
    assert [(e["cause"], e["reason"]) for e in events] == [("gate_rejected", "rework")]
    assert json.loads(events[0]["payload_json"])["gate_decision_id"] == body["decision"]["decision_id"]
    again = _decide(product, run_id, g, {"decision": "confirm", "request_id": "rj-2"}, 409)
    assert again["error"]["code"] == "node_not_open" and again["error"]["params"]["status"] == "rejected"
    # The unrelated branch goes on and the run ends blocked by the rejection.
    _finish(product, b, 91)
    final = _current(product)
    assert _nodes(final)[b]["status"] == "done" and _nodes(final)[a]["status"] == "pending"
    assert (final["status"], final["idle_reason"]) == ("idle", "blocked_rejected")
    # A newer revision is not adopted while the rejected gate is unresolved (its definition stays protected too).
    _confirm_next(b, "gn_extra")
    refused = product["client"].post(f"/api/v1/runs/{run_id}/resume", json={"mode": "full"}, headers=_h())
    assert refused.status_code == 409, refused.text
    err = refused.json()["error"]
    assert (err["code"], err["params"]["reason"], err["params"]["node_keys"]) == (
        "graph_revision_migration_blocked", "gate_rejected", [g])
    assert _run_row(product)["status"] == "idle"
    page = product["client"].get(f"/api/v1/runs/{run_id}/nodes/{g}/gate-decisions", headers=_h()).json()
    assert [i["status"] for i in page["items"]] == ["rejected"] and page["latest"]["comment"] == "rework"


# ── §8-5: duplicates, stale screens, two gates, a lost answer ────────────────

def test_the_same_request_is_answered_from_its_stored_result_and_other_content_conflicts(product):
    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    first = _decide(product, run_id, g, {"decision": "confirm", "comment": "c", "request_id": "dup-1"})
    again = _decide(product, run_id, g, {"decision": "confirm", "comment": "c", "request_id": "dup-1"})
    assert again["decision"]["decision_id"] == first["decision"]["decision_id"]
    assert len(_decisions(product)) == 1 and len(_gate_events(product, g)) == 1
    conflict = _decide(product, run_id, g, {"decision": "reject", "request_id": "dup-1"}, 409)
    assert conflict["error"]["code"] == "gate_request_conflict"
    assert conflict["error"]["params"]["decision_id"] == first["decision"]["decision_id"]
    later = _decide(product, run_id, g, {"decision": "confirm", "request_id": "dup-2"}, 409)
    assert later["error"]["code"] == "node_not_open"                       # a new key after the success
    assert len(_decisions(product)) == 1


def test_a_stale_screen_is_refused_before_anything_is_written(product):
    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    setting(product, FA, g, "archive", None)
    setting(product, FA, g, "merge_mode", "hold")                           # version 1
    attempt = _attempt(product, run_id, g)["attempt_id"]
    before = _marks(product)
    stale = _decide(product, run_id, g, {"decision": "confirm", "request_id": "st-1", "expected_settings_version": 0},
                    409)
    assert stale["error"]["code"] == "gate_context_changed" and stale["error"]["params"]["reason"] == "settings_version"
    other = _decide(product, run_id, g, {"decision": "reject", "request_id": "st-2", "expected_attempt_id": "att_old"},
                    409)
    assert (other["error"]["code"], other["error"]["params"]["reason"], other["error"]["params"]["attempt_id"]) == (
        "gate_context_changed", "attempt", attempt)
    _nothing_written(product, before)
    for body, reason in (({"decision": "confirm", "request_id": 7}, "not_a_string"),
                         ({"decision": "confirm", "request_id": " x"}, "invalid_format"),
                         ({"decision": "confirm", "request_id": "x" * 129}, "too_long"),
                         ({"decision": "confirm", "expected_settings_version": -1}, "not_a_version"),
                         ({"decision": "confirm", "expected_settings_version": True}, "not_a_version")):
        bad = _decide(product, run_id, g, body, 422)
        assert bad["error"]["params"]["errors"][0]["reason"] == reason, body
    _nothing_written(product, before)


def test_a_held_gate_takes_no_other_decision_and_a_lost_answer_is_answered_again(product, slot):
    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    setting(product, FA, g, "merge_mode", "commit")
    first = _decide(product, run_id, g, {"decision": "confirm", "request_id": "h-1"}, 202)
    for body in ({"decision": "confirm", "request_id": "h-2"}, {"decision": "reject", "request_id": "h-3"}):
        busy = _decide(product, run_id, g, body, 409)
        assert busy["error"]["code"] == "gate_decision_in_progress"
        assert busy["error"]["params"]["decision_id"] == first["decision"]["decision_id"]
    resent = _decide(product, run_id, g, {"decision": "confirm", "request_id": "h-1"}, 202)   # the answer was lost
    assert resent["decision"]["decision_id"] == first["decision"]["decision_id"]
    assert resent["job"]["job_id"] == first["job"]["job_id"]
    assert len(_decisions(product)) == 1 and len(_jobs(product)) == 1 and _gate_events(product, g) == []


def test_two_gates_are_accepted_in_order_with_one_job_each(product, slot):
    keys = _confirmed(product, [node("tmp_r", "R"), node("tmp_g1", None, "gate"), node("tmp_g2", None, "gate")],
                      [edge("tmp_e1", "tmp_r", "tmp_g1"), edge("tmp_e2", "tmp_r", "tmp_g2")])
    g1, g2 = keys["tmp_g1"], keys["tmp_g2"]
    run_id = _start(product)["run_id"]
    for key in (g1, g2):
        setting(product, FA, key, "merge_mode", "commit")
    one = _decide(product, run_id, g1, {"decision": "confirm", "request_id": "same-key"}, 202)
    two = _decide(product, run_id, g2, {"decision": "confirm", "request_id": "same-key"}, 202)   # another gate's key
    assert one["decision"]["decision_id"] != two["decision"]["decision_id"]
    assert [j["request_key"] for j in _jobs(product)] == ["gpb:" + one["decision"]["decision_id"],
                                                          "gpb:" + two["decision"]["decision_id"]]
    assert [d["node_key"] for d in _decisions(product)] == [g1, g2]


def test_a_new_round_is_not_touched_by_an_old_requests_resend(product, slot):
    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    setting(product, FA, g, "merge_mode", "commit")
    old = _decide(product, run_id, g, {"decision": "confirm", "request_id": "round-1"}, 202)
    _set_run(product, "running")
    assert product["client"].post(f"/api/v1/runs/{run_id}/stop", json={}, headers=_h()).status_code == 200
    resumed = product["client"].post(f"/api/v1/runs/{run_id}/resume", json={"mode": "full"}, headers=_h())
    assert resumed.status_code == 200, resumed.text
    assert _states(product)[g] == "open"
    new_attempt = _attempt(product, run_id, g)["attempt_id"]
    assert new_attempt != old["decision"]["attempt_id"]
    resent = _decide(product, run_id, g, {"decision": "confirm", "request_id": "round-1"})
    assert resent["decision"]["status"] == "canceled" and resent["decision"]["attempt_id"] == old["decision"]["attempt_id"]
    setting(product, FA, g, "merge_mode", "hold")
    fresh = _decide(product, run_id, g, {"decision": "confirm", "request_id": "round-2",
                                         "expected_attempt_id": new_attempt})
    assert fresh["decision"]["status"] == "succeeded" and fresh["decision"]["attempt_id"] == new_attempt
    assert _states(product)[g] == "done"


# ── D0007 §7.1: the confirm's checks before anything is accepted ─────────────

def test_the_confirm_checks_refuse_before_anything_is_accepted(product, monkeypatch, slot):
    from flowgate.gate_decisions import legacy_adapter

    monkeypatch.setattr(legacy_adapter, "git_slot", lambda p, f: {"ok": False, "reason": "git_disabled"})

    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    before = _marks(product)
    setting(product, FA, g, "merge_mode", "commit")
    before = _marks(product)
    nogit = _decide(product, run_id, g, {"decision": "confirm", "request_id": "pf-1"}, 409)  # an unavailable slot
    assert (nogit["error"]["code"], nogit["error"]["params"]["reason"]) == ("gate_git_unavailable", "git_disabled")
    # flowgate.v02.0098 T#1 (D0004 §3.5): push true now saves the origin remote with it; an old client's single
    # remote write still leaves push on with no remote chosen, which the confirm keeps refusing (fail closed)
    setting(product, FA, g, "push", True)
    setting(product, FA, g, "merge_target", None)                          # push with no remote chosen
    pushed = _decide(product, run_id, g, {"decision": "confirm", "request_id": "pf-2"}, 409)
    assert (pushed["error"]["code"], pushed["error"]["params"]["reason"]) == ("gate_settings_invalid",
                                                                             "push_requires_origin")
    slot = dict(SLOT, origin=False)
    monkeypatch.setattr(legacy_adapter, "git_slot", lambda p, f: dict(slot))
    setting(product, FA, g, "merge_target", "origin")
    no_origin = _decide(product, run_id, g, {"decision": "confirm", "request_id": "pf-3"}, 409)
    assert no_origin["error"]["params"]["reason"] == "origin_not_configured"
    setting(product, FA, g, "push", None)
    setting(product, FA, g, "merge_mode", "merge")
    slot.update(work_base=SLOT["group_branch"])
    own = _decide(product, run_id, g, {"decision": "confirm", "request_id": "pf-4"}, 409)
    assert own["error"]["params"]["reason"] == "merge_target_same_branch"
    assert (len(_decisions(product)), len(_jobs(product))) == before[:2] and _states(product)[g] == "open"
    slot.update(work_base="main")
    merged = _decide(product, run_id, g, {"decision": "confirm", "request_id": "pf-5"}, 202)
    assert (merged["decision"]["target_branch"], merged["decision"]["target_sha"],
            merged["decision"]["target_remote"]) == ("main", SLOT["work_base_sha"], "origin")
    stored = _decisions(product)[0]
    snapshot = json.loads(stored["settings_json"])
    assert snapshot["effective"] == {"merge_mode": "merge", "git_apply": True, "archive": False,
                                     "merge_target": "origin", "merge_target_branch": None, "push": False, "keep_branch": False}


def test_hold_without_archive_needs_no_git_and_ignores_the_stored_git_options(product):
    g, t = _linear(product)
    run_id = _start(product)["run_id"]
    for name, value in (("merge_target", "origin"), ("push", True), ("keep_branch", True), ("merge_mode", "hold")):
        setting(product, FA, g, name, value)
    body = _decide(product, run_id, g, {"decision": "confirm", "request_id": "hold-1"})   # no Git slot at all
    assert body["decision"]["status"] == "succeeded" and _states(product)[t] == "open"
    eff = json.loads(_decisions(product)[0]["settings_json"])["effective"]
    assert (eff["push"], eff["keep_branch"], eff["merge_target"]) == (False, False, None)


# ── §8-14: permission on the decision and the history, on every call ─────────

def test_the_decision_and_its_history_need_the_permission_on_every_call(product, allowed):
    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    allowed["allow"] = False
    before = _marks(product)
    denied = _decide(product, run_id, g, {"decision": "confirm", "request_id": "perm-1"}, 403)
    assert denied["error"]["code"] == "flow_access_denied"
    _nothing_written(product, before)
    allowed["allow"] = True
    done = _decide(product, run_id, g, {"decision": "reject", "comment": "secret", "request_id": "perm-2"})
    allowed["allow"] = False
    resent = _decide(product, run_id, g, {"decision": "reject", "comment": "secret", "request_id": "perm-2"}, 403)
    assert "decision" not in resent and "secret" not in json.dumps(resent)
    hidden = product["client"].get(f"/api/v1/runs/{run_id}/nodes/{g}/gate-decisions", headers=_h())
    assert hidden.status_code == 403 and "secret" not in hidden.text
    allowed["allow"] = True
    shown = product["client"].get(f"/api/v1/runs/{run_id}/nodes/{g}/gate-decisions", headers=_h())
    assert shown.status_code == 200 and shown.json()["latest"]["decision_id"] == done["decision"]["decision_id"]


def test_the_history_pages_with_a_stable_cursor(product, slot):
    g, t = _linear(product)
    run_id = _start(product)["run_id"]
    setting(product, FA, g, "merge_mode", "commit")
    accepted = _decide(product, run_id, g, {"decision": "confirm", "request_id": "hp-1"}, 202)
    _set_run(product, "running")
    assert product["client"].post(f"/api/v1/runs/{run_id}/stop", json={}, headers=_h()).status_code == 200
    assert product["client"].post(f"/api/v1/runs/{run_id}/resume", json={"mode": "full"}, headers=_h()).status_code == 200
    rejected = _decide(product, run_id, g, {"decision": "reject", "request_id": "hp-2"})
    url = f"/api/v1/runs/{run_id}/nodes/{g}/gate-decisions"
    first = product["client"].get(url + "?limit=1", headers=_h()).json()
    assert [i["decision_id"] for i in first["items"]] == [accepted["decision"]["decision_id"]]
    assert first["latest"]["decision_id"] == rejected["decision"]["decision_id"]
    second = product["client"].get(url, params={"limit": 1, "cursor": first["next_cursor"]}, headers=_h()).json()
    assert [i["decision_id"] for i in second["items"]] == [rejected["decision"]["decision_id"]]
    assert second["next_cursor"] is None
    assert [i["status"] for i in first["items"] + second["items"]] == ["canceled", "rejected"]
    for query, field in (("?limit=0", "limit"), ("?limit=x", "limit"), ("?cursor=nope", "cursor")):
        bad = product["client"].get(url + query, headers=_h())
        assert bad.status_code == 422 and bad.json()["error"]["params"]["errors"][0]["field"] == field
    other = product["client"].get(f"/api/v1/runs/{run_id}/nodes/{t}/gate-decisions", headers=_h())
    assert other.status_code == 404 and other.json()["error"]["code"] == "gate_node_not_found"
    missing = product["client"].get(f"/api/v1/runs/run_nope/nodes/{g}/gate-decisions", headers=_h())
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "run_not_found"


# ── §8-16: an old client's request (no request key, no expected values) ──────

def test_an_old_clients_request_keeps_its_meaning_and_is_refused_on_a_held_gate(product, slot):
    g, t = _linear(product)
    run_id = _start(product)["run_id"]
    setting(product, FA, g, "archive", True)
    accepted = _decide(product, run_id, g, {"decision": "confirm"}, 202)                # old shape, work needed
    assert accepted["decision"]["request_id"].startswith("legacy-")
    before = _marks(product)
    for body in ({"decision": "confirm"}, {"decision": "reject", "comment": "no"}):
        busy = _decide(product, run_id, g, body, 409)
        assert busy["error"]["code"] == "gate_decision_in_progress"
    _nothing_written(product, before)
    assert _gate_events(product, g) == []


def test_an_old_clients_plain_confirm_is_200_and_its_repeat_is_409(product):
    g, t = _linear(product)
    run_id = _start(product)["run_id"]
    body = _decide(product, run_id, g, {"decision": "confirm", "comment": "ok"})
    assert _nodes(body["run"])[g]["status"] == "done" and _states(product)[t] == "open"
    again = _decide(product, run_id, g, {"decision": "confirm", "comment": "ok"}, 409)
    assert again["error"]["code"] == "node_not_open" and again["error"]["params"]["status"] == "done"
    assert len(_decisions(product)) == 1 and len(_gate_events(product, g)) == 1


def test_the_history_route_is_mounted_and_the_gate_rule_reaches_the_intake():
    from flowgate.gate_decisions import intake, routes
    from flowgate.graph_policies import gate

    main = (_SERVER_DIR / "routers" / "main.py").read_text(encoding="utf-8")
    assert "from flowgate.gate_decisions.routes import router as _gate_decisions_router" in main
    assert 'app.include_router(_gate_decisions_router, prefix=f"{CONTEXT}"' in main
    assert any(getattr(r, "path", "") == "/api/v1/runs/{run_id}/nodes/{node_key}/gate-decisions"
               for r in routes.router.routes)
    assert gate.HUMAN_CONFIRM_V1.intake is intake


def test_a_time_machine_or_a_conflict_session_holds_the_decision_back(product, monkeypatch):
    from flowgate.gate_decisions import intake
    from flowgate.graph_time_machine import operation

    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    before = _marks(product)
    monkeypatch.setattr(operation, "_unfinished", lambda flow_id: [{"operation_id": "gtm_1"}])
    tm = _decide(product, run_id, g, {"decision": "confirm", "request_id": "b-1"}, 409)
    assert tm["error"]["code"] == "graph_time_machine_in_progress"
    assert _nodes(_current(product))[g]["gate"]["blocked_reason"] == "time_machine"
    monkeypatch.setattr(operation, "_unfinished", lambda flow_id: [])
    monkeypatch.setattr(intake, "_merge_sessions", lambda flow_id: [7])
    conflict = _decide(product, run_id, g, {"decision": "reject", "request_id": "b-2"}, 409)
    assert (conflict["error"]["code"], conflict["error"]["params"]["reason"], conflict["error"]["params"]["merge_ids"]) \
        == ("gate_decision_blocked", "merge_conflict", [7])
    wire = _nodes(_current(product))[g]["gate"]
    assert (wire["can_decide"], wire["blocked_reason"]) == (False, "merge_conflict")
    _nothing_written(product, before)


# ── D0007 §3.1 / §7.2: a stored ``undecided`` snapshot is read, never decided ──

def _legacy_event(env, run_id, key, cause, actor, reason):
    """An old ``gate_rejected`` / ``gate_confirmed`` node event as runs wrote it before migration 158 (no decision link)."""
    last = _sql(env["db"], "SELECT event_seq, created_at FROM graph_run_events WHERE run_id = ? "
                           "ORDER BY event_seq DESC LIMIT 1", (run_id,))[0]
    seq = int(last["event_seq"]) + 1
    _sql(env["db"], "INSERT INTO graph_run_events (run_id, event_seq, event_type, node_key, from_status, to_status, "
                    "cause, actor_user_id, reason, payload_json, created_at) "
                    "VALUES (?, ?, 'graph_node_state_changed', ?, 'open', ?, ?, ?, ?, '{}', ?)",
         (run_id, seq, key, "done" if cause == "gate_confirmed" else "rejected", cause, actor, reason,
          last["created_at"]))
    _sql(env["db"], "UPDATE graph_runs SET last_event_seq = ? WHERE run_id = ?", (seq, run_id))
    return seq


def test_an_undecided_snapshot_shows_its_gate_and_its_legacy_history_but_takes_no_decision(product):
    g, t = _linear(product)
    run_id = _start(product)["run_id"]
    _sql(product["db"], "UPDATE graph_run_policy_entries SET policy_value = 'undecided', impl_version = 'undecided.1' "
                        "WHERE policy_key = 'gate_node'")
    seq = _legacy_event(product, run_id, g, "gate_rejected", "u_lee", "old reason")
    snapshot = _sql(product["db"], "SELECT policy_key, policy_value, impl_version FROM graph_run_policy_entries "
                                   "WHERE run_id = ? ORDER BY policy_key", (run_id,))
    nodes = _nodes(_current(product))
    assert "gate" not in nodes[t]
    wire = nodes[g]["gate"]
    assert (wire["can_decide"], wire["policy_undecided"], wire["decision_in_progress"], wire["job"],
            wire["archived"], wire["recovery_reset_required"], wire["blocked_reason"]) == (
        False, True, False, None, False, False, None)
    assert wire["settings_locked"] in (True, False) and "settings_lock_reason" in wire
    legacy = wire["decision"]
    assert (legacy["source"], legacy["decision_id"], legacy["decision"], legacy["result"], legacy["actor_user_id"],
            legacy["comment"], legacy["event_seq"], legacy["run_id"]) == (
        "legacy_event", f"legacy:{run_id}:{seq}", "reject", "rejected", "u_lee", "old reason", seq, run_id)
    assert legacy["decided_at"]
    # Unknown, not "none": the old event never told its Git or archive result.
    assert (legacy["effects"], legacy["archived"], legacy["archive_id"], legacy["target_sha"], legacy["frozen_sha"],
            legacy["job_id"]) == (None, False, None, None, None, None)
    assert {"effects", "target_sha", "frozen_sha", "settings"} <= set(legacy["unknown"])
    page = product["client"].get(f"/api/v1/runs/{run_id}/nodes/{g}/gate-decisions", headers=_h())
    assert page.status_code == 200 and [i["decision_id"] for i in page.json()["items"]] == [legacy["decision_id"]]
    # The decision itself is still refused, and the read changed nothing of the stored snapshot.
    before = _marks(product)
    for body in ({"decision": "confirm", "request_id": "old-1"}, {"decision": "reject"}):
        refused = _decide(product, run_id, g, body, 409)
        assert refused["error"]["code"] == "run_policy_undecided"
    _nothing_written(product, before)
    assert _sql(product["db"], "SELECT policy_key, policy_value, impl_version FROM graph_run_policy_entries "
                               "WHERE run_id = ? ORDER BY policy_key", (run_id,)) == snapshot
    assert _states(product)[g] == "open"


def test_a_decided_snapshot_still_reads_the_gate_when_the_registered_rule_has_no_reader(product, monkeypatch):
    """The run read never depends on what is registered now for a run that took the product rule."""
    from modules.flow_gate.graph_workflow.domain import policies

    g, _t = _linear(product)
    _start(product)
    monkeypatch.setattr(policies, "_registered", {k: v for k, v in policies._registered.items() if k != "gate_node"})
    wire = _nodes(_current(product))[g]["gate"]
    assert (wire["can_decide"], wire["policy_undecided"]) == (True, False)


# ── D0007 §7.2: a bad ``limit`` is 422, whatever its characters ──────────────

@pytest.mark.parametrize("limit", ["²", "１", "١", "9" * 5000, "1" + "0" * 400, "201", "-1", "+5", "1.0",
                                   "", " "])
def test_a_limit_that_is_no_plain_number_in_range_is_422(product, limit):
    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    res = product["client"].get(f"/api/v1/runs/{run_id}/nodes/{g}/gate-decisions", params={"limit": limit},
                                headers=_h())
    assert res.status_code == 422, res.text
    err = res.json()["error"]
    assert err["code"] == "invalid_request"
    assert err["params"]["errors"] == [{"field": "limit", "reason": "not_in_range:1,200"}]


def test_the_raw_superscript_limit_of_the_url_is_422(product):
    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    res = product["client"].get(f"/api/v1/runs/{run_id}/nodes/{g}/gate-decisions?limit=%C2%B2", headers=_h())
    assert res.status_code == 422 and res.json()["error"]["code"] == "invalid_request", res.text
    ok = product["client"].get(f"/api/v1/runs/{run_id}/nodes/{g}/gate-decisions?limit=200", headers=_h())
    assert ok.status_code == 200 and ok.json()["items"] == [], ok.text


# ── D0007 §8-5: requests that really race (own threads, clients, DB connections) ──

def _race(env, monkeypatch, calls, first):
    """``calls``: ``{name: (run_id, node_key, body)}`` posted together, each from its own thread with its own
    TestClient (so its own request, served on its own worker thread, and its own SQLite connections: the store opens
    one per transaction / read). A barrier releases them at once, after the route's own checks and just before the
    Flow guard; ``first`` is let into the guard first and holds it a moment, so every other request is really waiting
    on that guard while it decides. A request is named at the intake by its gate and request key (identical requests
    take their names in arrival order). -> ``{name: response}`` and the order in which the requests got the guard."""
    import threading
    import time

    from fastapi.testclient import TestClient

    from flowgate.gate_decisions import intake, legacy_adapter
    from modules.flow_gate.graph_workflow.services import flow_guard

    real_guard, real_decide = legacy_adapter.guarded_run, intake.decide
    barrier = threading.Barrier(len(calls))
    entered = threading.Event()
    seen = threading.Lock()
    who = threading.local()
    names: dict = {}
    for name, (_run_id, key, body) in calls.items():
        names.setdefault((key, body.get("request_id")), []).append(name)
    order, waited, spans = [], {}, {}

    def decide(group, run_id, node_key, decision, comment, user_id, intent):
        with seen:
            who.name = names[(node_key, intent.get("request_id"))].pop(0)
        return real_decide(group, run_id, node_key, decision, comment, user_id, intent)

    def racing(run_id, body_of):
        name = who.name
        barrier.wait(timeout=10)
        if name != first:
            assert entered.wait(timeout=10)
            waited[name] = flow_guard._flow_lock(FA).locked()           # the first one holds the guard now

        def held(run, jobs):
            start = time.monotonic()
            with seen:
                order.append(name)
            if name == first:
                entered.set()
                time.sleep(0.3)
            try:
                return body_of(run, jobs)
            finally:
                spans[name] = (start, time.monotonic())

        return real_guard(run_id, held)

    monkeypatch.setattr(intake, "decide", decide)
    monkeypatch.setattr(legacy_adapter, "guarded_run", racing)
    results, errors = {}, {}

    def post(name, run_id, key, body):
        try:
            client = TestClient(env["client"].app)
            results[name] = client.post(f"/api/v1/runs/{run_id}/nodes/{key}/gate-decision", json=body, headers=_h())
        except BaseException as exc:  # noqa: BLE001 — reported below
            errors[name] = exc

    threads = [threading.Thread(target=post, args=(name, *call)) for name, call in calls.items()]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=60)
    monkeypatch.setattr(legacy_adapter, "guarded_run", real_guard)
    monkeypatch.setattr(intake, "decide", real_decide)
    assert not errors, errors
    assert set(results) == set(calls)
    assert order[0] == first and sorted(order) == sorted(calls)
    assert all(waited.values()) and set(waited) == set(calls) - {first}   # each other one waited on the held guard
    for name in calls:
        if name != first:
            assert spans[name][0] >= spans[first][1]                       # and decided only after the first one
    return results, order


@pytest.mark.parametrize("first", ["confirm", "reject"])
@pytest.mark.parametrize("merge_mode", [None, "commit"])
def test_a_confirm_and_a_reject_racing_on_one_gate_keep_one_decision(product, slot, monkeypatch, merge_mode, first):
    g, t = _linear(product)
    run_id = _start(product)["run_id"]
    if merge_mode:
        setting(product, FA, g, "merge_mode", merge_mode)
    results, _order = _race(product, monkeypatch, {
        "confirm": (run_id, g, {"decision": "confirm", "request_id": "race-c"}),
        "reject": (run_id, g, {"decision": "reject", "comment": "no", "request_id": "race-r"})}, first)
    loser = "reject" if first == "confirm" else "confirm"
    won, lost = results[first], results[loser]
    expected = 202 if (first == "confirm" and merge_mode) else 200
    assert won.status_code == expected, won.text
    assert lost.status_code == 409, lost.text
    held = first == "confirm" and merge_mode
    assert lost.json()["error"]["code"] == ("gate_decision_in_progress" if held else "node_not_open")
    decision = won.json()["decision"]
    stored = _decisions(product)
    assert [(d["decision_id"], d["decision"], d["request_id"]) for d in stored] == [
        (decision["decision_id"], first, "race-" + first[0])]
    if held:
        assert lost.json()["error"]["params"]["decision_id"] == decision["decision_id"]
    jobs = _jobs(product)
    events = _gate_events(product, g)
    if held:                                  # accepted: one job, no completion event yet, the gate held open
        assert [j["request_key"] for j in jobs] == ["gpb:" + decision["decision_id"]] and events == []
        assert _states(product)[g] == "open"
    else:                                     # finished at once: one event, linked to the one decision, no job
        assert jobs == [] and [e["cause"] for e in events] == [
            "gate_confirmed" if first == "confirm" else "gate_rejected"]
        assert json.loads(events[0]["payload_json"])["gate_decision_id"] == decision["decision_id"]
        assert _states(product)[g] == ("done" if first == "confirm" else "rejected")


def test_the_same_request_racing_with_itself_is_one_decision_answered_twice(product, slot, monkeypatch):
    g, _t = _linear(product)
    run_id = _start(product)["run_id"]
    setting(product, FA, g, "merge_mode", "commit")
    body = {"decision": "confirm", "comment": "go", "request_id": "twice"}
    results, _order = _race(product, monkeypatch, {"a": (run_id, g, dict(body)), "b": (run_id, g, dict(body))}, "a")
    assert results["a"].status_code == results["b"].status_code == 202
    one, two = results["a"].json(), results["b"].json()
    assert one["decision"]["decision_id"] == two["decision"]["decision_id"]
    assert one["job"]["job_id"] == two["job"]["job_id"]
    assert len(_decisions(product)) == 1 and len(_jobs(product)) == 1 and _gate_events(product, g) == []


@pytest.mark.parametrize("same_key", [False, True])
def test_two_gates_confirmed_at_once_store_one_decision_and_one_job_each(product, slot, monkeypatch, same_key):
    keys = _confirmed(product, [node("tmp_r", "R"), node("tmp_g1", None, "gate"), node("tmp_g2", None, "gate")],
                      [edge("tmp_e1", "tmp_r", "tmp_g1"), edge("tmp_e2", "tmp_r", "tmp_g2")])
    g1, g2 = keys["tmp_g1"], keys["tmp_g2"]
    run_id = _start(product)["run_id"]
    for key in (g1, g2):
        setting(product, FA, key, "merge_mode", "commit")
    results, order = _race(product, monkeypatch, {
        "one": (run_id, g1, {"decision": "confirm", "request_id": "k" if same_key else "k1"}),
        "two": (run_id, g2, {"decision": "confirm", "request_id": "k" if same_key else "k2"})}, "one")
    assert results["one"].status_code == results["two"].status_code == 202, (results["one"].text, results["two"].text)
    one, two = results["one"].json(), results["two"].json()
    assert one["decision"]["decision_id"] != two["decision"]["decision_id"]
    stored = _decisions(product)
    assert sorted(d["node_key"] for d in stored) == sorted([g1, g2])
    assert {d["node_key"]: d["job_id"] for d in stored} == {g1: one["job"]["job_id"], g2: two["job"]["job_id"]}
    assert sorted(j["request_key"] for j in _jobs(product)) == sorted(
        ["gpb:" + one["decision"]["decision_id"], "gpb:" + two["decision"]["decision_id"]])
    assert order == ["one", "two"] and _gate_events(product, g1) == _gate_events(product, g2) == []
    wires = _nodes(_current(product))
    assert wires[g1]["gate"]["decision_in_progress"] and wires[g2]["gate"]["decision_in_progress"]


# flowgate.v02.0098 T#2: the shown intent and the interpreted target are independent.
@pytest.mark.parametrize("mode", ["merge", "commit", "hold", None])
def test_0098_effective_preserves_branch_intent_only_for_merge(mode):
    from flowgate.gate_decisions import preflight
    assert preflight.effective({"merge_mode": mode, "merge_target_branch": "review/x"})["merge_target_branch"] == (
        "review/x" if mode == "merge" else None)


@pytest.mark.parametrize("decision", ["confirm", "reject"])
def test_0098_immediate_decisions_store_the_new_shown_and_effective_key(product, decision):
    g, _ = _linear(product)
    run_id = _start(product)["run_id"]
    _decide(product, run_id, g, {"decision": decision, "request_id": "snapshot-0098"})
    snapshot = json.loads(_decisions(product)[0]["settings_json"])
    assert snapshot["shown"]["merge_target_branch"] is None
    assert snapshot["effective"]["merge_target_branch"] is None
    assert snapshot["settings_version"] == 0


@pytest.mark.parametrize("reason", ["merge_target_not_found", "merge_target_remote_only", "merge_target_internal_slot",
    "merge_target_same_branch", "merge_target_invalid", "merge_target_busy", "merge_target_owner_mismatch",
    "merge_target_incoming_work_base", "merge_target_preview_unavailable", "base_checkout_branch_mismatch", "base_dirty"])
def test_0098_preflight_preserves_the_common_target_rejection(product, slot, monkeypatch, reason):
    from flowgate.gate_decisions import preflight
    from modules.flow_gate.services.git import merge_target as mt
    from modules.flow_gate.services.git.credentials import GitServiceError
    g, _ = _linear(product)
    run_id = _start(product)["run_id"]
    setting(product, FA, g, "merge_mode", "merge")
    def fail(*a, **kw):
        raise GitServiceError(409, reason, "private diagnostic", details={"path": "private/path"})
    monkeypatch.setattr(mt, "judge_check_target", fail)
    response = _decide(product, run_id, g, {"decision": "confirm", "request_id": "unfit-0098"}, 409)
    assert response["error"]["code"] == "gate_settings_invalid"
    assert response["error"]["params"]["reason"] == reason
    assert "private" not in json.dumps(response)
    assert not _decisions(product) and not _jobs(product)
