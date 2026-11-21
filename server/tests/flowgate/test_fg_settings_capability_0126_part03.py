"""Independently selectable scenarios; shared fixtures remain in test_fg_settings_capability_0126.py."""
from __future__ import annotations

from test_fg_settings_capability_0126 import (
    B,
    FA,
    X,
    _h,
    _sql,
    assignments,
    command,
    db_path,
    decided,
    edge,
    env,
    flow,
    get_settings,
    json,
    ledger,
    line_doc,
    node,
    node_run,
    patch,
    put_draft,
    pytest,
    roles,
    save,
    snapshot,
    start,
    starts,
    url,
    wired,
)

def test_real_loopback_patch_get_new_client_and_no_automatic_dispatch(env, decided, starts, tmp_path):
    """Actual TCP HTTP against an isolated ASGI app, not a mocked settings transport."""
    import socket
    import threading
    import time
    import urllib.request
    import uvicorn
    key=flow(env); roles(key,'not_used','not_used','not_used'); start(env)
    sock=socket.socket(); sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(env['client'].app,log_level='error',lifespan='off'))
    worker=threading.Thread(target=server.run,kwargs={'sockets':[sock]})
    worker.start()
    evidence=[]
    def request(method,body=None):
        req=urllib.request.Request(f'http://127.0.0.1:{port}'+url(key),method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={**_h(),'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=20) as res:
            result=json.load(res)
            evidence.append({'method':method,'status':res.status,'request':body,'response':result})
            return result
    try:
        deadline=time.monotonic()+20
        while not server.started and worker.is_alive() and time.monotonic()<deadline:time.sleep(.02)
        assert server.started
        before=request('GET')
        history=snapshot(env)
        for name,value in [('instruction_role',{'kind':'assigned','provider_id':X}),
                           ('execution_role',{'kind':'assigned','provider_id':B}),
                           ('review_role',{'kind':'assigned','provider_id':X}),('review_count',3)]:
            before=request('PATCH',{'field':name,'value':value,'expected_context':before['context'],
                                    'expected_settings_version':before['settings_version']})
        fresh=request('GET')
        assert fresh['settings_version']==before['settings_version']
        assert fresh['fields']['review_count']['value']==3
        assert fresh['ai_roles']['roles']['execution']['selected']['provider_id']==B
        assert snapshot(env)==history and starts==[] and ledger(env)==[]
    finally:
        server.should_exit=True
        worker.join(timeout=30)
        sock.close()
        assert not worker.is_alive()
        (tmp_path/'http-settings-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
def test_get_capability_and_patch_agree_on_read_only_permission(env, decided, starts, monkeypatch):
    from modules.flow_gate.graph_workflow.api import envelope
    key=flow(env);roles(key,'not_used','not_used','not_used');start(env)
    before=get_settings(env,FA,key)
    monkeypatch.setattr(envelope,'has_permission',lambda u,p,perm:perm==envelope.READ_PERMISSION)
    current=get_settings(env,FA,key)
    assert current['locked'] is True and current['node_running'] is False
    assert current['settings_capability']=={'editable':False,'reason':'forbidden'}
    assert save(env,key,'review_count',2,before,expect=403)['error']['code']=='flow_access_denied'
    assert get_settings(env,FA,key)['settings_version']==before['settings_version']
def test_ownership_resolver_conflict_blocks_capability_and_write(env, decided, starts, monkeypatch):
    from modules.flow_gate.graph_workflow.services import engine_router
    key=flow(env);roles(key,'not_used','not_used','not_used');start(env)
    before=get_settings(env,FA,key)
    # Real resolver combines the real Graph assignment with a conflicting sequence read.
    monkeypatch.setattr(engine_router,'read_sequence',lambda f:({'doc_id':f+'.0001-R'},[]))
    assert engine_router.read_ownership(FA).state=='conflict'
    answer=get_settings(env,FA,key)
    assert answer['locked'] and answer['settings_capability']['reason']=='flow_ownership_conflict'
    assert save(env,key,'review_count',2,before,expect=409)['error']['code']=='flow_ownership_conflict'
