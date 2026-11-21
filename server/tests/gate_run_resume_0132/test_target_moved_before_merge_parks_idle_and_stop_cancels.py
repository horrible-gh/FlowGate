"""Independently selectable scenario; shared harness stays in test_fg_gate_run_resume_0132."""
from __future__ import annotations

from test_fg_gate_run_resume_0132 import (
    FA,
    LEE,
    PROJECT,
    Path,
    _act,
    _advance,
    _confirm,
    _confirmed,
    _current,
    _decision,
    _due,
    _effects,
    _gate,
    _git,
    _h,
    _nodes,
    _rev,
    _run_job,
    _sql,
    _start,
    _states,
    _version,
    _work,
    assert_locked,
    db_path,
    edge,
    env,
    finish_sync_review,
    git_env,
    json,
    mixed,
    node,
    product,
    pushes,
    pytest,
    sys,
)

def test_target_moved_before_merge_parks_idle_and_stop_cancels(git_env,pushes):
    r=git_env
    e,g,s,other,rid=mixed(r,'waiting_confirm')
    _work(r,'ours.txt')
    _gate(e,g,merge_mode='merge',merge_target='origin',push=True)
    body=_confirm(e,rid,g,'target-moved-0132')
    did,jid=body['decision']['decision_id'],body['job']['job_id']
    (r['base']/'target.txt').write_text('another writer\n',encoding='utf-8')
    _git(['add','target.txt'],cwd=r['base'])
    _git(['commit','-m','advance target'],cwd=r['base'])
    job,_=_run_job(jid)
    assert (job['phase_note'],job['last_error_code'])==('gate_failed','target_moved'),job
    assert _current(e)['status']=='idle'
    res=e['client'].post(f'/api/v1/runs/{rid}/stop',json={},headers=_h())
    assert res.status_code==200,res.text
    assert _current(e)['idle_reason']=='stopped'
    assert _decision(e,did)['status']=='canceled'
