"""0149 T-A1: real-row HTTP adoption diagnostics and unchanged policy/read effects."""
import json
import pytest

from test_fg_revision_adoption_precheck_0010 import (
    FA, _h, _sql, db_path, env, decided, candidate, document_write_ports,
    facts, assess, confirm_candidate,
)
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.store import definition_store, layout_store, run_store

RUN = "run_precheck"
# v02test.0018: a live open/review card (review_only, active_open) no longer refuses; only an active attempt outside
# an open/review row does (ACTIVE).
CASES = ["active_pending", "mixed", "capped", "orphan_attempt", "empty", "review_only", "active_open",
         "gate_rejected", "gate_recovery_reset_required", "jobs", "reservation", "jobs_and_reservation",
         "protected", "reused", "policy", "allowed"]
ACTIVE = CASES[:5]
ALLOWED = ("review_only", "active_open", "allowed")


def all_facts(env):
    # Include the policy defaults, guards, jobs and gate rows in addition to the precheck facts.
    tables = [r["name"] for r in _sql(env["db"], "SELECT name FROM sqlite_master WHERE type='table'")
              if not r["name"].startswith("sqlite_")]
    return {**facts(env), **{t: _sql(env["db"], 'SELECT * FROM "' + t + '" ORDER BY rowid') for t in tables}}


def row(env, key, status):
    _sql(env["db"], "INSERT INTO graph_node_runs (run_id,node_key,status,root_bound,updated_at) "
         "VALUES (?,?,?,0,'t')", (RUN, key, status))


def attempt(env, key, status="active"):
    _sql(env["db"], "INSERT INTO graph_node_attempts "
         "(attempt_id,run_id,node_key,revision_no,status,active_node_key,created_at) VALUES (?,?,?,1,?,?, 't')",
         ("att_" + key, RUN, key, status, key if status == "active" else None))


def seed(candidate, monkeypatch, case):
    from flowgate.gate_decisions import repository
    from modules.flow_gate.services.git import job_store
    from modules.flow_gate.graph_workflow.domain import policies
    env, keys, nodes, edges = candidate
    gate = keys["tmp_g"]
    expected = {}
    if case == "review_only":
        _sql(env["db"], "UPDATE graph_node_runs SET status='review' WHERE node_key=?", (gate,))
    elif case == "active_open":
        _sql(env["db"], "UPDATE graph_node_runs SET status='open' WHERE node_key=?", (gate,)); attempt(env, gate)
    elif case == "active_pending":
        row(env, "K2", "pending"); attempt(env, "K2"); expected = {"K2": ("pending", True)}
    elif case in ("mixed", "capped"):
        # Live open/review rows (with or without an attempt) are no blockers; pending/orphan attempts are.
        row(env, "K1", "review"); row(env, "K2", "open"); attempt(env, "K2"); row(env, "K3", "open")
        row(env, "K4", "pending"); attempt(env, "K4"); attempt(env, "K_orphan")
        expected = {"K4": ("pending", True), "K_orphan": (None, True)}
        for key, status in [("excluded_done", "done"), ("excluded_pending", "pending")]:
            row(env, key, status); attempt(env, key, "ended")
        if case == "capped":
            for i in range(52):
                key = f"extra_{i:02d}"; row(env, key, "pending"); attempt(env, key); expected[key] = ("pending", True)
    elif case == "orphan_attempt":
        attempt(env, "K_orphan"); expected = {"K_orphan": (None, True)}
    elif case == "empty":
        monkeypatch.setattr(run_store, "has_active_attempts", lambda _: True)
    elif case == "gate_rejected":
        _sql(env["db"], "UPDATE graph_node_runs SET status='rejected' WHERE node_key=?", (gate,))
    elif case in ("gate_recovery_reset_required", "reservation", "jobs_and_reservation"):
        status = "abandoned" if case == "gate_recovery_reset_required" else "accepted"
        with get_store().transaction():
            decision = repository.insert_decision({"decision_id": "dec_private", "group_id": FA, "run_id": RUN,
                "node_key": gate, "attempt_id": "att_private", "revision_no": 1, "actor_user_id": "u_kim",
                "request_id": "request_private", "request_fingerprint": "a" * 64, "decision": "confirm", "status": "accepted"})
            if status == "abandoned":
                assert repository.move(decision["decision_id"], decision["decision_version"], "recovery_required")
                decision = repository.get(decision["decision_id"])
                assert repository.move(decision["decision_id"], decision["decision_version"], "abandoned", recovery_reset_required=True)
    if case in ("jobs", "jobs_and_reservation"):
        with get_store().transaction():
            job_store.insert_gate_publish("flowgate", {"decision_id": "job_private", "group_id": FA,
                "run_id": RUN, "node_key": "job_target"}, group_id=FA, target_key="job_target")
    if case == "protected":
        nodes[0] = {**nodes[0], "label": "changed protected root"}
        with get_store().transaction(): definition_store.replace_draft_content(FA, nodes, edges)
    elif case == "reused":
        with get_store().transaction(): layout_store.retire(FA, "gn_added", "draft_removed")
    elif case == "policy":
        monkeypatch.setitem(policies._system_default, "report_pair", (policies.UNDECIDED, policies.UNDECIDED_IMPL_VERSION))
    confirm_candidate()
    return env, gate, expected


def policy_tuple(result):
    err = result.get("error") or {}
    return [result["adoptable"], err.get("code"), (err.get("params") or {}).get("reason"), err.get("status"), 200]


@pytest.mark.parametrize("case", CASES)
def test_policy_baseline_and_http_parity(candidate, monkeypatch, case):
    env, gate, expected = seed(candidate, monkeypatch, case)
    before = all_facts(env)
    result = assess(env)
    assert all_facts(env) == before  # Evaluation alone must never reconcile/settle/write.
    active = case in ACTIVE
    reason = "active_attempts" if active else "gate_jobs_in_progress" if case in ("jobs", "reservation", "jobs_and_reservation") else case if case.startswith("gate_") else None
    code = "graph_revision_migration_blocked" if reason else {"protected": "graph_node_protected", "reused": "graph_node_key_reused", "policy": "run_policy_undecided"}.get(case)
    assert policy_tuple(result) == [case in ALLOWED, code, reason, 409 if code else None, 200]
    print("POLICY_0149", case, json.dumps(policy_tuple(result)), "evaluation_facts_unchanged=true")
    if case not in ALLOWED:
        params = result["error"]["params"]
        if active:
            selected = sorted(expected)[:50]
            assert params["node_keys"] == selected
            assert params["node_keys_total"] == len(expected)
            assert params["blockers"] == [{"node_key": k, "row_status": expected[k][0], "active_attempt": expected[k][1]} for k in selected]
            assert set(params) == {"flow_id", "run_id", "reason", "from_revision_no", "to_revision_no", "node_keys", "node_keys_total", "blockers"}
            assert params["from_revision_no"] == 1 and params["to_revision_no"] == 2
        elif reason == "gate_jobs_in_progress":
            selected = sorted(({"job_target"} if case in ("jobs", "jobs_and_reservation") else set()) |
                              ({gate} if case in ("reservation", "jobs_and_reservation") else set()))
            assert params["node_keys"] == selected and params["node_keys_total"] == len(selected)
            assert len(params["jobs"]) == (1 if case in ("jobs", "jobs_and_reservation") else 0)
            assert params["decision_ids"] == (["dec_private"] if case in ("reservation", "jobs_and_reservation") else [])
            assert set(params) == {"flow_id", "run_id", "reason", "jobs", "decision_ids", "node_keys", "node_keys_total"}
        elif case in ("gate_rejected", "gate_recovery_reset_required"):
            assert params == {"flow_id": FA, "run_id": RUN, "reason": case, "from_revision_no": 1, "to_revision_no": 2, "node_keys": [gate]}
        elif case == "protected":
            assert params == {"flow_id": FA, "run_id": RUN, "node_keys": [candidate[2][0]["node_key"]]}
        elif case == "reused":
            assert params == {"flow_id": FA, "node_keys": ["gn_added"]}
        assert not ({"attempt_id", "token", "doc_id"} & set(params))
        before_post = all_facts(env)
        response = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"mode": "full"}, headers=_h())
        assert response.status_code == 409, response.text
        assert response.json()["error"]["params"] == params
        assert response.json()["error"]["code"] == code
        assert all_facts(env) == before_post  # Command failure has its own unchanged-facts check.
    else:
        response = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"mode": "full"}, headers=_h())
        assert response.status_code == 201, response.text
        assert response.json()["run"]["revision_no"] == 2
        if case in ("review_only", "active_open"):
            kept = {"review_only": "review", "active_open": "open"}[case]
            assert {r["node_key"]: r["status"] for r in run_store.node_runs(RUN)}[gate] == kept   # the live card stays


@pytest.mark.parametrize("case", ["active_pending", "mixed", "orphan_attempt"])
def test_a3_a6_current_read_is_measured_separately(candidate, monkeypatch, case):
    env, _, _ = seed(candidate, monkeypatch, case)
    before = all_facts(env)
    assert assess(env)["adoptable"] is False
    after_failure = all_facts(env)
    assert after_failure == before
    response = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers=_h())
    assert response.status_code == 200, response.text
    after_read = all_facts(env)
    for table in ("graph_revisions", "graph_revision_nodes", "graph_revision_edges", "graph_drafts",
                  "graph_draft_nodes", "graph_draft_edges", "documents"):
        assert before[table] == after_failure[table] == after_read[table]
    node_keys = lambda f: sorted((r["run_id"], r["node_key"]) for r in f["graph_node_runs"])
    assert node_keys(before) == node_keys(after_failure) == node_keys(after_read)
    changes = {t: {"before": before[t], "after": after_read[t]} for t in before if before[t] != after_read[t]}
    print("CURRENT_READ_0149", case, json.dumps(changes, default=str, sort_keys=True))
    def counts(f):
        return {"definition_cards": len([r for r in f["graph_revision_nodes"] if r["revision_no"] == 2]),
                "definition_edges": len([r for r in f["graph_revision_edges"] if r["revision_no"] == 2]),
                "run_node_rows": len(f["graph_node_runs"]), "documents": len(f["documents"]),
                "run": [{k: r[k] for k in ("status", "idle_reason", "last_event_seq")} for r in f["graph_runs"]]}
    print("THREE_POINTS_0149", case, json.dumps([counts(f) for f in (before, after_failure, after_read)], sort_keys=True))


def test_a1_no_definition_change_with_mixed_parallel_rows(candidate):
    env, keys, *_ = candidate
    # Candidate has two real branches from the done root: the gate and gn_added.
    confirm_candidate()
    _sql(env["db"], "UPDATE graph_runs SET revision_no=2, idle_reason='needs_intervention' WHERE run_id=?", (RUN,))
    _sql(env["db"], "UPDATE graph_node_runs SET status='review' WHERE run_id=? AND node_key=?", (RUN, keys["tmp_g"]))
    row(env, "gn_added", "pending")
    before = all_facts(env)
    result = assess(env)
    assert result["adoptable"] is True and result["target"] is None
    assert all_facts(env) == before
    response = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"mode": "full"}, headers=_h())
    assert response.status_code == 201, response.text
    assert response.json()["run"]["revision_no"] == 2
    assert {r["node_key"] for r in run_store.node_runs(RUN)} == {n["node_key"] for n in definition_store.revision(FA, 2)["nodes"]}



def test_gate_key_filter_sort_dedup_and_cap(candidate, monkeypatch):
    from flowgate.gate_decisions import rounds, repository
    env, *_ = candidate
    jobs = [{"job_id": "private", "target_key": k} for k in [None, 42, "", " ", "K1", "K1"] + [f"N{i:02d}" for i in range(52)]]
    monkeypatch.setattr(rounds, "unfinished", lambda _: jobs)
    monkeypatch.setattr(repository, "reservations_of_group", lambda _: [{"decision_id":"private", "node_key":"K0"}])
    confirm_candidate()
    before = all_facts(env)
    result = assess(env)
    expected = sorted(["K0", "K1"] + [f"N{i:02d}" for i in range(52)])
    assert result["error"]["params"]["node_keys"] == expected[:50]
    assert result["error"]["params"]["node_keys_total"] == len(expected)
    assert result["error"]["params"]["jobs"] == jobs
    assert all_facts(env) == before


def test_diagnostic_attempt_query_is_only_used_after_refusal(candidate, monkeypatch):
    def unexpected(_):
        pytest.fail("diagnostic query on an allowed branch")
    monkeypatch.setattr(run_store, "active_attempt_node_keys", unexpected)
    env, *_ = candidate
    before = all_facts(env)
    assert assess(env)["adoptable"] is True
    assert all_facts(env) == before
