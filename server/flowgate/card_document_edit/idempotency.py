"""L#1 §2.5: workflow_events only; keyset scan never truncates before exact matching."""
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from modules.flow_gate.db.connection import get_store, now_iso
from .contracts import FINGERPRINT_ALG, EVENT_SCHEMA

IDEMPOTENCY_TTL_SEC = 86_400
IDEMPOTENCY_SCAN_BATCH = 50
IDEMPOTENCY_EXACT_STOP = 2
IDEMPOTENCY_LOOKUP_BUDGET_MS = 50
NEW, REPLAY, REUSED, STALE = "NEW", "REPLAY", "REUSED", "STALE"


def dumps(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_binding(req, current, *, node_key=None, slot=None):
    return {"idem_key": getattr(req, "idempotency_key", None), "project_id": current["project_id"],
            "actor_user_id": req.actor.user_id, "actor_kind": req.actor.kind, "origin": req.origin.kind,
            "flow_id": current.get("group_id"), "node_key": getattr(req, "node_key", node_key),
            "slot": getattr(req, "slot", slot), "doc_id": current["doc_id"],
            "base_revision_no": req.expected_revision,
            "content_sha256": fingerprint(req.content), "fp_alg": FINGERPRINT_ALG}


def lookup(project_id, key):
    cutoff = (datetime.fromisoformat(now_iso()) - timedelta(seconds=IDEMPOTENCY_TTL_SEC)).isoformat(timespec="seconds")
    needle = '"idem_key":' + dumps(key)
    pattern = "%" + needle.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
    exact, last_id = [], 0
    while True:
        rows = get_store()._fetch_all(
            "SELECT id, actor_user_id, group_id, metadata, created_at FROM workflow_events "
            "WHERE project_id = ? AND event_type = 'doc_edited' AND created_at >= ? "
            "AND metadata LIKE ? ESCAPE '!' AND id > ? ORDER BY id LIMIT ?",
            [project_id, cutoff, pattern, last_id, IDEMPOTENCY_SCAN_BATCH])
        for row in rows:
            raw = row.get("metadata") or ""
            try:
                meta = json.loads(raw)
                if not isinstance(meta, dict):
                    raise ValueError("metadata is not an object")
            except (ValueError, TypeError):
                if needle in raw:
                    exact.append({**row, "corrupt": True})
            else:
                if meta.get("idem_key") == key:
                    exact.append({**row, "parsed": meta})
            if len(exact) >= IDEMPOTENCY_EXACT_STOP:
                return exact
        if len(rows) < IDEMPOTENCY_SCAN_BATCH:
            return exact
        last_id = rows[-1]["id"]


@dataclass(frozen=True)
class Verdict:
    kind: str
    record: dict | None = None


def judge(records, binding, current):
    if not records:
        return Verdict(NEW)
    if len(records) != 1 or records[0].get("corrupt"):
        return Verdict(REUSED)
    record = records[0]
    m = record["parsed"]
    fields = ("actor_kind", "origin", "flow_id", "node_key", "slot", "doc_id",
              "base_revision_no", "content_sha256", "fp_alg")
    if (m.get("schema") != EVENT_SCHEMA or record.get("actor_user_id") != binding["actor_user_id"]
            or any(m.get(k) != binding[k] for k in fields)
            or type(m.get("base_revision_no")) is not int or type(m.get("revision_no")) is not int
            or not isinstance(m.get("updated_at"), str)):
        return Verdict(REUSED)
    if m["doc_id"] == current["doc_id"] and m["revision_no"] == current["revision_no"]:
        return Verdict(REPLAY, record)
    return Verdict(STALE)
