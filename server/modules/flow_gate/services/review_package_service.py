"""Build a safe, download-time snapshot of a group's effective worktree delta."""
from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Optional

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.services import git_service, path_exclusion_rules, tr_scope_service
from modules.flow_gate.services.git_service import GitServiceError

MAX_INCLUDED_FILE_BYTES = 5 * 1024 * 1024
_BINARY_PROBE_BYTES = 64 * 1024
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class ReviewPackage:
    filename: str
    content: bytes
    metadata: dict


def _safe_relative_path(raw: str) -> Optional[str]:
    """Return a canonical ZIP-safe repository path, or None."""
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return None
    value = raw.replace("\\", "/")
    if value.startswith("/") or _DRIVE_RE.match(value):
        return None
    parts = PurePosixPath(value).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return None
    if parts[0].lower() == ".git":
        return None
    normalized = "/".join(parts)
    return normalized if normalized == value else None


def _inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _classify_untracked(root: Path, rel: str) -> tuple[Optional[bytes], Optional[str]]:
    safe = _safe_relative_path(rel)
    if safe is None:
        return None, "unsafe_path"
    policy_reason = path_exclusion_rules.exclusion_reason(safe)
    if policy_reason:
        return None, "excluded_by_policy"
    lexical = root.joinpath(*PurePosixPath(safe).parts)
    try:
        if lexical.is_symlink():
            resolved = lexical.resolve(strict=True)
            if not _inside(root, resolved):
                return None, "symlink_outside_root"
            return None, "symlink"
        resolved = lexical.resolve(strict=True)
        if not _inside(root, resolved):
            return None, "unsafe_path"
        if not resolved.is_file():
            return None, "unsafe_path"
        size = resolved.stat().st_size
        if size > MAX_INCLUDED_FILE_BYTES:
            return None, "too_large"
        with resolved.open("rb") as stream:
            probe = stream.read(_BINARY_PROBE_BYTES)
            if b"\x00" in probe:
                return None, "binary"
            return probe + stream.read(), None
    except (OSError, RuntimeError):
        return None, "unsafe_path"


def _scope_evidence(group_id: str, target_doc_id: Optional[str], actual: dict) -> dict:
    reported: list[str] = []
    scope_stage = tr_scope_service.resolve_stage((group_id or "").split(".", 1)[0])
    scope_status = None
    doc_number = None
    if target_doc_id:
        doc = db_documents.get_by_id(target_doc_id)
        if doc is None or doc.get("group_id") != group_id:
            raise GitServiceError(404, "not_found", "target document was not found in this group")
        doc_number = target_doc_id.rsplit(".", 1)[-1]
        verdict = tr_scope_service.verdict_from_meta(doc.get("meta")) or {}
        reported_slice = verdict.get("reported") or {}
        reported = sorted({p for p in reported_slice.get("items", []) if isinstance(p, str)})
        scope_status = verdict.get("verdict")
        scope_stage = verdict.get("stage") or scope_stage
    prior = tr_scope_service.group_declared_paths(group_id, exclude_doc_id=target_doc_id)
    actual_paths = sorted({p for p in actual.get("paths", []) if isinstance(p, str)})
    actual_set, reported_set, prior_set = set(actual_paths), set(reported), set(prior)
    return {
        "doc_number": doc_number,
        "reported_files": reported,
        "prior_reported_files": prior,
        "unreported_files": sorted(actual_set - reported_set - prior_set),
        "missing_reported_files": sorted(reported_set - actual_set),
        "scope_stage": scope_stage,
        "scope_status": scope_status,
    }


def _changed_lines(entries: list[dict], excluded: list[dict]) -> str:
    reasons = {item["path"]: item["reason"] for item in excluded}
    lines = []
    for entry in entries:
        path = entry.get("path") or ""
        status = "??" if entry.get("untracked") else (entry.get("status") or "M")
        suffix = f" [excluded: {reasons[path]}]" if path in reasons else ""
        if status == "R" and entry.get("old_path"):
            lines.append(f"R {entry['old_path']} -> {path}{suffix}")
        else:
            lines.append(f"{status} {path}{suffix}")
    return "\n".join(lines) + "\n" if lines else "No changes.\n"


def _report(metadata: dict, entries: list[dict]) -> str:
    def listing(values: list[str], empty: str = "None.") -> str:
        return "\n".join(f"- `{value}`" for value in values) if values else empty

    excluded = metadata["excluded_files"]
    excluded_lines = [f"{item['path']} ({item['reason']})" for item in excluded]
    if not metadata["has_changes"]:
        note = "No worktree changes detected at package generation time."
    else:
        note = "Snapshot includes committed, staged, unstaged, and untracked changes present at generation time."
    return f"""# FlowGate Review Package

Project: {metadata['project']}
Module: {metadata['module']}
Group: {metadata['group']}
Document: {metadata.get('target_doc_id') or 'None'}
Base Branch: {metadata['base_branch']}
Group Branch: {metadata['group_branch']}
Base SHA: {metadata['base_sha']}
HEAD SHA: {metadata['head_sha']}
Initial Source Sync SHA: {metadata.get('initial_source_sync_sha') or 'None'}
Generated At: {metadata['generated_at']}

## Changed Files
{listing(metadata['changed_files'])}

## Untracked Files
{listing(metadata['untracked_files'])}

## Reported / Actual Scope
Reported:\n{listing(metadata['reported_files'])}

Prior reported:\n{listing(metadata['prior_reported_files'])}

Unreported:\n{listing(metadata['unreported_files'])}

Missing reported:\n{listing(metadata['missing_reported_files'])}

## Excluded Files
{listing(excluded_lines)}

## Notes
{note}
"""


def build_review_package(project_id: str, group_id: str, target_doc_id: Optional[str] = None) -> ReviewPackage:
    """Build a ZIP from the live worktree; never falls back to a base checkout."""
    state = db_git.get_state(group_id)
    if state is None or not state.get("worktree_registered"):
        raise GitServiceError(409, "worktree_missing", "group worktree is not registered")
    if (state.get("project_id") or group_id.split(".", 1)[0]) != project_id:
        raise GitServiceError(409, "invalid_state", "group does not belong to project")
    cfg = db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        raise GitServiceError(409, "git_inactive", "Git integration is not active")
    root, root_reason = git_service.effective_src_root_ex(project_id, group_id)
    if root is None:
        raise GitServiceError(409, "worktree_missing", f"group worktree is unavailable ({root_reason})")
    root = root.resolve()

    actual = git_service.collect_scope_changes(project_id, group_id)
    if not actual.get("available"):
        raise GitServiceError(500, "merge_base_failed", "current worktree delta could not be resolved")

    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    branch = (state.get("branch") or "").strip()
    base_proc = git_service._run_git(
        ["merge-base", f"refs/heads/{base_branch}", "HEAD"], cwd=root,
        timeout=git_service.GIT_READ_TIMEOUT_SEC,
    )
    base_sha = (base_proc.stdout or "").strip()
    if base_proc.returncode != 0 or not base_sha:
        raise GitServiceError(500, "merge_base_failed", "merge-base could not be resolved")
    head_proc = git_service._run_git(
        ["rev-parse", "HEAD"], cwd=root, timeout=git_service.GIT_READ_TIMEOUT_SEC,
    )
    head_sha = (head_proc.stdout or "").strip()
    if head_proc.returncode != 0 or not head_sha:
        raise GitServiceError(409, "group_ref_unavailable", "group HEAD could not be resolved")
    patch_proc = git_service._run_git(
        ["diff", "-M", base_sha, "--"], cwd=root,
        timeout=git_service.GIT_READ_TIMEOUT_SEC,
    )
    if patch_proc.returncode != 0:
        raise GitServiceError(500, "review_package_build_failed", "tracked diff could not be generated")

    raw_entries = list(actual.get("entries") or [])
    untracked_set = set(git_service._worktree_untracked_paths(root))
    entries: list[dict] = []
    excluded: list[dict] = []
    untracked_files: list[str] = []
    untracked_payloads: list[tuple[str, bytes]] = []
    for raw in raw_entries:
        rel = raw.get("path") or ""
        safe = _safe_relative_path(rel)
        entry = dict(raw)
        entry["untracked"] = rel in untracked_set
        if safe is None:
            entry["path"] = "<unsafe-path>"
            entries.append(entry)
            excluded.append({"path": "<unsafe-path>", "reason": "unsafe_path"})
            continue
        entry["path"] = safe
        entries.append(entry)
        policy_reason = path_exclusion_rules.exclusion_reason(safe)
        if policy_reason:
            excluded.append({"path": safe, "reason": "excluded_by_policy"})
            continue
        # Inspect every present file, not only untracked payloads. A tracked binary,
        # oversized file, or escaping symlink remains visible as explicit evidence
        # even though its filesystem contents are not copied into the archive.
        if entry.get("status") != "D":
            payload, reason = _classify_untracked(root, safe)
            if reason:
                excluded.append({"path": safe, "reason": reason})
                continue
            if entry["untracked"] and payload is not None:
                untracked_files.append(safe)
                untracked_payloads.append((safe, payload))

    scope = _scope_evidence(group_id, target_doc_id, actual)
    pieces = group_id.split(".")
    module = pieces[1] if len(pieces) > 1 else "default"
    group = pieces[2] if len(pieces) > 2 else group_id
    changed_files = sorted({str(e.get("path")) for e in entries if e.get("path")})
    metadata = {
        "project": project_id, "module": module, "group": group,
        "target_doc_id": target_doc_id, "doc_number": scope.pop("doc_number"),
        "base_branch": base_branch, "group_branch": branch,
        "base_sha": base_sha, "head_sha": head_sha,
        "initial_source_sync_sha": state.get("initial_source_sync_sha"),
        "initial_source_sync_at": state.get("initial_source_sync_at"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "has_changes": bool(entries), "changed_files": changed_files,
        "untracked_files": sorted(untracked_files),
        "excluded_files": sorted(excluded, key=lambda item: (item["path"], item["reason"])),
        **scope,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
        archive.writestr("changed-files.txt", _changed_lines(entries, metadata["excluded_files"]))
        archive.writestr("diff.patch", patch_proc.stdout or "")
        archive.writestr("report.md", _report(metadata, entries))
        for rel, payload in sorted(untracked_payloads):
            archive.writestr(f"untracked/{rel}", payload)
    filename = f"{project_id}.{module}.{group}-review-package.zip"
    return ReviewPackage(filename=filename, content=output.getvalue(), metadata=metadata)