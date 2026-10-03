"""Canonical Test Basis for approved contract-2 specifications."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

from modules.flow_gate.storage import paths as storage_paths

_LOCATOR = re.compile(r"^(tests?/[A-Za-z0-9_./-]+\.py)(?:::(\w+(?:::\w+)*))?$")


def canonical_hash(value: dict | list) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def locator(case: dict) -> dict:
    mode = case.get("execution_mode")
    if mode in ("manual", "external"):
        return {"capability": mode}
    raw = str(case.get("automation_ref") or "").strip()
    match = _LOCATOR.fullmatch(raw)
    if not match or ".." in Path(match.group(1)).parts:
        return {"capability": "unbound"}
    return {"capability": "case_selectable" if match.group(2) else "suite_only",
            "path": match.group(1), "node": match.group(2)}


def source_root(doc: dict) -> Path:
    root = storage_paths.resolve_project_src_root(
        doc.get("project_id"), doc.get("branch") or "main", group_id=doc.get("group_id")
    )
    if root is None or not root.is_dir():
        raise ValueError("source_root_missing")
    return root


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ValueError("source_identity_unavailable: " + result.stderr.strip()[:300])
    return result.stdout.strip()


def _test_only_path(path: str) -> bool:
    """Eligibility check; the approved manifest remains the write authority."""
    pure = PurePosixPath(path)
    return (not pure.is_absolute() and ".." not in pure.parts
            and len(pure.parts) >= 2 and pure.parts[0] in {"test", "tests"}
            and pure.suffix.lower() in {".py", ".json", ".yaml", ".yml", ".toml",
                                        ".ini", ".txt", ".csv", ".xml"})


def _dirty_paths(root: Path) -> set[str]:
    result = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-z",
                             "--untracked-files=all"], capture_output=True, timeout=20)
    if result.returncode:
        raise ValueError("source_identity_unavailable")
    fields = result.stdout.split(b"\0")
    dirty: set[str] = set()
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        status = field[:2].decode("ascii", errors="replace")
        dirty.add(field[3:].decode("utf-8", errors="surrogateescape").replace("\\", "/"))
        if "R" in status or "C" in status:
            if index < len(fields):
                dirty.add(fields[index].decode("utf-8", errors="surrogateescape").replace("\\", "/"))
                index += 1
    return dirty


def resolve(doc: dict, cases: list[dict]) -> dict:
    root = source_root(doc)
    revision = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    roles: dict[str, str] = {}
    for case in cases:
        loc = locator(case)
        if loc.get("path"):
            roles[loc["path"]] = "test"
        for raw in re.split(r"[,\n]", str(case.get("test_assets") or "")):
            path = raw.strip().replace("\\", "/")
            if path:
                roles.setdefault(path, "fixture")
    manifest = []
    for path, role in sorted(roles.items()):
        if not _test_only_path(path):
            raise ValueError("product_source_or_invalid_test_asset: " + path)
        target = (root / path).resolve()
        if (not target.is_relative_to(root.resolve()) or target != root.resolve() / path
                or not target.is_file()):
            raise ValueError("automation_asset_missing: " + path)
        _git(root, "ls-files", "--error-unmatch", "--", path)
        manifest.append({"path": path, "content_hash": hashlib.sha256(target.read_bytes()).hexdigest(),
                         "role": role})
    allowlist = {entry["path"] for entry in manifest}
    dirty = _dirty_paths(root)
    if dirty - allowlist:
        raise ValueError("source_worktree_dirty: " + ", ".join(sorted(dirty - allowlist)))
    identity = {
        "basis_version": 1,
        "ts_document_id": doc["doc_id"],
        "ts_revision_no": doc.get("revision_no") or 0,
        "source": {"kind": "compat_worktree", "git_revision": revision, "tree": tree,
                   "bundle_id": None, "bundle_hash": None},
        "test_assets": {"manifest_hash": canonical_hash(manifest), "asset_count": len(manifest)},
        "execution_profile": {"runner_generation": 1},
    }
    return {"basis_id": canonical_hash(identity), **identity, "manifest": manifest,
            "compat_dirty_paths": sorted(dirty)}


def current(doc: dict) -> dict | None:
    try:
        meta = json.loads(doc.get("meta") or "{}")
    except (TypeError, ValueError):
        return None
    basis = meta.get("test_basis")
    return basis if isinstance(basis, dict) and basis.get("basis_id") else None


def metadata_with_basis(doc: dict, basis: dict) -> str:
    try:
        meta = json.loads(doc.get("meta") or "{}")
    except (TypeError, ValueError):
        meta = {}
    meta["test_basis"] = basis
    return json.dumps(meta, ensure_ascii=False)


def initialize(doc: dict, parsed: dict, basis: dict, *, locale: str = "ko") -> dict:
    """Create the initialization run and paired report inside caller's DB transaction.

    The caller owns rollback. A report is a file too, so restore/remove it on failure.
    """
    from modules.flow_gate.db import test_runs as db_test_runs
    from modules.flow_gate.services import test_run_service, test_spec_service
    from modules.flow_gate.db import documents as db_docs

    existing = test_run_service._active_tsr_for_ts(doc)
    old_path = storage_paths.resolve_storage_path(
        (existing or {}).get("file_path") or "", doc.get("project_id"),
        branch=doc.get("branch") or "main",
    ) if existing else None
    old_body = old_path.read_bytes() if old_path and old_path.is_file() else None
    ts_path = storage_paths.resolve_storage_path(
        doc.get("file_path") or "", doc.get("project_id"), branch=doc.get("branch") or "main"
    )
    report_files_before = set(ts_path.parent.glob("*-TSR_document.md")) if ts_path else set()
    try:
        rows = test_spec_service.map_results(parsed["cases"], [])["cases"]
        summary = test_spec_service.compute_overall(rows)
        meta = {"run_kind": "initialization", "basis_id": basis["basis_id"],
                "test_basis": basis, "counts": summary["counts"],
                "required_counts": summary["required_counts"],
                "optional_counts": summary["optional_counts"],
                "gate_passed": False, "unmapped": [], "conflicts": []}
        run = db_test_runs.insert_spec_run(
            doc_id=doc["doc_id"], revision_no=doc.get("revision_no") or 0,
            triggered_via="ui", runner_id="system", rows=rows, status="failed",
            overall="NOT_RUN", result_meta=json.dumps(meta, ensure_ascii=False),
            case_passed=0, case_failed=0, error="spec_required_not_run", locale=locale,
            case_meta=[test_spec_service.case_row_to_meta(row) for row in rows],
        )
        report_id = test_run_service.assemble_tsr(
            doc, run, db_test_runs.list_cases(run["run_id"]), locale=locale, run_chain=False
        )
        db_test_runs.set_run_tsr_doc(run["run_id"], report_id)
        paired = db_docs.get_by_id(report_id)
        if not paired or paired.get("target_id") != doc["doc_id"]:
            raise RuntimeError("tsr_pair_missing")
        from modules.flow_gate.db import workflow_sequences as db_wfseq
        slot = test_run_service._tsr_slot_item(doc, db_wfseq)
        if slot is not None and not db_wfseq.get_item_by_result_doc_id(report_id):
            raise RuntimeError("tsr_workflow_pair_missing")
        from modules.flow_gate.db import events as db_events
        db_events.insert_event(doc["doc_id"], "test_spec_initialized", note=json.dumps({
            "basis_id": basis["basis_id"], "run_id": run["run_id"],
            "tsr_doc_id": report_id, "ts_revision_no": doc.get("revision_no"),
            "source": basis["source"],
            "manifest_hash": basis["test_assets"]["manifest_hash"],
        }, ensure_ascii=False))
        return {"run_id": run["run_id"], "tsr_doc_id": report_id}
    except Exception:
        if old_path and old_body is not None:
            old_path.write_bytes(old_body)
        if ts_path:
            for path in set(ts_path.parent.glob("*-TSR_document.md")) - report_files_before:
                path.unlink(missing_ok=True)
        raise
