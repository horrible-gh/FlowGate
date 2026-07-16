"""Authentication session persistence and display metadata."""
from __future__ import annotations
import uuid
from modules.flow_gate.db.connection import get_store, now_iso

REVOKE_REASONS = {"logout", "remote", "revoke_others", "password_change", "reuse_detected", "admin"}

def parse_device_label(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    browser = next((label for needle, label in (
        ("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser/", "Samsung Internet"),
        ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari"),
    ) if needle in user_agent), None)
    operating_system = next((label for needle, label in (
        ("Windows NT", "Windows"), ("iPhone", "iPhone"), ("iPad", "iPad"),
        ("Android", "Android"), ("Mac OS X", "macOS"), ("Linux", "Linux"),
    ) if needle in user_agent), None)
    if browser and operating_system:
        return f"{browser} · {operating_system}"[:64]
    return (browser or operating_system)

def resolve_ip_display(request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        leftmost = forwarded.split(",", 1)[0].strip()
        if leftmost:
            return leftmost
    client = getattr(request, "client", None)
    return getattr(client, "host", None) if client else None

def create_session(user_id: str, device_label: str | None = None, ip_display: str | None = None) -> str:
    sid, now = str(uuid.uuid4()), now_iso()
    get_store()._execute(
        "INSERT INTO auth_sessions (session_id,user_id,created_at,last_used_at,device_label,ip_display) VALUES (?,?,?,?,?,?)",
        [sid, user_id, now, now, device_label[:64] if device_label else None, ip_display],
    )
    return sid

def create_request_session(user_id: str, request) -> str:
    return create_session(user_id, parse_device_label(request.headers.get("User-Agent")), resolve_ip_display(request))

def get_session(session_id: str) -> dict | None:
    return get_store()._fetch_one("SELECT * FROM auth_sessions WHERE session_id = ?", [session_id])

def is_session_active(session_id: str) -> bool:
    row = get_session(session_id)
    return bool(row and row.get("revoked_at") is None)

def list_active_sessions(user_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT session_id,device_label,ip_display,created_at,last_used_at FROM auth_sessions WHERE user_id=? AND revoked_at IS NULL ORDER BY last_used_at DESC",
        [user_id],
    )

def touch_session(session_id: str) -> None:
    get_store()._execute("UPDATE auth_sessions SET last_used_at=? WHERE session_id=? AND revoked_at IS NULL", [now_iso(), session_id])

def revoke_session(session_id: str, user_id: str, reason: str) -> bool:
    if reason not in REVOKE_REASONS:
        raise ValueError("invalid revoke reason")
    store, now = get_store(), now_iso()
    with store.transaction():
        row = store._fetch_one("SELECT session_id FROM auth_sessions WHERE session_id=? AND user_id=? AND revoked_at IS NULL", [session_id, user_id])
        if not row:
            return False
        store._execute("UPDATE auth_sessions SET revoked_at=?,revoke_reason=? WHERE session_id=? AND user_id=? AND revoked_at IS NULL", [now, reason, session_id, user_id])
        store._execute("UPDATE refresh_tokens SET revoked_at=? WHERE session_id=? AND revoked_at IS NULL", [now, session_id])
    return True

def revoke_other_sessions(user_id: str, current_sid: str, reason: str) -> int:
    if reason not in REVOKE_REASONS:
        raise ValueError("invalid revoke reason")
    store, now = get_store(), now_iso()
    with store.transaction():
        rows = store._fetch_all("SELECT session_id FROM auth_sessions WHERE user_id=? AND session_id<>? AND revoked_at IS NULL", [user_id, current_sid])
        store._execute("UPDATE refresh_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL AND (session_id IS NULL OR session_id<>?)", [now, user_id, current_sid])
        store._execute("UPDATE auth_sessions SET revoked_at=?,revoke_reason=? WHERE user_id=? AND session_id<>? AND revoked_at IS NULL", [now, reason, user_id, current_sid])
    return len(rows)

def revoke_all_sessions(user_id: str, reason: str) -> None:
    if reason not in REVOKE_REASONS:
        raise ValueError("invalid revoke reason")
    store, now = get_store(), now_iso()
    with store.transaction():
        store._execute("UPDATE refresh_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", [now, user_id])
        store._execute("UPDATE auth_sessions SET revoked_at=?,revoke_reason=? WHERE user_id=? AND revoked_at IS NULL", [now, reason, user_id])
