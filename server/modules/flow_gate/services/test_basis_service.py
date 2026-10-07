"""Canonical Test Basis for approved contract-2 specifications.

Basis v3 (0684 T#2, D#1 §3-3, §3-5): the Basis is what a run executed. A spec run copies
the Group worktree into its disposable root and fingerprints the bytes it copied
(``measure_run``); uncommitted and untracked Group work is part of it, and so are the
POSIX execute bits the copy keeps (a helper the Case runs). The Basis is kept
on the run and on the result record it produced -- not on the TS. A result is valid while
its Basis equals the Basis the live source has now (``resolve``), so a source, asset or
TS revision change makes earlier results stale without any invalidation step.

The scan, exclusion and hash rules are the source fingerprint module's. Decision paths
(run finalize, manual result submission, TSR gate) measure; display paths only reuse a
memo of the last measurement and say "unchecked" when it no longer fits (D#1 §3-6).

The binding (git revision, dirty flag, measured time, run) is display information kept
beside the identity, never part of basis_id. v1/v2 Basis values (a HEAD tree or a Source
Bundle) are outdated: never valid again, never re-measured (D#1 §7).

Test Asset Policy (0682 D#1 §3.8): ``asset_kind`` is the one rule the run's manifest, the
asset API and the locator share. Git tracking is not part of it: an asset must be in the
measured source with a hash and pass the path safety checks, so untracked test files a TR
created are assets too.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from modules.flow_gate.services import source_fingerprint
from modules.flow_gate.storage import paths as storage_paths

_LOCATOR = re.compile(r"^([A-Za-z0-9_./-]+)(?:::(\w+(?:::\w+)*))?$")

BASIS_VERSION = 3
SOURCE_KIND = "run_source"
ASSET_POLICY_VERSION = "test-asset-v2"
# 2: spec runs execute through the shared process layer (tr_self_check_executor).
RUNNER_GENERATION = 2

# "unchecked" is a display-only state: no memo fits the live source, nothing was measured.
VALID, STALE, UNVERIFIABLE, UNCHECKED = "valid", "stale", "unverifiable", "unchecked"
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
# Unverifiable reasons are "source_unmeasurable:<source fingerprint failure code>".
REASON_UNMEASURABLE = "source_unmeasurable"
REASON_NOT_MEASURED = "source_not_measured"
# A TS whose Group has no worktree has no source to measure (D#1 §3-5 compat): its results
# are recorded without a Basis and judged by the record alone.
NO_WORKTREE = "group_worktree_unavailable"

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


class BasisUnverifiable(ValueError):
    """The live source could not be measured; distinct from a stale Basis."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"{REASON_UNMEASURABLE}:{code}")


class SourceNotMeasured(LookupError):
    """Display path: no memo fits the live source (``resolve(memo=True)`` never hashes)."""


def canonical_hash(value: dict | list) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def asset_kind(path) -> str | None:
    """Test Asset Policy: the asset kind of a source-relative path, or None if it cannot be one.

    Lexical only (no disk access), so approval, the asset API and the locator agree on
    it. Refused: absolute, drive, backslash, colon, control characters, empty/``.``/``..``
    segments, paths outside the test roots (product source), a co-located file outside
    ``__tests__``, a suffix with no kind, and anything the source exclusion policy leaves
    out (an excluded directory on the way or a secret file name): an asset the run's copy
    never holds can be neither run nor judged.
    """
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or ":" in path:
        return None
    parts = path.split("/")
    if any(part in ("", ".", "..") or any(ord(c) < 32 or ord(c) == 127 for c in part)
           for part in parts):
        return None
    if source_fingerprint.excluded(path):
        return None
    head = tuple(parts)
    inside = any(len(parts) > len(root) and head[:len(root)] == root for root in ASSET_ROOTS)
    if not inside:
        inside = any(len(parts) > len(root) + 1 and head[:len(root)] == root
                     and COLOCATED_DIR in parts[len(root):-1] for root in COLOCATED_ROOTS)
    if not inside:
        return None
    return ASSET_SUFFIXES.get(PurePosixPath(parts[-1]).suffix)


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
        if source_fingerprint._linked(st):
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
    """One manifest rule for the run's copy and the live measure; hashes come from the scan.

    With ``root`` (a run's copy; root is the live worktree it was copied from) every asset
    must pass the Test Asset Policy and be in the copy; Git tracking is not asked, so an
    untracked test file a TR created is an asset like a tracked one. Membership in the
    measured source stands for existence, hash and safety (the scan refuses links and
    specials and hashes what it holds). Without ``root`` (live measure) a gap is kept as a
    null hash, so it shows up as a manifest change.
    """
    manifest = []
    for path, role in sorted(roles.items()):
        kind = asset_kind(path)
        if root is not None:
            if kind is None:
                # Excluded by the source policy: refused explicitly, never copied or run.
                excluded = isinstance(path, str) and path and source_fingerprint.excluded(path)
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
    project_id, group_id = doc.get("project_id"), doc.get("group_id")
    if not project_id or not group_id:
        raise source_fingerprint.SourceFingerprintError(NO_WORKTREE, "TS has no group worktree")
    return project_id, group_id


def worktree(doc: dict) -> Path:
    """The TS's exact Group worktree (raises SourceFingerprintError)."""
    return source_fingerprint.resolve_worktree(*_group_of(doc))


def measure_run(doc: dict, cases: list[dict], target: Path, *, run_id: str | None = None) -> dict:
    """Run Basis (D#1 §3-3 preparing): copy the worktree to ``target`` and fingerprint it.

    The caller holds the Group's source lock around this call. A measurement failure is
    ``basis_capture_failed:<code>``; an asset the copy does not hold is refused with the
    manifest rule's code. Nothing is written to the DB or the TS here.
    """
    try:
        root = worktree(doc)
        measured = source_fingerprint.copy_measured(root, target)
    except source_fingerprint.SourceFingerprintError as exc:
        raise ValueError("basis_capture_failed:" + exc.code) from exc
    manifest = _manifest(_roles(cases), measured["hashes"], root=root)
    identity = _identity(doc, policy=measured["exclusion_policy_version"],
                         fingerprint=measured["content_fingerprint"], manifest=manifest)
    project_id, group_id = _group_of(doc)
    binding = {"git_revision": measured["source_revision"],
               "source_dirty": bool(measured["source_dirty"]),
               "measured_at": datetime.now(timezone.utc).isoformat(),
               "project_id": project_id, "group_id": group_id, "run_id": run_id,
               "metrics": measured.get("metrics")}
    return {"basis_id": canonical_hash(identity), **identity, "binding": binding,
            "manifest": manifest}


def resolve(doc: dict, cases: list[dict], *, memo: bool = False) -> dict:
    """The identity the live source has now (no binding).

    Decision paths measure (``memo=False``) and raise BasisUnverifiable when the source
    cannot be measured. Display paths (``memo=True``) never hash: the last measurement is
    reused while the scan is unchanged, otherwise SourceNotMeasured.
    """
    try:
        root = worktree(doc)
    except source_fingerprint.SourceFingerprintError as exc:
        raise BasisUnverifiable(exc.code) from exc
    if memo:
        live = source_fingerprint.memo_lookup(root)
        if live is None:
            raise SourceNotMeasured(REASON_NOT_MEASURED)
    else:
        try:
            live = source_fingerprint.measure(root)
        except source_fingerprint.SourceFingerprintError as exc:
            raise BasisUnverifiable(exc.code) from exc
    manifest = _manifest(_roles(cases), live["hashes"])
    identity = _identity(doc, policy=live["exclusion_policy_version"],
                         fingerprint=live["content_fingerprint"], manifest=manifest)
    return {"basis_id": canonical_hash(identity), **identity, "manifest": manifest,
            "probe": {"source_revision": live["source_revision"],
                      "source_dirty": live["source_dirty"]}}


def outdated(basis: dict | None) -> bool:
    """A v1 (HEAD tree) or v2 (Source Bundle) Basis can no longer be judged valid."""
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


def live_state(doc: dict | None, cases: list[dict] | None = None, *, memo: bool = False) -> dict:
    """Measure (or, for display, recall) the live Basis once for several judgements.

    ``{"live": <Basis>|None, "state": None|UNCHECKED|UNVERIFIABLE, "reasons": [...],
    "no_worktree": bool}``; ``state`` is None when ``live`` was obtained.
    """
    if not doc:
        return {"live": None, "state": UNVERIFIABLE, "no_worktree": False,
                "reasons": [REASON_UNMEASURABLE + ":ts_missing"]}
    try:
        if cases is None:
            cases = _doc_cases(doc)
        return {"live": resolve(doc, cases, memo=memo), "state": None, "reasons": [],
                "no_worktree": False}
    except SourceNotMeasured:
        return {"live": None, "state": UNCHECKED, "reasons": [REASON_NOT_MEASURED],
                "no_worktree": False}
    except BasisUnverifiable as exc:
        return {"live": None, "state": UNVERIFIABLE, "reasons": [str(exc)],
                "no_worktree": exc.code == NO_WORKTREE}
    except Exception as exc:  # noqa: BLE001 -- a source that cannot be judged is unverifiable
        reason = REASON_UNMEASURABLE + ":" + (
            (str(exc).split(":", 1)[0] if isinstance(exc, (ValueError, TypeError)) else "")
            or type(exc).__name__)
        return {"live": None, "state": UNVERIFIABLE, "reasons": [reason], "no_worktree": False}


def judge(basis: dict | None, state: dict) -> dict:
    """Basis Verdict of one record's Basis against a ``live_state`` (valid/stale/...)."""
    live = state["live"]
    if basis and outdated(basis):
        return _judged(STALE, [REASON_BASIS_OUTDATED], basis, live)
    if live is None:
        if basis is None and state["no_worktree"]:
            # No Group worktree to measure: a record made without a Basis stands as recorded.
            return _judged(VALID, [], None)
        return _judged(state["state"], state["reasons"], basis)
    if not basis:
        return _judged(STALE, [REASON_BASIS_MISSING], None, live)
    if live["basis_id"] == basis.get("basis_id"):
        return _judged(VALID, [], basis, live)
    return _judged(STALE, _stale_reasons(basis, live), basis, live)


def verdict(doc: dict | None, basis: dict | None, cases: list[dict] | None = None, *,
            memo: bool = False) -> dict:
    """Basis Verdict: is ``basis`` (the Basis a run executed or a result was recorded
    under) the Basis the live source has now? valid / stale / unverifiable, and on display
    paths (``memo``) also unchecked. An outdated Basis is stale without measuring."""
    if basis and outdated(basis):
        return _judged(STALE, [REASON_BASIS_OUTDATED], basis)
    return judge(basis, live_state(doc, cases, memo=memo))


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
        "git_revision": binding.get("git_revision") or source.get("git_revision"),
        "tree": source.get("tree"),
    }
    return {key: value for key, value in identity.items() if value not in (None, "")}


def source_summary(basis: dict | None) -> dict:
    """Display fields of an execution Basis: what the run executed (D#1 §3-4, §5)."""
    basis = basis or {}
    source, binding = basis.get("source") or {}, basis.get("binding") or {}
    fingerprint = source.get("content_fingerprint") or ""
    measured_at = binding.get("measured_at") or binding.get("captured_at")
    return {"kind": source.get("kind"), "fingerprint_prefix": fingerprint[:12] or None,
            "measured_at": measured_at, "captured_at": measured_at,
            "source_dirty": binding.get("source_dirty"),
            "git_revision": binding.get("git_revision"), "run_id": binding.get("run_id")}


def asset_manifest(doc: dict, cases: list[dict]) -> list[dict]:
    """The TS's test assets with their current on-disk hashes (asset API, D#1 §3-7).

    Authority is the approved specification (automation_ref / test_assets), checked with
    the Test Asset Policy; only those few files are read, never the whole source.
    """
    root = source_root(doc)
    manifest = []
    for path, role in sorted(_roles(cases).items()):
        kind = asset_kind(path)
        content_hash = None
        if kind is not None:
            try:
                content_hash = hashlib.sha256(safe_asset_file(root, path).read_bytes()).hexdigest()
            except (ValueError, OSError):
                content_hash = None
        manifest.append({"path": path, "content_hash": content_hash, "role": role, "kind": kind})
    return manifest


def metadata_without_basis(doc: dict) -> str:
    """0684 T#1: TS approval leaves no Basis -- the next run measures one.

    A Basis an earlier approval or run stored moves to ``superseded_test_basis`` (kept
    for audit, D#1 §7) and is never judged again.
    """
    try:
        meta = json.loads(doc.get("meta") or "{}")
    except (TypeError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    previous = meta.pop("test_basis", None)
    if previous:
        meta["superseded_test_basis"] = previous
    return json.dumps(meta, ensure_ascii=False)
