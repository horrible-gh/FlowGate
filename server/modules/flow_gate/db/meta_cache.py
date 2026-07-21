"""Short-lived TTL cache for hot per-request metadata reads (0282 NR0003 발견 2).

One screen load re-read the same ``projects`` row 11 times and the same
``project_git_config`` row 3 times: nearly every service entry point calls
``db_projects.get_by_id`` / ``db_git.get_config`` on its own. This module reuses
the 0276 T0009 auth_cache pattern (tiny TTL map + explicit invalidation from the
few write paths) for those two tables.

Policy:

  * TTL default is 5 seconds — same rationale as auth_cache: long enough to
    collapse one screen load's burst, short enough to bound any staleness.
    Override with FLOWGATE_META_CACHE_TTL; 0 disables caching entirely.
  * Under TESTING (and without an explicit FLOWGATE_META_CACHE_TTL) the cache
    is OFF. Parts of the test suite write these tables with raw SQL, and a
    5-second stale window spanning many fast tests would make failures order-
    dependent. The cache behaviour itself is exercised by the 0282 test, which
    sets the env var explicitly.
  * Every production writer lives in db/projects.py / db/git_integration.py and
    invalidates explicitly, so a settings save takes effect immediately; the
    TTL is the backstop, not the mechanism. Like auth_cache, the cache is per
    process — cross-worker writes converge within the TTL.
"""
from __future__ import annotations

import os

from ..utils.ttl_cache import TTLCache


def _configured_ttl() -> float:
    raw = os.environ.get("FLOWGATE_META_CACHE_TTL")
    if raw is None or raw.strip() == "":
        return 0.0 if os.environ.get("TESTING") else 5.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 5.0


_projects = TTLCache(_configured_ttl)     # project_id -> projects row | None
_git_configs = TTLCache(_configured_ttl)  # project_id -> git config row | None


def project_cache() -> TTLCache:
    return _projects


def git_config_cache() -> TTLCache:
    return _git_configs


def invalidate_project(project_id: str) -> None:
    """A projects row changed (created / updated / (de)activated / deleted)."""
    _projects.invalidate(project_id)


def invalidate_git_config(project_id: str) -> None:
    """A project's git config was saved or deleted."""
    _git_configs.invalidate(project_id)


def clear_all() -> None:
    """Full reset — for tests and wholesale rewrites."""
    _projects.clear()
    _git_configs.clear()
