"""ai_providers CRUD + per-scope default persistence (flowgate.default.0164 DB0005).

Scope follows the document_types precedent: project_id NULL = the global (system) row
set, a value = that project's own list. The routing chain is one scope's rows ordered
by sort_order; the current default selection is NOT a column here — it lives in
system_settings('ai_default_provider_id') for the global scope and in
project_settings.ai_default_provider_id for a project scope (DB0005 §2.2/§2.3).

Whole-list replace runs in ONE transaction (delete-missing → update/insert → persist
default/mode) so a failed save never leaves a partial list (L0004 §5, no partial save).
api_key is stored ENCRYPTED at rest (0371 NR0007 §3): every write goes through
utils.api_key_crypto (AES-256-GCM, "enc:v1:" prefix) immediately before the column and
every read decrypts here, so the service layer above keeps working in plaintext and the
value still never reaches an HTTP response (L0004 §2.3). A row whose ciphertext does not
decrypt is flagged api_key_unreadable instead of being read back as a plaintext key.
"""
from __future__ import annotations

from typing import Optional

from ..utils import api_key_crypto as _crypto
from . import system_settings as _system_settings
from .connection import get_store, now_iso

SYSTEM_DEFAULT_KEY = "ai_default_provider_id"


class _KeepStoredSecret:
    """Sentinel a caller puts in a row's api_key to mean "leave the stored ciphertext
    alone". The service layer merges keep/replace/delete on PLAINTEXT, and a row that
    cannot be decrypted has no plaintext to merge — without this, "keep" would erase a
    key the operator never asked to delete."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover — debugging aid only
        return "<KEEP_STORED_SECRET>"


KEEP_STORED_SECRET = _KeepStoredSecret()


_COLUMNS = (
    "provider_id, project_id, name, exec_type, kind, enabled, cli_command, "
    "api_base_url, api_model, api_key, sort_order, created_at, updated_at"
)


def _scope_where(project_id: Optional[str]) -> tuple[str, list]:
    if project_id is None:
        return "project_id IS NULL", []
    return "project_id = ?", [project_id]


def _decrypt_row(row: Optional[dict]) -> Optional[dict]:
    """Ciphertext → plaintext for one row.

    A row that does not decrypt keeps api_key=None and gains api_key_unreadable=True:
    the settings screen still renders (and can take a replacement key) instead of the
    whole list failing on one row whose master key was lost.
    """
    if row is None:
        return None
    stored = row.get("api_key")
    if stored is None:
        return row
    try:
        row["api_key"] = _crypto.decrypt_api_key(stored)
    except _crypto.ApiKeyCryptoError:
        row["api_key"] = None
        row["api_key_unreadable"] = True
    return row


def _list_scope_stored(project_id: Optional[str]) -> list[dict]:
    """One scope's rows exactly as stored — api_key still ciphertext."""
    where, params = _scope_where(project_id)
    return get_store()._fetch_all(
        f"SELECT {_COLUMNS} FROM ai_providers WHERE {where} ORDER BY sort_order, provider_id",
        params,
    )


def list_scope(project_id: Optional[str]) -> list[dict]:
    """All rows of one scope in chain order (enabled or not — callers filter)."""
    return [_decrypt_row(r) for r in _list_scope_stored(project_id)]


def _get_row_stored(project_id: Optional[str], provider_id: str) -> Optional[dict]:
    where, params = _scope_where(project_id)
    return get_store()._fetch_one(
        f"SELECT {_COLUMNS} FROM ai_providers WHERE {where} AND provider_id = ?",
        params + [provider_id],
    )


def get_row(project_id: Optional[str], provider_id: str) -> Optional[dict]:
    return _decrypt_row(_get_row_stored(project_id, provider_id))


def get_secret(project_id: Optional[str], provider_id: str) -> Optional[str]:
    """Raw api_key for the follow-up execution module (internal use only — never
    call from an HTTP serialization path; L0004 §2.3).

    Unlike the list/view path this does NOT swallow a decryption failure: a run that
    cannot read its own key must fail loudly (ApiKeyCryptoError) rather than start as
    if no key had ever been configured.
    """
    row = _get_row_stored(project_id, provider_id)
    return _crypto.decrypt_api_key(row.get("api_key")) if row else None


def _key_for_storage(row: dict, stored_keys: dict) -> Optional[str]:
    """The value that actually goes into the column: ciphertext, the untouched stored
    ciphertext (KEEP_STORED_SECRET), or None."""
    value = row.get("api_key")
    if value is KEEP_STORED_SECRET:
        return stored_keys.get(row["provider_id"])
    return _crypto.encrypt_api_key(value)


def replace_scope(
    project_id: Optional[str],
    rows: list[dict],
    existing_ids: set[str],
    default_provider_id: Optional[str],
    mode: Optional[str] = None,
    updated_by: Optional[str] = None,
) -> None:
    """Replace one scope's provider list atomically (L0004 §2.2 full-replace merge).

    `rows` carry final provider_id values (service issues ids for new items) with
    api_key already merged and in PLAINTEXT — encryption happens here, immediately
    before the value reaches the column (0371 NR0007 §3 recommendation 3); KEEP_STORED_SECRET
    means "this row's stored ciphertext is unreadable, leave it as it is". Rows absent
    from `rows` are deleted. The scope default —
    and for a project scope the tri-state `mode` — is persisted in the same
    transaction (DB0005 §4).
    """
    store = get_store()
    now = now_iso()
    where, params = _scope_where(project_id)
    stored_keys = {
        r["provider_id"]: r.get("api_key") for r in _list_scope_stored(project_id)
    }
    with store.transaction() as s:
        keep = [r["provider_id"] for r in rows]
        if keep:
            placeholders = ", ".join("?" for _ in keep)
            s._execute(
                f"DELETE FROM ai_providers WHERE {where} AND provider_id NOT IN ({placeholders})",
                params + keep,
            )
        else:
            s._execute(f"DELETE FROM ai_providers WHERE {where}", params)

        for r in rows:
            if r["provider_id"] in existing_ids:
                s._execute(
                    "UPDATE ai_providers SET name = ?, exec_type = ?, kind = ?, enabled = ?, "
                    "cli_command = ?, api_base_url = ?, api_model = ?, api_key = ?, "
                    "sort_order = ?, updated_at = ? WHERE provider_id = ?",
                    [
                        r["name"], r["exec_type"], r["kind"], r["enabled"],
                        r["cli_command"], r["api_base_url"], r["api_model"],
                        _key_for_storage(r, stored_keys),
                        r["sort_order"], now, r["provider_id"],
                    ],
                )
            else:
                s._execute(
                    "INSERT INTO ai_providers (provider_id, project_id, name, exec_type, kind, "
                    "enabled, cli_command, api_base_url, api_model, api_key, sort_order, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        r["provider_id"], project_id, r["name"], r["exec_type"], r["kind"],
                        r["enabled"], r["cli_command"], r["api_base_url"], r["api_model"],
                        _key_for_storage(r, stored_keys), r["sort_order"], now, now,
                    ],
                )

        if project_id is None:
            # 0148 precedent: set_value's upsert already table-qualifies the existing-row
            # reference, and _execute joins the ambient transaction.
            _system_settings.set_value(
                SYSTEM_DEFAULT_KEY,
                default_provider_id,
                "string",
                "Global default AI provider selection",
                updated_by=updated_by,
            )
        else:
            upsert_project_ai_state(project_id, mode, default_provider_id)


def encrypt_plaintext_api_keys() -> int:
    """Move legacy plaintext rows to ciphertext; returns how many were rewritten.

    Idempotent and resumable (NR0007 §3 recommendation 4): rows that already carry the enc:v1:
    prefix are skipped, so a half-finished pass — or a second boot — simply continues.
    Runs at startup for every dialect, because "UPDATE ... SET api_key = encrypt(...)"
    is not something SQL alone can do safely.
    """
    store = get_store()
    rows = store._fetch_all(
        "SELECT provider_id, api_key FROM ai_providers WHERE api_key IS NOT NULL", [],
    )
    migrated = 0
    for row in rows:
        stored = row.get("api_key")
        if not stored or _crypto.is_encrypted(stored):
            continue
        store._execute(
            "UPDATE ai_providers SET api_key = ? WHERE provider_id = ?",
            [_crypto.encrypt_api_key(stored), row["provider_id"]],
        )
        migrated += 1
    return migrated


def get_system_default_provider_id() -> Optional[str]:
    """Global default selection. Key absence reads as NULL (DB0005 §2.3)."""
    return _system_settings.get_value(SYSTEM_DEFAULT_KEY)


def get_project_ai_state(project_id: str) -> Optional[dict]:
    """The project's tri-state columns, or None when no project_settings row exists
    (row absence = inherit, L0004 §2.4)."""
    return get_store()._fetch_one(
        "SELECT ai_mode, ai_default_provider_id, updated_at FROM project_settings "
        "WHERE project_id = ?",
        [project_id],
    )


def upsert_project_ai_state(
    project_id: str,
    ai_mode: Optional[str],
    ai_default_provider_id: Optional[str],
) -> None:
    """Persist the tri-state mode + project default. Mode-only transitions call this
    directly and never touch ai_providers rows (list preservation, L0004 §3)."""
    get_store()._execute(
        "INSERT INTO project_settings (project_id, ai_mode, ai_default_provider_id, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(project_id) DO UPDATE SET "
        "ai_mode = excluded.ai_mode, "
        "ai_default_provider_id = excluded.ai_default_provider_id, "
        "updated_at = excluded.updated_at",
        [project_id, ai_mode, ai_default_provider_id, now_iso()],
    )
