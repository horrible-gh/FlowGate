"""Regression tests for B0101 — FLOWGATE_INBOX_CONTENT_MAX blank-string boot crash.

A fresh `setup` copies `.env.sample` verbatim, which used to ship
`FLOWGATE_INBOX_CONTENT_MAX=` (a present-but-blank value). pydantic only applies
the field default when a key is ABSENT, so the blank `""` was coerced to int and
crashed the boot at `settings = Settings()` (config.py module import).

The fix is a `mode="before"` field_validator on Settings that absorbs blank /
whitespace-only strings as "unset" -> default, while still rejecting genuinely
malformed (non-numeric) values loudly.
"""
import os

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

import pytest
from pydantic import ValidationError

from config import Settings

# Minimal set of required Settings fields so we can construct in isolation.
# Init kwargs take precedence over any value found in the on-disk .env.
_BASE = dict(
    ALLOWED_ORIGIN="*",
    SECRET_KEY="x" * 32,
    CONTEXT="/test",
    DB_TYPE="sqlite3",
)

_DEFAULT = 10485760


def _make(**overrides):
    return Settings(**{**_BASE, **overrides})


def test_blank_string_falls_back_to_default():
    # The exact B0101 input: key present, value empty string.
    s = _make(FLOWGATE_INBOX_CONTENT_MAX="")
    assert s.FLOWGATE_INBOX_CONTENT_MAX == _DEFAULT


def test_whitespace_only_falls_back_to_default():
    s = _make(FLOWGATE_INBOX_CONTENT_MAX="   ")
    assert s.FLOWGATE_INBOX_CONTENT_MAX == _DEFAULT


def test_absent_uses_default():
    s = _make()
    assert s.FLOWGATE_INBOX_CONTENT_MAX == _DEFAULT


def test_explicit_value_is_honored():
    s = _make(FLOWGATE_INBOX_CONTENT_MAX="2048")
    assert s.FLOWGATE_INBOX_CONTENT_MAX == 2048


def test_non_numeric_string_still_rejected():
    # Only blank is absorbed; a real misconfiguration must still fail loudly.
    with pytest.raises(ValidationError):
        _make(FLOWGATE_INBOX_CONTENT_MAX="not-a-number")
