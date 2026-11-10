"""Immutable binding of an approval/recovery request UUID to its full target."""
import hashlib
import json
from .connection import get_store, is_unique_violation, now_iso


def digest(binding):
    return hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def matches(kind, request_id, binding):
    row = get_store()._fetch_one(
        "SELECT binding_digest FROM git_merge_request_binding WHERE kind = ? AND request_id = ?",
        [kind, request_id])
    return None if row is None else row["binding_digest"] == digest(binding)


def bind(kind, request_id, binding):
    """True for the first insert or an identical replay; False for key reuse.

    Do not wrap a caught unique violation in a caller's transaction: PostgreSQL
    aborts that transaction. Session writers hold the domain lock, and this immutable
    insert precedes the journal write and every Git mutation.
    """
    try:
        get_store()._execute(
            "INSERT INTO git_merge_request_binding "
            "(kind, request_id, binding_digest, merge_id, group_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [kind, request_id, digest(binding), binding["merge_id"], binding.get("group_id"), now_iso()])
        return True
    except Exception as exc:
        if not is_unique_violation(exc):
            raise
        return matches(kind, request_id, binding) is True
