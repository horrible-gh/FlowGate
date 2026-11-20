"""0132 T#3 / NR §5.3-9(a-i): real bare origin, Runner/Sweeper and mixed human waits.

Each resume checks writer locks/wire, successful successor opening within full command, and untouched G2/W waits.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent/'flowgate'))
from test_fg_card_settings import FA, _h, _sql, db_path, edge, env, node
from test_fg_gate_intake_0085 import LEE, _confirmed, _current, _nodes, _start, _states, product
from test_fg_gate_publish_0085 import (
    PROJECT, _act, _confirm, _decision, _effects, _gate, _git, _rev, _run_job, _version, _work, git_env)
from test_fg_gate_push_sync_0132 import _advance, _due, pushes


def mixed(r,wait):
    e=r['env']
    ns=[node('tmp_r','R'), node('tmp_g',None,'gate'),node('tmp_s','T',assignee=LEE)]
    es=[edge('tmp_rg','tmp_r','tmp_g'),edge('tmp_gs','tmp_g','tmp_s')]
    if wait=='waiting_confirm':
        ns.append(node('tmp_other',None,'gate'))
    else:
        ns.append(node('tmp_other','N',assignee=LEE))
    es.append(edge('tmp_ro','tmp_r','tmp_other'))
    keys=_confirmed(e,ns,es)
    rid=_start(e,'full')['run_id']
    return e,keys['tmp_g'],keys['tmp_s'],keys['tmp_other'],rid


def assert_locked(e,rid,g,s):
    from flowgate.gate_decisions import legacy_adapter as legacy
    from flowgate.graph_run import editing,hold
    from flowgate.graph_time_machine import service
    wire=_current(e)
    assert _nodes(wire)[g]['gate']['git_active'] is True
    def check(run,jobs):
        for call in (lambda:editing.require_editable(FA),lambda:hold.require_settable(FA,s,True),lambda:service.require_safe(FA,g)):
            with pytest.raises(legacy.GraphApiError) as exc: call()
            assert exc.value.code=='graph_run_locked'
            assert exc.value.params.get('reason')=='gate_git_active'
    legacy.guarded_run(rid,check)




def finish_sync_review(r, e, jid, did):
    """Complete the real remote-sync review without waking through intake or explicit retry."""
    from modules.flow_gate.services import git_service as svc
    sync = _effects(e, did)['remote_sync']
    mid = json.loads(sync['evidence_json'])['merge_id']
    (r['base'] / 'shared.txt').write_text('resolved\n', encoding='utf-8')
    _git(['add', 'shared.txt'], cwd=r['base'])
    _git(['commit', '--no-edit'], cwd=r['base'])
    svc._finish_reviewed_attempt(FA, mid, PROJECT, _rev(r['base'], 'HEAD')[:7], False)
