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
