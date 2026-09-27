"""Canonical TR2 document domain: one validator, writer and fingerprint implementation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.documents.type_code import doc_id_type_code
from modules.flow_gate.utils.id_validators import DOC_ID
from modules.flow_gate.documents.tr2_errors import TR2_ERRORS
from modules.flow_gate.services.test_command_service import normalize_command
from modules.flow_gate.storage import paths as storage_paths
from modules.flow_gate.storage.safe_path import is_safe_relative, resolve_in_root

T2_TYPE_CODE = "T2"
TR2_TYPE_CODE = "TR2"
TR2_BODY_VERSION = 1
DOCUMENT_FILENAME = "document.json"
TR2_COMMAND_MAX_COUNT = 50
TR2_COMMAND_MAX_LEN = 500
_SPEC_HEX = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_HEX = re.compile(r"^sha256:[0-9a-f]{64}$")
_DEFERRED_REASONS = frozenset({
    "not_expressible_as_edit", "needs_runtime", "policy_direction",
    "multi_file_design", "anchor_not_grounded",
})
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class Tr2ValidationError(Exception):
    def __init__(self, code: str, loc: str = "", details: dict | None = None):
        if code not in TR2_ERRORS:
            raise ValueError(f"Unknown TR2 error: {code}")
        self.code = code
        self.loc = loc
        self.details = {"loc": loc, **(details or {})}
        super().__init__(f"{code}: {loc}")


def _invalid(loc: str, reason: str) -> None:
    raise Tr2ValidationError("tr2_spec_invalid", loc, {"reason": reason})


def _object(value, loc: str, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        _invalid(loc, "object required")
    unknown = set(value) - allowed
    if unknown:
        _invalid(loc, f"unknown keys: {sorted(unknown)}")
    missing = required - set(value)
    if missing:
        _invalid(loc, f"missing keys: {sorted(missing)}")
    return value


def _text(value, loc: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        _invalid(loc, "non-empty string required")
    return value


def parse(raw: str) -> dict:
    try:
        body = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise Tr2ValidationError("tr2_spec_invalid", "body", {"reason": str(exc)}) from exc
    if not isinstance(body, dict):
        _invalid("body", "object required")
    return body


def normalized_target_path(edit: dict) -> str:
    p = edit.get("file")
    if not isinstance(p, str) or not p or "\x00" in p or not is_safe_relative(p):
        raise Tr2ValidationError("tr2_path_unsafe", "file")
    p = p.replace("\\", "/")
    parts = [part for part in p.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise Tr2ValidationError("tr2_path_unsafe", "file")
    return "/".join(parts)


def target_set(edit_spec: dict) -> list[str]:
    return sorted({normalized_target_path(e) for e in edit_spec["edits"]},
                  key=lambda path: path.encode("utf-8"))


def target_kind(edit_spec: dict, path: str) -> str:
    return ("create_file" if any(normalized_target_path(e) == path and
            e.get("kind", "edit") == "create_file" for e in edit_spec["edits"]) else "edit")


def _target_path(source_root, path: str, kind: str) -> Path:
    root = Path(source_root).resolve()
    target = root.joinpath(*path.split("/"))
    checked = resolve_in_root(root, path)
    if checked is None or target.is_symlink():
        raise Tr2ValidationError("tr2_path_unsafe", path)
    parent = resolve_in_root(root, str(Path(path).parent))
    if parent is None or (kind == "create_file" and not parent.is_dir()):
        raise Tr2ValidationError("tr2_path_unsafe", path)
    if target.exists() and not target.is_file():
        raise Tr2ValidationError("tr2_path_unsafe", path)
    return target


def validate(body: dict, *, doc: dict) -> dict:
    # Baseline is server-managed. Drop it without inspecting its value.
    body = dict(body)
    body.pop("baseline_fingerprint", None)
    body.pop("codebase_root", None)
    body.pop("source_honey", None)
    _object(body, "body", {"tr2_version", "source_t2_doc_id", "edit_spec"},
            {"tr2_version", "source_t2_doc_id", "edit_spec"})
    if type(body["tr2_version"]) is not int or body["tr2_version"] != TR2_BODY_VERSION:
        _invalid("tr2_version", "unsupported version")
    source = _text(body["source_t2_doc_id"], "source_t2_doc_id")
    if DOC_ID.fullmatch(source) is None or doc_id_type_code(source) != T2_TYPE_CODE:
        _invalid("source_t2_doc_id", "canonical T2 document ID required")
    def reject_floats(value, loc):
        if isinstance(value, float):
            _invalid(loc, "floating point values are not supported")
        if isinstance(value, dict):
            for key, child in value.items():
                reject_floats(child, f"{loc}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                reject_floats(child, f"{loc}[{index}]")
    reject_floats(body, "body")
    spec = _object(body["edit_spec"], "edit_spec",
                   {"termination", "edits", "deferred", "gate", "verify", "notes"},
                   {"termination", "edits", "deferred", "gate"})
    if not isinstance(spec["termination"], str) or spec["termination"] not in {"ready_to_apply", "needs_more_work"}:
        _invalid("edit_spec.termination", "invalid termination")
    if not isinstance(spec["edits"], list):
        _invalid("edit_spec.edits", "array required")
    if not isinstance(spec["deferred"], list):
        _invalid("edit_spec.deferred", "array required")
    if spec["termination"] == "ready_to_apply" and not spec["edits"]:
        # Approval precheck and commit-time guards belong to the following task sets.
        _invalid("edit_spec.edits", "ready_to_apply requires at least one edit")
    if "notes" in spec:
        _text(spec["notes"], "edit_spec.notes", nonempty=False)
    ids = set()
    target_kinds: dict[str, str] = {}
    for i, edit in enumerate(spec["edits"]):
        loc = f"edit_spec.edits[{i}]"
        _object(edit, loc,
                {"id", "kind", "file", "anchor_old", "replacement_new", "content",
                 "rationale", "confidence", "evidence", "anchor_status"},
                {"id", "file", "rationale", "confidence"})
        ident = _text(edit["id"], loc+".id")
        if ident in ids:
            _invalid(loc+".id", "duplicate id")
        ids.add(ident)
        kind = edit.get("kind", "edit")
        if not isinstance(kind, str) or kind not in {"edit", "create_file"}:
            _invalid(loc+".kind", "invalid kind")
        path = normalized_target_path(edit)
        prior_kind = target_kinds.get(path)
        if prior_kind == "create_file" or (prior_kind is not None and kind == "create_file"):
            _invalid(loc+".file", "create_file cannot share a target with another edit")
        target_kinds[path] = kind
        _text(edit["rationale"], loc+".rationale")
        if not isinstance(edit["confidence"], str) or edit["confidence"] not in {"high", "medium", "low"}:
            _invalid(loc+".confidence", "invalid confidence")
        if kind == "edit":
            _text(edit.get("anchor_old"), loc+".anchor_old")
            _text(edit.get("replacement_new"), loc+".replacement_new", nonempty=False)
            if edit["replacement_new"] == edit["anchor_old"]:
                _invalid(loc+".replacement_new", "no-op edit")
            if "content" in edit:
                _invalid(loc+".content", "content only valid for create_file")
        else:
            _text(edit.get("content"), loc+".content")
            if any(edit.get(field) not in (None, "") for field in
                   ("anchor_old", "replacement_new", "anchor_status")):
                _invalid(loc, "anchor fields forbidden for create_file")
    for i, item in enumerate(spec["deferred"]):
        loc = f"edit_spec.deferred[{i}]"
        _object(item, loc, {"id", "reason", "rationale", "file"},
                {"id", "reason", "rationale"})
        ident = _text(item["id"], loc+".id")
        if ident in ids:
            _invalid(loc+".id", "duplicate id")
        ids.add(ident)
        if not isinstance(item["reason"], str) or item["reason"] not in _DEFERRED_REASONS:
            _invalid(loc+".reason", "invalid reason")
        _text(item["rationale"], loc+".rationale")
        if "file" in item:
            normalized_target_path(item)
    gate = _object(spec["gate"], "edit_spec.gate", {"commands", "apply"},
                   {"commands", "apply"})
    if gate["apply"] is not False:
        _invalid("edit_spec.gate.apply", "must be false")
    commands = gate["commands"]
    if not isinstance(commands, list) or len(commands) > TR2_COMMAND_MAX_COUNT:
        _invalid("edit_spec.gate.commands", "invalid command list")
    for i, command in enumerate(commands):
        loc = f"edit_spec.gate.commands[{i}]"
        if not isinstance(command, str):
            _invalid(loc, "string required")
        normalized = normalize_command(command)
        if not normalized or len(normalized) > TR2_COMMAND_MAX_LEN:
            _invalid(loc, "empty or too long command")
    if spec.get("verify") is not None:
        verify = _object(spec["verify"], "edit_spec.verify",
                         {"red_test_node", "test_edit_ids", "rationale"})
        if "red_test_node" in verify:
            _text(verify["red_test_node"], "edit_spec.verify.red_test_node")
        if "test_edit_ids" in verify:
            edit_ids = {edit["id"] for edit in spec["edits"]}
            if (not isinstance(verify["test_edit_ids"], list)
                    or any(not isinstance(ident, str) or ident not in edit_ids
                           for ident in verify["test_edit_ids"])):
                _invalid("edit_spec.verify.test_edit_ids", "unknown edit id")
        if "rationale" in verify:
            _text(verify["rationale"], "edit_spec.verify.rationale")
    try:
        _json(body)
    except (TypeError, ValueError, OverflowError) as exc:
        _invalid("body", f"not canonical JSON: {exc}")
    return body


def canonicalize(body: dict) -> dict:
    body = dict(body)
    body.pop("codebase_root", None)
    body.pop("source_honey", None)
    body.pop("baseline_fingerprint", None)
    spec = dict(body["edit_spec"])
    spec["edits"] = [
        dict(edit, kind=edit.get("kind", "edit"), file=normalized_target_path(edit))
        for edit in spec["edits"]
    ]
    spec["deferred"] = [
        dict(item, file=normalized_target_path(item)) if "file" in item else dict(item)
        for item in spec["deferred"]
    ]
    spec["gate"] = dict(spec["gate"], commands=[
        normalize_command(command) for command in spec["gate"]["commands"]])
    body["edit_spec"] = spec
    return body


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def dumps(body: dict) -> str:
    return _json(body) + "\n"


def spec_fingerprint(edit_spec: dict) -> str:
    result = hashlib.sha256(_json(edit_spec).encode("utf-8")).hexdigest()
    if not _SPEC_HEX.fullmatch(result):
        raise Tr2ValidationError("tr2_history_invariant_error", "spec_fingerprint")
    return result


def target_fingerprint(edit_spec: dict, source_root) -> str:
    material = bytearray()
    for path in target_set(edit_spec):
        kind = target_kind(edit_spec, path)
        target = _target_path(source_root, path, kind)
        digest = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else "ABSENT"
        material.extend(path.encode("utf-8") + b"\x00" + kind.encode("utf-8") +
                        b"\x00" + digest.encode("ascii") + b"\n")
    result = "sha256:" + hashlib.sha256(material).hexdigest()
    if not _SOURCE_HEX.fullmatch(result):
        raise Tr2ValidationError("tr2_history_invariant_error", "target_fingerprint")
    return result


def canonical_path_for_doc(doc: dict) -> Path:
    group_id = doc["group_id"]
    doc_id = doc["doc_id"]
    code = doc_id[len(group_id)+1:] if doc_id.startswith(group_id+".") else doc_id
    return storage_paths.document_path(doc["project_id"], group_id, code, DOCUMENT_FILENAME,
                                       module=doc.get("module") or "none",
                                       branch=doc.get("branch") or "main")


def load_body(path) -> dict:
    return parse(Path(path).read_text(encoding="utf-8"))


def _write_bytes_atomically(path, content: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".tr2-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_body_atomically(path, body: dict) -> None:
    _write_bytes_atomically(path, dumps(body).encode("utf-8"))


def resolve_source_root(project_id: str, group_id: str) -> Path:
    from modules.flow_gate.services import git_service
    from modules.flow_gate.db import projects as db_projects
    root, reason = git_service.effective_src_root_ex(project_id, group_id)
    if root is not None:
        return Path(root)
    if reason != git_service.SRC_ROOT_INTEGRATION_OFF:
        raise Tr2ValidationError("tr2_git_unavailable", "source_root", {"reason": reason})
    project = db_projects.get_by_id(project_id)
    name = (project or {}).get("project_name")
    if not name:
        raise Tr2ValidationError("tr2_git_unavailable", "source_root")
    settings = db_projects.get_settings(project_id) or {}
    root = storage_paths.src_root(name, settings.get("branch") or "main").resolve()
    if not root.is_dir():
        raise Tr2ValidationError("tr2_git_unavailable", "source_root")
    return root


def verify_pair(tr2_doc_id: str, body: dict) -> None:
    result = db_wfseq.get_item_by_result_doc_id(tr2_doc_id)
    paired = db_wfseq.get_paired_instruction_item(tr2_doc_id, T2_TYPE_CODE)
    tr2_doc = db_docs.get_by_id(tr2_doc_id)
    t2_doc = db_docs.get_by_id(body["source_t2_doc_id"])
    if (result is None or result.get("type") != TR2_TYPE_CODE
            or result.get("result_doc_id") != tr2_doc_id
            or paired is None
            or paired.get("result_doc_id") != body["source_t2_doc_id"]
            or paired.get("sequence_id") != result.get("sequence_id")
            or tr2_doc is None or t2_doc is None
            or t2_doc.get("type_code") != T2_TYPE_CODE
            or t2_doc.get("project_id") != tr2_doc.get("project_id")
            or t2_doc.get("group_id") != tr2_doc.get("group_id")):
        raise Tr2ValidationError("tr2_workflow_conflict", "source_t2_doc_id")


def verify_pending_pair(project_id: str, group_id: str, source_t2_doc_id: str) -> None:
    """Reject an inbox submission before it reserves a number or registers a slot."""
    result = db_wfseq.get_pending_head_by_group(group_id, project_id)
    items = db_wfseq.get_sequence_items(result["sequence_id"]) if result else []
    earlier = [item for item in items
               if (item.get("sort_order") or 0) < (result.get("sort_order") or 0)]
    paired = max(earlier, key=lambda item: item.get("sort_order") or 0) if earlier else None
    source = db_docs.get_by_id(source_t2_doc_id)
    if (result is None or result.get("type") != TR2_TYPE_CODE
            or paired is None or paired.get("type") != T2_TYPE_CODE
            or paired.get("result_doc_id") != source_t2_doc_id
            or source is None or source.get("type_code") != T2_TYPE_CODE
            or source.get("project_id") != project_id
            or source.get("group_id") != group_id):
        raise Tr2ValidationError("tr2_workflow_conflict", "source_t2_doc_id")


def derived_files(edit_spec: dict, source_root) -> list[dict]:
    files = []
    for path in target_set(edit_spec):
        kind = target_kind(edit_spec, path)
        target = _target_path(source_root, path, kind)
        exists = target.is_file()
        files.append({"path": path, "kind": kind, "exists": exists,
                      "is_regular_file": exists,
                      "size": target.stat().st_size if exists else None,
                      "current_sha256": hashlib.sha256(target.read_bytes()).hexdigest() if exists else None,
                      "edit_ids": [e["id"] for e in edit_spec["edits"]
                                   if normalized_target_path(e) == path]})
    return files


def read_file_projection(doc_id: str, requested_path: str) -> dict:
    """Read one declared target from the current source; never persist a preview."""
    doc = db_docs.get_by_id(doc_id)
    if not doc or doc.get("type_code") != TR2_TYPE_CODE:
        raise Tr2ValidationError("tr2_workflow_conflict", "doc_id")
    body = load_body(canonical_path_for_doc(doc))
    spec = body["edit_spec"]
    path = normalized_target_path({"file": requested_path})
    if path not in target_set(spec):
        raise Tr2ValidationError("tr2_path_unsafe", "file",
                                 {"reason": "path is not an edit target"})
    root = resolve_source_root(doc["project_id"], doc["group_id"])
    target = _target_path(root, path, target_kind(spec, path))
    exists = target.is_file()
    size = target.stat().st_size if exists else None
    if exists:
        with target.open("rb") as stream:
            prefix = stream.read(1024 * 1024 + 1)
        truncated = len(prefix) > 1024 * 1024
        sample = prefix[:1024 * 1024]
        try:
            text = sample.decode("utf-8")
        except UnicodeDecodeError:
            text = None
    else:
        truncated, text = False, None
    return {
        "path": path, "kind": target_kind(spec, path), "exists": exists,
        "size": size, "truncated": truncated, "before_text": text,
        "edits": [edit for edit in spec["edits"]
                  if normalized_target_path(edit) == path],
    }


def _history_state(doc: dict) -> str:
    # Time Machine ownership: T#3. This seam is replaced there.
    return "aligned"


def read_view(doc: dict, body: dict) -> dict:
    from modules.flow_gate.db import tr_commit_ledger
    from modules.flow_gate.documents import document_service
    source_root = resolve_source_root(doc["project_id"], doc["group_id"])
    spec = body["edit_spec"]
    live = target_fingerprint(spec, source_root)
    baseline = body["baseline_fingerprint"]
    if not _SOURCE_HEX.fullmatch(baseline):
        raise Tr2ValidationError("tr2_history_invariant_error", "baseline_fingerprint")
    ledger = [row for row in tr_commit_ledger.list_by_group(doc["group_id"])
              if row.get("doc_id", row.get("tr_doc_id")) == doc["doc_id"]]
    return {
        "document": {**doc, "editable": document_service.is_document_editable(
            doc, final_approved=document_service.is_final_approved(doc))},
        "body": body,
        "derived": {
            "files": derived_files(spec, source_root),
            "spec_fingerprint": spec_fingerprint(spec),
            "live_precheck": {"baseline_fingerprint": baseline, "live_fingerprint": live,
                              "drift": baseline != live,
                              "targets": [{"path": p, "kind": target_kind(spec, p),
                                           "exists": (source_root / p).is_file(),
                                           "is_regular_file": (source_root / p).is_file()}
                                          for p in target_set(spec)], "anchors": None}},
        "approval": {"latest_attempt": None, "attempts": []},
        "history": {"source_history_state": _history_state(doc), "ledger": ledger},
    }


def read(doc_id: str) -> dict:
    doc = db_docs.get_by_id(doc_id)
    if not doc or doc.get("type_code") != TR2_TYPE_CODE:
        raise Tr2ValidationError("tr2_workflow_conflict", "doc_id")
    return read_view(doc, load_body(canonical_path_for_doc(doc)))


def create(doc_id: str, raw_body: str | dict, *, actor: str) -> dict:
    """Create the canonical body after inbox has registered its workflow slot."""
    return save(doc_id, raw_body, actor=actor, expected_revision=0)


def _lock(doc_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(doc_id, threading.Lock())


def save(doc_id: str, raw_body: str | dict, *, actor: str, expected_revision: int) -> dict:
    # This is the only canonical writer for human PUT, AI inbox and direct API.
    doc = db_docs.get_by_id(doc_id)
    if not doc or doc.get("type_code") != TR2_TYPE_CODE:
        raise Tr2ValidationError("tr2_workflow_conflict", "doc_id")
    body = parse(raw_body) if isinstance(raw_body, str) else raw_body
    if not isinstance(body, dict):
        _invalid("body", "object required")
    dropped = [key for key in ("codebase_root", "source_honey") if key in body]
    validated = validate(body, doc=doc)
    verify_pair(doc_id, validated)
    canonical = canonicalize(validated)
    spec = canonical["edit_spec"]
    target_set(spec)
    root = resolve_source_root(doc["project_id"], doc["group_id"])
    canonical["baseline_fingerprint"] = target_fingerprint(spec, root)
    derived = {"files": derived_files(spec, root),
               "spec_fingerprint": spec_fingerprint(spec)}
    path = canonical_path_for_doc(doc)
    with _lock(doc_id):
        fresh = db_docs.get_by_id(doc_id)
        current = (fresh or {}).get("revision_no") or 0
        if fresh is None or expected_revision != current:
            raise Tr2ValidationError("tr2_spec_changed", "expected_revision",
                                     {"current_revision_no": current,
                                      "updated_at": (fresh or {}).get("updated_at")})
        now = now_iso()
        rel = storage_paths.to_storage_relative(path, doc["project_id"])
        store = get_store()
        # Keep the CAS and file replacement in one DB transaction. If the file
        # replacement or commit fails, restore its previous bytes before returning.
        previous = path.read_bytes() if path.is_file() else None
        write_attempted = False
        try:
            with store.transaction():
                store._execute("UPDATE documents SET revision_no = revision_no + 1, "
                               "updated_at = ?, file_path = ?, filename = ? "
                               "WHERE doc_id = ? AND revision_no = ?",
                               [now, rel, path.name, doc_id, current])
                refreshed = db_docs.get_by_id(doc_id)
                if refreshed is None or refreshed.get("revision_no") != current + 1:
                    raise Tr2ValidationError(
                        "tr2_spec_changed", "expected_revision",
                        {"current_revision_no": (refreshed or {}).get("revision_no")})
                write_attempted = True
                write_body_atomically(path, canonical)
        except Exception:
            if write_attempted:
                try:
                    if previous is None:
                        path.unlink(missing_ok=True)
                    else:
                        _write_bytes_atomically(path, previous)
                except OSError as restore_error:
                    raise Tr2ValidationError(
                        "tr2_history_invariant_error", "document.json",
                        {"reason": "save rollback failed"}) from restore_error
            raise
    try:
        from modules.flow_gate.api.v1.events.publisher import publish_event_threadsafe, FlowEvent
        from modules.flow_gate.api.v1.events.event_types import EventType
        publish_event_threadsafe(FlowEvent(
            event_type=EventType.DOCUMENT_EXPLORER_REFRESH,
            payload={"operation": "updated", "doc_id": doc_id, "type": TR2_TYPE_CODE,
                     "revision_no": current + 1},
            project=doc["project_id"], group_id=doc["group_id"], doc_id=doc_id,
            audience=actor))
    except Exception:
        pass  # Save is durable; refresh delivery is best-effort.
    return {"ok": True, "doc_id": doc_id, "new_revision": current + 1,
            "body": canonical,
            "derived": derived,
            "dropped_keys": dropped, "updated_at": now, "updated_by": actor,
            "doc_review_status": refreshed.get("doc_review_status")}
