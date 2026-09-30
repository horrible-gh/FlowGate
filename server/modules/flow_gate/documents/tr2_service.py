"""Canonical TR2 document domain: one validator, writer and fingerprint implementation."""
from __future__ import annotations

import hashlib
import json
import logging
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
log = logging.getLogger(__name__)

# 0565 T0030 §6: why the stored proposal cannot be read. These describe the TR2 document
# itself and are never approval-attempt states (rollback/recovery_required stay separate).
BODY_ERROR_CODES = frozenset({"tr2_body_missing", "tr2_body_corrupt",
                              "tr2_body_schema_invalid", "tr2_storage_mismatch"})
REVISIONS_DIRNAME = "revisions"
# document_revisions.edit_reason is a closed set; a TR2 snapshot records who saved it.
ORIGIN_HUMAN = "user_comment"
ORIGIN_AI = "worker_self"


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
    if not parts or any(part in ("..", ".git") or ":" in part for part in parts):
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
        if any(char in command for char in "\x00\r\n"):
            from modules.flow_gate.db import project_test_commands as registry
            row = registry.find_by_command(doc["project_id"], normalized)
            if row is None or normalize_command(row["command"]) != normalized:
                _invalid(loc, "candidate_control_character")
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


def canonical_relative_path(doc: dict) -> str:
    return storage_paths.to_storage_relative(canonical_path_for_doc(doc), doc["project_id"])


def _same_storage_path(stored, expected: str) -> bool:
    return str(stored or "").replace("\\", "/").strip() == expected.replace("\\", "/")


def _registered_at_canonical(doc: dict, expected: str) -> bool:
    stored = doc.get("file_path")
    if _same_storage_path(stored, expected):
        return True
    # Older rows may hold an absolute or /storage/-prefixed spelling of the same file.
    resolved = storage_paths.resolve_storage_path(stored or "", doc["project_id"]) if stored else None
    return resolved is not None and resolved.resolve() == canonical_path_for_doc(doc).resolve()


def _body_error(code: str, doc: dict, loc: str, reason: str, *, cause=None) -> Tr2ValidationError:
    # The operator log keeps the diagnosis; the error sent to screens carries only a code,
    # a location inside the proposal and a path-free reason (T0030 §5).
    log.warning("TR2 body unreadable doc=%s code=%s loc=%s cause=%r",
                doc.get("doc_id"), code, loc, cause)
    return Tr2ValidationError(code, loc, {"reason": reason})


def load_current(doc: dict) -> dict:
    """The one reader of the current canonical proposal.

    The contract (T0030 §4): ``documents.file_path`` names
    ``<code>_document.json`` under the document's storage directory, and that file holds
    the canonical JSON of the current revision. Anything else is reported as what it is
    (mismatch, missing, damaged, schema-invalid) instead of being guessed around.
    """
    expected = canonical_relative_path(doc)
    if not _registered_at_canonical(doc, expected):
        raise _body_error("tr2_storage_mismatch", doc, "file_path",
                          "document is registered with a non-canonical file",
                          cause=doc.get("file_path"))
    path = canonical_path_for_doc(doc)
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise _body_error("tr2_body_missing", doc, "document.json",
                          "proposal file does not exist", cause=exc) from exc
    except OSError as exc:
        raise _body_error("tr2_body_corrupt", doc, "document.json",
                          "proposal file cannot be read", cause=exc) from exc
    return _decode_canonical(raw, doc, loc="document.json")


def _decode_canonical(raw: bytes, doc: dict, *, loc: str) -> dict:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _body_error("tr2_body_corrupt", doc, loc, "not UTF-8 text", cause=exc) from exc
    try:
        body = json.loads(text)
    except ValueError as exc:
        raise _body_error("tr2_body_corrupt", doc, loc,
                          f"invalid JSON at line {getattr(exc, 'lineno', '?')}",
                          cause=exc) from exc
    if not isinstance(body, dict):
        raise _body_error("tr2_body_schema_invalid", doc, loc, "object required")
    try:
        validate(body, doc=doc)
    except Tr2ValidationError as exc:
        raise Tr2ValidationError("tr2_body_schema_invalid", exc.loc,
                                 {"reason": exc.details.get("reason") or exc.code}) from exc
    baseline = body.get("baseline_fingerprint")
    if not isinstance(baseline, str) or not _SOURCE_HEX.fullmatch(baseline):
        raise Tr2ValidationError("tr2_body_schema_invalid", "baseline_fingerprint",
                                 {"reason": "server baseline fingerprint missing"})
    return body


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


def effective_head_for(doc_id: str) -> dict | None:
    """The effective head of the sequence that owns ``doc_id``'s slot.

    ``get_pending_head_by_group`` only returns slots that are still unregistered, so it
    never names a registered TR2; approval admission and progression need the D030
    effective head (a registered, not yet approved slot first).
    """
    item = db_wfseq.get_item_by_result_doc_id(doc_id)
    if item is None:
        return None
    return db_wfseq.get_effective_head(item["sequence_id"])


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


def unavailable_files(edit_spec: dict) -> list[dict]:
    """``derived_files`` when the source cannot be read: the declared targets, facts unknown."""
    return [{"path": path, "kind": target_kind(edit_spec, path), "exists": None,
             "is_regular_file": None, "size": None, "current_sha256": None,
             "edit_ids": [e["id"] for e in edit_spec["edits"]
                          if normalized_target_path(e) == path]}
            for path in target_set(edit_spec)]


def read_file_projection(doc_id: str, requested_path: str) -> dict:
    """Read one declared target from the current source; never persist a preview."""
    doc = db_docs.get_by_id(doc_id)
    if not doc or doc.get("type_code") != TR2_TYPE_CODE:
        raise Tr2ValidationError("tr2_workflow_conflict", "doc_id")
    body = load_current(doc)
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
    from modules.flow_gate.documents.tr2_history import source_history_state
    return source_history_state(doc["doc_id"])


def read_view(doc: dict, body: dict) -> dict:
    from modules.flow_gate.db import tr_commit_ledger
    from modules.flow_gate.db import tr2_approval_attempts
    from modules.flow_gate.documents import document_service
    from modules.flow_gate.documents.tr2_errors import TR2_ERRORS
    from modules.flow_gate.documents.tr2_approval_service import retry_state
    spec = body["edit_spec"]
    baseline = body["baseline_fingerprint"]
    if not _SOURCE_HEX.fullmatch(baseline):
        raise Tr2ValidationError("tr2_history_invariant_error", "baseline_fingerprint")
    # The stored proposal loads without the source (T0030 §7): an unregistered, missing or
    # broken group worktree leaves the source-derived facts unknown, and readiness below
    # carries the authoritative tr2_git_unavailable instead of failing the whole read.
    try:
        source_root = resolve_source_root(doc["project_id"], doc["group_id"])
    except Tr2ValidationError as exc:
        if exc.code != "tr2_git_unavailable":
            raise
        source_root = None
    if source_root is not None and not Path(source_root).is_dir():
        source_root = None
    live = target_fingerprint(spec, source_root) if source_root is not None else None
    ledger = [row for row in tr_commit_ledger.list_by_group(doc["group_id"])
              if row.get("doc_id", row.get("tr_doc_id")) == doc["doc_id"]]
    anchors = None
    if (source_root is not None and spec.get("termination") == "ready_to_apply"
            and spec.get("edits")):
        from modules.flow_gate.documents.tr2_apply_adapter import adapter
        anchors = adapter.evaluate(spec, source_root, baseline=baseline)["edits"]
    editable = document_service.is_document_editable(
        doc, final_approved=document_service.is_final_approved(doc))
    block = mutation_block(doc)
    attempts = [{**public_attempt(row),
                 "retryable": (TR2_ERRORS[row["error_code"]].retryable
                               if row.get("error_code") in TR2_ERRORS else None)}
                for row in tr2_approval_attempts.list_by_doc(doc["doc_id"])]
    from modules.flow_gate.documents.tr2_precheck import readiness
    from modules.flow_gate.documents.tr2_command_admission import (
        classify_gate_commands, public_admission,
    )
    gate_admission = public_admission(classify_gate_commands(
        doc["project_id"], spec.get("gate", {}).get("commands", [])))
    return {
        "document": {**doc, "editable": editable},
        "readiness": readiness(doc, body),
        "gate_admission": gate_admission,
        "mutation": {"allowed": editable and block is None,
                     "reason": block if block is not None else (None if editable else "not_editable")},
        "body": body,
        "derived": {
            "files": (derived_files(spec, source_root) if source_root is not None
                      else unavailable_files(spec)),
            "spec_fingerprint": spec_fingerprint(spec),
            "live_precheck": {"source_available": source_root is not None,
                              "baseline_fingerprint": baseline, "live_fingerprint": live,
                              "drift": baseline != live if source_root is not None else None,
                              "targets": [{"path": p, "kind": target_kind(spec, p),
                                           "exists": ((source_root / p).is_file()
                                                      if source_root is not None else None),
                                           "is_regular_file": ((source_root / p).is_file()
                                                               if source_root is not None
                                                               else None)}
                                          for p in target_set(spec)], "anchors": anchors}},
        "approval": {"latest_attempt": attempts[0] if attempts else None,
                     "attempts": attempts,
                     "retry": retry_state(doc)},
        "history": {"source_history_state": _history_state(doc), "ledger": ledger},
    }


def public_attempt(row: dict) -> dict:
    """An approval attempt as clients see it: ``error_detail`` is exception text for the log."""
    return {key: value for key, value in row.items() if key != "error_detail"}


def read(doc_id: str) -> dict:
    doc = db_docs.get_by_id(doc_id)
    if not doc or doc.get("type_code") != TR2_TYPE_CODE:
        raise Tr2ValidationError("tr2_workflow_conflict", "doc_id")
    body = load_current(doc)
    _backfill_current_snapshot(doc)
    return read_view(doc, body)


def create(doc_id: str, raw_body: str | dict, *, actor: str,
           origin: str = ORIGIN_HUMAN) -> dict:
    """Create the canonical body after the workflow slot has been registered.

    Every creation path (AI inbox, the human "create next document" route) ends here,
    so revision 1 is written, snapshotted and registered by the same writer.
    """
    return save(doc_id, raw_body, actor=actor, expected_revision=0, origin=origin,
                operation="create")


def _lock(doc_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(doc_id, threading.Lock())


def mutation_block(doc: dict) -> str | None:
    """Why the proposal is immutable right now, or None. Approval history owns these states."""
    from modules.flow_gate.db import tr2_approval_attempts
    if doc.get("doc_review_status") == "approved":
        return "approved"
    if tr2_approval_attempts.in_progress(doc["doc_id"]):
        return "applying"
    if tr2_approval_attempts.recovery_required(doc["doc_id"]):
        return "recovery_required"
    return None


def _assert_mutable(doc: dict) -> None:
    reason = mutation_block(doc)
    if reason == "applying":
        raise Tr2ValidationError("tr2_in_progress", "document", {"reason": reason})
    if reason is not None:
        raise Tr2ValidationError("tr2_spec_immutable", "document", {"reason": reason})


def save(doc_id: str, raw_body: str | dict, *, actor: str, expected_revision: int,
         origin: str = ORIGIN_HUMAN, operation: str = "save") -> dict:
    # This is the only canonical writer for human PUT, AI inbox and direct API. A whole
    # replacement does not read the stored body, so it also replaces an unreadable one.
    return _save(doc_id, lambda _current: raw_body, actor=actor,
                 expected_revision=expected_revision, origin=origin,
                 operation=operation, needs_current=False)


def revisions_dir(doc: dict) -> Path:
    return canonical_path_for_doc(doc).parent / REVISIONS_DIRNAME


def snapshot_path(doc: dict, revision_no: int) -> Path:
    return revisions_dir(doc) / f"{doc['doc_id']}.r{int(revision_no)}.json"


def _cas_bump(store, doc_id: str, current: int, now: str, rel: str, filename: str) -> None:
    """DB-level revision CAS (T0030 §8): the UPDATE's own affected-row count decides.

    Re-reading the row afterwards cannot tell our bump from a concurrent writer's
    ``N+1``, so the count reported by the driver is the only authority.
    """
    affected = store._execute_affected(
        "UPDATE documents SET revision_no = revision_no + 1, "
        "updated_at = ?, file_path = ?, filename = ? "
        "WHERE doc_id = ? AND revision_no = ?",
        [now, rel, filename, doc_id, current])
    if affected == 1:
        return
    if affected == 0:
        fresh = db_docs.get_by_id(doc_id)
        raise Tr2ValidationError("tr2_spec_changed", "expected_revision",
                                 {"current_revision_no": (fresh or {}).get("revision_no"),
                                  "updated_at": (fresh or {}).get("updated_at")})
    raise Tr2ValidationError("tr2_history_invariant_error", "revision_no",
                             {"reason": f"revision CAS matched {affected} rows"})


def _record_snapshot(store, doc: dict, revision_no: int, content: bytes, *,
                     actor: str, origin: str, now: str) -> Path:
    """Keep the exact canonical bytes of ``revision_no`` as its recovery source.

    Reuses the work-plan revision layout (``revisions/{doc_id}.r{n}{suffix}`` next to the
    document, one ``document_revisions`` row per file). The row runs in the caller's
    transaction, so a revision is never committed without its snapshot.
    """
    target = snapshot_path(doc, revision_no)
    _write_bytes_atomically(target, content)
    store._execute(
        "INSERT INTO document_revisions "
        "(doc_id, revision_no, backup_path, edit_reason, linked_doc_id, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [doc["doc_id"], int(revision_no),
         storage_paths.to_storage_relative(target, doc["project_id"]),
         origin if origin in (ORIGIN_HUMAN, ORIGIN_AI) else ORIGIN_HUMAN,
         None, actor, now])
    return target


def _save(doc_id: str, build, *, actor: str, expected_revision: int,
          origin: str = ORIGIN_HUMAN, operation: str = "save",
          needs_current: bool = True) -> dict:
    """Validate and persist the body ``build`` returns under the per-document lock.

    ``build`` receives the stored canonical body (None before creation, or when
    ``needs_current`` is False) read after the revision check, so a partial mutation is
    merged into exactly the revision it expected. The authoritative CAS is the
    conditional UPDATE inside the transaction; the process lock only serialises writers
    of this process.
    """
    doc = db_docs.get_by_id(doc_id)
    if not doc or doc.get("type_code") != TR2_TYPE_CODE:
        raise Tr2ValidationError("tr2_workflow_conflict", "doc_id")
    path = canonical_path_for_doc(doc)
    with _lock(doc_id):
        fresh = db_docs.get_by_id(doc_id)
        current = (fresh or {}).get("revision_no") or 0
        if fresh is None or expected_revision != current:
            raise Tr2ValidationError("tr2_spec_changed", "expected_revision",
                                     {"current_revision_no": current,
                                      "updated_at": (fresh or {}).get("updated_at")})
        _assert_mutable(fresh)
        raw_body = build(load_current(fresh) if needs_current and current else None)
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
        content = dumps(canonical).encode("utf-8")
        now = now_iso()
        rel = storage_paths.to_storage_relative(path, doc["project_id"])
        store = get_store()
        # Keep the CAS, the file replacement and the revision snapshot in one DB
        # transaction. If any of them fails, restore the previous bytes before returning.
        previous = path.read_bytes() if path.is_file() else None
        write_attempted = False
        snapshot = None
        try:
            with store.transaction():
                _cas_bump(store, doc_id, current, now, rel, path.name)
                refreshed = db_docs.get_by_id(doc_id)
                write_attempted = True
                write_body_atomically(path, canonical)
                snapshot = snapshot_path(doc, current + 1)
                _record_snapshot(store, doc, current + 1, content, actor=actor,
                                 origin=origin, now=now)
        except Exception:
            if write_attempted:
                try:
                    if snapshot is not None:
                        snapshot.unlink(missing_ok=True)
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
        # Every open screen of the project re-reads the new revision, not only the
        # actor's: an AI inbox save must reach the reviewer looking at this TR2.
        from modules.flow_gate.api.v1.events.publisher import broadcast_event_threadsafe, FlowEvent
        from modules.flow_gate.api.v1.events.event_types import EventType
        broadcast_event_threadsafe(FlowEvent(
            event_type=EventType.DOCUMENT_EXPLORER_REFRESH,
            payload={"operation": "updated", "doc_id": doc_id, "type": TR2_TYPE_CODE,
                     "revision_no": current + 1},
            project=doc["project_id"], group_id=doc["group_id"], doc_id=doc_id,
            audience="*"))
    except Exception:
        pass  # Save is durable; refresh delivery is best-effort.
    return {"ok": True, "doc_id": doc_id, "new_revision": current + 1,
            "operation": operation,
            "body": canonical,
            "derived": derived,
            "dropped_keys": dropped, "updated_at": now, "updated_by": actor,
            "doc_review_status": refreshed.get("doc_review_status")}


ITEM_COLLECTIONS = ("edits", "deferred")


def empty_edit_spec() -> dict:
    """The smallest valid proposal: nothing to apply yet."""
    return {"termination": "needs_more_work", "edits": [], "deferred": [],
            "gate": {"commands": [], "apply": False}}


def find_item(edit_spec: dict, item_id: str) -> tuple[str, int, dict] | None:
    for collection in ITEM_COLLECTIONS:
        for index, item in enumerate(edit_spec.get(collection) or []):
            if isinstance(item, dict) and item.get("id") == item_id:
                return collection, index, item
    return None


def read_item(doc_id: str, item_id: str) -> dict:
    doc = db_docs.get_by_id(doc_id)
    if not doc or doc.get("type_code") != TR2_TYPE_CODE:
        raise Tr2ValidationError("tr2_workflow_conflict", "doc_id")
    found = find_item(load_current(doc)["edit_spec"], item_id)
    if found is None:
        raise Tr2ValidationError("tr2_item_not_found", "item_id", {"item_id": item_id})
    collection, index, item = found
    return {"doc_id": doc_id, "revision_no": doc.get("revision_no") or 0,
            "collection": collection, "index": index, "item": item}


def _collection(value, loc: str) -> str:
    if value not in ITEM_COLLECTIONS:
        _invalid(loc, f"collection must be one of {list(ITEM_COLLECTIONS)}")
    return value


def mutate(doc_id: str, operation: str, *, actor: str, expected_revision: int,
           item_id: str | None = None, collection: str | None = None,
           item: dict | None = None, origin: str = ORIGIN_HUMAN) -> dict:
    """Whole/individual edit-spec CRUD, merged on the server and saved by ``_save``.

    Clients never assemble the stored document: they send one operation, the merge
    happens against the revision the CAS admitted, and the result passes the same
    validator, pair check, baseline fingerprint and revision bump as a full save.
    """
    if operation not in {"reset_spec", "add_item", "replace_item", "delete_item"}:
        _invalid("operation", "unknown operation")
    if operation in {"add_item", "replace_item"} and not isinstance(item, dict):
        _invalid("item", "object required")
    if operation in {"replace_item", "delete_item"} and (
            not isinstance(item_id, str) or not item_id):
        _invalid("item_id", "non-empty string required")
    pruned: list[str] = []

    def build(current: dict | None) -> dict:
        if current is None:
            raise Tr2ValidationError("tr2_workflow_conflict", "document",
                                     {"reason": "TR2 body has not been created"})
        spec = json.loads(_json(current["edit_spec"]))
        if operation == "reset_spec":
            spec = empty_edit_spec()
        elif operation == "add_item":
            spec.setdefault(_collection(collection, "collection"), []).append(dict(item))
        else:
            found = find_item(spec, item_id)
            if found is None:
                raise Tr2ValidationError("tr2_item_not_found", "item_id", {"item_id": item_id})
            source, index, _old = found
            del spec[source][index]
            if operation == "replace_item":
                target = _collection(collection or source, "collection")
                if target == source:
                    spec[target].insert(index, dict(item))
                else:
                    spec[target].append(dict(item))
            live_edits = {edit.get("id") for edit in spec.get("edits") or []
                          if isinstance(edit, dict)}
            if item_id not in live_edits:
                # A verify block may only name live edits; drop the reference with the edit.
                verify = spec.get("verify")
                if isinstance(verify, dict) and isinstance(verify.get("test_edit_ids"), list):
                    kept = [ident for ident in verify["test_edit_ids"] if ident != item_id]
                    if len(kept) != len(verify["test_edit_ids"]):
                        pruned.append(item_id)
                        verify["test_edit_ids"] = kept
        return {"tr2_version": current["tr2_version"],
                "source_t2_doc_id": current["source_t2_doc_id"], "edit_spec": spec}

    result = _save(doc_id, build, actor=actor, expected_revision=expected_revision,
                   origin=origin, operation=operation)
    result["pruned_verify_edit_ids"] = pruned
    return result


# ── Proposal revision recovery (0565 T0030 §6) ─────────────────────────────────────
# "반영안 복구" is about the TR2 document itself: its current canonical file went missing,
# got damaged or no longer validates. It never touches an approval attempt, a rollback or
# recovery_required — those describe applying a proposal to the source and live in
# tr2_approval_service. A restore is a new revision produced by the canonical writer.


def paired_t2_doc_id(doc_id: str) -> str:
    """The T2 whose slot directly precedes this TR2 in its workflow sequence."""
    paired = db_wfseq.get_paired_instruction_item(doc_id, T2_TYPE_CODE)
    source = (paired or {}).get("result_doc_id")
    if not source:
        raise Tr2ValidationError("tr2_workflow_conflict", "source_t2_doc_id")
    return source


def initial_body(doc_id: str) -> dict:
    """A deliberately empty proposal. ``needs_more_work`` can never be approved."""
    return {"tr2_version": TR2_BODY_VERSION, "source_t2_doc_id": paired_t2_doc_id(doc_id),
            "edit_spec": empty_edit_spec()}


def _snapshot_rows(doc: dict) -> list[dict]:
    from modules.flow_gate.db import document_revisions as db_revisions
    rows = []
    for row in db_revisions.list_by_doc(doc["doc_id"]):
        backup = str(row.get("backup_path") or "").replace("\\", "/")
        if backup.endswith(f"/{REVISIONS_DIRNAME}/{doc['doc_id']}.r{row.get('revision_no')}.json"):
            rows.append(row)
    return rows


def _snapshot_row(doc: dict, revision_no: int) -> dict | None:
    return next((row for row in _snapshot_rows(doc)
                 if int(row.get("revision_no") or -1) == int(revision_no)), None)


def _inspect_snapshot(doc: dict, row: dict) -> tuple[dict, bytes | None, dict | None]:
    """Describe one snapshot without exposing where it is stored."""
    revision_no = int(row["revision_no"])
    info = {"revision_no": revision_no, "created_at": row.get("created_at"),
            "created_by": row.get("created_by"),
            "origin": "ai" if row.get("edit_reason") == ORIGIN_AI else "human",
            "size": None, "sha256": None, "usable": False, "problem": None}
    resolved = storage_paths.resolve_storage_path(row["backup_path"], doc["project_id"])
    if resolved is None or not resolved.is_file():
        info["problem"] = "tr2_body_missing"
        return info, None, None
    try:
        raw = resolved.read_bytes()
    except OSError:
        info["problem"] = "tr2_body_corrupt"
        return info, None, None
    info["size"] = len(raw)
    info["sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        body = _decode_canonical(raw, doc, loc=f"revisions.r{revision_no}")
    except Tr2ValidationError as exc:
        info["problem"] = exc.code
        return info, raw, None
    if dumps(body).encode("utf-8") != raw:
        # A snapshot is written once from the canonical serialisation; bytes that do not
        # round-trip are not the revision that was saved.
        info["problem"] = "tr2_body_corrupt"
        return info, raw, None
    info["usable"] = True
    return info, raw, body


def _backfill_current_snapshot(doc: dict) -> None:
    """Revisions saved before snapshots existed get one when their body is read intact.

    Only the exact bytes of the current revision are copied, under the document lock and
    only while the revision is unchanged — nothing is regenerated from current sources.
    """
    revision = int(doc.get("revision_no") or 0)
    if revision <= 0 or _snapshot_row(doc, revision) is not None:
        return
    try:
        with _lock(doc["doc_id"]):
            fresh = db_docs.get_by_id(doc["doc_id"])
            if not fresh or int(fresh.get("revision_no") or 0) != revision:
                return
            if _snapshot_row(fresh, revision) is not None:
                return
            raw = canonical_path_for_doc(fresh).read_bytes()
            _decode_canonical(raw, fresh, loc="document.json")
            store = get_store()
            target = snapshot_path(fresh, revision)
            try:
                with store.transaction():
                    _record_snapshot(store, fresh, revision, raw, actor=fresh["owner_id"],
                                     origin=ORIGIN_HUMAN, now=now_iso())
            except Exception:
                target.unlink(missing_ok=True)
                raise
    except Exception:
        log.warning("TR2 snapshot backfill skipped for %s", doc.get("doc_id"), exc_info=True)


def _raw_current(doc: dict) -> tuple[bytes | None, str | None]:
    """Whatever bytes are still stored for the current body, canonical file first."""
    candidates = [canonical_path_for_doc(doc)]
    registered = storage_paths.resolve_storage_path(doc.get("file_path") or "", doc["project_id"]) \
        if doc.get("file_path") else None
    if registered is not None and registered not in candidates:
        candidates.append(registered)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.read_bytes(), candidate.name
        except OSError:
            continue
    return None, None


def _doc_or_conflict(doc_id: str) -> dict:
    doc = db_docs.get_by_id(doc_id)
    if not doc or doc.get("type_code") != TR2_TYPE_CODE:
        raise Tr2ValidationError("tr2_workflow_conflict", "doc_id")
    return doc


def recovery_view(doc_id: str) -> dict:
    """State of the stored proposal and every revision it can be recovered from."""
    from modules.flow_gate.documents import document_service
    doc = _doc_or_conflict(doc_id)
    error = None
    try:
        load_current(doc)
    except Tr2ValidationError as exc:
        if exc.code not in BODY_ERROR_CODES:
            raise
        error = {"code": exc.code, "loc": exc.loc, "reason": exc.details.get("reason")}
    revisions = [_inspect_snapshot(doc, row)[0] for row in _snapshot_rows(doc)]
    current = int(doc.get("revision_no") or 0)
    for item in revisions:
        item["is_current"] = item["revision_no"] == current
    raw, raw_name = _raw_current(doc)
    editable = document_service.is_document_editable(
        doc, final_approved=document_service.is_final_approved(doc))
    block = mutation_block(doc)
    usable = [item for item in revisions if item["usable"]]
    return {
        "doc_id": doc_id, "revision_no": current,
        "state": "ok" if error is None else error["code"],
        "error": error,
        "approval_blocked": error is not None,
        "revisions": revisions,
        "recommended_revision_no": usable[0]["revision_no"] if usable else None,
        "recoverable": bool(usable),
        "raw": {"available": raw is not None, "size": len(raw) if raw is not None else None,
                "filename": raw_name},
        "mutation": {"allowed": editable and block is None,
                     "reason": block if block is not None else (None if editable else "not_editable")},
    }


def read_revision(doc_id: str, revision_no: int) -> dict:
    doc = _doc_or_conflict(doc_id)
    row = _snapshot_row(doc, revision_no)
    if row is None:
        raise Tr2ValidationError("tr2_revision_not_found", "revision_no",
                                 {"revision_no": int(revision_no)})
    info, raw, body = _inspect_snapshot(doc, row)
    return {**info, "is_current": int(revision_no) == int(doc.get("revision_no") or 0),
            "content": raw.decode("utf-8", errors="replace") if raw is not None else None,
            "body": body}


def read_raw(doc_id: str) -> dict:
    doc = _doc_or_conflict(doc_id)
    raw, name = _raw_current(doc)
    if raw is None:
        raise Tr2ValidationError("tr2_body_missing", "document.json",
                                 {"reason": "no stored content remains"})
    return {"doc_id": doc_id, "revision_no": int(doc.get("revision_no") or 0),
            "filename": name, "size": len(raw),
            "content": raw.decode("utf-8", errors="replace")}


def restore_revision(doc_id: str, revision_no: int, *, actor: str,
                     expected_revision: int) -> dict:
    """Make the exact saved body of ``revision_no`` the next revision.

    History is never rewritten: the chosen snapshot is re-validated, its baseline is
    recomputed against today's source by the canonical writer, and it lands as N+1.
    """
    doc = _doc_or_conflict(doc_id)
    row = _snapshot_row(doc, revision_no)
    if row is None:
        raise Tr2ValidationError("tr2_revision_not_found", "revision_no",
                                 {"revision_no": int(revision_no)})
    info, _raw, body = _inspect_snapshot(doc, row)
    if body is None:
        raise Tr2ValidationError("tr2_revision_unusable", "revision_no",
                                 {"revision_no": int(revision_no), "problem": info["problem"]})
    result = _save(doc_id, lambda _current: body, actor=actor,
                   expected_revision=expected_revision, operation="restore",
                   needs_current=False)
    result["restored_from_revision"] = int(revision_no)
    return result


def start_new(doc_id: str, *, actor: str, expected_revision: int) -> dict:
    """Explicitly begin again from an empty proposal (never approvable as is)."""
    _doc_or_conflict(doc_id)
    return _save(doc_id, lambda _current: initial_body(doc_id), actor=actor,
                 expected_revision=expected_revision, operation="new_proposal",
                 needs_current=False)


def notify_group_history_changed(group_id: str | None) -> None:
    """Tell open 반영안 screens of a group that approval/source history moved (T0030 §11).

    A Time Machine rewind or forward restore re-opens or re-approves documents and cancels
    or reapplies their commits outside the TR2 routes, so nothing else would reach a
    screen that is only watching. Reuses the doc-scoped refresh the approval journal sends.
    Best-effort: the durable state is already committed.
    """
    if not group_id:
        return
    try:
        rows = [row for row in db_docs.get_documents_by_group_id(group_id)
                if row.get("type_code") == TR2_TYPE_CODE]
    except Exception:
        log.warning("TR2 history refresh lookup failed for %s", group_id, exc_info=True)
        return

    def publish():
        try:
            from modules.flow_gate.api.v1.events.event_types import EventType
            from modules.flow_gate.api.v1.events.publisher import (
                FlowEvent, broadcast_event_threadsafe,
            )
            for row in rows:
                broadcast_event_threadsafe(FlowEvent(
                    event_type=EventType.GROUP_VIEW_REFRESH,
                    payload={"group_id": group_id, "reason": "tr2_history_changed",
                             "doc_id": row["doc_id"]},
                    audience="*", project=row["project_id"], group_id=group_id,
                    doc_id=row["doc_id"]))
        except Exception:
            pass

    from modules.flow_gate.db.connection import after_commit
    if not after_commit(publish):
        publish()
