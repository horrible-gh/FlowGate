"""Taskbar style API: partial writes, validation, user isolation and migration round trip."""
import sqlite3
from pathlib import Path
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from modules.flow_gate.api.v1 import ui_settings_routes
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import ui_settings_service as uss
from modules.flow_gate.db import user_designer_theme as theme

FIELD = uss.TASKBAR_STYLE_FIELD

@pytest.fixture
def settings(monkeypatch):
    class Memory:
        def __init__(self, row): self.row = row; self.writes = []
        def get(self, user): return self.row
        def upsert(self, user, **kw):
            self.writes.append(kw['columns']); self.row.update(kw['columns'])
    retention = Memory({uss.RETENTION_FIELD: 120, 'updated_at': 'before'})
    layout = Memory({'left_width': 410, 'right_width': 520, 'bottom_height': 233})
    rows = {}
    monkeypatch.setattr(uss, 'ui_settings_store', retention)
    monkeypatch.setattr(uss, 'designer_layout_store', layout)
    monkeypatch.setattr(theme, 'get', lambda user: rows.get(user))
    monkeypatch.setattr(theme, 'save', lambda user, style, at: rows.update({user: {'taskbar_style': style}}))
    return rows, retention, layout

def client(user='u1'):
    app = FastAPI()
    app.include_router(ui_settings_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {'user_id': user, 'username': user}
    return TestClient(app)

def test_round_trip_preserves_other_settings_and_user(settings):
    rows, retention, layout = settings
    c = client()
    before = c.get('/me/ui-settings').json()['settings']
    response = c.patch('/me/ui-settings', json={FIELD: 'flowgate'})
    assert response.status_code == 200
    assert c.get('/me/ui-settings').json()['settings'] == before
    assert response.json()['defaults'][FIELD] == 'flowgate'
    assert response.json()['domain'][FIELD] == ['flowgate']
    assert rows == {'u1': {'taskbar_style': 'flowgate'}}
    assert client('u2').get('/me/ui-settings').json()['settings'][FIELD] == 'flowgate'
    assert 'u2' not in rows
    assert not retention.writes and not layout.writes
    c.patch('/me/ui-settings', json={uss.DESIGNER_LEFT_WIDTH_FIELD: 350})
    c.patch('/me/ui-settings', json={uss.RETENTION_FIELD: 60})
    assert rows == {'u1': {'taskbar_style': 'flowgate'}}

@pytest.mark.parametrize('value', [None, '', 'future', 1, True, [], {}])
def test_invalid_incoming_value_rejects_entire_patch(settings, value):
    rows, retention, layout = settings
    response = client().patch('/me/ui-settings', json={FIELD: value, uss.DESIGNER_LEFT_WIDTH_FIELD: 350})
    assert response.status_code == 422
    assert response.json()['error']['field'] == FIELD
    assert rows == {} and not retention.writes and not layout.writes

@pytest.mark.parametrize('value', [None, 'future', 1, True, [], {}])
def test_stored_unknown_value_defaults_without_repair_write(settings, value):
    rows, _, _ = settings
    rows['u1'] = {'taskbar_style': value}
    assert client().get('/me/ui-settings').json()['settings'][FIELD] == 'flowgate'
    assert rows['u1']['taskbar_style'] == value

def test_database_round_trip_and_isolation(monkeypatch):
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE users (user_id TEXT PRIMARY KEY)')
    migration = Path(__file__).resolve().parents[3] / 'sql/migrations'
    name = '207_user_designer_theme.sql'
    for dialect in ('sqlite', 'postgres', 'mysql'):
        assert (migration / dialect / name).is_file()
    db.executescript((migration / 'sqlite' / name).read_text())
    class Store:
        def _fetch_one(self, sql, params):
            row = db.execute(sql, params).fetchone()
            return dict(row) if row else None
        def _execute(self, sql, params): db.execute(sql, params)
    monkeypatch.setattr(theme, 'get_store', lambda: Store())
    assert theme.get('u1') is None
    theme.save('u1', 'flowgate', 'first')
    theme.save('u2', 'future', 'second')
    theme.save('u1', 'flowgate', 'third')
    assert theme.get('u1') == {'taskbar_style': 'flowgate'}
    assert theme.get('u2') == {'taskbar_style': 'future'}
    row = db.execute("SELECT * FROM user_designer_theme WHERE user_id = 'u1'").fetchone()
    assert row['created_at'] == 'first' and row['updated_at'] == 'third'
    db.close()
