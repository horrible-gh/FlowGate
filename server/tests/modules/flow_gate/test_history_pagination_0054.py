"""Keyset pagination preserves equal-timestamp rows and excludes other chats."""
import sqlite3
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from modules.flow_gate.db import ai_invoke_runs as db
from modules.flow_gate.services.ai_invoke import diagnostics as svc

@pytest.fixture
def history(monkeypatch):
    conn=sqlite3.connect(':memory:')
    conn.row_factory=sqlite3.Row
    conn.execute('CREATE TABLE ai_invoke_runs(run_id TEXT, started_at TEXT, group_id TEXT, project_id TEXT, doc_ref TEXT, mode TEXT)')
    for n in range(205):
        conn.execute('INSERT INTO ai_invoke_runs VALUES(?,?,?,?,?,?)',(f'r{n:04}', '2026-01-01', 'p.m.0001','p','chat','cli'))
    conn.execute('INSERT INTO ai_invoke_runs VALUES(?,?,?,?,?,?)',('foreign','2027-01-01','p.m.0001','p','other','cli'))
    monkeypatch.setattr(db,'get_store',lambda:SimpleNamespace(_fetch_all=lambda sql,params:[dict(r) for r in conn.execute(sql,params)]))
    monkeypatch.setattr(db,'_row_to_payload',lambda r:r)
    monkeypatch.setattr(svc.db_projects,'get_by_id',lambda _: {'project_id':'p'})
    monkeypatch.setattr(svc,'list_live_runs',lambda **kw:[])
    yield conn
    conn.close()

def test_all_pages_no_duplicates_and_new_rows_do_not_shift_cursor(history):
    seen=[]; cursor=None
    while True:
        page=svc.list_runs(group_id='p.m.0001',doc_ref='chat',limit=20,cursor=cursor)
        seen.extend(i['run_id'] for i in page['items'])
        if cursor is None:
            history.execute('INSERT INTO ai_invoke_runs VALUES(?,?,?,?,?,?)',('new','2027-01-01','p.m.0001','p','chat','cli'))
        if not page['has_more']: break
        cursor=page['next_cursor']
    assert len(seen)==len(set(seen))==205
    assert 'foreign' not in seen and 'new' not in seen

def test_live_and_stored_same_run_deduplicate(history,monkeypatch):
    monkeypatch.setattr(svc,'list_live_runs',lambda **kw:[{'run_id':'r0204','started_at':'2026-01-01','doc_ref':'chat','status':'running'}])
    page=svc.list_runs(group_id='p.m.0001',doc_ref='chat',limit=20)
    assert len(page['items'])==20
    assert page['items'][0]['status']=='running'
    assert len({i['run_id'] for i in page['items']})==20

def test_bad_cursor_is_not_silently_first_page(history):
    with pytest.raises(HTTPException) as error:
        svc.list_runs(group_id='p.m.0001',doc_ref='chat',cursor='invalid')
    assert error.value.status_code==422
