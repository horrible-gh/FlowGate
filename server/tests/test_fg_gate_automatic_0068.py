"""Automatic gate approval: real guarded intake and persisted once-per-attempt failure."""
from test_fg_gate_intake_0085 import (
    product, env, db_path, slot, pre_feature_attempts, _linear, _start, _current,
    _nodes, _decisions, _jobs, _sql, FA, setting,
)
from flowgate.gate_decisions import preflight


def test_legacy_archive_setting_is_history_only():
    assert preflight.effective({"archive": True, "merge_mode": "hold"})["archive"] is False


def test_missing_auto_approve_keeps_a_gate_manual(product):
    gate, _ = _linear(product)
    _start(product)
    assert _nodes(_current(product))[gate]["status"] == "open"
    assert not _decisions(product)


def test_hold_automatically_confirms_with_run_issuer(product):
    gate, following = _linear(product)
    setting(product, FA, gate, "merge_mode", "hold")
    setting(product, FA, gate, "auto_approve", True)
    _start(product)
    current = _nodes(_current(product))
    assert current[gate]["status"] == "done"
    assert current[following]["status"] == "open"
    decisions = _decisions(product)
    assert len(decisions) == 1
    assert decisions[0]["actor_user_id"] == "u_kim"
    assert decisions[0]["decision_comment"] == "자동 승인"
    assert not _jobs(product)


def test_git_failure_waits_without_repeating(product, monkeypatch):
    from flowgate.gate_decisions import legacy_adapter
    calls = []
    def unavailable(*args):
        calls.append(args)
        return {"ok": False, "reason": "disabled"}
    monkeypatch.setattr(legacy_adapter, "git_slot", unavailable)
    gate, _ = _linear(product)
    setting(product, FA, gate, "merge_mode", "commit")
    setting(product, FA, gate, "auto_approve", True)
    _start(product)
    for _ in range(3):
        node = _nodes(_current(product))[gate]
        assert node["status"] == "open"
        assert node["gate"]["auto_approval_error"] == "gate_git_unavailable"
        assert node["gate"]["can_decide"]
    assert len(calls) == 1
    assert not _decisions(product)


def test_commit_uses_the_existing_publish_job(product, slot, monkeypatch):
    from flowgate.gate_decisions import legacy_adapter
    monkeypatch.setattr(legacy_adapter, "notify_job", lambda job: None)
    gate, _ = _linear(product)
    setting(product, FA, gate, "merge_mode", "commit")
    setting(product, FA, gate, "auto_approve", True)
    _start(product)
    assert len(_jobs(product)) == 1
    assert _jobs(product)[0]["kind"] == "gate_publish"
    assert _nodes(_current(product))[gate]["status"] == "open"
    assert len(_decisions(product)) == 1


def test_creation_defaults_preserve_explicit_values(monkeypatch):
    from flowgate.ai_roles import card_init
    rows={'new':{},'explicit':{'auto_approve':False,'merge_mode':'hold'}}
    bumped=[]
    monkeypatch.setattr(card_init.repository,'available',lambda:True)
    monkeypatch.setattr(card_init.repository,'values',lambda flow,key:dict(rows[key]))
    def put(flow,key,name,value,actor):
        rows[key][name]=value
        return True
    monkeypatch.setattr(card_init.repository,'put',put)
    monkeypatch.setattr(card_init.repository,'bump_version',lambda flow,key:bumped.append(key))
    monkeypatch.setattr(card_init.role_store,'available',lambda:False)
    nodes=[{'node_key':key,'node_type':'gate'} for key in rows]
    card_init.cards_created({'group_id':'f'},'u',nodes)
    card_init.cards_created({'group_id':'f'},'u',nodes)
    assert rows == {'new':{'auto_approve':True,'merge_mode':'commit'},'explicit':{'auto_approve':False,'merge_mode':'hold'}}
    assert bumped == ['new']
