"""Canonical review payload identity and durable receipt lifecycle."""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any, Optional

from modules.flow_gate.db import review_receipts as db_receipts
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.services import token_service


def payload_identity(*, doc_id: str, revision_no: int, verdict: Any, findings: Any,
                     comment: Any, body_sha256: Any, body_chars: Any,
                     force_encoding_reason: Any) -> str:
    payload = {
        "action_scope": "review",
        "doc_id": doc_id,
        "revision_no": int(revision_no),
        "verdict": verdict,
        "findings": findings,
        "comment": comment,
        "body_sha256": body_sha256,
        "body_chars": body_chars,
        "force_encoding_reason": force_encoding_reason,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def encoding_provenance(body: dict) -> str:
    data = {
        "body_sha256_present": body.get("body_sha256") is not None,
        "body_chars_present": body.get("body_chars") is not None,
        "force_encoding_reason_present": body.get("force_encoding_reason") is not None,
    }
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def issue(*, token_rec: dict, project_id: str, group_id: Optional[str], doc_id: str,
          revision_no: int, identity: str, body: dict) -> dict:
    issued_at = now_iso()
    receipt_id = secrets.token_urlsafe(32)
    # A receipt can never outlive its bearer token.
    expires_at = str(token_rec.get("expires_at") or issued_at)
    with get_store().transaction():
        token_service.increment_dry_run(token_rec["token_id"])
        db_receipts.supersede_active(token_rec["token_id"], issued_at)
        db_receipts.create(
            receipt_id=receipt_id, token_id=token_rec["token_id"],
            project_id=project_id, group_id=group_id, doc_id=doc_id,
            revision_no=revision_no, payload_identity=identity,
            issued_at=issued_at, expires_at=expires_at,
            encoding_provenance=encoding_provenance(body),
        )
    return {"receipt": receipt_id, "payload_identity": identity, "expires_at": expires_at}


def classify(receipt_id: Any, *, token_rec: dict, project_id: str,
             group_id: Optional[str], doc_id: str, revision_no: int,
             identity: str) -> str:
    if not isinstance(receipt_id, str) or not receipt_id:
        return "receipt_missing"
    row = db_receipts.get(receipt_id)
    if row is None:
        return "receipt_invalid"
    if row.get("used_at") is not None:
        return "receipt_used"
    if row.get("superseded_at") is not None:
        return "receipt_superseded"
    if str(row.get("expires_at") or "") < now_iso():
        return "receipt_expired"
    if (
        row.get("token_id") == token_rec.get("token_id")
        and row.get("project_id") == project_id
        and row.get("group_id") == group_id
        and row.get("doc_id") == doc_id
        and row.get("action_scope") == "review"
        and int(row.get("revision_no") or 0) != int(revision_no)
    ):
        return "receipt_stale_revision"
    bindings = (
        row.get("token_id") == token_rec.get("token_id")
        and row.get("project_id") == project_id
        and row.get("group_id") == group_id
        and row.get("doc_id") == doc_id
        and int(row.get("revision_no") or 0) == int(revision_no)
        and row.get("action_scope") == "review"
    )
    if not bindings:
        return "receipt_binding_mismatch"
    if row.get("payload_identity") != identity:
        return "receipt_payload_mismatch"
    return "ok"
