"""Stable, versioned execution fingerprints. Secret material never enters the DB."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
from pathlib import Path

from modules.flow_gate.db.connection import get_store

FINGERPRINT_VERSION = 1
KEY_ENV = "FLOWGATE_AI_EVALUATION_HMAC_KEY"
KEY_FILE = ".flowgate-ai-evaluation-hmac-key"
MARKER_FILE = ".flowgate-ai-evaluation-hmac-version"


class FingerprintKeyError(RuntimeError):
    pass


def _root() -> Path:
    from modules.flow_gate.storage.paths import get_storage_root
    return get_storage_root(create=True)


def _read_secret(path: Path) -> bytes:
    try:
        raw = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
    except Exception as exc:
        raise FingerprintKeyError("Invalid persisted evaluation HMAC key") from exc
    if len(raw) != 32:
        raise FingerprintKeyError("Evaluation HMAC key must have 32 bytes")
    return raw


def _history_exists() -> bool:
    store = get_store()
    return bool(store.table_exists("ai_evaluation_runs") and
                store._fetch_one("SELECT evaluation_id FROM ai_evaluation_runs LIMIT 1"))


def _key() -> bytes:
    root = _root()
    key_path, marker_path = root / KEY_FILE, root / MARKER_FILE
    configured = os.environ.get(KEY_ENV)
    if not configured:
        try:
            from config import settings
            configured = getattr(settings, KEY_ENV, None)
        except Exception:
            configured = None
    if configured:
        try:
            key = base64.b64decode(configured, validate=True)
        except Exception as exc:
            raise FingerprintKeyError("Invalid evaluation HMAC key encoding") from exc
        if len(key) != 32:
            raise FingerprintKeyError("Evaluation HMAC key must have 32 bytes")
    elif key_path.exists():
        key = _read_secret(key_path)
    else:
        if marker_path.exists() or _history_exists():
            raise FingerprintKeyError("Persisted evaluation HMAC key is missing")
        key = os.urandom(32)
        try:
            with key_path.open("x", encoding="ascii") as stream:
                stream.write(base64.b64encode(key).decode("ascii"))
            os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
        except FileExistsError:
            key = _read_secret(key_path)
    identity = hashlib.sha256(key).hexdigest()
    if marker_path.exists():
        try:
            marker = json.loads(marker_path.read_text(encoding="ascii"))
        except Exception as exc:
            raise FingerprintKeyError("Invalid evaluation HMAC version marker") from exc
        old_version = int(marker.get("version", 0))
        if old_version > FINGERPRINT_VERSION or (old_version == FINGERPRINT_VERSION and marker.get("key_sha256") != identity):
            raise FingerprintKeyError("Evaluation HMAC key changed without fingerprint version increase")
        if old_version < FINGERPRINT_VERSION:
            marker_path.write_text(json.dumps({"version": FINGERPRINT_VERSION, "key_sha256": identity}), encoding="ascii")
    else:
        if _history_exists():
            raise FingerprintKeyError("Evaluation HMAC version marker is missing")
        with marker_path.open("x", encoding="ascii") as stream:
            stream.write(json.dumps({"version": FINGERPRINT_VERSION, "key_sha256": identity}))
        os.chmod(marker_path, stat.S_IRUSR | stat.S_IWUSR)
    return key


def subject_fingerprint(*, project_id: str, provider_id: str, scope: str, provider: dict,
                        credential: str | None) -> str:
    secret = _key()
    credential_digest = (hmac.new(secret, credential.encode("utf-8"), hashlib.sha256).hexdigest()
                         if credential is not None else None)
    data = {
        "fingerprint_version": FINGERPRINT_VERSION,
        "project_id": project_id,
        "provider_id": provider_id,
        "provider_scope": scope,
        "exec_type": provider.get("exec_type"),
        "kind": provider.get("kind"),
        "cli_command": provider.get("cli_command") if provider.get("exec_type") == "cli" else None,
        "api_base_url": provider.get("api_base_url") if provider.get("exec_type") == "api" else None,
        "api_model": provider.get("api_model") if provider.get("exec_type") == "api" else None,
        "credential_state_digest": credential_digest if provider.get("exec_type") == "api" else None,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()
