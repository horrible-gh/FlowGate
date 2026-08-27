"""System settings service — K/V read/write + allowlist validation (D018 r1 §A)."""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone

from modules.flow_gate.db import system_settings as _db
from modules.flow_gate.storage.paths import get_storage_root

_JST = timezone(timedelta(hours=9))

ALLOWLIST: set[str] = {
    "storage_root",
    "log_retention_days",
    "log_level",
    "jwt_expiry_minutes",
    "refresh_token_expiry_days",
    "mail_smtp_host",
    "mail_smtp_port",
    "mail_from",
    "file_log",
    "audit",
    "rate_limit_login",
    "rate_limit_upload",
    "cors_origin",
    "totp",
    "token_blacklist",
    "source_mode",
}

_VALUE_TYPES: dict[str, str] = {
    "storage_root": "string",
    "log_retention_days": "integer",
    "log_level": "string",
    "jwt_expiry_minutes": "integer",
    "refresh_token_expiry_days": "integer",
    "mail_smtp_host": "string",
    "mail_smtp_port": "integer",
    "mail_from": "string",
    "file_log": "boolean",
    "audit": "boolean",
    "rate_limit_login": "integer",
    "rate_limit_upload": "integer",
    "cors_origin": "string",
    "totp": "boolean",
    "token_blacklist": "boolean",
    "source_mode": "string",
}


def get_effective_storage_root() -> str:
    return str(get_storage_root())


def _apply_effective_defaults(row: dict | None) -> dict | None:
    if row is None:
        return None
    if row.get("setting_key") == "storage_root" and not (row.get("setting_value") or "").strip():
        return {**row, "setting_value": get_effective_storage_root()}
    return row



def get_all() -> list[dict]:
    rows = [
        row
        for row in (_apply_effective_defaults(row) for row in _db.list_settings())
        if row is not None
    ]
    if not any(row.get("setting_key") == "storage_root" for row in rows):
        rows.append({
            "setting_key": "storage_root",
            "setting_value": get_effective_storage_root(),
            "value_type": "string",
            "description": "Default storage root path",
            "updated_at": "",
            "updated_by": None,
        })
    return rows



def get_one(key: str) -> dict | None:
    row = _apply_effective_defaults(_db.get(key))
    if row is None and key == "storage_root":
        return {
            "setting_key": "storage_root",
            "setting_value": get_effective_storage_root(),
            "value_type": "string",
            "description": "Default storage root path",
            "updated_at": "",
            "updated_by": None,
        }
    return row



def set_values(updates: dict[str, str | None], updated_by: str | None = None) -> list[dict]:
    """Update multiple K/V pairs. Raises ValueError if any key is not in the allowlist."""
    invalid = set(updates.keys()) - ALLOWLIST
    if invalid:
        raise ValueError(f"Disallowed setting key(s): {', '.join(sorted(invalid))}")
    mode = updates.get("source_mode")
    if mode is not None and mode not in {"local", "remote"}:
        raise ValueError("mode must be one of: local, remote")

    results = []
    for key, val in updates.items():
        vtype = _VALUE_TYPES.get(key, "string")
        results.append(
            _db.set_value(key, str(val) if val is not None else "", vtype, updated_by=updated_by)
        )
    return results



def _runtime_build() -> tuple[str, str]:
    app_version = os.environ.get("APP_VERSION", "dev")
    build_id = "(unknown)"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            build_id = result.stdout.strip()
    except Exception:
        pass
    return app_version, build_id


def record_deployment_started() -> dict:
    """Persist this process deployment identity once from the startup hook."""
    from modules.flow_gate.workflow import event_logger

    app_version, build_id = _runtime_build()
    started_at = datetime.now(_JST).isoformat(timespec="seconds")
    return event_logger.log_event(
        event_type=event_logger.EVT_DEPLOYMENT_STARTED,
        project_id="__SYSTEM__", actor_user_id="u-system",
        metadata={"app_version": app_version, "build_id": build_id, "started_at": started_at},
    )


def get_system_info() -> dict:
    """Return app version, build ID, DB status, server time, and annotation counters."""
    app_version, build_id = _runtime_build()

    try:
        from config import settings as _cfg

        db_path = _cfg.DB_PATH or "(N/A)"
    except Exception:
        db_path = "(N/A)"

    db_ok = False
    try:
        _db.list_settings()
        db_ok = True
    except Exception:
        pass

    return {
        "app_version": app_version,
        "build_id": build_id,
        "db_path": db_path,
        "db_status": "ok" if db_ok else "error",
        "server_time": datetime.now(_JST).isoformat(timespec="seconds"),
        "annotation_failures": _annotation_failure_counts(),
    }


def _annotation_failure_counts() -> dict:
    try:
        from modules.flow_gate.workflow.event_logger import count_review_annotation_failures
        return count_review_annotation_failures()
    except Exception:
        return {"read_failed": 0, "write_failed": 0, "total": 0, "since": None}
