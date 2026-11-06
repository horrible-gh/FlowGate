"""Hand unsubmitted drafts to the next token (flowgate.v02.0137 T0004 §3, NR0003 A-4/A-5).

A worker can only submit `doc_path` files from its OWN token scratch. Two moments move a
worker onto a new token while its previous drafts stay behind:

* R2 — the same run gets a new token (`_prepare_retry_token` reissue: a review-loop stage
  switch or a spent/expiring token). Sources: the previous token's scratch and this run's
  own scratch (the CLI worker's cwd).
* R3 — a follow-up run starts on the same document after a run that left a retained
  scratch. Sources: that run's retained scratch and its last token's scratch.

In both cases the files are COPIED (token_scratch.carry_over) into
`<new token scratch>/carryover/<label>/` and the new prompt says so. Nothing is submitted
for the worker and no path validation is relaxed: the worker must read, check and submit
with its current token like any other file.

Every check here fails closed into "no copy": a project / group / document / requester
mismatch, a dead target token, an unowned directory, or — while run ids can still repeat
across servers (0138) — a retained scratch whose owner record does not list the row's
token, document, group and requester (or whose manifest does not line up in time with
the row). The original files are never moved or deleted here.

TR0005 rev1: a token scratch that itself received a carry-over hands it on (flattened by
origin, newest copy wins), so drafts survive a chain of reissues or failed runs; and a
file is refused on content — credential shapes, or any string the tokens table knows —
not only on the raw tokens the current run issued.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.services import token_scratch
from modules.flow_gate.storage import paths as storage_paths

from .runtime import _known_run_raw_tokens, logger

# Run scratch top-level entries that are infrastructure, not drafts (T0004 §3-5).
RUN_SCRATCH_EXCLUDE = ("tmp", "cache", "last_message.txt")
TOKEN_SCRATCH_EXCLUDE = (token_scratch.CARRYOVER_DIR, "source-snapshots")
# R3 identity cross-check (0138 guard): how far apart the run row and the scratch
# manifest may be before the directory is treated as another run's.
_RUN_ROW_SKEW = timedelta(minutes=10)
# Scopes whose product is a document submission a draft could feed.
_CARRY_SCOPES = frozenset({"new", "edit", "review", "rework", "failure_origin_review"})

NOTICE_HEADING = "## Carried-over drafts (not submitted)"

# TR0005 rev1 (0138 guard, review finding 2): who a run scratch belongs to. The run-scratch
# manifest only names project / run id / path / time, and run ids can repeat across
# servers while 0138 is open, so "same id, close in time" proved nothing. This record is
# written next to it when the run's first token exists and gains every token the run is
# later given. Token ids are the tokens table's primary key (unique in the shared DB), so a
# row whose `token_id` is listed here, with the same document, group and requester, names
# THIS directory. Without it a retained run scratch is never a carry-over source.
RUN_OWNER_NAME = ".flowgate-run-owner.json"
RUN_OWNER_KIND = "flowgate.ai-invoke.run-owner"
RUN_OWNER_SCHEMA = 1
_RUN_OWNER_TOKENS_MAX = 256
_owner_lock = threading.Lock()


def _aware(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else None


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def target_token_problem(row: Optional[dict], *, project_id: str, group_id: Optional[str],
                         doc_ref: Optional[str], issued_to: Optional[str],
                         run_id: Optional[str]) -> Optional[str]:
    """Why the NEW token may not receive drafts, or None (T0004 §3-5)."""
    if row is None:
        return "target_token_missing"
    if row.get("revoked_at") or row.get("consumed_at"):
        return "target_token_not_live"
    expires = _aware(row.get("expires_at"))
    if expires is None or expires <= datetime.now(timezone.utc):
        return "target_token_expired"
    if _text(row.get("project")) != _text(project_id):
        return "project_mismatch"
    if group_id and _text(row.get("group_id")) not in ("", _text(group_id)):
        return "group_mismatch"
    if _text(row.get("doc_ref")) != _text(doc_ref):
        return "doc_mismatch"
    if _text(row.get("issued_to")) != _text(issued_to):
        return "requester_mismatch"
    if run_id and row.get("ai_run_id") and row.get("ai_run_id") != run_id:
        return "target_token_other_run"
    return None


def source_token_problem(row: Optional[dict], target: dict) -> Optional[str]:
    """Why the PREVIOUS token's scratch may not be a source, or None.

    The previous token is normally spent (consumed / expired) — its state is not checked,
    only that it was minted for the same project, group, document and requester.
    """
    if row is None:
        return "source_token_missing"
    for key, code in (("project", "project_mismatch"), ("doc_ref", "doc_mismatch"),
                      ("issued_to", "requester_mismatch")):
        if _text(row.get(key)) != _text(target.get(key)):
            return code
    if _text(row.get("group_id")) and _text(target.get("group_id")) \
            and _text(row.get("group_id")) != _text(target.get("group_id")):
        return "group_mismatch"
    if not row.get("scratch_dir"):
        return "source_scratch_unrecorded"
    return None


def _write_owner(path: Path, record: dict) -> None:
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
    os.replace(temporary, path)


def bind_run_scratch(project_id: str, run_id: str, scratch: Path, *, group_id: Optional[str],
                     doc_ref: Optional[str], issued_to: Optional[str],
                     token_id: Optional[str]) -> bool:
    """Write the owner record of a NEW run scratch (admission, right after _create_scratch).

    Never raises; a failure only means the directory can never be a carry-over source.
    """
    try:
        from .runtime import _validate_scratch_manifest

        manifest, _reason = _validate_scratch_manifest(project_id, run_id, Path(scratch))
        if manifest is None:
            return False
        record = {
            "schema": RUN_OWNER_SCHEMA, "owner": RUN_OWNER_KIND, "project_id": project_id,
            "run_id": run_id, "run_nonce": uuid.uuid4().hex, "group_id": group_id,
            "doc_ref": doc_ref, "issued_to": issued_to,
            "token_ids": [token_id] if token_id else [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with _owner_lock:
            path = Path(scratch) / RUN_OWNER_NAME
            if path.exists() or path.is_symlink():
                return False
            _write_owner(path, record)
        return True
    except Exception:
        logger.warning("ai-invoke %s: run scratch owner record not written", run_id, exc_info=True)
        return False


def _run_owner(project_id: str, run_id: str, scratch: Path) -> Optional[dict]:
    try:
        path = Path(scratch) / RUN_OWNER_NAME
        if path.is_symlink() or not path.is_file() or token_scratch._unsafe_link(path):
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("owner") != RUN_OWNER_KIND \
            or record.get("schema") != RUN_OWNER_SCHEMA or record.get("project_id") != project_id \
            or record.get("run_id") != run_id or not isinstance(record.get("token_ids"), list):
        return None
    return record


def bind_run_token(run: Optional[dict], token_id: Optional[str]) -> bool:
    """Add a reissued token to the live run's owner record. Never raises."""
    try:
        if not run or not token_id or not run.get("scratch_dir"):
            return False
        scratch = Path(run["scratch_dir"])
        with _owner_lock:
            record = _run_owner(run.get("project_id"), run.get("run_id"), scratch)
            if record is None:
                return False
            if token_id in record["token_ids"]:
                return True
            if len(record["token_ids"]) >= _RUN_OWNER_TOKENS_MAX:
                return False
            record["token_ids"].append(token_id)
            _write_owner(scratch / RUN_OWNER_NAME, record)
        return True
    except Exception:
        logger.warning("ai-invoke %s: run scratch owner token not recorded",
                       (run or {}).get("run_id"), exc_info=True)
        return False


def run_owner_problem(owner: Optional[dict], row: dict) -> Optional[str]:
    """Why this owner record does NOT prove the row names its directory, or None."""
    if owner is None:
        return "run_scratch_owner_unproven"
    for key in ("doc_ref", "group_id", "issued_to"):
        if _text(owner.get(key)) != _text(row.get(key)):
            return "run_scratch_owner_mismatch"
    if not row.get("token_id") or row.get("token_id") not in owner.get("token_ids", []):
        return "run_scratch_owner_mismatch"
    return None


def _flowgate_token_probe(candidate: str) -> bool:
    """Is this string a FlowGate token (any state)? Hash lookup only; a DB failure raises,
    so token_scratch refuses the file (`secret_check_failed`) instead of guessing."""
    from modules.flow_gate.services import token_service

    return token_service._find_token_by_raw(candidate) is not None


def _token_scratch_path(row: dict, project_id: str) -> Optional[Path]:
    try:
        return storage_paths.resolve_storage_dir(row["scratch_dir"], project_id)
    except Exception:
        return None


def _run_scratch_source(project_id: str, run_id: str, scratch: Path) -> tuple[Optional[dict], Optional[dict], str]:
    from .runtime import _validate_scratch_manifest

    manifest, reason = _validate_scratch_manifest(project_id, run_id, scratch)
    if manifest is None:
        return None, None, f"run_scratch_{reason}"
    return ({"kind": "run_scratch", "id": run_id, "path": str(scratch),
             "exclude_top": RUN_SCRATCH_EXCLUDE}, manifest, "ok")


def _doc_revision(doc_ref: Optional[str]) -> Optional[int]:
    try:
        doc = db_docs.get_by_id(doc_ref) if doc_ref else None
    except Exception:
        return None
    return int((doc or {}).get("revision_no") or 0) if doc else None


def notice(summary: dict, *, source_text: str) -> str:
    """The prompt section that tells the next worker what was copied, or ''."""
    if summary.get("status") != "copied":
        return ""
    files = summary.get("files") or []
    skipped = summary.get("skipped") or []
    lines = [
        NOTICE_HEADING,
        "---",
        f"Drafts left by {source_text} were COPIED (not moved, not submitted) into this "
        f"token's scratch: {summary.get('dest')}",
        f"{len(files)} file(s), {int(summary.get('bytes') or 0)} bytes. "
        f"{token_scratch.CARRYOVER_MANIFEST} in that folder lists each file's source, size "
        "and sha256 and the document revision at copy time.",
        "Read them, compare with the document's CURRENT revision (it may have moved since), "
        "fix what is needed, then submit with THIS token as usual (doc_path must be a file "
        "inside this token's scratch). Never reuse the previous token.",
    ]
    if skipped:
        lines.append(f"{len(skipped)} entr(y/ies) were not copied (limits / unsafe / secret); "
                     f"see `skipped` in {token_scratch.CARRYOVER_MANIFEST}.")
    if summary.get("relayed"):
        lines.append(f"{int(summary.get('relayed') or 0)} of them were relayed from earlier "
                     "carry-overs (newest copy of each kept, flattened by origin).")
    lines.append("If this path is not reachable from where you run (remote agent), ignore "
                 "this section.")
    return "\n".join(lines)


def _attach(mention: str, text: str) -> str:
    return mention if not text else mention.rstrip() + "\n\n" + text


def _remember(run: Optional[dict], record: dict) -> None:
    if run is not None:
        run.setdefault("carryovers", []).append(record)


def _short(summary: dict) -> dict:
    return {
        "status": summary.get("status"),
        "reason": summary.get("reason"),
        "label": summary.get("label"),
        "files": len(summary.get("files") or []),
        "bytes": int(summary.get("bytes") or 0),
        "skipped": len(summary.get("skipped") or []),
    }


def carry_into_reissued_token(run: dict, *, previous_token_id: Optional[str],
                              previous_token_scratch: Optional[str], issue: dict,
                              mention: Optional[str]) -> Optional[str]:
    """R2: same run, new token. Returns the (possibly extended) mention. Never raises."""
    if not mention:
        return mention
    try:
        new_token_id = issue.get("token_id")
        new_scratch = issue.get("scratch_dir")
        if not new_token_id or not new_scratch or new_token_id == previous_token_id:
            # Same token (or nothing new): its scratch is unchanged, nothing to copy (§3-2).
            return mention
        record = {"kind": "reissue", "from_token_id": previous_token_id,
                  "to_token_id": new_token_id, "hop_kind": run.get("hop_kind")}
        new_row = db_tokens.get_by_id(new_token_id)
        problem = target_token_problem(
            new_row, project_id=run.get("project_id"), group_id=run.get("group_id"),
            doc_ref=(new_row or {}).get("doc_ref"), issued_to=run.get("issued_to"),
            run_id=run.get("run_id"),
        )
        if problem is None and previous_token_id:
            prev_row = db_tokens.get_by_id(previous_token_id)
            # The previous token must be bound to the same document as the new one.
            problem = None if prev_row is None else (
                "doc_mismatch" if _text(prev_row.get("doc_ref")) != _text(new_row.get("doc_ref")) else None
            )
        else:
            prev_row = None
        if problem is not None:
            record.update(status="skipped", reason=problem)
            logger.warning("ai-invoke %s: carry-over to reissued token skipped: %s",
                           run.get("run_id"), problem)
            _remember(run, record)
            return mention
        sources: list[dict] = []
        notes: list[dict] = []
        if prev_row is not None:
            prev_problem = source_token_problem(prev_row, new_row)
            prev_path = _token_scratch_path(prev_row, run["project_id"]) if prev_problem is None else None
            if prev_problem is None and prev_path is not None and previous_token_scratch \
                    and not token_scratch._same_path(prev_path.resolve(strict=False),
                                                     Path(previous_token_scratch).resolve(strict=False)):
                prev_problem = "source_scratch_path_mismatch"
            if prev_problem is None and prev_path is not None:
                sources.append({"kind": "token_scratch", "id": previous_token_id,
                                "path": str(prev_path), "exclude_top": TOKEN_SCRATCH_EXCLUDE,
                                "relay_carryover": True})
            else:
                notes.append({"path": str(previous_token_id), "reason": prev_problem or "source_scratch_missing"})
        elif previous_token_id:
            notes.append({"path": str(previous_token_id), "reason": "source_token_missing"})
        run_source, _manifest, reason = _run_scratch_source(
            run["project_id"], run["run_id"], Path(run["scratch_dir"]))
        if run_source is not None:
            sources.append(run_source)
        else:
            notes.append({"path": str(run.get("run_id")), "reason": reason})
        label = f"hop{int(run.get('attempt_no') or 0) + 1}_{previous_token_id or 'none'}"
        summary = token_scratch.carry_over(
            run["project_id"], target_token_id=new_token_id, target_scratch=Path(new_scratch),
            label=label, sources=sources,
            meta={"kind": "reissue", "run_id": run.get("run_id"),
                  "from_token_id": previous_token_id, "doc_ref": new_row.get("doc_ref"),
                  "doc_revision_at_copy": _doc_revision(new_row.get("doc_ref")),
                  "source_notes": notes},
            secrets=tuple(_known_run_raw_tokens(run)),
            token_probe=_flowgate_token_probe,
        )
        record.update(_short(summary))
        _remember(run, record)
        return _attach(mention, notice(summary, source_text=f"the previous attempt (token {previous_token_id})"))
    except Exception:
        logger.warning("ai-invoke %s: carry-over to reissued token failed", run.get("run_id"),
                       exc_info=True)
        return mention


def _previous_run_row(group_id: str, doc_ref: str) -> Optional[dict]:
    from modules.flow_gate.db import ai_invoke_runs as db_runs

    return db_runs.latest_finished_for_group(group_id, doc_ref=doc_ref)


def carry_into_new_run(*, project_id: str, group_id: Optional[str], doc_ref: Optional[str],
                       issued_to: Optional[str], run_id: str, action_scope: Optional[str],
                       issue: dict, mention: Optional[str],
                       run_live=None) -> tuple[Optional[str], Optional[dict]]:
    """R3: a new run on the same document after a retained one. Never raises.

    Returns `(mention, record)`; `record` is None when there was nothing to consider.
    """
    if not mention or not group_id or not doc_ref or action_scope not in _CARRY_SCOPES:
        return mention, None
    record: dict = {"kind": "new_run", "to_token_id": issue.get("token_id")}
    try:
        row = _previous_run_row(group_id, doc_ref)
        problem = _previous_run_problem(row, project_id=project_id, group_id=group_id,
                                        doc_ref=doc_ref, issued_to=issued_to, run_id=run_id,
                                        run_live=run_live)
        if row is not None:
            record["from_run_id"] = row.get("run_id")
        if problem is None:
            new_row = db_tokens.get_by_id(issue.get("token_id")) if issue.get("token_id") else None
            problem = target_token_problem(new_row, project_id=project_id, group_id=group_id,
                                           doc_ref=doc_ref, issued_to=issued_to, run_id=run_id)
        if problem is not None:
            record.update(status="skipped", reason=problem)
            if problem not in ("no_previous_run", "previous_settled"):
                logger.warning("ai-invoke %s: carry-over from previous run skipped: %s",
                               run_id, problem)
            return mention, record
        sources, notes = _previous_run_sources(row, project_id)
        if not sources:
            record.update(status="skipped", reason="no_verified_source",
                          notes=[n.get("reason") for n in notes])
            logger.warning("ai-invoke %s: carry-over from %s skipped: no verified source %s",
                           run_id, row.get("run_id"), [n.get("reason") for n in notes])
            return mention, record
        summary = token_scratch.carry_over(
            project_id, target_token_id=issue["token_id"], target_scratch=Path(issue["scratch_dir"]),
            label=f"run_{row.get('run_id')}", sources=sources,
            meta={"kind": "new_run", "run_id": run_id, "from_run_id": row.get("run_id"),
                  "from_run_outcome": row.get("outcome"), "from_run_finished_at": row.get("finished_at"),
                  "from_token_id": row.get("token_id"), "doc_ref": doc_ref,
                  "doc_revision_at_copy": _doc_revision(doc_ref), "source_notes": notes},
            secrets=tuple(t for t in (issue.get("raw_token"),) if t),
            token_probe=_flowgate_token_probe,
        )
        record.update(_short(summary))
        return _attach(mention, notice(summary, source_text=f"the previous run {row.get('run_id')}")), record
    except Exception:
        logger.warning("ai-invoke %s: carry-over from previous run failed", run_id, exc_info=True)
        record.update(status="failed")
        return mention, record


def _previous_run_problem(row: Optional[dict], *, project_id: str, group_id: str, doc_ref: str,
                          issued_to: Optional[str], run_id: str, run_live=None) -> Optional[str]:
    if row is None:
        return "no_previous_run"
    if row.get("run_id") == run_id:
        # 0138: the id this run was just given already names a finished row — that row is
        # another run's, and so may be the directory. Never hand it over.
        return "run_id_collision"
    if row.get("status") != "finished" or not row.get("finished_at"):
        return "previous_not_finished"
    if _text(row.get("project_id")) != _text(project_id) or _text(row.get("group_id")) != _text(group_id) \
            or _text(row.get("doc_ref")) != _text(doc_ref):
        return "owner_mismatch"
    if _text(row.get("issued_to")) != _text(issued_to):
        return "requester_mismatch"
    if not row.get("scratch_retained"):
        return "previous_settled"
    finished = _aware(row.get("finished_at"))
    if finished is None:
        return "previous_not_finished"
    try:
        from modules.flow_gate.settings import scratch_retention

        retention = scratch_retention.effective_retention()
    except Exception:
        retention = timedelta(days=7)
    if datetime.now(timezone.utc) - finished >= retention:
        return "previous_expired"
    if run_live is not None:
        try:
            if run_live(row.get("run_id")):
                return "previous_id_live"
        except Exception:
            return "previous_liveness_unknown"
    return None


def _previous_run_sources(row: dict, project_id: str) -> tuple[list[dict], list[dict]]:
    """Verified sources of a retained run, cross-checked against its own row (0138 guard)."""
    sources: list[dict] = []
    notes: list[dict] = []
    started = _aware(row.get("started_at"))
    finished = _aware(row.get("finished_at"))
    run_id = row.get("run_id")
    scratch = None
    try:
        scratch = storage_paths.resolve_storage_dir(row["scratch_retained"], project_id)
    except Exception:
        scratch = None
    if scratch is None:
        notes.append({"path": str(run_id), "reason": "retained_path_unresolved"})
    else:
        source, manifest, reason = _run_scratch_source(project_id, str(run_id), scratch)
        if source is None:
            notes.append({"path": str(run_id), "reason": reason})
        else:
            created = _aware(manifest.get("created_at"))
            completed = _aware(manifest.get("completed_at"))
            owner_problem = run_owner_problem(_run_owner(project_id, str(run_id), scratch), row)
            # 0138 (merged after this group's base) stamps an execution_nonce on both the
            # manifest and the run row. Where either side carries one they must agree; on a
            # tree without 0138 both are absent and the owner record above decides alone.
            row_nonce, dir_nonce = row.get("execution_nonce"), manifest.get("execution_nonce")
            if owner_problem is None and (row_nonce or dir_nonce) and row_nonce != dir_nonce:
                owner_problem = "run_scratch_nonce_mismatch"
            if (started is None or finished is None or created is None or completed is None
                    or abs(created - started) > _RUN_ROW_SKEW
                    or abs(completed - finished) > _RUN_ROW_SKEW):
                # Same id, different run (0138) — or a row this check cannot pin down.
                notes.append({"path": str(run_id), "reason": "run_scratch_identity_unproven"})
            elif owner_problem is not None:
                # TR0005 rev1: time alone never proves ownership. The directory must list
                # the row's (DB-unique) token and the row's document / group / requester.
                notes.append({"path": str(run_id), "reason": owner_problem})
            else:
                sources.append(source)
    token_id = row.get("token_id")
    token_row = db_tokens.get_by_id(token_id) if token_id else None
    if token_row is None:
        notes.append({"path": str(token_id), "reason": "source_token_missing"})
    else:
        problem = source_token_problem(token_row, {
            "project": row.get("project_id"), "group_id": row.get("group_id"),
            "doc_ref": row.get("doc_ref"), "issued_to": row.get("issued_to"),
        })
        minted = _aware(token_row.get("created_at"))
        if problem is None and token_row.get("ai_run_id") and token_row.get("ai_run_id") != run_id:
            problem = "source_token_other_run"
        if problem is None and (minted is None or started is None or finished is None
                                or minted < started - _RUN_ROW_SKEW or minted > finished + _RUN_ROW_SKEW):
            problem = "source_token_outside_run_window"
        path = _token_scratch_path(token_row, project_id) if problem is None else None
        if problem is None and path is not None:
            sources.append({"kind": "token_scratch", "id": token_id, "path": str(path),
                            "exclude_top": TOKEN_SCRATCH_EXCLUDE, "relay_carryover": True})
        else:
            notes.append({"path": str(token_id), "reason": problem or "source_scratch_missing"})
    return sources, notes
