"""Backup code creation, verification, and marking.

Generate 10 codes in the XXXX-XXXX-XXXX format and store bcrypt hashes in the backup_codes table.
"""
from __future__ import annotations
import secrets
import string
from passlib.context import CryptContext

from modules.flow_gate.db import totp_backup_codes as _db

_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
_NUM_CODES = 10
_CHARS = string.ascii_uppercase + string.digits


def _random_segment(length: int = 4) -> str:
    return "".join(secrets.choice(_CHARS) for _ in range(length))


def generate_codes() -> list[str]:
    """Generate 10 backup codes. Format: XXXX-XXXX-XXXX"""
    return [f"{_random_segment()}-{_random_segment()}-{_random_segment()}" for _ in range(_NUM_CODES)]


def hash_codes(codes: list[str]) -> list[str]:
    """bcrypt hashes of the codes, in the form store_hashes() keeps."""
    return [_ctx.hash(code) for code in codes]


def store_hashes(user_id: str, hashes: list[str]) -> None:
    """Delete existing backup codes and store the given bcrypt hashes."""
    _db.delete_all(user_id)
    for stored_hash in hashes:
        _db.create({"user_id": user_id, "code": stored_hash})


def store_codes(user_id: str, codes: list[str]) -> None:
    """Delete existing backup codes and store bcrypt hashes."""
    store_hashes(user_id, hash_codes(codes))


def verify_backup_code(user_id: str, code: str) -> bool:
    """Verify a backup code. If a matching unused code is found, mark it as used and return True."""
    rows = _db.list_by_user(user_id)
    for row in rows:
        if row.get("used_at"):
            continue
        stored_hash = row.get("code", "")
        try:
            if _ctx.verify(code, stored_hash):
                _db.mark_used(user_id, stored_hash)
                return True
        except Exception:
            continue
    return False


def count_unused(user_id: str) -> int:
    """Number of available (unused) backup codes."""
    rows = _db.list_by_user(user_id)
    return sum(1 for r in rows if not r.get("used_at"))
