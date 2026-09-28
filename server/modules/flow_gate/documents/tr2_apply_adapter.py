"""TR2 bridge for HiveWork's deterministic edit, backup and restore contract.

The edit spec remains the only source of proposed changes.  The source root is
always supplied by FlowGate, and callers hold the project source lock across
precheck, backup, apply, validation and (when needed) restore.  The matching,
EOL/BOM and binary snapshot primitives follow HiveWork's hive/apply.py and
hive/backup.py at 4ed4f6119fb54faacf93d48efac363ff55a9af5a.  Partial
application is intentionally absent from this adapter.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

from modules.flow_gate.documents import tr2_service as tr2

BACKUP_TTL_HOURS = 168
_BOM = b"\xef\xbb\xbf"
_BUNDLE_ID = re.compile(r"^[0-9a-f]{32}$")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _norm_nl(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _decode_preserving(raw: bytes) -> tuple[str, str, bool]:
    """HiveWork apply's UTF-8, dominant EOL and BOM matching primitive."""
    bom = raw.startswith(_BOM)
    text = raw[len(_BOM):].decode("utf-8") if bom else raw.decode("utf-8")
    crlf = text.count("\r\n")
    cr = text.count("\r") - crlf
    lf = text.count("\n") - crlf
    eol = "\r\n" if crlf and crlf >= lf and crlf >= cr else "\r" if cr > lf else "\n"
    return _norm_nl(text), eol, bom


def _encode_preserving(text: str, eol: str, bom: bool) -> bytes:
    body = text if eol == "\n" else text.replace("\n", eol)
    encoded = body.encode("utf-8")
    return _BOM + encoded if bom else encoded


def _post_apply_defects(rel: str, before: str, anchor: str,
                        replacement: str, after: str) -> list[str]:
    """HiveWork's deterministic dry-apply checks, before mutation."""
    defects: list[str] = []
    following = before[before.find(anchor) + len(anchor):]
    distinctive = lambda line: len(line.strip()) >= 6 and re.search(r"[A-Za-z_]\w\w", line)
    following_lines = [line for line in following.splitlines() if distinctive(line)][:8]
    replacement_lines = {line for line in replacement.splitlines() if distinctive(line)}
    run = 0
    for line in following_lines:
        if line not in replacement_lines:
            break
        run += 1
    if run >= 2:
        defects.append("replacement repeats lines after anchor")
    if rel.endswith(".vue"):
        template = lambda text: re.search(r"<template\b[^>]*>(.*?)</template>", text, re.DOTALL)
        old, new = template(before), template(after)
        if old and new and len(re.findall(r"\b\w+\.value\b", new.group(1))) > len(
                re.findall(r"\b\w+\.value\b", old.group(1))):
            defects.append("Vue template introduces ref.value")
    if rel.endswith(".py"):
        try:
            ast.parse(after)
        except SyntaxError:
            defects.append("applied Python file does not parse")
    return defects


def _safe_target(root: Path, rel: str, kind: str) -> Path:
    # tr2._target_path shares the source-root realpath jail with remote tools.
    target = tr2._target_path(root, rel, kind)
    parent = target.parent
    while parent != root:
        if parent.is_symlink():
            raise tr2.Tr2ValidationError("tr2_path_unsafe", rel)
        parent = parent.parent
    return target


def _atomic_bytes(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".tr2-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _manifest_path(backup_root: Path, bundle_id: str) -> Path:
    if not _BUNDLE_ID.fullmatch(bundle_id):
        raise tr2.Tr2ValidationError("tr2_recovery_required", "backup_bundle_id")
    return backup_root / bundle_id / "manifest.json"


def _save_manifest(path: Path, manifest: dict) -> None:
    _atomic_bytes(path, (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8"))


def _load_manifest(backup_root: Path, bundle_id: str) -> tuple[Path, dict]:
    path = _manifest_path(backup_root, bundle_id)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise tr2.Tr2ValidationError("tr2_recovery_required", "backup_bundle_id",
                                     {"reason": "backup manifest unavailable"}) from exc
    if manifest.get("bundle_id") != bundle_id or manifest.get("version") != 1:
        raise tr2.Tr2ValidationError("tr2_recovery_required", "backup_bundle_id")
    return path, manifest


class Tr2ApplyAdapter:
    """Source-only adapter; it owns no document, workflow, Git or attempt state."""

    def evaluate(self, spec: dict, source_root, *, baseline: str | None = None) -> dict:
        root = Path(source_root).resolve()
        if spec.get("termination") != "ready_to_apply" or not spec.get("edits"):
            raise tr2.Tr2ValidationError("tr2_spec_invalid", "edit_spec.edits")
        paths = tr2.target_set(spec)
        live = tr2.target_fingerprint(spec, root)
        drift = baseline is not None and baseline != live
        results: list[dict] = []
        composed: dict[str, str] = {}
        for edit in spec["edits"]:
            rel = tr2.normalized_target_path(edit)
            kind = edit.get("kind", "edit")
            target = _safe_target(root, rel, kind)
            status = "applicable"
            count = None
            if kind == "create_file":
                if target.exists() or target.is_symlink():
                    status = "file_exists"
                elif not edit.get("content", "").strip():
                    status = "empty_content"
            elif not target.is_file():
                status = "file_missing"
            else:
                try:
                    text = composed.get(rel)
                    if text is None:
                        text = _decode_preserving(target.read_bytes())[0]
                    anchor = _norm_nl(edit["anchor_old"])
                    replacement = _norm_nl(edit["replacement_new"])
                    count = text.count(anchor) if anchor else 0
                    if count != 1:
                        status = "anchor_missing" if count == 0 else "anchor_ambiguous"
                    elif anchor == replacement:
                        status = "no_change"
                    else:
                        modified = text.replace(anchor, replacement, 1)
                        if _post_apply_defects(rel, text, anchor, replacement, modified):
                            status = "post_apply_broken"
                        else:
                            composed[rel] = modified
                except UnicodeError:
                    status = "not_utf8"
            results.append({"id": edit["id"], "path": rel, "kind": kind,
                            "status": status, "anchor_count": count,
                            "applicable": status == "applicable"})
        ready = not drift and all(item["applicable"] for item in results)
        return {"ready": ready, "drift": drift, "baseline_fingerprint": baseline,
                "live_fingerprint": live, "targets": paths, "edits": results,
                "code": ("tr2_source_drift" if drift else
                         "tr2_edit_not_applicable" if not ready else None)}

    def create_backup(self, spec: dict, source_root, backup_root,
                      ttl_hours: int = BACKUP_TTL_HOURS, *, baseline: str | None = None) -> str:
        root = Path(source_root).resolve()
        store = Path(backup_root).resolve()
        if store == root or root in store.parents or ttl_hours <= 0:
            raise tr2.Tr2ValidationError("tr2_path_unsafe", "backup_root")
        result = self.evaluate(spec, root, baseline=baseline)
        if not result["ready"]:
            raise tr2.Tr2ValidationError(result["code"], "edit_spec", result)
        store.mkdir(parents=True, exist_ok=True)
        bundle_id = uuid.uuid4().hex
        bundle = store / bundle_id
        bundle.mkdir(mode=0o700)
        files: dict[str, dict] = {}
        created: dict[str, str] = {}
        for rel in result["targets"]:
            kind = tr2.target_kind(spec, rel)
            target = _safe_target(root, rel, kind)
            if kind == "create_file":
                edit = next(e for e in spec["edits"] if tr2.normalized_target_path(e) == rel)
                created[rel] = _digest(edit["content"].encode("utf-8"))
            else:
                data = target.read_bytes()
                snapshot = bundle / "files" / rel
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                _atomic_bytes(snapshot, data)
                files[rel] = {"sha256": _digest(data), "mode": target.stat().st_mode & 0o777}
        if tr2.target_fingerprint(spec, root) != result["live_fingerprint"]:
            raise tr2.Tr2ValidationError("tr2_source_drift", "backup")
        manifest = {"version": 1, "bundle_id": bundle_id, "source_root": str(root),
                    "phase": "snapshot", "baseline": result["live_fingerprint"],
                    "files": files, "created": created, "ttl_hours": ttl_hours}
        _save_manifest(bundle / "manifest.json", manifest)
        return bundle_id

    def apply_all(self, spec: dict, source_root, backup_root, bundle_id: str) -> dict:
        root = Path(source_root).resolve()
        store = Path(backup_root).resolve()
        path, manifest = _load_manifest(store, bundle_id)
        if manifest["source_root"] != str(root) or manifest["phase"] != "snapshot":
            raise tr2.Tr2ValidationError("tr2_recovery_required", "backup_bundle_id")
        result = self.evaluate(spec, root, baseline=manifest["baseline"])
        if not result["ready"]:
            raise tr2.Tr2ValidationError(result["code"], "edit_spec", result)
        if set(result["targets"]) != set(manifest["files"]) | set(manifest["created"]):
            raise tr2.Tr2ValidationError("tr2_recovery_required", "backup_bundle_id")
        manifest["phase"] = "apply"
        _save_manifest(path, manifest)  # durable recovery marker before first source write
        written: list[str] = []
        created_opened: set[str] = set()
        try:
            for edit in spec["edits"]:
                rel = tr2.normalized_target_path(edit)
                kind = edit.get("kind", "edit")
                target = _safe_target(root, rel, kind)
                if kind == "create_file":
                    if target.exists() or target.is_symlink():
                        raise ValueError(f"create collision: {rel}")
                    data = edit["content"].encode("utf-8")
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                    flags |= getattr(os, "O_NOFOLLOW", 0)
                    fd = os.open(target, flags, 0o644)
                    with os.fdopen(fd, "wb") as stream:
                        created_opened.add(rel)
                        manifest["created_opened"] = sorted(created_opened)
                        _save_manifest(path, manifest)
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                else:
                    text, eol, bom = _decode_preserving(target.read_bytes())
                    anchor = _norm_nl(edit["anchor_old"])
                    replacement = _norm_nl(edit["replacement_new"])
                    if not anchor or text.count(anchor) != 1:
                        raise ValueError(f"anchor no longer unique: {rel}")
                    modified = text.replace(anchor, replacement, 1)
                    if _post_apply_defects(rel, text, anchor, replacement, modified):
                        raise ValueError(f"post-apply check failed: {rel}")
                    mode = target.stat().st_mode & 0o777
                    _atomic_bytes(target, _encode_preserving(
                        modified, eol, bom))
                    os.chmod(target, mode)
                if rel not in written:
                    written.append(rel)
            manifest["phase"] = "applied"
            _save_manifest(path, manifest)
            return {"ok": True, "bundle_id": bundle_id, "written": written}
        except Exception as exc:
            manifest["phase"] = "rollback"
            _save_manifest(path, manifest)  # durable before any restore mutation
            try:
                self.restore(store, bundle_id, source_root=root,
                             owned_created=created_opened)
            except Exception as restore_exc:
                raise tr2.Tr2ValidationError("tr2_recovery_required", "restore",
                                             {"reason": str(restore_exc)}) from restore_exc
            raise tr2.Tr2ValidationError("tr2_apply_failed", "apply",
                                         {"reason": str(exc)}) from exc

    def restore(self, backup_root, bundle_id: str, *, source_root,
                owned_created: set[str] | None = None) -> dict:
        store = Path(backup_root).resolve()
        path, manifest = _load_manifest(store, bundle_id)
        root = Path(manifest["source_root"]).resolve()
        if root != Path(source_root).resolve():
            raise tr2.Tr2ValidationError("tr2_recovery_required", "source_root")
        if manifest["phase"] == "restored":
            return {"ok": True, "bundle_id": bundle_id, "restored": True}
        if manifest["phase"] == "applied":
            # All declared creates were exclusively opened by this attempt.
            # Validation may subsequently edit their bytes; they still belong
            # to this rollback, including after a restart.
            manifest["rollback_from"] = "applied"
        manifest["phase"] = "rollback"
        _save_manifest(path, manifest)
        for rel, metadata in manifest["files"].items():
            target = _safe_target(root, rel, "edit")
            snapshot = path.parent / "files" / rel
            data = snapshot.read_bytes()
            if _digest(data) != metadata["sha256"]:
                raise tr2.Tr2ValidationError("tr2_recovery_required", rel,
                                             {"reason": "backup bytes changed"})
            _atomic_bytes(target, data)
            os.chmod(target, metadata["mode"])
        for rel, expected_sha in manifest["created"].items():
            owned = (set(manifest.get("created_opened") or [])
                     if owned_created is None else owned_created)
            if (owned_created is not None and rel not in owned_created
                    and manifest.get("rollback_from") != "applied"):
                continue
            target = _safe_target(root, rel, "create_file")
            if target.exists():
                if (rel not in owned and manifest.get("rollback_from") != "applied" and
                        (not target.is_file() or _digest(target.read_bytes()) != expected_sha)):
                    raise tr2.Tr2ValidationError("tr2_recovery_required", rel,
                                                 {"reason": "created target differs"})
                target.unlink()
        for rel, metadata in manifest["files"].items():
            if _digest(_safe_target(root, rel, "edit").read_bytes()) != metadata["sha256"]:
                raise tr2.Tr2ValidationError("tr2_recovery_required", rel)
        if any(_safe_target(root, rel, "create_file").exists() for rel in manifest["created"]):
            raise tr2.Tr2ValidationError("tr2_recovery_required", "created")
        manifest["phase"] = "restored"
        _save_manifest(path, manifest)
        return {"ok": True, "bundle_id": bundle_id, "restored": True}

    def recover(self, backup_root, bundle_id: str, *, source_root) -> dict:
        """Restart path: an interrupted apply/rollback resumes restore, never apply."""
        path, manifest = _load_manifest(Path(backup_root).resolve(), bundle_id)
        if manifest["phase"] not in {"snapshot", "apply", "applied", "rollback", "restored"}:
            raise tr2.Tr2ValidationError("tr2_recovery_required", "phase")
        if manifest["phase"] == "snapshot":
            root = Path(source_root).resolve()
            if str(root) != manifest["source_root"]:
                raise tr2.Tr2ValidationError("tr2_recovery_required", "source_root")
            try:
                unchanged = all(
                    _digest(_safe_target(root, rel, "edit").read_bytes()) == info["sha256"]
                    for rel, info in manifest["files"].items()
                ) and all(not _safe_target(root, rel, "create_file").exists()
                          for rel in manifest["created"])
            except (OSError, tr2.Tr2ValidationError):
                unchanged = False
            if not unchanged:
                raise tr2.Tr2ValidationError("tr2_recovery_required", "snapshot",
                                             {"reason": "source changed before apply"})
            manifest["phase"] = "restored"
            _save_manifest(path, manifest)
            return {"ok": True, "bundle_id": bundle_id, "recovered_no_write": True}
        return self.restore(backup_root, bundle_id, source_root=source_root)


adapter = Tr2ApplyAdapter()
