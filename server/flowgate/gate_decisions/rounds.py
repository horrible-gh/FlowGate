"""0085 T#4: Time Machine rounds preserve decisions, effects and checkpoints.

Only the caller's Flow-guard transaction writes. No Git/remote read or rollback is
performed here: external effects and archive refs survive the round split.
"""
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.db import operation_job

from . import history, repository as repo


def unfinished(group_id):
    if not get_store().table_exists("operation_job"):
        return []
    return [{k: r.get(k) for k in ("job_id", "kind", "status", "target_key")}
            for kind in ("gate_publish", "gate_cleanup")
            for r in operation_job.active_jobs_of_group(group_id, kind)]


def impact_entries(group_id, node_keys):
    """All prior rounds, including legacy unknowns, for the existing impact view."""
    out = []
    for key in sorted(node_keys):
        entries = [history.entry(r) for r in repo.of_node(group_id, key)]
        entries += history.legacy_events(group_id, key)
        for entry in entries:
            effects = entry.get("effects") or []
            retained = any(e["phase"] in ("merge", "push") and e["status"] in ("succeeded", "unknown")
                           for e in effects)
            external = "retained" if retained else "none"
            unknown = any(e["status"] == "unknown" for e in effects)
            if entry["source"] == history.SOURCE_LEGACY:
                # Missing metadata is not evidence that a Git effect happened. Reject never publishes.
                external = "none" if entry["decision"] == "reject" else "unknown"
                unknown = entry["decision"] == "confirm"
            out.append({**entry, "external_effects": external,
                        "unknown_effects": unknown,
                        "archive_action": "preserved" if entry.get("archive_id") else "none"})
    return out


def split(group_id, run_id, keys, attempt_ids, operation_id):
    """Annotate effective terminal decisions and clear reset flags atomically with rewind."""
    linked = []
    for row in repo.decisions_of_group(group_id):
        if row["run_id"] != run_id or row["node_key"] not in keys:
            continue
        if row["status"] not in (repo.SUCCEEDED, repo.REJECTED, repo.ABANDONED):
            continue
        legacy_attempt = (row["attempt_id"] == f"pre151:{run_id}:{row['node_key']}"
                          and not row.get("superseded_by_decision_id"))
        if row["attempt_id"] not in attempt_ids and not row["recovery_reset_required"] and not legacy_attempt:
            continue
        if not repo.move(row["decision_id"], row["decision_version"],
                         time_machine_ref=operation_id, recovery_reset_required=False):
            raise RuntimeError("gate round changed during rewind")
        linked.append(row["decision_id"])
    return linked


def insert_decision(data):
    """Link a new attempt's decision to the effective decision split by Time Machine."""
    rows = repo.of_node(data["group_id"], data["node_key"])
    canceled = [r for r in rows if r["run_id"] == data["run_id"] and r["attempt_id"] == data["attempt_id"]
                and r["status"] == repo.CANCELED and r.get("previous_decision_id") and r.get("time_machine_ref")]
    # A no-effect cancel keeps the attempt open. Its replacement inherits the round's original predecessor,
    # even though that predecessor already points at the canceled intent. Keep the canceled history intact.
    inherited = canceled[-1] if canceled else None
    predecessor = repo.get(inherited["previous_decision_id"]) if inherited else None
    if inherited and (not predecessor or predecessor.get("superseded_by_decision_id") != inherited["decision_id"]):
        raise RuntimeError("gate canceled predecessor changed during decision intake")
    prior = [r for r in rows
             if r.get("time_machine_ref") and not r.get("superseded_by_decision_id")
             and r["status"] in (repo.SUCCEEDED, repo.REJECTED, repo.ABANDONED)
             and r["attempt_id"] != data["attempt_id"]]
    previous = predecessor or (prior[-1] if prior else None)
    if previous:
        data = {**data, "previous_decision_id": previous["decision_id"],
                "time_machine_ref": inherited["time_machine_ref"] if inherited else previous["time_machine_ref"]}
    stored = repo.insert_decision(data)
    if previous and not repo.move(previous["decision_id"], previous["decision_version"],
                                  superseded_by_decision_id=stored["decision_id"]):
        raise RuntimeError("gate predecessor changed during decision intake")
    return stored
