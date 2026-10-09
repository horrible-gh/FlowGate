"""Run policy snapshot (L0009 §2.6.2 / §1.6): taken once at run start, the only policy source after.

``policy_set_for_run`` resolves the Flow's policy set (today the system default, DB0010 DB-8) and
records all nine ``(value, impl_version)`` pairs with ``SNAPSHOT_DIGEST``. ``check_active_runs``
is the start-up restore check: every running / stopped run must still find its implementations.
"""
from __future__ import annotations

import hashlib
import json
import logging

from modules.flow_gate.graph_workflow.domain import policies
from modules.flow_gate.graph_workflow.domain.run_policies import PolicySnapshotUnavailable, policies_of
from modules.flow_gate.graph_workflow.store import run_store

_log = logging.getLogger(__name__)


def snapshot_digest(entries: dict) -> str:
    """``sha256(canonical_json(entries))``: keys sorted by code point, no spaces, UTF-8."""
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def policy_set_for_run(_flow_id: str) -> dict:
    """``{source, set_id, set_version, entries{9 keys: {value, impl_version}}, digest}``."""
    resolved = policies.system_default_set()
    entries = {key: dict(resolved["entries"][key]) for key in policies.POLICY_KEYS}
    return {"source": resolved["source"], "set_id": resolved["set_id"], "set_version": resolved["set_version"],
            "entries": entries, "digest": snapshot_digest(entries)}


def check_active_runs() -> list[dict]:
    """Q-R7 restore check (L0009 §2.6.2): log every snapshot triple with no implementation."""
    missing = []
    for row in run_store.active_policy_triples():
        entry = {"value": row["policy_value"], "impl_version": row["impl_version"]}
        try:
            policies_of({key: entry if key == row["policy_key"] else _undecided() for key in policies.POLICY_KEYS})
        except PolicySnapshotUnavailable as exc:
            _log.error("graph run policy implementation missing: %s", exc)
            missing.append({"policy": row["policy_key"], **entry})
    return missing


def _undecided() -> dict:
    return {"value": policies.UNDECIDED, "impl_version": policies.UNDECIDED_IMPL_VERSION}
