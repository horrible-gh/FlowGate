"""0010-L 5.10: real lookup cost, 200 samples, exact 191st p95, no budget relaxation.

External DBs require explicitly provisioned isolated databases. No application DB
configuration is used. Temporary tables shadow existing names on that connection.
"""
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit, unquote

import pytest

from l3verify_support_0127 import l3, kind, PROJECT, FLOW, USER, emit
from flowgate.card_document_edit import idempotency
from modules.flow_gate.db import projects, connection


class External:
    def __init__(self,engine,conn):self.engine,self.conn,self.candidates=engine,conn,0
    def _fetch_all(self,sql,params):
        with self.conn.cursor() as cur:
            cur.execute(sql.replace('?','%s'),params)
            names=[x[0] for x in cur.description]
            result=[dict(zip(names,row)) for row in cur.fetchall()]
            self.candidates+=len(result)
            return result


def external(engine):
    name='L3VERIFY_'+engine.upper()+'_DSN'
    dsn=os.environ.get(name)
    if not dsn:
        pytest.skip(name+' missing: isolated engine not provisioned; not a PASS')
    parsed=urlsplit(dsn)
    database=unquote(parsed.path.lstrip('/'))
    assert database.startswith('l3verify_') and parsed.hostname in ('127.0.0.1','localhost','::1'), 'non-isolated DB refused'
    kwargs={'host':parsed.hostname,'port':parsed.port or (3306 if engine=='mysql' else 5432),
            'user':unquote(parsed.username or ''),'password':unquote(parsed.password or '')}
    if engine=='mysql':
        driver=pytest.importorskip('pymysql',reason='isolated MySQL driver unavailable')
        conn=driver.connect(database=database,**kwargs)
    else:
        driver=pytest.importorskip('psycopg',reason='isolated PostgreSQL driver unavailable')
        conn=driver.connect(dbname=database,**kwargs)
    with conn.cursor() as cur:
        cur.execute('CREATE TEMPORARY TABLE workflow_events (id BIGINT PRIMARY KEY,event_type VARCHAR(64),'
                    'project_id VARCHAR(128),group_id VARCHAR(128),actor_user_id VARCHAR(128),metadata TEXT,created_at VARCHAR(64))')
        # The relevant product indexes, not a hypothetical idempotency-key index.
        cur.execute('CREATE INDEX idx_workflow_events_project_created ON workflow_events(project_id,created_at)')
        cur.execute('CREATE INDEX idx_workflow_events_type_created ON workflow_events(event_type,created_at)')
        cur.execute('CREATE INDEX idx_workflow_events_group_created ON workflow_events(group_id,created_at)')
    conn.commit()
    return External(engine,conn)


@pytest.mark.parametrize('engine',['sqlite','mysql','postgres'])
@pytest.mark.parametrize('size',[1000,10000,100000])
def test_lookup_200_samples_per_cardinality(l3,monkeypatch,engine,size):
    other='l3verify-other-project'
    projects.create({'project_id':other,'project_name':other})
    now=connection.now_iso()
    keys=['chat-api:'+str(i)*64 for i in (1,2,3)]
    duplicates=['chat-cli:'+str(i)*64 for i in (4,5,6)]
    positions={}
    for i,base in enumerate((0,size//2,size-3)):
        positions[base]=keys[i]
        positions[base+1]=positions[base+2]=duplicates[i]
    batch=[]
    for project in (PROJECT,other):
        for i in range(size):
            key=positions.get(i,('chat-api:' if i%3==0 else 'chat-cli:')+f'{i:064x}' if i%3!=2 else str(uuid.UUID(int=i)))
            meta=idempotency.dumps({'idem_key':key,'doc_id':l3.doc_id,'flow_id':FLOW,'node_key':l3.node['node_key'],
                'slot':l3.slot,'origin':'chat','actor_kind':'person_delegated','revision_no':8,'base_revision_no':7,
                'content_sha256':'a'*64,'updated_at':now,'schema':1})
            batch.append(('doc_edited',project,FLOW,USER,meta,now))
    if engine=='sqlite':
        with sqlite3.connect(l3.db_path) as conn:
            conn.executemany('INSERT INTO workflow_events (event_type,project_id,group_id,actor_user_id,metadata,created_at) VALUES (?,?,?,?,?,?)',batch)
        target=l3.db
        original=target._fetch_all
        counts=[0]
        def counted(sql,params):
            rows=original(sql,params)
            counts[0]+=len(rows)
            return rows
        monkeypatch.setattr(target,'_fetch_all',counted)
        indexes=original("SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='workflow_events'",[])
    else:
        target=external(engine)
        with target.conn.cursor() as cur:
            cur.executemany('INSERT INTO workflow_events (id,event_type,project_id,group_id,actor_user_id,metadata,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                            [(i+1,*row) for i,row in enumerate(batch)])
        target.conn.commit()
        indexes=['idx_workflow_events_project_created','idx_workflow_events_type_created','idx_workflow_events_group_created']
    monkeypatch.setattr(idempotency,'get_store',lambda:target)
    metrics=[]
    try:
        for cardinality,queries in ((0,['l3verify-missing']), (1,keys), (2,duplicates)):
            samples=[]
            previous=counts[0] if engine=='sqlite' else target.candidates
            for i in range(200):
                start=time.perf_counter_ns()
                rows=idempotency.lookup(PROJECT,queries[i%len(queries)])
                samples.append((time.perf_counter_ns()-start)/1e6)
                assert len(rows)==cardinality
            samples.sort()
            assert len(samples)==200, 'zero samples are invalid'
            metrics.append({'matches':cardinality,'samples':200,'p50_ms':samples[100],
                'p95_ms':samples[190],'p99_ms':samples[198],'max_ms':samples[-1],
                'candidate_rows':(counts[0] if engine=='sqlite' else target.candidates)-previous})
        emit(l3,'lookup-'+engine+'-'+str(size),{'engine':engine,'events_per_project':size,'projects':2,
            'indexes':indexes,'metrics':metrics,'budget_ms':50})
        assert all(m['p95_ms']<=50 for m in metrics), (
            '0010-L 5.10 budget exceeded; 0011-WP T#8 requires reporting only, no DB card; do not change 50ms',metrics)
    finally:
        if engine!='sqlite':target.conn.close()
