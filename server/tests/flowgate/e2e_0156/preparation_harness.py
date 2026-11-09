"""0156 T6 preparation refusal E2E. Explicit CLI only; never collected by pytest.

python server/tests/flowgate/e2e_0156/preparation_harness.py SCRATCH build
python server/tests/flowgate/e2e_0156/preparation_harness.py SCRATCH conflict lan all baseline

One cell per invocation, no retry loop of a scenario. Reuses W7's full application,
real JWT/RBAC/migrations, scratch SQLite, production build and same-origin DOM driver.
Only the test ASGI front is extended; product services are not patched. The two
cards are approved R -> human T: this checks preparation refusals and retry
acceptance, and makes no claim about AI completion or F1's human confirmation gate.
"""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import traceback
from urllib.parse import parse_qs, urlencode

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
import w7_gui_harness_0156 as w7

DRIVER = r'''<!doctype html><meta charset="utf-8"><title>0156 preparation</title><script>
(async()=>{
 const q=new URLSearchParams(location.search), flow=q.get('flow'), mode=q.get('mode');
 const sleep=ms=>new Promise(r=>setTimeout(r,ms));
 const report=o=>fetch('/harness/report',{method:'POST',body:JSON.stringify(o)});
 let w=null, watch=null;
 try {
  const login=await (await fetch('/flowgate/auth/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({username:'w7user',password:'W7-harness-pass-0156!',locale:'ko'})})).json();
  sessionStorage.clear();sessionStorage.setItem('fg_access_token',login.access_token);sessionStorage.setItem('fg_refresh_token',login.refresh_token);
  localStorage.setItem('preferred_locale','ko');
  sessionStorage.setItem('flowgate.user.w7user.designer.session',JSON.stringify({v:1,projectId:'flowgate',explorer:{selection:{kind:'flow',id:flow},expandedGroupIds:['module:flowgate:v02']},tabs:[{flowId:flow,name:flow,groupId:'module:flowgate:v02'}],activeFlowId:flow,leftMode:'project',rightTool:'palette'}));
  w=window.open('/app/designer','prep');
  const sel='button[data-test="designer-tool-run"][data-command="run.'+mode+'"]';
  async function ready(){for(let i=0;i<600;i++){await sleep(100);try{let b=w.document.querySelector(sel);if(b&&!b.disabled&&w.document.querySelectorAll('[data-test="designer-canvas-node"]').length>=2)return b;}catch(e){}}throw Error('run button not ready');}
  let button=await ready();
  const env={origin:w.location.origin,isSecureContext:w.isSecureContext,randomUUID:typeof w.crypto.randomUUID,locks:typeof w.navigator.locks};
  const samples=[],seen=new Set();
  watch=setInterval(()=>{try{
    for(const el of w.document.querySelectorAll('.ui-toast,[role="alert"],[role="status"],[data-test="designer-draft-notice"]')){
     const text=(el.textContent||'').trim(), key=text+'|'+el.className;
     if(!text||seen.has(key))continue;
     const rect=el.getBoundingClientRect(), style=w.getComputedStyle(el);
     seen.add(key);samples.push({at:Date.now(),text,cls:el.className,test:el.getAttribute('data-test'),code:el.getAttribute('data-code'),visible:rect.width>0&&rect.height>0&&style.display!=='none'&&style.visibility!=='hidden'});
    }
  }catch(e){}},25);
  const injection=await(await fetch('/harness/inject',{method:'POST'})).json();
  const clickedAt=Date.now();button.click();await sleep(6500);
  const beforeRetry=await(await fetch('/harness/probe')).json();
  await report({kind:'refusal',flow,mode,env,injection,clickedAt,samples:[...samples],body:w.document.body.innerText.slice(0,16000),beforeRetry});
  // The same cell's correction and retry, not a repetition of the refusal scenario.
  const correction=await(await fetch('/harness/correct',{method:'POST'})).json();
  const reloadedAt=Date.now();w.location.reload();button=await ready();button.click();await sleep(5000);
  await report({kind:'retry',flow,mode,correction,reloadedAt,samples:[...samples],diagnostics:typeof w.__flowgateRunDiagnostics==='function'?w.__flowgateRunDiagnostics():null,body:w.document.body.innerText.slice(0,16000)});
 }catch(e){await report({kind:'error',error:String(e&&e.stack||e)});}
 finally{if(watch)clearInterval(watch);if(w)w.close();await report({kind:'done'});}
})();</script>'''

class Front(w7.Front):
    flow = ''
    base = ''
    token = ''
    db = None
    variant = ''
    armed = False
    def snapshot(self):
        with sqlite3.connect(self.db) as con:
            con.row_factory=sqlite3.Row
            out={}
            for name,col in [('graph_runs','group_id'),('graph_run_command_requests','group_id'),('graph_revisions','group_id')]:
                out[name]=[dict(r) for r in con.execute(f'SELECT * FROM {name} WHERE {col}=?',(self.flow,))]
            return out
    def inject(self):
        if self.variant=='conflict':
            self.armed=True
            return {'kind':'confirm externally only after browser preparation POST arrives','at':int(time.time()*1000)}
        return {'at':int(time.time()*1000),'kind':'preseeded invalid draft'}
    def conflict(self):
        s,g=w7.http('GET',f'{self.base}/flowgate/api/v1/flows/{self.flow}/graph',token=self.token)
        data=w7._unwrap(g);draft=data['draft']
        s,r=w7.http('POST',f'{self.base}/flowgate/api/v1/flows/{self.flow}/graph/revisions',{'draft_version':draft['draft_version'],'base_revision_no':data['latest_revision']['revision_no']},self.token)
        assert s==201,(s,r)
        with self.lock:self.calls.append({'kind':'injection_confirm','flow':self.flow,'status':s,'at':int(time.time()*1000),'response':r})
    async def __call__(self,scope,receive,send):
        path=scope.get('path','')
        if path=='/harness/preparation.html':return await self._send(send,200,DRIVER.encode(),'text/html; charset=utf-8')
        if path=='/harness/inject':
            result=await asyncio.to_thread(self.inject)
            return await self._send(send,200,json.dumps(result).encode(),'application/json')
        if path=='/harness/correct':
            # conflict already has revision 2; issues draft is replaced by a valid
            # graph from revision 1, through the real graph API.
            if self.variant=='issues':
                result=await asyncio.to_thread(self.correct)
            else:result={'kind':'use externally confirmed revision 2'}
            return await self._send(send,200,json.dumps(result).encode(),'application/json')
        if path=='/harness/probe':return await self._send(send,200,json.dumps(self.snapshot()).encode(),'application/json')
        if scope['type']=='http' and scope['method']=='POST' and path==f'/flowgate/api/v1/flows/{self.flow}/graph/revisions' and self.armed:
            # Hold only the browser's already-created request. An SSE graph read
            # can no longer erase the intended stale request before it is sent.
            self.armed=False
            await asyncio.to_thread(self.conflict)
        # Record graph rereads with timestamp and response (including revision),
        # so the notice alone cannot satisfy Q589.
        if scope['type']=='http' and scope['method']=='GET' and path==f'/flowgate/api/v1/flows/{self.flow}/graph':
            entry={'kind':'graph_get','flow':self.flow,'at':int(time.time()*1000),'bytes':b''}
            async def logged(msg):
                if msg['type']=='http.response.start':entry['status']=msg['status']
                if msg['type']=='http.response.body':entry['bytes']+=msg.get('body',b'')
                await send(msg)
            try:return await self.app(scope,receive,logged)
            finally:
                entry['response']=json.loads(entry.pop('bytes') or b'{}')
                with self.lock:self.calls.append(entry)
        return await super().__call__(scope,receive,send)
    def correct(self):
        s,g=w7.http('GET',f'{self.base}/flowgate/api/v1/flows/{self.flow}/graph',token=self.token)
        data=w7._unwrap(g);source=data['latest_revision'];draft=data.get('draft') or {}
        body=graph_body(source,draft.get('draft_version',0))
        s,r=w7.http('PUT',f'{self.base}/flowgate/api/v1/flows/{self.flow}/graph/draft',body,self.token)
        assert s==200,(s,r)
        return {'status':s,'response':r}

def graph_body(graph,version):
    def assignee(n):
        if 'assignee' in n:return n['assignee']
        kind=n.get('assignee_kind')
        return {'kind':kind,'user_id':n.get('assignee_user_id'),'provider_id':n.get('assignee_provider_id')} if kind else None
    return {'draft_version':version,'nodes':[{'node_key':n['node_key'],'client_key':None,'node_type':n['node_type'],'doc_type':n.get('doc_type'),'palette_item_id':n.get('palette_item_id'),'label':n.get('label'),'assignee':assignee(n)} for n in graph['nodes']],
            'edges':[{'edge_id':e.get('edge_id'),'client_key':None,'source':e['source'],'target':e['target'],'condition':e.get('condition')} for e in graph['edges']]}

def main():
    scratch=Path(sys.argv[1]).resolve()
    if sys.argv[2]=='build':
        work=scratch/('preparation-build'+('-'+sys.argv[3] if len(sys.argv)>3 else ''));work.mkdir(parents=True,exist_ok=False)
        dist=w7.build_gui(work)
        (scratch/'preparation-current-build.txt').write_text(str(dist),encoding='utf-8')
        return 0
    variant,origin,mode,label=sys.argv[2:6]
    assert variant in ('conflict','issues') and origin in ('lan','local') and mode in ('all','next')
    work=scratch/('preparation-'+label);work.mkdir(parents=True,exist_ok=False)
    current=scratch/'preparation-current-build.txt'
    dist=Path(current.read_text(encoding='utf-8')) if current.exists() else scratch/'preparation-build'/'dist'
    assert (dist/'index.html').exists()
    # Only replace the test front class, never an application function.
    w7.Front=Front
    w7.CASES=[(origin,mode,'flowgate.v02.9501')]
    os.environ['FLOWGATE_WORKERS']='1'
    front,server,thread,port,db=w7.start_server(work,dist)
    front.flow=w7.CASES[0][2];front.db=db;front.base=f'http://127.0.0.1:{port}';front.variant=variant
    try:
        w7.seed(db,front.base)
        s,login=w7.http('POST',front.base+'/flowgate/auth/login',{'username':w7.USER,'password':w7.PASSWORD,'locale':'ko'})
        assert s==200
        front.token=login['access_token']
        before=front.snapshot()
        if variant=='issues':
            s,g=w7.http('GET',f'{front.base}/flowgate/api/v1/flows/{front.flow}/graph',token=front.token)
            draft=w7._unwrap(g)['draft'];body=graph_body(draft,draft['draft_version'])
            # A directed cycle is rejected by graph validation. Missing
            # assignees are legal person-work cards and are not invalid drafts.
            first,last=body['nodes'][0]['node_key'],body['nodes'][-1]['node_key']
            body['edges'].append({'edge_id':None,'client_key':'tmp_cycle','source':last,'target':first,'condition':None})
            s,r=w7.http('PUT',f'{front.base}/flowgate/api/v1/flows/{front.flow}/graph/draft',body,front.token)
            assert s==200,(s,r)
        target=f'http://{w7.lan_ipv4() if origin=="lan" else "127.0.0.1"}:{port}'
        w7.browse(work,target+'/harness/preparation.html?'+urlencode({'flow':front.flow,'mode':mode}),front,1,110)
        after=front.snapshot();reports=list(front.reports);calls=list(front.calls)
        refusal=next((r for r in reports if r.get('kind')=='refusal'),{})
        retry=next((r for r in reports if r.get('kind')=='retry'),{})
        clicked=refusal.get('clickedAt',0);reload_at=retry.get('reloadedAt',0)
        refusal_posts=[c for c in calls if c['kind']=='start' and clicked<=c['at']<reload_at]
        retry_posts=[c for c in calls if c['kind']=='start' and c['at']>=reload_at]
        revision_posts=[c for c in calls if c['kind']=='revision' and clicked<=c['at']<reload_at]
        reads=[c for c in calls if c['kind']=='graph_get' and clicked<=c['at']<reload_at]
        samples=refusal.get('samples',[])
        codes=('revision_conflict','draft_version_conflict','draft_not_found')
        notice=[s for s in samples if s['visible'] and any(c in s['text'] for c in codes)]
        toast=[s for s in samples if s['visible'] and 'ui-toast--danger' in s['cls']]
        probe=refusal.get('beforeRetry',{})
        env=refusal.get('env',{})
        checks={'driver_complete':bool(refusal and retry) and not any(r.get('kind')=='error' for r in reports),
                'origin':env.get('isSecureContext') is (origin=='local') and (origin!='lan' or (env.get('randomUUID')=='undefined' and env.get('locks')=='undefined')),
                'refusal_posts_zero':not refusal_posts,'refusal_runs_zero':probe.get('graph_runs')==[],
                'refusal_registry_zero':probe.get('graph_run_command_requests')==[],
                'reason_visible':bool(notice if variant=='conflict' else toast),
                'reread_after_refusal':any(r['at']>c['at'] for r in reads for c in revision_posts if c.get('status')==409) if variant=='conflict' else True,
                'retry_accepted':len(retry_posts)==1 and retry_posts[0]['status']==201,
                'retry_registry':len(after['graph_run_command_requests'])==1 and after['graph_run_command_requests'][0]['state']=='accepted',
                'retry_run_one':len(after['graph_runs'])==1}
        if variant=='conflict':checks['real_conflict']=any(c.get('status')==409 for c in revision_posts)
        else:checks['real_issues']=any(c.get('status') in (409,422) for c in revision_posts)
        summary={'variant':variant,'origin':origin,'mode':mode,'label':label,'checks':checks,'env':env,
                 'notice':notice,'toasts':toast,'refusal_revision_posts':revision_posts,'refusal_run_posts':refusal_posts,
                 'reread_at':[r['at'] for r in reads],'retry_posts':retry_posts,'runs':after['graph_runs'],
                 'registry':after['graph_run_command_requests'],'artifacts':str(work)}
        (work/'report.json').write_text(json.dumps({'summary':summary,'reports':reports,'calls':calls,'before':before,'after':after},ensure_ascii=False,indent=2),encoding='utf-8')
        print('PREPARATION_CELL '+json.dumps(summary,ensure_ascii=False),flush=True)
        return 0 if all(checks.values()) else 1
    finally:
        server.should_exit=True;thread.join(timeout=10)

if __name__=='__main__':
    try:code=main()
    except BaseException:traceback.print_exc();code=2
    sys.stdout.flush();os._exit(code)
