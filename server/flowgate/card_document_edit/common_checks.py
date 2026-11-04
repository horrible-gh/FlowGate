"""Reuse the existing identity and encoding validators without transport writes."""
import json
from modules.flow_gate import linter
from .errors import invalid


def identity_frontmatter(text, doc, rule, current_body):
    if doc["type_code"] in {"WP", "TR2"}:
        return
    from modules.flow_gate.api import inbox_routes as inbox
    result = inbox._frontmatter_submission_guard(
        text, expected_project=doc["project_id"], expected_module=doc.get("module") or "none",
        expected_group_id=doc.get("group_id") or "", expected_doc_type=doc["type_code"],
        expected_doc_id=doc["doc_id"], expected_target_id=doc.get("target_id") or doc.get("triggered_by"))
    if result is not None:
        reason = "document_identity_changed" if result.status_code == 409 else "invalid_format"
        raise invalid("invalid_content", "content", reason, doc)
    header, _ = linter.parse_yaml_header(text)
    previous, _ = linter.parse_yaml_header(current_body.text or "")
    header = header if isinstance(header, dict) else {}
    previous = previous if isinstance(previous, dict) else {}
    identity = {"project", "module", "group", "group_id", "type", "doc_number", "doc_id", "target_id"}
    if any(k in previous and previous[k] not in (None, "") and header.get(k) in (None, "") for k in identity):
        raise invalid("invalid_content", "content", "document_identity_changed", doc)
    if header.get("doc_id") not in (None, "", doc["doc_id"]):
        raise invalid("invalid_content", "content", "document_identity_changed", doc)
    if header.get("target_id") and not (doc.get("target_id") or doc.get("triggered_by")):
        raise invalid("invalid_content", "content", "document_identity_changed", doc)


def encoding_guard(text, doc, extras=None, locale="ko"):
    from modules.flow_gate.api import inbox_routes as inbox
    extras = extras or {}
    try:
        text.encode("utf-8")
    except UnicodeError as exc:
        raise invalid("invalid_content", "content", "invalid_format", doc,
                      validation={"reason": "encoding"}) from exc
    result = inbox._encoding_guard(fields={"body": text}, fingerprint_field="body",
        body_sha256=extras.get("body_sha256"), body_chars=extras.get("body_chars"),
        force_encoding_reason=extras.get("force_encoding_reason"), locale=locale)
    if result is not None:
        payload = json.loads(result.body)
        raise invalid("invalid_content", "content", "invalid_format", doc, validation=payload)
