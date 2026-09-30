"""Canonical Test Basis for approved contract-2 specifications."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

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


def resolve(doc: dict, cases: list[dict]) -> dict:
    root = source_root(doc)
    # Until Source Bundle service lands in the product tree, exact clean git identity is
    # the compatibility authority. Never use a mutable working directory as the run root.
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("source_worktree_dirty")
    revision = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    manifest = []
    for path in sorted({entry["path"] for case in cases
                        if (entry := locator(case)).get("path")}):
        target = (root / path).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            raise ValueError("automation_asset_missing: " + path)
        _git(root, "ls-files", "--error-unmatch", "--", path)
        manifest.append({"path": path, "content_hash": hashlib.sha256(target.read_bytes()).hexdigest(),
                         "role": "test"})
    identity = {
        "basis_version": 1,
        "ts_document_id": doc["doc_id"],
        "ts_revision_no": doc.get("revision_no") or 0,
        "source": {"kind": "compat_worktree", "git_revision": revision, "tree": tree,
                   "bundle_id": None, "bundle_hash": None},
        "test_assets": {"manifest_hash": canonical_hash(manifest), "asset_count": len(manifest)},
        "execution_profile": {"runner_generation": 1},
    }
    return {"basis_id": canonical_hash(identity), **identity, "manifest": manifest}


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
        return {"run_id": run["run_id"], "tsr_doc_id": report_id}
    except Exception:
        if old_path and old_body is not None:
            old_path.write_bytes(old_body)
        if ts_path:
            for path in set(ts_path.parent.glob("*-TSR_document.md")) - report_files_before:
                path.unlink(missing_ok=True)
        raise
