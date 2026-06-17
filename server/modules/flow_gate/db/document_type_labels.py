"""Document type label lookup with locale support and in-memory caching.

Replaces hardcoded type-label dicts in service layer.
Fallback order: requested locale → 'en' → type_code itself.
"""
from __future__ import annotations

import threading
from typing import Optional

from .connection import get_store


_cache: dict[tuple, dict[str, str]] = {}
_lock = threading.Lock()


def get_type_name(type_code: str, locale: str = "en") -> str:
    """Return the localized display name for *type_code*.

    Fallback order:
      1. ``locale`` row in document_type_names
      2. ``'en'`` row (if locale != 'en')
      3. ``type_code`` itself
    """
    m = get_type_names_map(locale)
    if type_code in m:
        return m[type_code]
    return type_code


def get_type_names_map(locale: str = "en") -> dict[str, str]:
    """{type_code: type_name} map for all global system types at *locale*.

    Results are cached per locale. Call ``invalidate_type_label_cache()``
    after any document_types / document_type_names write.
    """
    key = (locale,)
    if key not in _cache:
        with _lock:
            if key not in _cache:
                _cache[key] = _load_type_names_map(locale)
    return _cache[key]


def _load_type_names_map(locale: str) -> dict[str, str]:
    try:
        store = get_store()
        rows = store._fetch_all(
            """
            SELECT dt.type_code,
                   COALESCE(
                       (SELECT type_name FROM document_type_names
                        WHERE  document_type_id = dt.id AND locale = ?),
                       (SELECT type_name FROM document_type_names
                        WHERE  document_type_id = dt.id AND locale = 'en'),
                       dt.type_code
                   ) AS type_name
            FROM   document_types dt
            WHERE  dt.project_id IS NULL
            ORDER  BY dt.sort_order
            """,
            [locale],
        )
    except Exception:
        return {}
    return {r["type_code"]: r["type_name"] for r in rows}


def invalidate_type_label_cache() -> None:
    """Clear the in-memory type label cache.

    Call this after any document_type or document_type_names update so the
    next request fetches fresh labels from the database.
    """
    with _lock:
        _cache.clear()
