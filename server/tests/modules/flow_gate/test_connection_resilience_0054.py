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


def test_route_db_outage_is_503_and_other_errors_stay_500():
    """0060: a route that loses the DB after the cached auth check must answer 503, not 500."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.request_observability import RequestIdMiddleware, unhandled_error_response

    app = FastAPI()
    app.add_exception_handler(Exception, unhandled_error_response)
    app.add_middleware(RequestIdMiddleware)

    @app.get("/db-down")
    def db_down():
        raise OperationalError("connection lost")

    @app.get("/db-down-wrapped")
    def db_down_wrapped():
        try:
            raise OperationalError("connection lost")
        except OperationalError as exc:
            raise RuntimeError("load failed") from exc

    @app.get("/bug")
    def bug():
        raise RuntimeError("plain bug")

    client = TestClient(app, raise_server_exceptions=False)
    for path in ("/db-down", "/db-down-wrapped"):
        response = client.get(path, headers={"X-Request-ID": "outage-0060"})
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "1"
        assert response.headers["X-Request-ID"] == "outage-0060"
        assert "connection lost" not in response.text
    response = client.get("/bug", headers={"X-Request-ID": "bug-0060"})
    assert response.status_code == 500
    assert "Retry-After" not in response.headers
    assert response.headers["X-Request-ID"] == "bug-0060"


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


# Local route error handlers must not hide a DB outage from GET recovery.
def _read_failure(kind, wrapper=RuntimeError):
    if kind == "db":
        return OperationalError("private database connection details")
    if kind == "wrapped_db":
        error = wrapper("read failed")
        error.__cause__ = OperationalError("private database connection details")
        return error
    return wrapper("ordinary read failure")


def _recovery_client(router):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.request_observability import RequestIdMiddleware, unhandled_error_response

    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(Exception, unhandled_error_response)
    app.add_middleware(RequestIdMiddleware)
    return TestClient(app, raise_server_exceptions=False)


def _assert_read_failure(response, kind):
    assert response.status_code == (503 if kind in ("db", "wrapped_db") else 500)
    assert response.headers["X-Request-ID"] == "read-recovery-0060"
    if response.status_code == 503:
        assert response.headers["Retry-After"] == "1"
    else:
        assert "Retry-After" not in response.headers
    assert "private database" not in response.text


@pytest.mark.parametrize("endpoint", ["dashboard/summary", "notifications", "notifications/seen"])
@pytest.mark.parametrize("kind", ["db", "wrapped_db", "ordinary"])
@pytest.mark.parametrize("stage", ["watermark", "service"])
def test_notification_db_outages_reach_recovery(monkeypatch, endpoint, kind, stage):
    from modules.flow_gate.api.v1 import dashboard_routes as routes

    client = _recovery_client(routes.router)
    client.app.dependency_overrides[routes._dashboard_current_user] = lambda: {"user_id": "u1"}
    monkeypatch.setattr(routes.db_projects, "get_by_id", lambda _: {"project_id": "flowgate"})
    monkeypatch.setattr(routes, "has_permission", lambda *args: True)
    monkeypatch.setattr(routes.db_notification_seen, "get_last_seen", lambda *args: None)
    wrapper = routes.DashboardDataError if stage == "service" and endpoint != "notifications/seen" else RuntimeError
    failure = Mock(side_effect=_read_failure(kind, wrapper))
    if endpoint == "notifications/seen":
        monkeypatch.setattr(routes.db_notification_seen, "mark_seen", failure)
    elif stage == "watermark":
        monkeypatch.setattr(routes.db_notification_seen, "get_last_seen", failure)
    else:
        service = "get_dashboard_summary" if endpoint == "dashboard/summary" else "get_notification_feed"
        monkeypatch.setattr(routes, service, failure)
    method = client.post if endpoint.endswith("/seen") else client.get
    response = method("/api/v1/projects/flowgate/" + endpoint,
                      headers={"X-Request-ID": "read-recovery-0060"})
    _assert_read_failure(response, kind)
    assert failure.call_count == 1


@pytest.mark.parametrize("endpoint", ["body", "path", "outline", "download"])
@pytest.mark.parametrize("kind", ["db", "wrapped_db", "ordinary"])
def test_document_db_outages_reach_recovery(monkeypatch, endpoint, kind):
    from modules.flow_gate.api.v1 import document_routes as routes
    from modules.flow_gate.graph_workflow.services import card_document_ports

    doc_id = "flowgate.v02test.0060.0001-R"
    doc = {"doc_id": doc_id, "project_id": "flowgate", "revision_no": 1}
    monkeypatch.setattr(routes, "verify_bearer", lambda _: {"_is_user_jwt": True, "issued_to": "u1"})
    monkeypatch.setattr(routes.db_docs, "get_by_id", lambda _: doc)
    monkeypatch.setattr(routes, "has_permission", lambda *args: True)
    monkeypatch.setattr(routes, "_download_available", lambda _: True)
    monkeypatch.setattr(routes, "_compose_group_doc_ids", lambda *args: ("flowgate.v02test.0060", doc_id))
    failure = Mock(side_effect=_read_failure(kind))
    monkeypatch.setattr(card_document_ports, "card_edit", failure)
    paths = {
        "body": "/api/v1/document/" + doc_id,
        "path": "/api/v1/document/flowgate/branches/main/__ALL__/0060/0001-R",
        "outline": "/api/v1/document/" + doc_id + "/outline",
        "download": "/api/v1/document/" + doc_id + "/download",
    }
    response = _recovery_client(routes.router).get(paths[endpoint],
                      headers={"X-Request-ID": "read-recovery-0060"})
    _assert_read_failure(response, kind)
    assert failure.call_count == 1
