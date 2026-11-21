"""Independently selectable scenarios; shared fixtures remain in test_revision_diagnostics_0149.py."""
from __future__ import annotations

from test_revision_diagnostics_0149 import (
    ACTIVE,
    ALLOWED,
    CASES,
    FA,
    RUN,
    _h,
    _sql,
    all_facts,
    assess,
    attempt,
    candidate,
    confirm_candidate,
    db_path,
    decided,
    definition_store,
    document_write_ports,
    env,
    facts,
    get_store,
    json,
    layout_store,
    policy_tuple,
    pytest,
    row,
    run_store,
    seed,
)

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
