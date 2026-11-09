"""Deterministic local CLI fixture for 0156 full application E2E; never calls a model.

Only connects to the loopback API base of the isolated application. All files go
to the token's scratch. The fixture submits using the real minted token and the
registration identity from the server's own prompt, never a database shortcut.
"""
from __future__ import annotations
import json, os, re, sys, time, urllib.request, urllib.error
from pathlib import Path
from urllib.parse import urlsplit
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')

def main():
    prompt=sys.stdin.read()
    root=Path(os.environ['FLOWGATE_SCRATCH']).resolve();root.mkdir(parents=True,exist_ok=True)
    (root/'fixture-prompt.txt').write_text(prompt,encoding='utf-8')
    base=os.environ['FLOWGATE_API_BASE'].rstrip('/')
    assert urlsplit(base).hostname in ('127.0.0.1','localhost'),base
    decoder=json.JSONDecoder();candidates=[]
    for match in re.finditer(r'\{',prompt):
        try:value,_=decoder.raw_decode(prompt[match.start():])
        except ValueError:continue
        if isinstance(value,dict) and value.get('action') in ('new','edit') and value.get('project')=='flowgate' and (value.get('doc_id') or value.get('prev_doc_id')):
            candidates.append(value)
    assert candidates,'No registration template in server prompt'
    template=candidates[-1]
    payload={k:v for k,v in template.items() if k in ('action','project','module','group_name','doc_id','prev_doc_id','doc_type','edit_reason','expected_revision','rejection_id','review_id','rejection_review_id')}
    # TR's scope report is mandatory even when this isolated project has no source.
    payload['content']='# 0156 deterministic fixture\n\nThis is the scripted result of an isolated test.\n\n## 변경 파일\n\n없음\n'
    if payload.get('edit_reason')=='rejected':payload['rejection_response']='The scripted fixture applied the review feedback.'
    data=json.dumps(payload,ensure_ascii=False).encode('utf-8')
    req=urllib.request.Request(base+'/inbox',data=data,headers={'Authorization':'Bearer '+os.environ['FLOWGATE_TOKEN'],'Content-Type':'application/json'})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req,timeout=30) as response:status=response.status;result=json.load(response)
    except urllib.error.HTTPError as e:status=e.code;result=json.loads(e.read())
    (root/'fixture-result.json').write_text(json.dumps({'status':status,'result':result},ensure_ascii=False,indent=2),encoding='utf-8')
    print('scripted_registration',status,flush=True)
    return 0 if 200<=status<300 else 1

if __name__=='__main__':
    raise SystemExit(main())
