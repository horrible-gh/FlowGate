"""Input checks in the normative P#1 / L#1 order (no DB reads)."""
import re
from .errors import GraphApiError, invalid, internal_error

KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", re.ASCII)
BODY_FIELDS = frozenset({"expected_doc_id", "expected_revision", "content", "idempotency_key"})


def parse_body(body):
    if not isinstance(body, dict):
        raise invalid("invalid_request", "body", "invalid_format")
    unknown = sorted(set(body) - BODY_FIELDS)
    if unknown:
        raise GraphApiError(422, "invalid_request", {"errors": [
            {"field": k, "reason": "unknown_field"} for k in unknown]})
    return body


def required_text(value, field, code, nonempty=True):
    if value is None:
        raise invalid(code, field, "required")
    if not isinstance(value, str):
        raise invalid(code, field, "invalid_type")
    if nonempty and not value.strip():
        raise invalid(code, field, "invalid_format")


def check_input_complete(req):
    for name in ("flow_id", "node_key", "slot", "expected_doc_id"):
        required_text(getattr(req, name), name, "card_binding_missing")
    if req.expected_revision is None:
        raise invalid("expected_revision_missing", "expected_revision", "required")
    if type(req.expected_revision) is not int:
        raise invalid("expected_revision_missing", "expected_revision", "invalid_type")
    if req.expected_revision < 0:
        raise invalid("expected_revision_missing", "expected_revision", "invalid_format")
    required_text(req.content, "content", "invalid_content", nonempty=False)
    if req.origin.kind in {"designer", "chat", "agent"}:
        required_text(req.idempotency_key, "idempotency_key", "invalid_request")
        if not KEY_RE.fullmatch(req.idempotency_key):
            raise invalid("invalid_request", "idempotency_key", "invalid_format")
    expected_kind = {"designer": "person", "chat": "person_delegated", "agent": "worker"}
    if req.actor.kind != expected_kind.get(req.origin.kind) or not req.actor.user_id:
        raise internal_error()
    if req.validate_only and req.origin.kind != "agent":
        raise internal_error()
    if req.origin.kind != "agent" and req.transport_extras:
        raise internal_error()
    if set(req.transport_extras.get("row_updates", {})) - {"rejection_history"}:
        raise internal_error()
