from types import SimpleNamespace
import jwt
from modules.flow_gate.auth import jwt_service
from modules.flow_gate.auth.session_store import parse_device_label,resolve_ip_display

def test_device_label_priority():
    assert parse_device_label("Mozilla/5.0 Windows NT Edg/120 Chrome/120 Safari/537")=="Edge · Windows"
    assert parse_device_label("Mozilla/5.0 Android SamsungBrowser/22 Chrome/110 Safari/537")=="Samsung Internet · Android"
    assert parse_device_label("Mozilla/5.0 iPhone Safari/605")=="Safari · iPhone"
    assert parse_device_label("curl/8.0") is None

def test_ip_display_prefers_leftmost_forwarded():
    request=SimpleNamespace(headers={"X-Forwarded-For":" 203.0.113.1, 10.0.0.1"},client=SimpleNamespace(host="127.0.0.1"))
    assert resolve_ip_display(request)=="203.0.113.1"

def test_sid_is_added_to_access_and_refresh(monkeypatch):
    monkeypatch.setattr(jwt_service,"SECRET_KEY","test-secret")
    access,_=jwt_service.create_access_token("u1","user",[],sid="s1")
    refresh,_,_=jwt_service.create_refresh_token("u1",sid="s1")
    assert jwt.decode(access,"test-secret",algorithms=["HS256"])["sid"]=="s1"
    assert jwt.decode(refresh,"test-secret",algorithms=["HS256"])["sid"]=="s1"
