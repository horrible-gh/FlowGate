"""Git rerere candidates for held ordinary finalize conflicts.

Rerere owns replay. The checkpoint ledger owns lineage and per-path provenance;
the existing resolver/review gate decides whether a candidate can be accepted.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from .credentials import GitServiceError

_ID = re.compile(r"^[0-9a-f]{40,64}(?:\.[0-9]+)?$")
_RERERE = ["-c", "rerere.enabled=true", "-c", "rerere.autoupdate=false", "rerere"]


def _git_path(root: Path, name: str) -> Path:
    from modules.flow_gate.services import git_service as gs
    proc = gs._run_git(["rev-parse", "--git-path", name], cwd=root)
    if proc.returncode:
        raise GitServiceError(500, "rerere_error", "Cannot locate Git rerere cache")
    path = Path(proc.stdout.strip())
    return path if path.is_absolute() else root / path


def _ids(root: Path) -> dict[str, str]:
    path = _git_path(root, "MERGE_RR")
    if not path.exists():
        return {}
    result = {}
    for record in path.read_bytes().split(b"\0"):
        if not record or b"\t" not in record:
            continue
        digest, name = record.split(b"\t", 1)
        try:
            rerere_id = digest.decode("ascii")
            file_path = name.decode("utf-8", "surrogateescape")
        except UnicodeError:
            continue
        if _ID.fullmatch(rerere_id):
            result[file_path] = rerere_id
    return result


def _unmerged_stages(root: Path, path: str) -> list[str]:
    """Record the exact path-level merge input before rerere can clear MERGE_RR."""
    from modules.flow_gate.services import git_service as gs
    proc = gs._run_git(["ls-files", "-u", "--", path], cwd=root)
    if proc.returncode:
        raise GitServiceError(500, "git_error", f"Cannot inspect conflict stages for '{path}'")
    return sorted(line.split("\t", 1)[0] for line in proc.stdout.splitlines() if "\t" in line)


def _base_classification(root: Path, old_head: str | None, new_head: str | None) -> str:
    from modules.flow_gate.services import git_service as gs
    if not old_head or not new_head:
        return "INVALID"
    if old_head == new_head:
        return "SAFE"
    # Exit 1 means rewind/divergence; all other errors also fail closed.
    proc = gs._run_git(["merge-base", "--is-ancestor", old_head, new_head], cwd=root)
    return "REVALIDATE" if proc.returncode == 0 else "INVALID"


def _postimage_hash(root: Path, rerere_id: str) -> str | None:
    if not _ID.fullmatch(rerere_id):
        return None
    conflict_id, _, variant = rerere_id.partition(".")
    cache = _git_path(root, "rr-cache") / conflict_id
    postimage = cache / ("postimage" + ("." + variant if variant else ""))
    # MERGE_RR can assign per-path variants for identical conflict hunks while
    # Git stores their shared resolution in the unsuffixed cache entry.
    if not postimage.is_file() and variant:
        postimage = cache / "postimage"
    return hashlib.sha256(postimage.read_bytes()).hexdigest() if postimage.is_file() else None


def _normalized_preimage(raw: bytes) -> bytes:
    # Git removes marker labels when it writes rr-cache/preimage.
    normalized = re.sub(rb"(?m)^(<{7}|>{7}|\|{7})[^\r\n]*", rb"\1", raw)
    return re.sub(rb"(?m)^(<{7}|>{7}|\|{7})\r$", rb"\1", normalized).replace(b"<<<<<<<\r\n", b"<<<<<<<\n").replace(b"=======\r\n", b"=======\n").replace(b">>>>>>>\r\n", b">>>>>>>\n")


def _cached_replay_ids(root: Path, originals: dict[str, bytes], candidates: dict[str, bytes]) -> dict[str, str]:
    """Recover the path ID when rerere consumed MERGE_RR during an old-cache replay.

    A matching postimage alone is insufficient: another conflict may have produced
    identical output. Require its normalized preimage to match this path as well.
    Ambiguous cache entries are deliberately left unsupported.
    """
    cache = _git_path(root, "rr-cache")
    if not cache.is_dir():
        return {}
    recovered = {}
    for path, original in originals.items():
        candidate = candidates.get(path)
        if candidate is None or candidate == original:
            continue
        matches = []
        for directory in cache.iterdir():
            if not directory.is_dir() or not re.fullmatch(r"[0-9a-f]{40,64}", directory.name):
                continue
            for postimage in directory.glob("postimage*"):
                suffix = postimage.name[len("postimage"):]
                if suffix and not re.fullmatch(r"\.[0-9]+", suffix):
                    continue
                preimage = directory / ("preimage" + suffix)
                if not preimage.is_file() and suffix:
                    preimage = directory / "preimage"
                if (preimage.is_file() and postimage.read_bytes() == candidate
                        and _normalized_preimage(preimage.read_bytes())
                        == _normalized_preimage(original)):
                    matches.append(directory.name + suffix)
        if matches and len({entry.partition(".")[0] for entry in matches}) == 1:
            recovered[path] = min(matches, key=lambda entry: ("." in entry, entry))
    return recovered


def _record_replaced_cache_resolution(root: Path, rerere_id: str, content: bytes) -> None:
    """Replace an earlier cache answer after Git has consumed this path's MERGE_RR.

    The ID and original preimage were verified at session start. Atomic replacement
    keeps the repo-wide cache usable if the write fails.
    """
    conflict_id, _, variant = rerere_id.partition(".")
    cache = _git_path(root, "rr-cache") / conflict_id
    postimage = cache / ("postimage." + variant if variant else "postimage")
    if variant and not postimage.is_file():
        postimage = cache / "postimage"
    if not postimage.is_file():
        raise GitServiceError(500, "rerere_error", "Earlier rerere resolution disappeared")
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache, prefix="postimage-flowgate-", delete=False) as output:
            temp_name = output.name
            output.write(content)
        os.replace(temp_name, postimage)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def _run(root: Path) -> None:
    from modules.flow_gate.services import git_service as gs
    proc = gs._run_git(_RERERE, cwd=root)
    if proc.returncode:
        raise GitServiceError(500, "rerere_error", "Git rerere failed", diagnostic=gs._last_line(proc.stderr))


def _restore(root: Path, originals: dict[str, bytes], keep: set[str]) -> None:
    from modules.flow_gate.storage.safe_path import resolve_in_root
    for name, raw in originals.items():
        if name in keep:
            continue
        target = resolve_in_root(root, name)
        if target is not None:
            target.write_bytes(raw)


def start_session(group_id: str, merge_id: int, root: Path) -> dict | None:
    """Capture raw conflicts, register preimages, then admit only ledger-owned replay."""
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.storage.safe_path import resolve_in_root
    from .conflict import _classify_conflict_chunks, _conflict_side_violations
    from . import merge_target

    db = gs.db_git
    session = db.get_session(merge_id)
    context = db.session_context(session)
    files = db.session_files(merge_id)
    originals = {}
    for row in files:
        target = resolve_in_root(root, row["path"])
        if target is not None and target.is_file():
            originals[row["path"]] = target.read_bytes()
    stages = {path: _unmerged_stages(root, path) for path in originals}
    _run(root)  # explicit enabled=true/autoupdate=false; index remains unmerged
    ids = {path: value for path, value in _ids(root).items() if path in originals}
    replayed = {path: (resolve_in_root(root, path).read_bytes()) for path in originals}
    checkpoint = db.active_resolution_checkpoint(gs._project_of_group(group_id), group_id)
    checkpoint_paths = set(checkpoint["resolved_paths"]) if checkpoint else set()
    consumed_ids = _cached_replay_ids(
        root, {path: raw for path, raw in originals.items() if path not in ids}, replayed,
    )
    # Git removes MERGE_RR after a clean replay. Recover checkpoint paths too,
    # keeping their original variant only when the cache preimage and candidate
    # confirm the same conflict family. This ID is needed if a user edits a
    # replayed answer before submitting it.
    if checkpoint:
        for path in checkpoint_paths:
            recovered = consumed_ids.get(path)
            expected = (checkpoint["provenance"].get(path) or {}).get("rerere_id")
            if (recovered and expected
                    and recovered.partition(".")[0] == expected.partition(".")[0]):
                consumed_ids[path] = expected
    ids.update(consumed_ids)
    context["rerere_consumed_ids"] = sorted(consumed_ids)
    context["rerere_ids"] = ids
    context["rerere_stages"] = stages
    context["replayed_provenance"] = {}
    if checkpoint is None:
        _restore(root, originals, set())  # a repo-wide cache is not authorization
        db.set_session_context(merge_id, context)
        return None

    state = db.get_state(group_id) or {}
    baseline = context.get("resolver_baseline") or {}
    target_branch = merge_target.resolve_session_target(session).target_branch
    same_lineage = (
        checkpoint["project_id"] == gs._project_of_group(group_id)
        and checkpoint["group_id"] == group_id
        and checkpoint["target_branch"] == target_branch
        and checkpoint["source_branch"] == (
            state.get("branch") or gs.worktree_branch_name(
                gs._project_of_group(group_id), gs._module_of(group_id), group_id
            )
        )
        and checkpoint["merge_head"] == baseline.get("merge_head")
    )
    if not same_lineage:
        db.invalidate_resolution_checkpoint(checkpoint["checkpoint_id"])
    classification = (
        _base_classification(root, checkpoint["base_head"], baseline.get("base_head"))
        if same_lineage else "INVALID"
    )
    accepted = []
    invalid = []
    origins = list(context.get("conflict_origins") or [])
    if same_lineage and classification != "INVALID":
        for path in checkpoint["resolved_paths"]:
            provenance = checkpoint["provenance"].get(path) or {}
            original = originals.get(path)
            target = resolve_in_root(root, path)
            if (original is None or target is None or not target.is_file()
                    or b"\0" in original
                    or not stages.get(path)
                    or stages[path] != provenance.get("stages")
                    or ids.get(path) != provenance.get("rerere_id")
                    or _postimage_hash(root, provenance.get("rerere_id") or "")
                       != provenance.get("postimage_sha256")):
                invalid.append(path)
                continue
            candidate = target.read_bytes()
            if hashlib.sha256(candidate).hexdigest() != provenance.get("resolution_sha256"):
                invalid.append(path)
                continue
            text = candidate.decode("utf-8", "replace")
            raw_text = original.decode("utf-8", "replace")
            old_origins = [o for o in checkpoint["conflict_origins"] if o.get("path") == path]
            new_origins = _classify_conflict_chunks(path, raw_text, text)
            if (candidate == original or gs.has_conflict_markers(text)
                    or _conflict_side_violations(raw_text, text)
                    or (old_origins and [
                        (o.get("chunk_id"), o.get("selection")) for o in old_origins
                    ] != [
                        (o.get("chunk_id"), o.get("selection")) for o in new_origins
                    ])):
                invalid.append(path)
                continue
            # The only stage operation follows all path/provenance/marker/side checks.
            proc = gs._run_git(["add", "--", path], cwd=root)
            if proc.returncode:
                invalid.append(path)
                continue
            db.mark_file_resolved(merge_id, path)
            accepted.append(path)
            context["replayed_provenance"][path] = provenance
            origins.extend(new_origins)
    else:
        invalid = list(checkpoint["resolved_paths"])
    _restore(root, originals, set(accepted))
    context["conflict_origins"] = origins
    result = {
        "checkpoint_id": checkpoint["checkpoint_id"],
        "classification": classification,
        "previous_resolved": len(checkpoint["resolved_paths"]),
        "reused_paths": accepted,
        "invalid_paths": invalid,
        "previous_unresolved": max(
            0, len(db.session_files(checkpoint["source_merge_id"])) - len(checkpoint["resolved_paths"])
        ),
        "remaining_conflicts": len(db.remaining_conflicts(merge_id)),
    }
    context["checkpoint_recovery"] = result
    db.set_session_context(merge_id, context)
    if same_lineage:
        db.set_resolution_checkpoint_replay(checkpoint["checkpoint_id"], merge_id, result)
    return result


def record_resolution(merge_id: int, root: Path, path: str) -> None:
    """Flush a staged file's postimage without modifying any still-unresolved file."""
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.storage.safe_path import resolve_in_root
    db = gs.db_git
    session = db.get_session(merge_id)
    rerere_id = (db.session_context(session).get("rerere_ids") or {}).get(path)
    if not rerere_id:
        return  # unsupported conflict kind (e.g. binary/delete); hold cannot save it
    untouched = {}
    for name in db.remaining_conflicts(merge_id):
        if name == path:
            continue
        target = resolve_in_root(root, name)
        if target is not None and target.is_file():
            untouched[name] = target.read_bytes()
    context = db.session_context(session)
    if path in (context.get("rerere_consumed_ids") or []):
        target = resolve_in_root(root, path)
        if target is None or not target.is_file():
            raise GitServiceError(500, "rerere_error", f"Cannot read resolution for '{path}'")
        _record_replaced_cache_resolution(root, rerere_id, target.read_bytes())
    else:
        try:
            _run(root)
        finally:
            _restore(root, untouched, set())
    digest = _postimage_hash(root, rerere_id)
    if not digest:
        raise GitServiceError(500, "rerere_error", f"Git did not record resolution for '{path}'")
    inherited = (context.get("replayed_provenance") or {}).get(path)
    if inherited:
        target = resolve_in_root(root, path)
        if target is None or not target.is_file():
            raise GitServiceError(500, "rerere_error", f"Cannot read resolution for '{path}'")
        context["replayed_provenance"][path] = {
            **inherited,
            "postimage_sha256": digest,
            "resolution_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
        db.set_session_context(merge_id, context)


def hold(group_id: str, merge_id: int) -> dict:
    """Persist provenance before aborting an ordinary finalize merge."""
    from modules.flow_gate.services import git_service as gs
    from . import approval_intent, merge_target
    from modules.flow_gate.storage.safe_path import resolve_in_root

    session, _cfg, project_id, root = gs._session_context(group_id, merge_id)
    if gs.db_git.session_kind(session) != gs.db_git.SESSION_KIND_MERGE:
        raise GitServiceError(409, "hold_not_supported", "Only finalize merge conflicts can be held")
    # 0669 unit 8a (D §3.11, §7 "rerere checkpoint"): the session target's W or B, and M
    # only for the moment the shared rerere cache is written — not the project mutex.
    from . import branch_merge_publish
    from . import lock_manager as locks
    lock_ctx, lock_key = branch_merge_publish.attempt_lock(
        merge_target.resolve_session_target(session), holder_kind="rerere")
    try:
        merge_target.raise_if_not_workspace_owner(merge_target.resolve_session_target(session))
        rows = gs.db_git.session_files(merge_id)
        resolved = [row["path"] for row in rows if row["resolved"]]
        if not resolved:
            raise GitServiceError(409, "nothing_to_hold", "Resolve and submit at least one file before holding")
        m = locks.acquire("M", project_id, holder_kind="rerere", ctx=lock_ctx)
        if not m.ok:
            raise GitServiceError(409, "git_busy", "Another Git operation is in progress",
                                  details=locks.outcome_details(m))
        try:
            _run(root)
        finally:
            locks.release(lock_ctx, m.lock_key)
        context = gs.db_git.session_context(session)
        ids = context.get("rerere_ids") or {}
        stages = context.get("rerere_stages") or {}
        replayed = context.get("replayed_provenance") or {}
        provenance = {}
        for path in resolved:
            inherited = replayed.get(path) or {}
            rerere_id = ids.get(path) or inherited.get("rerere_id")
            digest = _postimage_hash(root, rerere_id or "")
            path_stages = stages.get(path)
            target = resolve_in_root(root, path)
            resolution_hash = (
                hashlib.sha256(target.read_bytes()).hexdigest()
                if target is not None and target.is_file() else None
            )
            if (rerere_id and digest and path_stages and resolution_hash
                    and (not inherited or (
                        inherited.get("rerere_id") == rerere_id
                        and inherited.get("postimage_sha256") == digest
                        and inherited.get("stages") == path_stages
                        and inherited.get("resolution_sha256") == resolution_hash))):
                provenance[path] = {
                    "rerere_id": rerere_id,
                    "postimage_sha256": digest,
                    "stages": path_stages,
                    "resolution_sha256": resolution_hash,
                }
            elif inherited:
                # A replayed file changed after its last submitted resolution.
                # Never silently omit that path while preserving other files.
                raise GitServiceError(409, "resolution_changed",
                                      f"Submit the edited resolution for '{path}' before holding")
        if not provenance:
            raise GitServiceError(409, "nothing_to_hold", "No reusable text resolution is recorded")
        baseline = context.get("resolver_baseline") or {}
        state = gs.db_git.get_state(group_id) or {}
        previous = gs.db_git.active_resolution_checkpoint(project_id, group_id)
        previous_checkpoint_id = previous["checkpoint_id"] if previous else None
        checkpoint = gs.db_git.create_resolution_checkpoint({
            "project_id": project_id, "group_id": group_id, "source_merge_id": merge_id,
            "target_branch": merge_target.resolve_session_target(session).target_branch,
            "source_branch": state.get("branch") or gs.worktree_branch_name(
                project_id, gs._module_of(group_id), group_id
            ),
            "base_head": baseline.get("base_head"),
            "merge_head": baseline.get("merge_head"),
            "expected_remote_head": baseline.get("expected_remote_head"),
            "resolved_paths": sorted(provenance),
            "conflict_origins": [o for o in context.get("conflict_origins") or [] if o.get("path") in provenance],
            "provenance": provenance,
        })
        # No abort can happen before the checkpoint INSERT has committed. If Git
        # refuses the abort, restore the previous active ledger entry atomically.
        proc = gs._run_git(["merge", "--abort"], cwd=root)
        if proc.returncode:
            gs.db_git.rollback_resolution_checkpoint(
                checkpoint["checkpoint_id"], previous_checkpoint_id,
            )
            raise GitServiceError(500, "git_error", "Merge abort failed after checkpoint save",
                                  diagnostic=gs._last_line(proc.stderr))
        discarded = approval_intent.discard_intent(merge_id)
        merge_target.close_session_attempt(
            session, merge_target.ATTEMPT_ABORTED, error={"code": "user_hold"},
        )
        gs._set_status(group_id, "waiting")
        return {"ok": True, "result": {
            "status": "held", "checkpoint_id": checkpoint["checkpoint_id"],
            "preserved_paths": sorted(provenance),
            "unsupported_paths": sorted(set(resolved) - set(provenance)),
            "final_approval_intent_discarded": discarded is not None,
        }}
    finally:
        if lock_ctx.find_held(lock_key) is not None:
            locks.release(lock_ctx, lock_key)
