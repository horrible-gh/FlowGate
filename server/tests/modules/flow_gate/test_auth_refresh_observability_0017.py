"""Refresh failure log + request id + access-log token redaction (flowgate.v02gui.0017, NR 0003 B-R8).

* every refused /auth/refresh writes one WARNING with a reason code, ids and the request id,
  and never the refresh token itself; status and detail stay what they were;
* every HTTP response carries X-Request-ID (well-formed incoming ids are kept);
* the uvicorn access log never prints the value of ?token= (the SSE stream's access token).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

_SERVER_DIR = Path(__file__).resolve().parents[3]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api import request_observability as obs  # noqa: E402
from modules.flow_gate.auth import auth_api  # noqa: E402

SECRET = "eyJhbGciOiJIUzI1NiJ9.secret-refresh-token-value.sig"
LOGGER = "flowgate.auth.refresh"


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="10.0.0.7"))


def _refresh(token=SECRET):
    return auth_api.refresh(_request(), auth_api.RefreshRequest(refresh_token=token))


def _only_record(caplog):
    records = [r for r in caplog.records if r.name == LOGGER]
    assert len(records) == 1
    rec = records[0]
    assert rec.levelno == logging.WARNING
    text = rec.getMessage()
    assert SECRET not in text and "secret-refresh-token-value" not in text
    return text


def test_malformed_token_is_logged_with_reason_and_keeps_the_401_detail(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    with pytest.raises(HTTPException) as exc:
        _refresh("not-a-jwt")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid authentication credentials"
    text = _only_record(caplog)
    assert "reason=token_invalid" in text and "client=10.0.0.7" in text


def test_reuse_detection_is_logged_with_user_and_session(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    revoked = []
    monkeypatch.setattr(auth_api, "decode_token", lambda t: {"type": "refresh", "jti": "j1", "sub": "u1"})
    monkeypatch.setattr(auth_api, "get_refresh_token",
                        lambda jti: {"revoked_at": "2026-11-14T00:00:00", "replaced_by": "j2", "session_id": "s1"})
    monkeypatch.setattr(auth_api, "get_session", lambda sid: {"revoked_at": None})
    monkeypatch.setattr(auth_api, "revoke_all_sessions", lambda uid, reason: revoked.append((uid, reason)))
    with pytest.raises(HTTPException) as exc:
        _refresh()
    assert exc.value.status_code == 401
    assert exc.value.detail == "Token reuse detected. All sessions revoked."
    assert revoked == [("u1", "reuse_detected")]
    text = _only_record(caplog)
    assert "reason=reuse_detected" in text and "user_id=u1" in text and "session_id=s1" in text


def test_idle_session_is_logged_as_session_idle(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    monkeypatch.setattr(auth_api, "decode_token", lambda t: {"type": "refresh", "jti": "j1", "sub": "u1"})
    monkeypatch.setattr(auth_api, "get_refresh_token",
                        lambda jti: {"revoked_at": None, "session_id": "s9", "expires_at": "2999-01-01T00:00:00+00:00"})
    monkeypatch.setattr(auth_api.db_users, "get_by_id", lambda uid: {"is_active": 1, "username": "u"})
    monkeypatch.setattr(auth_api, "get_session", lambda sid: {"revoked_at": None, "last_used_at": "2000-01-01T00:00:00"})
    monkeypatch.setattr(auth_api, "revoke_session", lambda sid, uid, reason: None)
    with pytest.raises(HTTPException) as exc:
        _refresh()
    assert exc.value.detail == "session_idle"
    assert "reason=session_idle" in _only_record(caplog)


def _app():
    app = FastAPI()

    @app.get("/probe")
    def probe():
        return {"request_id": obs.current_request_id()}

    app.add_middleware(obs.RequestIdMiddleware)
    return app


def test_request_id_is_generated_echoed_and_visible_to_the_handler():
    client = TestClient(_app())
    resp = client.get("/probe")
    rid = resp.headers["x-request-id"]
    assert len(rid) == 32 and resp.json() == {"request_id": rid}
    assert client.get("/probe").headers["x-request-id"] != rid
    assert obs.current_request_id() is None


def test_well_formed_incoming_request_id_is_kept_and_garbage_is_replaced():
    client = TestClient(_app())
    assert client.get("/probe", headers={"X-Request-ID": "proxy-abc.123"}).headers["x-request-id"] == "proxy-abc.123"
    replaced = client.get("/probe", headers={"X-Request-ID": "bad id\twith spaces"}).headers["x-request-id"]
    assert replaced != "bad id\twith spaces" and len(replaced) == 32


def test_access_log_redacts_the_sse_token_value():
    logger = logging.getLogger("test.uvicorn.access.0017")
    flt = obs.install_access_log_redaction(logger.name)
    assert obs.install_access_log_redaction(logger.name) is flt  # idempotent
    assert len(logger.filters) == 1
    record = logger.makeRecord(
        logger.name, logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", f"/flowgate/api/v1/events/stream?token={SECRET}&project=p1", "1.1", 200),
        None)
    assert flt.filter(record) is True
    line = record.getMessage()
    assert SECRET not in line
    assert "?token=[redacted]&project=p1" in line


def test_redaction_leaves_other_parameters_alone():
    assert obs.redact_secrets("/x?mytoken=1&tokenize=2&a=b") == "/x?mytoken=1&tokenize=2&a=b"
    assert obs.redact_secrets("/x?a=1&refresh_token=zz&access_token=yy") == \
        "/x?a=1&refresh_token=[redacted]&access_token=[redacted]"


def test_the_app_installs_request_id_outermost_and_access_log_redaction():
    """Source-level: importing routers.main needs the full settings env, which other tests in the
    same session rewrite; the live middleware order is pinned by test_card_document_edit_composition_0127."""
    src = (_SERVER_DIR / "routers" / "main.py").read_text(encoding="utf-8")
    cors = src.index("CORSMiddleware,\n")
    rid = src.index("app.add_middleware(RequestIdMiddleware)")
    assert rid > cors  # added after CORS -> outermost layer
    assert src.rfind("app.add_middleware(") == rid  # nothing is stacked outside it
    assert "install_access_log_redaction()" in src[rid:]
    assert '"X-Request-ID"' in src[cors:rid]  # exposed to the browser client
