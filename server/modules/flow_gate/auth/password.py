"""Password hashing, verification, and policy (bcrypt-based).

Also supports verification of legacy pbkdf2_sha256 hashes (automatically falls back with deprecated scheme).
"""
from __future__ import annotations
import re
from passlib.context import CryptContext

# bcrypt primary + pbkdf2_sha256 deprecated (backward compatibility with existing passwords)
_ctx = CryptContext(schemes=["bcrypt", "pbkdf2_sha256"], deprecated=["pbkdf2_sha256"])


def hash_password(plain: str) -> str:
    """Hash a password using bcrypt."""
    return _ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Compare a plain password to a hash. Also supports pbkdf2_sha256 hashes."""
    try:
        return _ctx.verify(plain, hashed)
    except Exception:
        return False


def validate_password(plain: str) -> list[str]:
    """Validate password against policy. Returns: list of violations (empty list = pass).

    Policy:
    - At least 12 characters
    - At least 3 of the following: uppercase, lowercase, digits, special characters
    """
    errors: list[str] = []
    if len(plain) < 12:
        errors.append("Password must be at least 12 characters long.")

    type_count = sum([
        bool(re.search(r"[A-Z]", plain)),
        bool(re.search(r"[a-z]", plain)),
        bool(re.search(r"[0-9]", plain)),
        bool(re.search(r"[^A-Za-z0-9]", plain)),
    ])
    if type_count < 3:
        errors.append("Password must include at least 3 categories: uppercase letters, lowercase letters, digits, special characters.")

    return errors
