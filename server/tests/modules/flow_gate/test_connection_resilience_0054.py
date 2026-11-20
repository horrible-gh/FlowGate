"""0054: dead idle pools, authentication outages and independent token allocation."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from psycopg2 import OperationalError
from modules.flow_gate.db.resilience import _checkout
from modules.flow_gate.auth import middleware
from modules.flow_gate.services import token_service, token_scratch

class Conn:
    closed = False
    autocommit = False
    def __init__(self, dead=False):
        self.dead = dead
        self.rollbacks = 0
        self.queries = []
    def cursor(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql):
        self.queries.append(sql)
        if self.dead: raise OperationalError("connection lost")
    def rollback(self): self.rollbacks += 1

def test_dead_pool_is_drained_before_application_statement():
    connections = [Conn(True) for _ in range(20)] + [Conn()]
    discarded = []
    db = SimpleNamespace(pool_max=24, pool=SimpleNamespace(getconn=Mock(side_effect=connections)),
        _is_stale=lambda c: False, _discard=discarded.append,
        _meta_lock=threading.Lock(), _conn_meta={})
    assert _checkout(db) is connections[-1]
    assert discarded == connections[:-1]
    assert connections[-1].rollbacks == 1
    assert all(c.queries == ["SELECT 1"] for c in connections)

def test_persistent_outage_is_bounded_and_every_connection_discarded():
    discarded=[]
    db=SimpleNamespace(pool_max=2,pool=SimpleNamespace(getconn=lambda:Conn(True)),
        _is_stale=lambda c:False,_discard=discarded.append)
    with pytest.raises(OperationalError): _checkout(db)
    assert len(discarded)==3

def test_auth_outage_503_even_in_testing(monkeypatch):
    monkeypatch.setattr(middleware, 'decode_token', lambda _: {'type':'access','jti':'id'})
    monkeypatch.setattr(middleware, 'is_blacklisted', Mock(side_effect=OperationalError('down')))
    from modules.flow_gate.auth import auth_preamble
    monkeypatch.setattr(auth_preamble,'prefetch',lambda *args:None)
    with pytest.raises(HTTPException) as error: middleware.verify_token('test')
    assert error.value.status_code==503

def test_real_auth_revocation_stays_401(monkeypatch):
    monkeypatch.setattr(middleware, 'decode_token', lambda _: {'type':'access','jti':'id'})
    monkeypatch.setattr(middleware, 'is_blacklisted', lambda _:True)
    from modules.flow_gate.auth import auth_preamble
    monkeypatch.setattr(auth_preamble,'prefetch',lambda *args:None)
    with pytest.raises(HTTPException) as error: middleware.verify_token('test')
    assert error.value.status_code==401

def test_db_failure_logs_request_id_without_exception_secrets(caplog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.request_observability import RequestIdMiddleware
    from modules.flow_gate.db.resilience import auth_unavailable

    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/probe")
    def probe():
        db = SimpleNamespace(pool_max=0, pool=SimpleNamespace(getconn=lambda: Conn(True)),
            _is_stale=lambda c: False, _discard=lambda c: None)
        try:
            _checkout(db)
        except OperationalError:
            raise auth_unavailable(OperationalError("secret SQL parameter"))

    response = TestClient(app).get("/probe", headers={"X-Request-ID": "recovery-0060"})
    assert response.status_code == 503
    lines = [record.getMessage() for record in caplog.records if "database" in record.getMessage()]
    assert len(lines) == 2
    assert all("request_id=recovery-0060" in line and "type=OperationalError" in line for line in lines)
    assert "secret SQL parameter" not in caplog.text


def test_parallel_token_ids_and_owned_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(token_scratch.storage_paths,'get_storage_root',lambda:tmp_path)
    monkeypatch.setattr(token_scratch.db_projects,'get_by_id',lambda _: {'project_name':'Project'})
    def allocate(_):
        token_id=token_service._next_token_id()
        token_scratch.create('p',token_id,tmp_path/'work'/'Project'/token_id)
        return token_id
    with ThreadPoolExecutor(max_workers=8) as executor:
        ids=list(executor.map(allocate,range(32)))
    assert len(set(ids))==32
    assert all((tmp_path/'work'/'Project'/i/token_scratch.MANIFEST_NAME).exists() for i in ids)
