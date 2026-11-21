"""Independently selectable scenarios; shared fixtures remain in test_card_document_l3_cost_0127.py."""
from __future__ import annotations

from test_card_document_l3_cost_0127 import (
    External,
    FLOW,
    PROJECT,
    USER,
    connection,
    datetime,
    emit,
    external,
    idempotency,
    json,
    kind,
    l3,
    os,
    projects,
    pytest,
    sqlite3,
    time,
    unquote,
    urlsplit,
    uuid,
)

@pytest.mark.parametrize('engine',['sqlite','mysql','postgres'])
@pytest.mark.parametrize("size", [10000])
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
