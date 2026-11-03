"""0126 T#5: completion repair precedes AI after settings changes; actual HTTP/SQLite."""
import pytest
from test_fg_human_completion_0126 import (  # noqa: F401
    FA,_h,_sql,db_path,decided,env,legacy_cards,decisions_env,completions,
    graph,confirm,approve,state,proof,_get,_body,_command,_rows,
)
from test_fg_settings_capability_0126 import save

pytestmark=pytest.mark.usefixtures('legacy_cards','completions')


@pytest.mark.parametrize('mode',['step','full'])
def test_valid_outputs_with_lost_completion_repair_without_ai_after_settings_change(
        decisions_env,decided,monkeypatch,mode):
    from flowgate.card_completion import human
    e=decisions_env
    keys=graph(e,names=('a','b'))
    report=_get(e,keys['a'])['documents']['report']['doc_id']
    _body(report,'')
    confirm(e)
    _command(e,mode)
    _body(report)
    refresh=human.refresh
    monkeypatch.setattr(human,'refresh',lambda flow:None)
    approve(e,keys['a'])
    assert state(e,keys['a'])['status']!='done'
    decisions=_rows(e,'graph_document_decisions')
    for field,value in [('execution_role',{'kind':'assigned','provider_id':'aip_card'}),
                        ('review_role',{'kind':'assigned','provider_id':'aip_card'}),('review_count',1)]:
        save(e,keys['a'],field,value)
    assert _rows(e,'graph_document_decisions')==decisions
    monkeypatch.setattr(human,'refresh',refresh)
    _command(e,mode)
    assert state(e,keys['a'])['status']=='done'
    proof(e,keys['a'])
    assert e['jobs']==[] and _rows(e,'graph_attempt_runs')==[]
    if mode=='step': assert state(e,keys['b'])['status']=='pending'
    assert _rows(e,'graph_document_decisions')==decisions
