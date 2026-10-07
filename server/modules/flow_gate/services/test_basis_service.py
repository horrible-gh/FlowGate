"""Canonical Test Basis for approved contract-2 specifications.

Basis v2 (0682 D#1): the identity is the whole-source content fingerprint of the Source
Bundle captured at approval, so uncommitted and untracked Group work is part of it. The
binding (bundle id/sha, git revision, dirty flag) is stored beside it but kept out of
basis_id. Every caller judges a Basis with ``verdict`` against a Live Probe that measures
the worktree with the Bundle's own scan, exclusion and hash rules.

Test Asset Policy (0682 D#1 §3.8, T#2): ``asset_kind`` is the one rule approval (manifest),
the asset API and the locator share. Git tracking is not part of it: an asset must be in
the captured Bundle (approval) or the Live Probe scan (judgement) with the same hash, and
pass the path safety checks, so untracked test files a TR created are assets too.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from modules.flow_gate.storage import paths as storage_paths

_LOCATOR = re.compile(r"^([A-Za-z0-9_./-]+)(?:::(\w+(?:::\w+)*))?$")

BASIS_VERSION = 2
SOURCE_KIND = "source_bundle"
ASSET_POLICY_VERSION = "test-asset-v2"
RUNNER_GENERATION = 1

VALID, STALE, UNVERIFIABLE = "valid", "stale", "unverifiable"
# Stale reasons, in the order they are reported.
REASON_BASIS_MISSING = "basis_missing"
REASON_BASIS_REPLACED = "basis_replaced"
REASON_BASIS_OUTDATED = "basis_version_outdated"
REASON_TS_REVISION = "ts_revision_changed"
REASON_SOURCE = "source_changed"
REASON_MANIFEST = "manifest_changed"
REASON_BUNDLE_POLICY = "bundle_policy_changed"
REASON_ASSET_POLICY = "asset_policy_changed"
REASON_PROFILE = "execution_profile_changed"
REASON_IDENTITY = "identity_changed"
# Unverifiable reasons are "source_unmeasurable:<Source Bundle failure code>".
REASON_UNMEASURABLE = "source_unmeasurable"

# ── Test Asset Policy (D#1 §3.8) ──────────────────────────────────────────────
# Roots match case-sensitively. Co-located roots only hold assets under a __tests__ directory.
ASSET_ROOTS = (("tests",), ("test",), ("server", "tests"), ("client", "tests"))
COLOCATED_ROOTS = (("src",), ("client", "src"))
COLOCATED_DIR = "__tests__"
KIND_RUNNABLE = "runnable_test"               # pytest through the existing runner
KIND_UNSUPPORTED = "runner_unsupported_test"  # stored, pinned and editable; never auto-run
KIND_FIXTURE = "fixture"
ASSET_SUFFIXES = {
    ".py": KIND_RUNNABLE,
    **dict.fromkeys((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"), KIND_UNSUPPORTED),
    **dict.fromkeys((".json", ".yaml", ".yml", ".toml", ".ini", ".txt", ".csv", ".xml",
                     ".html", ".patch", ".snap"), KIND_FIXTURE),
}

_MEMO: dict[str, tuple[str, dict]] = {}
_MEMO_GUARD = threading.Lock()
_MEMO_LIMIT = 64


class BasisUnverifiable(ValueError):
    """The live source could not be measured; distinct from a stale Basis."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"{REASON_UNMEASURABLE}:{code}")


def canonical_hash(value: dict | list) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def asset_kind(path) -> str | None:
    """Test Asset Policy: the asset kind of a source-relative path, or None if it cannot be one.

    Lexical only (no disk access), so approval, the asset API and the locator agree on
    it. Refused: absolute, drive, backslash, colon, control characters, empty/``.``/``..``
    segments, paths outside the test roots (product source), a co-located file outside
    ``__tests__``, a suffix with no kind, and anything the Source Bundle exclusion policy
    leaves out (an excluded directory on the way or a secret file name): an asset the
    capture never holds can be neither run nor judged.
    """
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or ":" in path:
        return None
    parts = path.split("/")
    if any(part in ("", ".", "..") or any(ord(c) < 32 or ord(c) == 127 for c in part)
           for part in parts):
        return None
    if bundle_excluded(path):
        return None
    head = tuple(parts)
    inside = any(len(parts) > len(root) and head[:len(root)] == root for root in ASSET_ROOTS)
    if not inside:
        inside = any(len(parts) > len(root) + 1 and head[:len(root)] == root
                     and COLOCATED_DIR in parts[len(root):-1] for root in COLOCATED_ROOTS)
    if not inside:
        return None
    return ASSET_SUFFIXES.get(PurePosixPath(parts[-1]).suffix)


def bundle_excluded(path: str) -> bool:
    """Whether the Source Bundle exclusion policy leaves ``path`` out of every capture."""
    from modules.flow_gate.services import source_bundle_materializer as materializer
    parts = path.split("/")
    return (any(materializer._excluded("/".join(parts[:end]), True) for end in range(1, len(parts)))
            or materializer._excluded(path, False))


def _test_only_path(path: str) -> bool:
    """Eligibility check; the approved manifest remains the write authority."""
    return asset_kind(path) is not None


def safe_asset_file(root: Path, path: str) -> Path:
    """The on-disk file of an eligible asset, refusing anything that could escape ``root``.

    Every segment is checked with lstat: no symlink or reparse point anywhere on the way,
    the exact on-disk name (a case-insensitive filesystem would otherwise accept another
    spelling), directories on the way and a regular file at the end, and the resolved
    target must still be ``root / path``. ``ValueError`` carries the refusal code.
    """
    from modules.flow_gate.services import source_bundle_materializer as materializer
    if asset_kind(path) is None:
        raise ValueError("test_asset_not_allowlisted")
    base = Path(root)
    current = base
    parts = path.split("/")
    for index, part in enumerate(parts):
        try:
            names = os.listdir(current)
        except OSError as exc:
            raise ValueError("test_asset_missing") from exc
        if part not in names:
            if any(name.casefold() == part.casefold() for name in names):
                raise ValueError("test_asset_path_case_mismatch")
            raise ValueError("test_asset_missing")
        current = current / part
        try:
            st = current.lstat()
        except OSError as exc:
            raise ValueError("test_asset_missing") from exc
        if materializer._linked(st):
            raise ValueError("test_asset_unsafe_path")
        last = index == len(parts) - 1
        if not (stat.S_ISREG(st.st_mode) if last else stat.S_ISDIR(st.st_mode)):
            raise ValueError("test_asset_unsafe_path" if last else "test_asset_missing")
    if current.resolve() != base.resolve().joinpath(*parts):
        raise ValueError("test_asset_unsafe_path")
    return current


def locator(case: dict) -> dict:
    """Automation Locator: binds ``automation_ref`` through the Test Asset Policy.

    A ``.py`` test under an asset root runs on the existing pytest runner
    (``case_selectable`` with a node, ``suite_only`` without). A JS/TS test is pinned as a
    "test" asset but has no runner: ``runner_unsupported``, never auto-selected. Anything
    else (malformed, product source, a fixture) is ``unbound``.
    """
    mode = case.get("execution_mode")
    if mode in ("manual", "external"):
        return {"capability": mode}
    raw = str(case.get("automation_ref") or "").strip()
    match = _LOCATOR.fullmatch(raw)
    kind = asset_kind(match.group(1)) if match else None
    if kind == KIND_UNSUPPORTED:
        return {"capability": "runner_unsupported", "path": match.group(1),
                "node": match.group(2)}
    if kind != KIND_RUNNABLE:
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


def _roles(cases: list[dict]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for case in cases:
        loc = locator(case)
        if loc.get("path"):
            roles[loc["path"]] = "test"
        for raw in re.split(r"[,\n]", str(case.get("test_assets") or "")):
            # Kept verbatim: the policy refuses a backslash path instead of rewriting it.
            path = raw.strip()
            if path:
                roles.setdefault(path, "fixture")
    return roles


def _manifest(roles: dict[str, str], hashes: dict[str, str], *, root: Path | None = None) -> list[dict]:
    """One manifest rule for capture and probe; hashes always come from the scan.

    With ``root`` (capture) every asset must pass the Test Asset Policy and be in the
    captured Bundle; Git tracking is not asked, so an untracked test file a TR created is
    an asset like a tracked one. Bundle membership stands for existence, hash and safety
    (the capture refuses links and specials and hashes what it holds). Without ``root``
    (Live Probe) a gap is kept as a null hash, so it shows up as a manifest change.
    """
    manifest = []
    for path, role in sorted(roles.items()):
        kind = asset_kind(path)
        if root is not None:
            if kind is None:
                # Excluded by the Bundle policy: refused explicitly, never captured or run.
                excluded = isinstance(path, str) and path and bundle_excluded(path)
                raise ValueError(("test_asset_not_captured: " if excluded else
                                  "product_source_or_invalid_test_asset: ") + path)
            if path not in hashes:
                folded = path.casefold()
                if any(name.casefold() == folded for name in hashes):
                    raise ValueError("test_asset_path_case_mismatch: " + path)
                missing = not (root / path).is_file()
                raise ValueError(("automation_asset_missing: " if missing else
                                  "test_asset_not_captured: ") + path)
        manifest.append({"path": path, "content_hash": hashes.get(path) if kind else None,
                         "role": role, "kind": kind})
    return manifest


def _identity(doc: dict, *, policy: str, fingerprint: str, manifest: list[dict]) -> dict:
    return {
        "basis_version": BASIS_VERSION,
        "ts_document_id": doc["doc_id"],
        "ts_revision_no": doc.get("revision_no") or 0,
        "source": {"kind": SOURCE_KIND, "exclusion_policy_version": policy,
                   "content_fingerprint": fingerprint},
        "test_assets": {"policy_version": ASSET_POLICY_VERSION,
                        "manifest_hash": canonical_hash(manifest), "asset_count": len(manifest)},
        "execution_profile": {"runner_generation": RUNNER_GENERATION},
    }


def _group_of(doc: dict) -> tuple[str, str]:
    from modules.flow_gate.services import source_bundle_materializer as materializer
    project_id, group_id = doc.get("project_id"), doc.get("group_id")
    if not project_id or not group_id:
        raise materializer.SourceBundleError("group_worktree_unavailable", "TS has no group worktree")
    return project_id, group_id


def capture(doc: dict, cases: list[dict], *, locked: bool = False) -> dict:
    """Basis Capture: snapshot the Group worktree into a Source Bundle and build the Basis.

    ``locked`` means the caller already holds this Group's G (asset edit). A Bundle failure
    is raised as ``basis_capture_failed:<code>``; nothing is written to the DB here.
    """
    from modules.flow_gate.services import source_bundle_materializer as materializer
    from modules.flow_gate.services import source_bundle_service as bundles
    try:
        project_id, group_id = _group_of(doc)
        ensure = bundles.ensure_locked if locked else bundles.ensure
        bundle = ensure(project_id, group_id)
        opened = bundles.open_verified(bundle["bundle_id"])
        root = materializer.resolve_worktree(project_id, group_id)
    except materializer.SourceBundleError as exc:
        raise ValueError("basis_capture_failed:" + exc.code) from exc
    if opened["row"]["group_id"] != group_id or opened["row"]["project_id"] != project_id:
        raise ValueError("basis_capture_failed:bundle_group_mismatch")
    hashes = {entry["path"]: entry["sha256"] for entry in opened["manifest"]["files"]}
    manifest = _manifest(_roles(cases), hashes, root=root)
    identity = _identity(doc, policy=bundle["exclusion_policy_version"],
                         fingerprint=bundle["content_fingerprint"], manifest=manifest)
    binding = {"bundle_id": bundle["bundle_id"], "bundle_sha256": bundle["bundle_sha256"],
               "git_revision": bundle["source_revision"], "source_dirty": bool(bundle["source_dirty"]),
               "captured_at": datetime.now(timezone.utc).isoformat(),
               "project_id": project_id, "group_id": group_id}
    return {"basis_id": canonical_hash(identity), **identity, "binding": binding,
            "manifest": manifest}


def preflight(doc: dict, cases: list[dict]) -> dict:
    """Approval precheck (dry run): the capture's manifest rule on a Live Probe, no Bundle."""
    from modules.flow_gate.services import source_bundle_materializer as materializer
    try:
        live = probe(doc)
        root = materializer.resolve_worktree(*_group_of(doc))
    except BasisUnverifiable as exc:
        raise ValueError("basis_capture_failed:" + exc.code) from exc
    except materializer.SourceBundleError as exc:
        raise ValueError("basis_capture_failed:" + exc.code) from exc
    manifest = _manifest(_roles(cases), live["hashes"], root=root)
    identity = _identity(doc, policy=live["exclusion_policy_version"],
                         fingerprint=live["content_fingerprint"], manifest=manifest)
    return {"basis_id": canonical_hash(identity), **identity, "manifest": manifest}


def probe(doc: dict, *, memo: bool = False) -> dict:
    """Live Probe: measure the worktree like a capture would, without copying or locking.

    inspect_source re-checks HEAD/status and the file list around hashing, so a change
    during measurement is ``source_changed`` (unverifiable), never a wrong fingerprint.
    ``memo`` is for display paths only: hashes are reused while every scanned path, size
    and mtime is unchanged.
    """
    from modules.flow_gate.services import source_bundle_materializer as materializer
    try:
        root = materializer.resolve_worktree(*_group_of(doc))
        deadline = time.monotonic() + materializer.BUILD_SECONDS
        key = str(root)
        if memo:
            files, dirs = materializer._scan(root, deadline)
            with _MEMO_GUARD:
                hit = _MEMO.get(key)
            if hit and hit[0] == _scan_signature(files, dirs):
                return hit[1]
        inspected = materializer.inspect_source(root, deadline)
    except materializer.SourceBundleError as exc:
        raise BasisUnverifiable(exc.code) from exc
    result = {"exclusion_policy_version": materializer.POLICY_VERSION,
              "content_fingerprint": inspected["content_fingerprint"],
              "hashes": {entry["path"]: entry["sha256"] for entry in inspected["entries"]},
              "source_revision": inspected["source_revision"],
              "source_dirty": inspected["source_dirty"]}
    signature = _scan_signature(inspected["files"], inspected["dirs"])
    with _MEMO_GUARD:
        if key not in _MEMO and len(_MEMO) >= _MEMO_LIMIT:
            _MEMO.pop(next(iter(_MEMO)))
        _MEMO[key] = (signature, result)
    return result


def _scan_signature(files, dirs) -> str:
    return canonical_hash([[name, st.st_size, st.st_mtime_ns] for name, st in files] +
                          [["D", name] for name, _st in dirs])


def resolve(doc: dict, cases: list[dict], *, memo: bool = False) -> dict:
    """The identity the live source would have now (no binding). Raises BasisUnverifiable."""
    live = probe(doc, memo=memo)
    manifest = _manifest(_roles(cases), live["hashes"])
    identity = _identity(doc, policy=live["exclusion_policy_version"],
                         fingerprint=live["content_fingerprint"], manifest=manifest)
    return {"basis_id": canonical_hash(identity), **identity, "manifest": manifest,
            "probe": {"source_revision": live["source_revision"],
                      "source_dirty": live["source_dirty"]}}


def outdated(basis: dict | None) -> bool:
    """A v1 (compat_worktree, HEAD-only) Basis can no longer be judged valid."""
    return bool(basis) and (basis.get("basis_version") != BASIS_VERSION or
                            (basis.get("source") or {}).get("kind") != SOURCE_KIND)


def _doc_cases(doc: dict) -> list[dict]:
    from modules.flow_gate.services import test_run_service, test_spec_service
    return test_spec_service.parse_spec(test_run_service._read_doc_content_or_empty(doc))["cases"]


def _judged(state: str, reasons: list[str], basis: dict | None, live: dict | None = None) -> dict:
    return {"state": state, "reasons": reasons, "basis_valid": state == VALID,
            "stale": state != VALID, "basis_id": (basis or {}).get("basis_id"),
            "live_basis_id": (live or {}).get("basis_id")}


def _stale_reasons(stored: dict, live: dict) -> list[str]:
    reasons = []
    if stored.get("ts_revision_no") != live.get("ts_revision_no"):
        reasons.append(REASON_TS_REVISION)
    source, live_source = stored.get("source") or {}, live.get("source") or {}
    if source.get("content_fingerprint") != live_source.get("content_fingerprint"):
        reasons.append(REASON_SOURCE)
    assets, live_assets = stored.get("test_assets") or {}, live.get("test_assets") or {}
    if (assets.get("manifest_hash"), assets.get("asset_count")) != (
            live_assets.get("manifest_hash"), live_assets.get("asset_count")):
        reasons.append(REASON_MANIFEST)
    if source.get("exclusion_policy_version") != live_source.get("exclusion_policy_version"):
        reasons.append(REASON_BUNDLE_POLICY)
    if assets.get("policy_version") != live_assets.get("policy_version"):
        reasons.append(REASON_ASSET_POLICY)
    if stored.get("execution_profile") != live.get("execution_profile"):
        reasons.append(REASON_PROFILE)
    return reasons or [REASON_IDENTITY]


def verdict(doc: dict | None, basis: dict | None, cases: list[dict] | None = None, *,
            execution_basis: dict | None = None, memo: bool = False) -> dict:
    """Basis Verdict: the one judgement every caller uses (valid / stale / unverifiable).

    ``basis`` is the Basis stored on the TS now. ``execution_basis`` is the Basis a run or
    result was made under; when it is not the stored one the result is stale at once.
    """
    if not basis:
        return _judged(STALE, [REASON_BASIS_MISSING], execution_basis)
    if execution_basis is not None and execution_basis.get("basis_id") != basis.get("basis_id"):
        return _judged(STALE, [REASON_BASIS_REPLACED], basis)
    if outdated(basis):
        return _judged(STALE, [REASON_BASIS_OUTDATED], basis)
    if not doc:
        return _judged(UNVERIFIABLE, [REASON_UNMEASURABLE + ":ts_missing"], basis)
    try:
        if cases is None:
            cases = _doc_cases(doc)
        live = resolve(doc, cases, memo=True) if memo else resolve(doc, cases)
    except (ValueError, TypeError) as exc:
        reason = str(exc) if isinstance(exc, BasisUnverifiable) else (
            REASON_UNMEASURABLE + ":" + (str(exc).split(":", 1)[0] or type(exc).__name__))
        return _judged(UNVERIFIABLE, [reason], basis)
    if live["basis_id"] == basis["basis_id"]:
        return _judged(VALID, [], basis, live)
    return _judged(STALE, _stale_reasons(basis, live), basis, live)


def verdict_error(judged: dict, http_error, **context):
    """409 basis_stale / basis_unavailable for a non-valid verdict, else None."""
    if judged["state"] == VALID:
        return None
    code = "basis_stale" if judged["state"] == STALE else "basis_unavailable"
    return http_error(409, code, basis_state=judged["state"], reasons=judged["reasons"],
                      basis_id=judged["basis_id"], live_basis_id=judged["live_basis_id"],
                      **context)


def source_identity(basis: dict | None) -> dict:
    """The executed-source identity a result row records (server authority)."""
    basis = basis or {}
    source, binding = basis.get("source") or {}, basis.get("binding") or {}
    identity = {
        "kind": source.get("kind"), "basis_id": basis.get("basis_id"),
        "content_fingerprint": source.get("content_fingerprint"),
        "exclusion_policy_version": source.get("exclusion_policy_version"),
        "bundle_id": binding.get("bundle_id") or source.get("bundle_id"),
        "bundle_sha256": binding.get("bundle_sha256"),
        "git_revision": binding.get("git_revision") or source.get("git_revision"),
        "tree": source.get("tree"),
    }
    return {key: value for key, value in identity.items() if value not in (None, "")}


def source_summary(basis: dict | None) -> dict:
    """Display fields for the TS detail Basis area."""
    basis = basis or {}
    source, binding = basis.get("source") or {}, basis.get("binding") or {}
    fingerprint = source.get("content_fingerprint") or ""
    return {"kind": source.get("kind"), "fingerprint_prefix": fingerprint[:12] or None,
            "captured_at": binding.get("captured_at"), "source_dirty": binding.get("source_dirty"),
            "bundle_id": binding.get("bundle_id"), "git_revision": binding.get("git_revision")}


# ── Bundle retention, execution source and rebind ─────────────────────────────

def pin(doc: dict, basis: dict) -> None:
    """Retention Pin for the TS's current Basis; replaces (releases) the previous one.

    Call inside the transaction that stores the Basis so a rollback leaves no Pin.
    """
    binding = basis.get("binding") or {}
    if not binding.get("bundle_id"):
        return
    from modules.flow_gate.db import source_bundles as db_source_bundles
    db_source_bundles.pin_set(doc["doc_id"], binding["bundle_id"], basis["basis_id"],
                              binding.get("project_id") or doc.get("project_id"),
                              binding.get("group_id") or doc.get("group_id"))


def open_bundle(doc: dict, basis: dict) -> dict:
    """Open the Basis's Bundle with integrity, refusing any Group/policy/fingerprint drift."""
    from modules.flow_gate.services import source_bundle_materializer as materializer
    from modules.flow_gate.services import source_bundle_service as bundles
    binding, source = basis.get("binding") or {}, basis.get("source") or {}
    try:
        opened = bundles.open_verified(binding.get("bundle_id") or "")
    except materializer.SourceBundleError as exc:
        raise ValueError("bundle_unavailable:" + exc.code) from exc
    row = opened["row"]
    if (row["project_id"] != doc.get("project_id") or row["group_id"] != doc.get("group_id")
            or row["exclusion_policy_version"] != source.get("exclusion_policy_version")
            or row["content_fingerprint"] != source.get("content_fingerprint")
            or row["bundle_sha256"] != binding.get("bundle_sha256")):
        raise ValueError("bundle_mismatch")
    return opened


def rebind(doc: dict, basis: dict, *, run_id: str | None = None) -> dict:
    """Basis Rebind: a new Bundle for the same content; basis_id and results stay valid."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import events as db_events
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import test_run_service
    fresh = capture(doc, _doc_cases(doc))
    if fresh["basis_id"] != basis["basis_id"]:
        raise ValueError("basis_stale_before_execution")
    rebound = {**basis, "binding": fresh["binding"]}
    with test_run_service._admission_lock, get_store().transaction():
        current_doc = db_docs.get_by_id(doc["doc_id"])
        stored = current(current_doc or {})
        if not stored or stored["basis_id"] != basis["basis_id"]:
            raise ValueError("basis_stale_before_execution")
        if not db_docs.update(doc["doc_id"], {"meta": metadata_with_basis(current_doc, rebound)}):
            raise RuntimeError("test_basis_rebind_failed")
        pin(current_doc, rebound)
        db_events.insert_event(doc["doc_id"], "test_spec_basis_rebound", note=json.dumps({
            "basis_id": basis["basis_id"], "run_id": run_id,
            "old_bundle_id": (basis.get("binding") or {}).get("bundle_id"),
            "bundle_id": rebound["binding"]["bundle_id"],
        }, ensure_ascii=False))
    return rebound


def edited_only(basis: dict, successor: dict, path: str, old: bytes) -> bool:
    """Whether a successor's Bundle is the Basis's exact source with only ``path`` edited.

    The asset edit captures under G, but G does not stop an editor or tool writing to the
    worktree: putting ``path``'s old bytes back into the successor's Bundle manifest must
    give the approved fingerprint again, so a product change made meanwhile never enters
    a successor (product changes need TS reopen and approval, D §3.5).
    """
    from modules.flow_gate.services import source_bundle_materializer as materializer
    from modules.flow_gate.services import source_bundle_service as bundles
    try:
        manifest = bundles.open_verified(successor["binding"]["bundle_id"])["manifest"]
    except materializer.SourceBundleError as exc:
        raise ValueError("basis_capture_failed:" + exc.code) from exc
    if ((successor.get("source") or {}).get("exclusion_policy_version") !=
            (basis.get("source") or {}).get("exclusion_policy_version")
            or not any(entry["path"] == path for entry in manifest["files"])):
        return False
    before = {"path": path, "size": len(old), "sha256": hashlib.sha256(old).hexdigest()}
    restored = [before if entry["path"] == path else entry for entry in manifest["files"]]
    return materializer._fingerprint(restored, [(name, None) for name in manifest["dirs"]]) == (
        (basis.get("source") or {}).get("content_fingerprint"))


def _copy_hashed(source: Path, target: Path) -> str:
    """Copy one file and return the sha256 of the bytes actually written to ``target``."""
    digest = hashlib.sha256()
    # A plain new file, not copy2: the Bundle is read-only and the run root must be removable.
    with source.open("rb") as reader, target.open("xb") as writer:
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
            writer.write(chunk)
    return digest.hexdigest()


def copy_bundle_source(source: Path, target: Path, manifest: dict) -> None:
    """Copy a verified Bundle source into a disposable root, byte-checked against its manifest.

    ``open_verified`` checks the manifest, not the files under it, so every copied file is
    hashed as it is written and the file and directory sets must equal the manifest: a
    Bundle file changed after opening (or during the copy) never reaches the run root.
    Links and specials abort.
    """
    from modules.flow_gate.services import source_bundle_materializer as materializer
    expected_files = {entry["path"]: entry["sha256"] for entry in manifest.get("files") or []}
    expected_dirs = set(manifest.get("dirs") or [])
    target.mkdir(parents=True, exist_ok=True)
    copied, created = {}, set()
    for current_dir, dirnames, filenames in os.walk(source):
        base = Path(current_dir)
        relative = base.relative_to(source)
        for name in dirnames:
            if materializer._linked((base / name).lstat()):
                raise ValueError("unsafe_execution_source")
            (target / relative / name).mkdir(exist_ok=True)
            created.add((relative / name).as_posix())
        for name in filenames:
            st = (base / name).lstat()
            if materializer._linked(st) or not stat.S_ISREG(st.st_mode):
                raise ValueError("unsafe_execution_source")
            copied[(relative / name).as_posix()] = _copy_hashed(base / name, target / relative / name)
    if created != expected_dirs or copied != expected_files:
        raise ValueError("execution_source_mismatch")


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
            "source": basis["source"], "binding": basis.get("binding"),
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
