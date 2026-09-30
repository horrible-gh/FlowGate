"""AI Evaluation runs: a judge AI scores a target AI on a real Scratch task
(flowgate.v02.0017 T0002 §3, migration 129).

lifecycle   pending -> preparing -> running -> reviewing -> completed
            any active state -> failed | cancelled
Result (score, per-criterion scores, review) is separate from the lifecycle.
Migration 130: an optional human review on a completed evaluation (its score, when present, is
the final score) and reusable evaluation sets (ai_evaluation_sets).
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Optional

from modules.flow_gate.db.connection import get_store, now_iso

ACTIVE = ("pending", "preparing", "running", "reviewing")
TERMINAL = ("completed", "failed", "cancelled")
LIFECYCLES = ACTIVE + TERMINAL
_SECRET_PATTERN = re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|api[_ -]?key\s*[:=]\s*)[^\s,;]+")


class AlreadyRunning(Exception):
    def __init__(self, evaluation_id: str):
        self.evaluation_id = evaluation_id
        super().__init__(evaluation_id)


def _dedupe(project_id: str, target_id: str, judge_id: str) -> str:
    return hashlib.sha256(f"{project_id}\0{target_id}\0{judge_id}".encode()).hexdigest()


def redact(text: Optional[str], limit: int) -> Optional[str]:
    if text is None:
        return None
    return _SECRET_PATTERN.sub(lambda m: m.group(1) + "[redacted]", text)[:limit]


def get(project_id: str, evaluation_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM ai_evaluations WHERE project_id = ? AND evaluation_id = ?",
        [project_id, evaluation_id])


def scores(evaluation_ids: list[str]) -> dict[str, list[dict]]:
    if not evaluation_ids:
        return {}
    marks = ", ".join("?" for _ in evaluation_ids)
    rows = get_store()._fetch_all(
        "SELECT evaluation_id, criterion_key, position, criterion_description, score, rationale "
        f"FROM ai_evaluation_scores WHERE evaluation_id IN ({marks}) ORDER BY position",
        list(evaluation_ids))
    grouped: dict[str, list[dict]] = {eid: [] for eid in evaluation_ids}
    for row in rows:
        grouped[row["evaluation_id"]].append(row)
    return grouped


def final_score(row: dict) -> Optional[int]:
    """Human score when a person reviewed the evaluation, otherwise the judge's total."""
    human = row.get("human_score")
    return human if human is not None else row.get("total_score")


def create_pending(*, project_id: str, target_id: str, target_name: Optional[str],
                   judge_id: str, judge_name: Optional[str], created_by: Optional[str],
                   evaluation_set: Optional[dict] = None) -> dict:
    key = _dedupe(project_id, target_id, judge_id)
    store = get_store()
    evaluation_id = "aie_" + uuid.uuid4().hex
    now = now_iso()
    try:
        with store.transaction():
            active = store._fetch_one(
                "SELECT evaluation_id FROM ai_evaluations WHERE active_dedupe_key = ?", [key])
            if active:
                raise AlreadyRunning(active["evaluation_id"])
            store._execute("""INSERT INTO ai_evaluations
                (evaluation_id, project_id, target_provider_id, target_provider_name,
                 judge_provider_id, judge_provider_name, lifecycle, active_dedupe_key,
                 evaluation_set_id, evaluation_set_name, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)""",
                [evaluation_id, project_id, target_id, target_name, judge_id, judge_name, key,
                 (evaluation_set or {}).get("set_id"), (evaluation_set or {}).get("name"),
                 created_by, now, now])
    except AlreadyRunning:
        raise
    except Exception:
        # A competing insert may have won the active key; inspect from a new transaction.
        active = store._fetch_one(
            "SELECT evaluation_id FROM ai_evaluations WHERE active_dedupe_key = ?", [key])
        if active:
            raise AlreadyRunning(active["evaluation_id"])
        raise
    return get(project_id, evaluation_id)


def advance(project_id: str, evaluation_id: str, lifecycle: str, **fields) -> bool:
    """Move an active evaluation to the next active stage, writing stage output fields.
    Conditional on still being active, so a cancel that already won is never overwritten."""
    if lifecycle not in ACTIVE:
        raise ValueError("advance() only moves between active states")
    allowed = {"task_title", "task_summary", "task_instruction", "target_report",
               "artifacts", "scratch_ref", "started_at"}
    if set(fields) - allowed:
        raise ValueError("unknown evaluation field")
    now = now_iso()
    sets = ["lifecycle = ?", "updated_at = ?"] + [f"{name} = ?" for name in fields]
    params = [lifecycle, now] + [
        json.dumps(value, ensure_ascii=False) if name == "artifacts" and value is not None else value
        for name, value in fields.items()]
    marks = ", ".join("?" for _ in ACTIVE)
    return get_store()._execute_affected(
        f"UPDATE ai_evaluations SET {', '.join(sets)} "
        f"WHERE project_id = ? AND evaluation_id = ? AND lifecycle IN ({marks})",
        params + [project_id, evaluation_id, *ACTIVE]) == 1


def write_criteria(evaluation_id: str, criteria: list[dict]) -> None:
    store = get_store()
    with store.transaction():
        store._execute("DELETE FROM ai_evaluation_scores WHERE evaluation_id = ?", [evaluation_id])
        for position, item in enumerate(criteria, start=1):
            store._execute(
                "INSERT INTO ai_evaluation_scores (evaluation_id, criterion_key, position, "
                "criterion_description, score, rationale) VALUES (?, ?, ?, ?, NULL, NULL)",
                [evaluation_id, item["key"], position, redact(item.get("description"), 2000)])


def complete(project_id: str, evaluation_id: str, *, total_score: int, review_summary: str,
             item_scores: list[dict]) -> bool:
    """Write every per-criterion score, the total and the review, and settle completed --
    all in one transaction, and only if the evaluation is still reviewing."""
    store = get_store()
    now = now_iso()
    with store.transaction():
        won = store._execute_affected(
            """UPDATE ai_evaluations SET lifecycle = 'completed', total_score = ?, review_summary = ?,
                   active_dedupe_key = NULL, finished_at = ?, updated_at = ?
               WHERE project_id = ? AND evaluation_id = ? AND lifecycle = 'reviewing'""",
            [total_score, redact(review_summary, 8000), now, now, project_id, evaluation_id]) == 1
        if not won:
            return False
        for item in item_scores:
            store._execute(
                "UPDATE ai_evaluation_scores SET score = ?, rationale = ? "
                "WHERE evaluation_id = ? AND criterion_key = ?",
                [item["score"], redact(item.get("rationale"), 4000), evaluation_id, item["key"]])
    return True


def finish(project_id: str, evaluation_id: str, *, lifecycle: str, reason_code: str,
           failure_stage: Optional[str] = None, diagnostic_summary: Optional[str] = None) -> bool:
    if lifecycle not in ("failed", "cancelled"):
        raise ValueError("finish() settles failed/cancelled only")
    now = now_iso()
    marks = ", ".join("?" for _ in ACTIVE)
    return get_store()._execute_affected(
        f"""UPDATE ai_evaluations SET lifecycle = ?, reason_code = ?, failure_stage = ?,
               diagnostic_summary = ?, active_dedupe_key = NULL, finished_at = ?, updated_at = ?
           WHERE project_id = ? AND evaluation_id = ? AND lifecycle IN ({marks})""",
        [lifecycle, reason_code, failure_stage, redact(diagnostic_summary, 2000), now, now,
         project_id, evaluation_id, *ACTIVE]) == 1


def set_human_review(project_id: str, evaluation_id: str, *, score: Optional[int],
                     comment: Optional[str], reviewer_id: Optional[str]) -> bool:
    """Record (or clear, with score and comment both None) the human review. Completed only."""
    cleared = score is None and not comment
    now = now_iso()
    return get_store()._execute_affected(
        """UPDATE ai_evaluations SET human_score = ?, human_comment = ?, human_reviewer_id = ?,
               human_reviewed_at = ?, updated_at = ?
           WHERE project_id = ? AND evaluation_id = ? AND lifecycle = 'completed'""",
        [score, None if cleared else redact(comment, 8000), None if cleared else reviewer_id,
         None if cleared else now, now, project_id, evaluation_id]) == 1


def history(project_id: str, *, target_id: Optional[str] = None, limit: int = 20,
            before: Optional[tuple[str, str]] = None) -> list[dict]:
    if not 1 <= limit <= 101:
        raise ValueError("limit must be 1..101")
    where, params = ["project_id = ?"], [project_id]
    if target_id is not None:
        where.append("target_provider_id = ?")
        params.append(target_id)
    if before is not None:
        where.append("(created_at < ? OR (created_at = ? AND evaluation_id < ?))")
        params.extend([before[0], before[0], before[1]])
    params.append(limit)
    return get_store()._fetch_all(
        "SELECT * FROM ai_evaluations WHERE " + " AND ".join(where) +
        " ORDER BY created_at DESC, evaluation_id DESC LIMIT ?", params)


def latest_completed(project_id: str, target_ids: list[str]) -> list[dict]:
    """Newest completed evaluation per target connection (one row per target id)."""
    ids = sorted({tid for tid in target_ids if tid})
    if not ids:
        return []
    marks = ", ".join("?" for _ in ids)
    rows = get_store()._fetch_all(
        "SELECT evaluation_id, target_provider_id, total_score, human_score, finished_at FROM ai_evaluations "
        f"WHERE project_id = ? AND lifecycle = 'completed' AND target_provider_id IN ({marks}) "
        "ORDER BY finished_at DESC, evaluation_id DESC", [project_id, *ids])
    latest: dict[str, dict] = {}
    for row in rows:
        latest.setdefault(row["target_provider_id"], row)
    return list(latest.values())


def active_count(project_id: str) -> int:
    marks = ", ".join("?" for _ in ACTIVE)
    row = get_store()._fetch_one(
        f"SELECT COUNT(*) AS n FROM ai_evaluations WHERE project_id = ? AND lifecycle IN ({marks})",
        [project_id, *ACTIVE])
    return int(row["n"]) if row else 0


def recover_orphans() -> int:
    """At boot no worker from the previous process can still own an active evaluation."""
    now = now_iso()
    marks = ", ".join("?" for _ in ACTIVE)
    return get_store()._execute_affected(
        f"""UPDATE ai_evaluations SET lifecycle = 'failed', reason_code = 'internal_error',
               diagnostic_summary = 'Evaluation interrupted by server restart.',
               active_dedupe_key = NULL, finished_at = ?, updated_at = ?
           WHERE lifecycle IN ({marks})""", [now, now, *ACTIVE])


# ── Evaluation sets (reusable tasks) ─────────────────────────────────────────────

_SET_COLUMNS = ("set_id, project_id, name, name_key, name_key_hash, instruction, constraints, "
                "created_by, created_at, updated_at")


def list_sets(project_id: str) -> list[dict]:
    return get_store()._fetch_all(
        f"SELECT {_SET_COLUMNS} FROM ai_evaluation_sets WHERE project_id = ? "
        "ORDER BY name_key, set_id", [project_id])


def get_set(project_id: str, set_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        f"SELECT {_SET_COLUMNS} FROM ai_evaluation_sets WHERE project_id = ? AND set_id = ?",
        [project_id, set_id])


def get_set_by_name_hash(project_id: str, name_key_hash: str) -> Optional[dict]:
    return get_store()._fetch_one(
        f"SELECT {_SET_COLUMNS} FROM ai_evaluation_sets WHERE project_id = ? AND name_key_hash = ?",
        [project_id, name_key_hash])


def create_set(row: dict) -> None:
    now = now_iso()
    get_store()._execute(
        f"INSERT INTO ai_evaluation_sets ({_SET_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [row["set_id"], row["project_id"], row["name"], row["name_key"], row["name_key_hash"],
         row["instruction"], row["constraints"], row.get("created_by"), now, now])


def update_set(project_id: str, set_id: str, row: dict) -> None:
    get_store()._execute(
        "UPDATE ai_evaluation_sets SET name = ?, name_key = ?, name_key_hash = ?, instruction = ?, "
        "constraints = ?, updated_at = ? WHERE project_id = ? AND set_id = ?",
        [row["name"], row["name_key"], row["name_key_hash"], row["instruction"], row["constraints"],
         now_iso(), project_id, set_id])


def delete_set(project_id: str, set_id: str) -> None:
    # Evaluations keep evaluation_set_id/name as a snapshot of what they ran.
    get_store()._execute("DELETE FROM ai_evaluation_sets WHERE project_id = ? AND set_id = ?",
                         [project_id, set_id])
