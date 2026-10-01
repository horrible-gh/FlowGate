"""Project AI Evaluation: a judge AI scores a target AI on a real Scratch task
(flowgate.v02.0017 T0002 §3).

One evaluation run:
  1. judge prepares the evaluation Scratch            (lifecycle: preparing)
  2. judge writes the actual task
  3. judge writes the per-criterion evaluation standard
  4. target performs the task inside the Scratch      (lifecycle: running)
  5. results / work products are collected
  6. judge reviews the results                        (lifecycle: reviewing)
  7. per-criterion scores (0-100)
  8. total score (mean of the criterion scores, computed here)
  9. review / rationale
 10. everything is stored as evaluation history       (lifecycle: completed)

Execution reuses the existing infrastructure instead of a second engine: the manifest-owned
ai_invoke run scratch (_create_scratch / retention sweep), the shared provider transports
(provider_api / provider_cli probes via ai_connection_test_service.execute_prompt) and the
same effective-provider resolution the connection test uses. The judge contract is JSON so
API and CLI connections take part the same way; the server materializes the task files.

Scratch layout of one evaluation (09-ai-settings.html):
  eval_xxxx/
    instruction.md               task instruction the target receives
    sample-project/              starting files written by the judge
    expected-constraints.json    checkable constraints + per-criterion standard
    output/                      the target's deliverables

There is no suitable/unsuitable verdict here. "Does the connection answer at all" is the
separate AI connection test (ai_connection_test_service).

Evaluation sets fix the task instruction/constraints so several AIs can be compared on the same
task; the judge still lays out the Scratch and writes the standard. A person may add a human
review to a completed evaluation; the human score, when given, is the final score.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import unicodedata
import uuid
from pathlib import Path, PurePosixPath

from modules.flow_gate.api.v1.events.publisher import FlowEvent, broadcast_event_threadsafe
from modules.flow_gate.db import ai_evaluations as repo
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import ai_connection_test_service as connection
from modules.flow_gate.services.ai_invoke import runtime as probe

logger = logging.getLogger(__name__)

# Minimum categories the product keeps from the original design (R §2 / T §3.5).
CRITERIA = (
    ("instruction_understanding", "Instruction understanding: grasped the goal, constraints and deliverables."),
    ("task_decomposition", "Task decomposition: broke the work into sensible steps and ordered them."),
    ("reasoning", "Judgment / reasoning: made sound decisions and explained trade-offs."),
    ("execution", "Execution: actually produced correct, working deliverables in the workspace."),
    ("verification", "Verification: checked its own result (tests, runs, re-reading) before finishing."),
    ("reporting", "Reporting / completeness: reported what was done and left nothing silently unfinished."),
)
CRITERION_KEYS = tuple(key for key, _ in CRITERIA)

PREPARE_TIMEOUT_SEC = 600
TARGET_TIMEOUT_SEC = 1800
REVIEW_TIMEOUT_SEC = 900
MODEL_MAX_TOKENS = 8192
INSTRUCTION_FILE = "instruction.md"
SAMPLE_DIR = "sample-project"
CONSTRAINTS_FILE = "expected-constraints.json"
OUTPUT_DIR = "output"
MAX_CONSTRAINTS = 20
MAX_TASK_FILES = 20
MAX_TASK_FILE_CHARS = 64_000
MAX_ARTIFACT_FILES = 60
MAX_ARTIFACT_READ_BYTES = 256 * 1024
ARTIFACT_EXCERPT_CHARS = 4_000
REVIEW_ARTIFACT_BUDGET_CHARS = 60_000
_PATH_PART = re.compile(r"[A-Za-z0-9_\-][A-Za-z0-9_.\-]{0,99}\Z")
_SKIPPED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
_LANGUAGES = {"ko": "Korean", "en": "English", "ja": "Japanese"}

_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.RLock()


class EvaluationError(Exception):
    def __init__(self, code: str, status: int, evaluation_id: str | None = None):
        self.code, self.status, self.evaluation_id = code, status, evaluation_id
        super().__init__(code)


class _StageFailed(Exception):
    def __init__(self, reason_code: str, diagnostic: str | None = None):
        self.reason_code, self.diagnostic = reason_code, diagnostic
        super().__init__(reason_code)


# ── Read model ─────────────────────────────────────────────────────────────────

def _artifacts(row: dict) -> list[dict]:
    try:
        value = json.loads(row.get("artifacts") or "[]")
        return value if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def _public(row: dict, scores: list[dict] | None, *, full: bool) -> dict:
    human = None
    if row.get("human_reviewed_at"):
        human = {"score": row.get("human_score"), "comment": row.get("human_comment"),
                 "reviewer_id": row.get("human_reviewer_id"), "reviewed_at": row.get("human_reviewed_at")}
    result = {
        "evaluation_id": row["evaluation_id"], "project_id": row["project_id"],
        "target": {"provider_id": row["target_provider_id"], "provider_name": row.get("target_provider_name")},
        "judge": {"provider_id": row["judge_provider_id"], "provider_name": row.get("judge_provider_name")},
        "lifecycle": row["lifecycle"],
        "task": {"title": row.get("task_title"), "summary": row.get("task_summary")},
        "total_score": row.get("total_score"),
        "final_score": repo.final_score(row),
        "human_review": human,
        "evaluation_set": ({"set_id": row.get("evaluation_set_id"), "name": row.get("evaluation_set_name")}
                           if row.get("evaluation_set_id") else None),
        "review_summary": row.get("review_summary"),
        "scores": [{"key": s["criterion_key"], "description": s.get("criterion_description"),
                    "score": s.get("score"), "rationale": s.get("rationale")} for s in (scores or [])],
        "failure_stage": row.get("failure_stage"), "reason_code": row.get("reason_code"),
        "diagnostic_summary": row.get("diagnostic_summary"),
        "scratch_ref": row.get("scratch_ref"),
        "created_at": row.get("created_at"), "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
    }
    if full:
        result["task"]["instruction"] = row.get("task_instruction")
        result["target_report"] = row.get("target_report")
        result["artifacts"] = _artifacts(row)
    else:
        result["artifact_count"] = len(_artifacts(row))
    return result


def _emit(kind: str, row: dict | None) -> None:
    if row is None:
        return
    payload = {"type": "ai_evaluation." + kind, "project_id": row["project_id"],
               "evaluation_id": row["evaluation_id"], "target_provider_id": row["target_provider_id"],
               "judge_provider_id": row["judge_provider_id"], "lifecycle": row["lifecycle"],
               "total_score": row.get("total_score")}
    broadcast_event_threadsafe(FlowEvent("ai_evaluation." + kind, payload, "*",
                                         project=row["project_id"]))


# ── Start / cancel / read ───────────────────────────────────────────────────────

def start(project_id: str, target_id: str, judge_id: str, created_by: str | None,
          locale: str | None = None, set_id: str | None = None) -> dict:
    if not isinstance(target_id, str) or not isinstance(judge_id, str) or not target_id or not judge_id:
        raise EvaluationError("invalid_request", 422)
    if target_id == judge_id:
        raise EvaluationError("evaluation_same_provider", 422)
    evaluation_set = None
    if set_id:
        evaluation_set = repo.get_set(project_id, set_id)
        if evaluation_set is None:
            raise EvaluationError("evaluation_set_not_found", 404)
        evaluation_set = {**evaluation_set, "constraints": _set_constraints(evaluation_set)}
    # Both connections are frozen before 202 and used unchanged by the worker.
    target = connection.resolve_subject(project_id, target_id, error=EvaluationError, fingerprint=False)
    judge = connection.resolve_subject(project_id, judge_id, error=EvaluationError, fingerprint=False)
    try:
        row = repo.create_pending(project_id=project_id, target_id=target_id, target_name=target.name,
                                  judge_id=judge_id, judge_name=judge.name, created_by=created_by,
                                  evaluation_set=evaluation_set)
    except repo.AlreadyRunning as exc:
        raise EvaluationError("evaluation_already_running", 409, exc.evaluation_id) from exc
    state = {"handle": probe.ProbeHandle(), "target": target, "judge": judge, "set": evaluation_set,
             "language": _LANGUAGES.get((locale or "").split("-")[0].lower(), "English")}
    with _RUNS_LOCK:
        _RUNS[row["evaluation_id"]] = state
    _emit("updated", row)
    try:
        threading.Thread(target=_worker, args=(project_id, row["evaluation_id"], state), daemon=True,
                         name="ai-evaluation-" + row["evaluation_id"][-8:]).start()
    except Exception:
        with _RUNS_LOCK:
            _RUNS.pop(row["evaluation_id"], None)
        repo.finish(project_id, row["evaluation_id"], lifecycle="failed", reason_code="internal_error",
                    failure_stage="pending")
        raise
    return _public(row, [], full=True)


def cancel(project_id: str, evaluation_id: str) -> dict:
    row = repo.get(project_id, evaluation_id)
    if row is None:
        raise EvaluationError("evaluation_not_found", 404)
    stage = row["lifecycle"]
    # DB first: if completion/failure already won, this loses with 409 and interrupts nothing.
    if not repo.finish(project_id, evaluation_id, lifecycle="cancelled", reason_code="cancelled_by_user",
                       failure_stage=stage):
        raise EvaluationError("evaluation_not_cancellable", 409)
    with _RUNS_LOCK:
        state = _RUNS.get(evaluation_id)
    if state:
        state["handle"].cancel()
    row = repo.get(project_id, evaluation_id)
    _emit("cancelled", row)
    return _public(row, repo.scores([evaluation_id]).get(evaluation_id), full=True)


def detail(project_id: str, evaluation_id: str) -> dict:
    row = repo.get(project_id, evaluation_id)
    if row is None:
        raise EvaluationError("evaluation_not_found", 404)
    return _public(row, repo.scores([evaluation_id]).get(evaluation_id), full=True)


def human_review(project_id: str, evaluation_id: str, score, comment, reviewer_id: str | None) -> dict:
    """Human reviewer's score (0-100) and comment on a completed evaluation. Both empty = clear."""
    row = repo.get(project_id, evaluation_id)
    if row is None:
        raise EvaluationError("evaluation_not_found", 404)
    if row["lifecycle"] != "completed":
        raise EvaluationError("evaluation_not_completed", 409)
    if score is not None and (isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100):
        raise EvaluationError("invalid_request", 422)
    if comment is not None and (not isinstance(comment, str) or len(comment) > 8000):
        raise EvaluationError("invalid_request", 422)
    comment = comment.strip() if comment else None
    if not repo.set_human_review(project_id, evaluation_id, score=score, comment=comment,
                                 reviewer_id=reviewer_id):
        raise EvaluationError("evaluation_not_completed", 409)
    row = repo.get(project_id, evaluation_id)
    _emit("updated", row)
    return _public(row, repo.scores([evaluation_id]).get(evaluation_id), full=True)


def _cursor(value: str | None) -> tuple[str, str] | None:
    if value is None:
        return None
    created_at, sep, evaluation_id = value.partition("|")
    if not sep or not created_at or not evaluation_id.startswith("aie_"):
        raise ValueError("invalid cursor")
    return created_at, evaluation_id


def history(project_id: str, target_id: str | None, limit: int, cursor: str | None) -> dict:
    try:
        rows = repo.history(project_id, target_id=target_id, limit=limit + 1, before=_cursor(cursor))
    except ValueError as exc:
        raise EvaluationError("invalid_request", 422) from exc
    page, more = rows[:limit], len(rows) > limit
    scores = repo.scores([row["evaluation_id"] for row in page])
    return {"items": [_public(row, scores.get(row["evaluation_id"]), full=False) for row in page],
            "next_cursor": f"{page[-1]['created_at']}|{page[-1]['evaluation_id']}" if more else None}


def summary(project_id: str, limit: int = 5) -> dict:
    """Overview section: recent scores, running count and last evaluation time."""
    recent = history(project_id, None, limit, None)["items"]
    completed = [item for item in recent if item["lifecycle"] == "completed"]
    return {
        "active": repo.active_count(project_id),
        "recent": [{key: item[key] for key in ("evaluation_id", "target", "judge", "lifecycle",
                                                 "total_score", "final_score", "human_review",
                                                 "evaluation_set", "scores", "review_summary",
                                                 "created_at", "finished_at")}
                   for item in recent],
        "latest_score": completed[0]["final_score"] if completed else None,
        "latest_at": recent[0]["created_at"] if recent else None,
    }


def latest_by_target(project_id: str, provider_ids: list[str]) -> dict[str, dict]:
    """Latest completed evaluation per target AI connection (group member scores)."""
    return {row["target_provider_id"]: {"evaluation_id": row["evaluation_id"],
                                        "total_score": repo.final_score(row),
                                        "finished_at": row["finished_at"]}
            for row in repo.latest_completed(project_id, provider_ids)}


# ── Evaluation sets ──────────────────────────────────────────────────────────────

def _set_constraints(row: dict) -> list[str]:
    try:
        value = json.loads(row.get("constraints") or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _set_public(row: dict) -> dict:
    return {"set_id": row["set_id"], "name": row["name"], "instruction": row["instruction"],
            "constraints": _set_constraints(row), "created_at": row["created_at"],
            "updated_at": row["updated_at"]}


def list_sets(project_id: str) -> dict:
    return {"items": [_set_public(row) for row in repo.list_sets(project_id)]}


def _set_fields(project_id: str, data: dict, own_id: str | None = None) -> dict:
    name, instruction, constraints = data.get("name"), data.get("instruction"), data.get("constraints") or []
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
        raise EvaluationError("evaluation_set_invalid", 422)
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 12_000:
        raise EvaluationError("evaluation_set_invalid", 422)
    if (not isinstance(constraints, list) or len(constraints) > MAX_CONSTRAINTS or
            not all(isinstance(item, str) and len(item) <= 500 for item in constraints)):
        raise EvaluationError("evaluation_set_invalid", 422)
    name = name.strip()
    key = unicodedata.normalize("NFKC", name).casefold()
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    same = repo.get_set_by_name_hash(project_id, digest)
    if same is not None and same["set_id"] != own_id:
        raise EvaluationError("evaluation_set_name_conflict", 409)
    return {"name": name, "name_key": key, "name_key_hash": digest, "instruction": instruction.strip(),
            "constraints": json.dumps([c.strip() for c in constraints if c.strip()], ensure_ascii=False)}


def create_set(project_id: str, data: dict, created_by: str | None) -> dict:
    row = _set_fields(project_id, data)
    row.update(set_id="aies_" + uuid.uuid4().hex, project_id=project_id, created_by=created_by)
    repo.create_set(row)
    return _set_public(repo.get_set(project_id, row["set_id"]))


def update_set(project_id: str, set_id: str, data: dict) -> dict:
    if repo.get_set(project_id, set_id) is None:
        raise EvaluationError("evaluation_set_not_found", 404)
    repo.update_set(project_id, set_id, _set_fields(project_id, data, set_id))
    return _set_public(repo.get_set(project_id, set_id))


def delete_set(project_id: str, set_id: str) -> dict:
    if repo.get_set(project_id, set_id) is None:
        raise EvaluationError("evaluation_set_not_found", 404)
    repo.delete_set(project_id, set_id)
    return {"ok": True}


# ── Worker ───────────────────────────────────────────────────────────────────────

def _fail(project_id: str, evaluation_id: str, stage: str, reason: str, diagnostic: str | None = None) -> None:
    if repo.finish(project_id, evaluation_id, lifecycle="failed", reason_code=reason,
                   failure_stage=stage, diagnostic_summary=diagnostic):
        _emit("failed", repo.get(project_id, evaluation_id))


def _advance(project_id: str, evaluation_id: str, lifecycle: str, **fields) -> bool:
    if not repo.advance(project_id, evaluation_id, lifecycle, **fields):
        return False
    _emit("updated", repo.get(project_id, evaluation_id))
    return True


def _call(subject, prompt: str, handle, timeout: float, scratch: Path, run_id: str) -> str:
    if handle.cancelled:
        raise probe.ProbeCancelled()
    if not connection.still_effective(subject):
        raise _StageFailed("provider_became_unavailable")
    outcome = connection.execute_prompt(subject, prompt, handle, timeout, max_tokens=MODEL_MAX_TOKENS,
                                        scratch=scratch, run_id=run_id)
    if outcome.kind == probe.PROBE_CANCELLED:
        raise probe.ProbeCancelled()
    if outcome.kind != probe.PROBE_OK:
        raise _StageFailed(connection.FAILURE_REASON.get(outcome.kind, "internal_error"))
    if not outcome.text.strip():
        raise _StageFailed("empty_response")
    return outcome.text


def _worker(project_id: str, evaluation_id: str, state: dict) -> None:
    handle, target, judge = state["handle"], state["target"], state["judge"]
    stage, scratch, run_id = "pending", None, None
    try:
        if handle.cancelled:
            return
        run_id = probe._next_run_id()
        scratch = probe._create_scratch(project_id, run_id)
        root = _eval_dir_name(evaluation_id)
        workspace = scratch / root
        workspace.mkdir()

        # 1-3. The judge prepares the Scratch task and the evaluation standard.
        stage = "preparing"
        if not _advance(project_id, evaluation_id, "preparing", scratch_ref=run_id, started_at=now_iso()):
            return
        fixed = state.get("set")
        task = _parse_task(_call(judge, _prepare_prompt(state["language"], root, fixed), handle,
                                 PREPARE_TIMEOUT_SEC, scratch, run_id))
        if fixed:
            # An evaluation set fixes the task; the judge only adds files and the standard.
            task["instruction"] = fixed["instruction"][:12_000]
            task["constraints"] = list(fixed["constraints"])
        baseline = _materialize(workspace, task)
        repo.write_criteria(evaluation_id, [{"key": key, "description": task["criteria"][key]}
                                            for key in CRITERION_KEYS])

        # 4. The target performs the task inside the Scratch.
        stage = "running"
        if not _advance(project_id, evaluation_id, "running", task_title=task["title"],
                        task_summary=task["summary"], task_instruction=task["instruction"]):
            return
        report = _call(target, _target_prompt(task, target.exec_type, root), handle,
                       TARGET_TIMEOUT_SEC, scratch, run_id)
        if target.exec_type == "api":
            # An API connection has no file tools: its answer is its work product.
            (workspace / OUTPUT_DIR / "RESPONSE.md").write_text(report, encoding="utf-8")

        # 5. Collect results / work products.
        artifacts = _collect(workspace, baseline)
        stage = "reviewing"
        if not _advance(project_id, evaluation_id, "reviewing",
                        target_report=repo.redact(report, 20_000), artifacts=artifacts):
            return

        # 6-9. The judge reviews, scores each criterion and writes the review.
        review = _parse_review(_call(judge, _review_prompt(task, report, workspace, root, artifacts,
                                                           judge.exec_type, state["language"]),
                                     handle, REVIEW_TIMEOUT_SEC, scratch, run_id))
        total = round(sum(item["score"] for item in review["scores"]) / len(review["scores"]))
        # 10. Persist as history.
        if repo.complete(project_id, evaluation_id, total_score=total, review_summary=review["review"],
                         item_scores=review["scores"]):
            _emit("completed", repo.get(project_id, evaluation_id))
    except probe.ProbeCancelled:
        if repo.finish(project_id, evaluation_id, lifecycle="cancelled", reason_code="cancelled_by_user",
                       failure_stage=stage):
            _emit("cancelled", repo.get(project_id, evaluation_id))
    except _StageFailed as exc:
        _fail(project_id, evaluation_id, stage, exc.reason_code, exc.diagnostic)
    except Exception:
        logger.exception("AI evaluation %s failed", evaluation_id)
        _fail(project_id, evaluation_id, stage, "internal_error")
    finally:
        with _RUNS_LOCK:
            _RUNS.pop(evaluation_id, None)
        if scratch is not None:
            # Kept as the evaluation's work-product reference; the standard retention sweep
            # removes it once the retention window has passed.
            probe._mark_scratch_completed(project_id, run_id, scratch, now_iso())


# ── Judge / target contracts ─────────────────────────────────────────────────────

def _eval_dir_name(evaluation_id: str) -> str:
    return "eval_" + evaluation_id.removeprefix("aie_")[:12]


def _criteria_lines() -> str:
    return "\n".join(f'- "{key}": {text}' for key, text in CRITERIA)


def _prepare_prompt(language: str, root: str, fixed: dict | None = None) -> str:
    keys = ", ".join(f'"{key}"' for key in CRITERION_KEYS)
    if fixed:
        given = "\n".join(f"- {item}" for item in fixed["constraints"]) or "- (none)"
        design = f"""The task is FIXED by an evaluation set. Do not change it.
Instruction (copy it unchanged into "instruction"):
{fixed['instruction']}
Constraints (copy them into "constraints"; add none if the list is non-empty):
{given}
Provide the starting files the instruction needs, a title, a summary and the standard below."""
    else:
        design = """Design ONE realistic, self-contained software task that another AI (the TARGET) will perform
inside the evaluation directory. The task must be finishable in about 20 minutes
without network access or external services, must require real work (writing or changing files,
reasoning, checking the result), and must have an objectively checkable outcome."""
    return f"""You are the JUDGE of an AI capability evaluation. The evaluation directory is `{root}/`.
{design}
The server lays out the directory from your reply:
  {root}/{INSTRUCTION_FILE}             your "instruction"
  {root}/{SAMPLE_DIR}/                  your starting "files" (may be empty)
  {root}/{CONSTRAINTS_FILE}   your "constraints" and "criteria"
  {root}/{OUTPUT_DIR}/                  where the TARGET must put its deliverables
Do not create or modify any files yourself.

Also write, for this specific task, what good performance looks like for each criterion:
{_criteria_lines()}

Write every human-readable text value in {language}.
Reply with ONLY one JSON object, no prose before or after it:
{{
  "title": "short task title",
  "summary": "one or two sentences describing the task",
  "instruction": "the complete instruction the TARGET will receive",
  "files": [{{"path": "relative/path.ext", "content": "file content"}}],
  "constraints": ["objectively checkable constraint the result must satisfy"],
  "criteria": {{{keys}: "what good looks like for this task"}}
}}
"criteria" must contain exactly these keys: {keys}. "files" and "constraints" may be empty.
File paths are relative to `{root}/{SAMPLE_DIR}/`, use '/' and never '..'."""


def _target_prompt(task: dict, exec_type: str, root: str) -> str:
    if exec_type == "api":
        mode = ("You cannot run commands or edit files in this mode. Give your complete solution in this "
                "reply: include every file you would create or change as a fenced code block preceded by "
                f"its path under `{root}/{OUTPUT_DIR}/` or `{root}/{SAMPLE_DIR}/`, then describe how you "
                "would verify it.")
    else:
        mode = (f"Your current directory contains `{root}/`. Read `{root}/{INSTRUCTION_FILE}` and "
                f"`{root}/{CONSTRAINTS_FILE}`, work on `{root}/{SAMPLE_DIR}/` where the task needs it and "
                f"put your deliverables in `{root}/{OUTPUT_DIR}/`. Do ALL work inside `{root}/` only. "
                "Do not access the network or anything outside the current directory.")
    files = "\n".join(f"- {root}/{SAMPLE_DIR}/{item['path']}" for item in task["files"]) or "- (none)"
    constraints = "\n".join(f"- {item}" for item in task["constraints"]) or "- (none)"
    return f"""You are being evaluated on a practical task.
{mode}

Task: {task['title']}

{task['instruction']}

Constraints the result must satisfy:
{constraints}

Starting files:
{files}

When you are finished, reply with a concise report: what you did, which files you created or
changed, how you verified the result, and anything you could not finish."""


def _review_prompt(task: dict, report: str, workspace: Path, root: str, artifacts: list[dict],
                   exec_type: str, language: str) -> str:
    budget = REVIEW_ARTIFACT_BUDGET_CHARS
    blocks = []
    for item in artifacts:
        text = _read_text(workspace / item["path"])
        if text is None or budget <= 0:
            blocks.append(f"### {item['path']} ({item['origin']}, {item['size']} bytes, content not included)")
            continue
        text = text[:budget]
        budget -= len(text)
        blocks.append(f"### {item['path']} ({item['origin']}, {item['size']} bytes)\n```\n{text}\n```")
    inspect = (f"You may inspect `{root}/` in the current directory, but do not modify anything."
               if exec_type == "cli" else "")
    criteria = "\n".join(f'- "{key}": {task["criteria"][key]}' for key in CRITERION_KEYS)
    constraints = "\n".join(f"- {item}" for item in task["constraints"]) or "- (none)"
    keys = ", ".join(f'"{key}"' for key in CRITERION_KEYS)
    return f"""You are the JUDGE of an AI capability evaluation. Review the TARGET's work strictly and fairly.
{inspect}

Task: {task['title']}
Instruction given to the TARGET:
{task['instruction']}

Constraints the result must satisfy ({CONSTRAINTS_FILE}):
{constraints}

Evaluation standard for this task:
{criteria}

TARGET's final report:
{report[:20000]}

Files in `{root}/` (deliverables belong in `{OUTPUT_DIR}/`):
{chr(10).join(blocks) or '(no files)'}

Score each criterion from 0 to 100 with a short rationale grounded in concrete evidence from the
files and the report. Then write an overall review with the key strengths, weaknesses and evidence.
Write every human-readable text value in {language}.
Reply with ONLY one JSON object:
{{
  "scores": {{{keys}: {{"score": 0, "rationale": "evidence-based reason"}}}},
  "review": "overall review"
}}"""


def _json_object(text: str, required: str) -> dict:
    """First JSON object in a model reply that carries `required` (fences/prose tolerated)."""
    stripped = text.strip()
    candidates = [stripped]
    candidates += re.findall(r"```(?:json)?\s*(.*?)```", stripped, flags=re.S)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict) and required in value:
                return value
        except ValueError:
            pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", stripped):
        try:
            value, _ = decoder.raw_decode(stripped, match.start())
        except ValueError:
            continue
        if isinstance(value, dict) and required in value:
            return value
    raise _StageFailed("judge_output_invalid", "The judge reply did not contain the expected JSON object.")


def _text(value, limit: int, *, required: bool = True) -> str:
    if not isinstance(value, str) or (required and not value.strip()):
        raise _StageFailed("judge_output_invalid", "A required text field was missing.")
    return value.strip()[:limit]


def _safe_path(raw) -> str:
    if not isinstance(raw, str):
        raise _StageFailed("judge_output_invalid", "A task file path was not a string.")
    path = PurePosixPath(raw.strip().replace("\\", "/"))
    parts = path.parts
    if not parts or path.is_absolute() or len(parts) > 6 or not all(_PATH_PART.fullmatch(p) for p in parts):
        raise _StageFailed("judge_output_invalid", "A task file path was not a safe relative path.")
    return path.as_posix()


def _parse_task(text: str) -> dict:
    data = _json_object(text, "instruction")
    criteria = data.get("criteria")
    if not isinstance(criteria, dict):
        raise _StageFailed("judge_output_invalid", "The judge did not write the evaluation criteria.")
    files = data.get("files") or []
    if not isinstance(files, list) or len(files) > MAX_TASK_FILES:
        raise _StageFailed("judge_output_invalid", "The task file list was invalid or too long.")
    seen, clean_files = set(), []
    for item in files:
        if not isinstance(item, dict):
            raise _StageFailed("judge_output_invalid", "A task file entry was not an object.")
        path = _safe_path(item.get("path"))
        content = item.get("content")
        if path in seen or not isinstance(content, str) or len(content) > MAX_TASK_FILE_CHARS:
            raise _StageFailed("judge_output_invalid", "A task file was duplicated, missing content or too large.")
        seen.add(path)
        clean_files.append({"path": path, "content": content})
    constraints = data.get("constraints") or []
    if not isinstance(constraints, list) or len(constraints) > MAX_CONSTRAINTS:
        raise _StageFailed("judge_output_invalid", "The constraint list was invalid or too long.")
    return {
        "title": _text(data.get("title"), 200),
        "summary": _text(data.get("summary"), 1000),
        "instruction": _text(data.get("instruction"), 12_000),
        "files": clean_files,
        "constraints": [_text(item, 500) for item in constraints],
        "criteria": {key: _text(criteria.get(key), 1000) for key in CRITERION_KEYS},
    }


def _parse_review(text: str) -> dict:
    data = _json_object(text, "scores")
    raw = data.get("scores")
    if not isinstance(raw, dict):
        raise _StageFailed("judge_output_invalid", "The judge did not return per-criterion scores.")
    scores = []
    for key in CRITERION_KEYS:
        item = raw.get(key)
        score = item.get("score") if isinstance(item, dict) else item
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
            raise _StageFailed("judge_output_invalid", f"The score for {key} was missing or out of range.")
        rationale = item.get("rationale") if isinstance(item, dict) else None
        scores.append({"key": key, "score": int(round(score)),
                       "rationale": rationale.strip()[:4000] if isinstance(rationale, str) else None})
    return {"scores": scores, "review": _text(data.get("review"), 8000)}


# ── Scratch materialize / collect ─────────────────────────────────────────────────

def _materialize(workspace: Path, task: dict) -> dict[str, str]:
    """Lay out instruction.md / sample-project/ / expected-constraints.json / output/."""
    root = workspace.resolve(strict=True)
    (workspace / SAMPLE_DIR).mkdir()
    (workspace / OUTPUT_DIR).mkdir()
    baseline = {}

    def write(relative: str, data: bytes) -> None:
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if root not in target.parent.resolve(strict=True).parents and target.parent.resolve(strict=True) != root:
            raise _StageFailed("judge_output_invalid", "A task file escaped the workspace.")
        target.write_bytes(data)
        baseline[relative] = hashlib.sha256(data).hexdigest()

    write(INSTRUCTION_FILE, f"# {task['title']}\n\n{task['instruction']}\n".encode("utf-8"))
    write(CONSTRAINTS_FILE, json.dumps({"title": task["title"], "summary": task["summary"],
                                        "constraints": task["constraints"], "criteria": task["criteria"]},
                                       ensure_ascii=False, indent=2).encode("utf-8"))
    for item in task["files"]:
        write(f"{SAMPLE_DIR}/{item['path']}", item["content"].encode("utf-8"))
    return baseline


def _read_text(path: Path) -> str | None:
    try:
        if probe._is_reparse_or_symlink(path) or path.stat().st_size > MAX_ARTIFACT_READ_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def _collect(workspace: Path, baseline: dict[str, str]) -> list[dict]:
    items = []
    for path in sorted(workspace.rglob("*")):
        if len(items) >= MAX_ARTIFACT_FILES:
            break
        relative = path.relative_to(workspace).as_posix()
        if any(part in _SKIPPED_DIRS for part in relative.split("/")[:-1]):
            continue
        if probe._is_reparse_or_symlink(path) or not path.is_file():
            continue
        try:
            data = path.read_bytes() if path.stat().st_size <= MAX_ARTIFACT_READ_BYTES else None
            size = path.stat().st_size
        except OSError:
            continue
        digest = hashlib.sha256(data).hexdigest() if data is not None else None
        origin = ("created" if relative not in baseline else
                  "unchanged" if digest == baseline[relative] else "modified")
        text = _read_text(path)
        items.append({"path": relative, "size": size, "sha256": digest, "origin": origin,
                      "excerpt": repo.redact(text[:ARTIFACT_EXCERPT_CHARS], ARTIFACT_EXCERPT_CHARS)
                      if text is not None else None})
    return items
