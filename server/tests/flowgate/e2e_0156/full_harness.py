"""0156 T6 preserved F1/F2 E2E, one explicit cell per invocation.

python server/tests/flowgate/e2e_0156/full_harness.py SCRATCH f1 lan all LABEL
Uses the production full app, JWT/RBAC, a deterministic CLI through the real
admission/launcher/lease/finish path, real GUI toolbar, and authenticated human
gate decisions. No product service is monkeypatched. A fake provider is a fixture,
not a model. All runtime artifacts remain under the supplied scratch directory.
"""
from __future__ import annotations
import asyncio,json,os,sqlite3,sys,time,traceback,uuid,shutil
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlencode
HERE=Path(__file__).resolve();sys.path.insert(0,str(HERE.parents[1]))
import w7_gui_harness_0156 as w7

class Front(w7.Front):
    db=None;flow='';base='';token='';series=[]
    def facts(self):
        with sqlite3.connect(self.db) as con:
            con.row_factory=sqlite3.Row
            out={}
            for table in ['graph_runs','graph_run_command_requests','documents','graph_revisions','graph_nodes','graph_edges']:
                out[table]=[dict(r) for r in con.execute(f'SELECT * FROM {table} WHERE group_id=?',(self.flow,))]
            runs=out['graph_runs'];rid=runs[0]['run_id'] if runs else ''
            for table in ['graph_node_runs','graph_node_attempts','graph_run_events']:
                out[table]=[dict(r) for r in con.execute(f'SELECT * FROM {table} WHERE run_id=? ORDER BY rowid',(rid,))]
            out['runs']=runs
            return out
    def confirm(self):
        state=self.facts();run=state['runs'][0]
        gate=next(n for n in state['graph_node_runs'] if n['node_key']==self.gate_key and n['status']=='open')
        s,r=w7.http('POST',f"{self.base}/flowgate/api/v1/runs/{run['run_id']}/nodes/{self.gate_key}/gate-decision",{'decision':'confirm','comment':'0156 isolated account confirmation','request_id':str(uuid.uuid4())},self.token)
        assert s==200,(s,r)
        return {'status':s,'response':r,'user':w7.USER,'node_key':gate['node_key']}
    async def __call__(self,scope,receive,send):
        path=scope.get('path','')
        if path=='/harness/full.html':return await self._send(send,200,(HERE.parent/'full_driver.html').read_bytes(),'text/html; charset=utf-8')
        if path=='/harness/state':
            state=self.facts();self.series.append({'at':int(time.time()*1000),'state':state})
            return await self._send(send,200,json.dumps(state).encode(),'application/json')
        if path=='/harness/confirm':
            result=await asyncio.to_thread(self.confirm)
            return await self._send(send,200,json.dumps(result).encode(),'application/json')
        return await super().__call__(scope,receive,send)

def setup(front,work,variant,db):
    from modules.flow_gate.db import documents as docs
    from modules.flow_gate.storage import paths
    seeded=w7.seed(db,front.base)
    s,login=w7.http('POST',front.base+'/flowgate/auth/login',{'username':w7.USER,'password':w7.PASSWORD,'locale':'ko'});assert s==200
    front.token=login['access_token']
    # Provider row is fixture seeding in this new SQLite only, never an operational provider.
    now=datetime.now(timezone.utc).isoformat();pid='aip_e2e4'
    cli=f'"{sys.executable}" "{HERE.parent / "fake_cli.py"}"'
    with sqlite3.connect(db) as con:
        con.execute('INSERT INTO ai_providers(provider_id,name,exec_type,kind,enabled,cli_command,sort_order,created_at,updated_at,owner_scope,exec_location,project_access) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(pid,'0156 scripted fixture','cli','custom',1,cli,0,now,now,'system','local','all'))
        con.commit()
    roles={role:{'kind':'assigned','provider_id':pid} if role!='review' else {'kind':'not_used','provider_id':None} for role in ['instruction','execution','review']}
    s,r=w7.http('PUT',front.base+'/flowgate/api/v1/me/ai-roles',{'roles':roles},front.token);assert s==200,(s,r)
    root=f'{front.flow}.0001-R'
    root_path=paths.document_path('flowgate',front.flow.split('.')[-1],'R0001','root.md',module='v02')
    assert str(root_path.resolve()).startswith(str(work.resolve())),root_path
    root_path.parent.mkdir(parents=True,exist_ok=True);root_path.write_text('# Isolated root\n\n0156 scripted test.\n',encoding='utf-8')
    docs.update(root,{'file_path':paths.to_storage_relative(root_path,'flowgate')})
    ai={'kind':'ai','user_id':None,'provider_id':pid}
    nodes=[]
    for key,typ,assignee in [('tmp_root','R',None),('tmp_n1','N',ai),*([('tmp_n2','N',ai)] if variant=='f2' else []),('tmp_work','T',ai),*([('tmp_gate',None,None)] if variant=='f1' else [])]:
        nodes.append({'node_key':None,'client_key':key,'node_type':'gate' if typ is None else 'document','doc_type':typ,'palette_item_id':None,'label':key,'assignee':assignee})
    links=[('tmp_root','tmp_n1'),('tmp_n1','tmp_work')]
    if variant=='f2':links += [('tmp_root','tmp_n2'),('tmp_n2','tmp_work')]
    else:links += [('tmp_work','tmp_gate')]
    edges=[{'edge_id':None,'client_key':f'tmp_edge{i}','source':a,'target':b,'condition':None} for i,(a,b) in enumerate(links)]
    s,g=w7.http('GET',f'{front.base}/flowgate/api/v1/flows/{front.flow}/graph',token=front.token);assert s==200
    data=w7._unwrap(g);version=data['draft']['draft_version']
    s,r=w7.http('PUT',f'{front.base}/flowgate/api/v1/flows/{front.flow}/graph/draft',{'draft_version':version,'nodes':nodes,'edges':edges},front.token);assert s==200,(s,r)
    saved=w7._unwrap(r)
    if variant=='f1':front.gate_key=saved['key_map']['tmp_gate']
    front.node_keys=saved['key_map']
    s,r=w7.http('POST',f'{front.base}/flowgate/api/v1/flows/{front.flow}/graph/revisions',{'draft_version':saved['draft_version'],'base_revision_no':data['latest_revision']['revision_no']},front.token);assert s==201,(s,r)
    return {'user_id':seeded['user_id'],'provider':pid,'node_keys':front.node_keys,'root_path':str(root_path)}

def main():
    scratch=Path(sys.argv[1]).resolve();variant,origin,mode,label=sys.argv[2:6]
    assert variant in ('f1','f2') and origin in ('lan','local') and mode in ('all','next')
    work=scratch/('full-'+label);work.mkdir(parents=True,exist_ok=False)
    dist=Path((scratch/'preparation-current-build.txt').read_text(encoding='utf-8'))
    port=w7.free_port();w7.free_port=lambda:port;w7.Front=Front
    os.environ.update({'FLOWGATE_PORT':str(port),'FLOWGATE_WORKERS':'1'})
    w7.CASES=[(origin,mode,'flowgate.v02.9511')]
    front,server,thread,port,db=w7.start_server(work,dist)
    front.db=db;front.base=f'http://127.0.0.1:{port}';front.flow=w7.CASES[0][2];front.series=[]
    try:
        seed=setup(front,work,variant,db);before=front.facts()
        base=f'http://{w7.lan_ipv4() if origin=="lan" else "127.0.0.1"}:{port}'
        w7.browse(work,base+'/harness/full.html?'+urlencode({'flow':front.flow,'mode':mode}),front,1,265)
        after=front.facts();reports=list(front.reports);calls=list(front.calls)
        env=next((r for r in reports if r.get('kind')=='env'),{})
        done=next((r for r in reports if r.get('kind')=='completed'),{})
        dom=[r for r in reports if r.get('kind')=='dom']
        gate=[r for r in reports if r.get('kind')=='human_gate']
        starts=[c for c in calls if c['kind']=='start']
        run=after['runs'][0] if after['runs'] else {}
        events=after['graph_run_events'];node_runs=after['graph_node_runs']
        dangers=[t for r in dom for t in r['toasts'] if 'ui-toast--danger' in t['cls']]
        checks={'environment':env.get('isSecureContext') is (origin=='local') and (origin!='lan' or (env.get('randomUUID')=='undefined' and env.get('locks')=='undefined')),
                'completed':bool(done) and run.get('idle_reason')=='completed','all_nodes_done':bool(node_runs) and all(n['status']=='done' for n in node_runs),
                'no_failure_toast':not dangers,'posts_201':bool(starts) and all(c['status']==201 for c in starts),
                'registry_ids':{r['command_request_id'] for r in after['graph_run_command_requests']}=={c['body']['command_request_id'] for c in starts},
                'run_one':len(after['runs'])==1,'human_gate':len(gate)==1 and any(r.get('label')=='waiting_confirm_observed' for r in dom) if variant=='f1' else True,
                'gui_final':any(r.get('label')=='completed' for r in dom),
                'no_driver_error':not any(r.get('kind')=='error' for r in reports)}
        # Preserve full event payloads and series; parallel order is judged from
        # actual opening/done events by the report, not inferred from completed.
        result={'variant':variant,'origin':origin,'mode':mode,'label':label,'checks':checks,'seed':seed,'env':env,'run':run,
                'registry':after['graph_run_command_requests'],'attempts':after['graph_node_attempts'],
                'events':events,'gate':gate,'dom':dom,'errors':[r for r in reports if r.get('kind')=='error']}
        (work/'report.json').write_text(json.dumps({'result':result,'before':before,'after':after,'reports':reports,'calls':calls,'series':front.series},ensure_ascii=False,indent=2),encoding='utf-8')
        order=[]
        for e in events:
            payload=json.loads(e.get('payload_json') or '{}')
            order.append({'cause':e.get('cause'),'seq':e.get('event_seq'),'at':e.get('created_at'),'node':e.get('node_key') or payload.get('node_key'),'from':e.get('from_status'),'to':e.get('to_status')})
        concise={**result,'events':order,'attempts':[{k:a.get(k) for k in ['attempt_id','node_key','status','stage','result_doc_id']} for a in result['attempts']],
                 'gate':[{'status':r['decision']['status'],'user':r['decision']['user'],'node_key':r['decision']['node_key']} for r in gate],
                 'dom':[{k:r[k] for k in ['label','at','states','toasts']} for r in dom if r['label']!='poll']}
        print('E2E_CELL '+json.dumps(concise,ensure_ascii=False),flush=True)
        return 0 if all(checks.values()) else 1
    finally:server.should_exit=True;thread.join(timeout=10)

if __name__=='__main__':
    try:code=main()
    except BaseException:
        detail=traceback.format_exc();print('HARNESS_ERROR '+detail,flush=True)
        traceback.print_exc();code=2
    sys.stdout.flush();sys.stderr.flush();os._exit(code)
