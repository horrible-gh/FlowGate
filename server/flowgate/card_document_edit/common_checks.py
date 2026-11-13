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


# flowgate.v02gui.0009: origins whose text crossed a worker's own transport (a console,
# a shell, a hand-built curl) and can therefore have lost characters. Every other origin
# is a browser session (``designer``) or FlowGate itself (deploy, recovery, generated
# bodies ...), UTF-8 end to end in any language, and is never corruption-checked.
_WORKER_TRANSPORT_ORIGINS = frozenset({"agent", "chat"})


def transport_trusted(origin):
    """True when ``origin``'s text cannot have crossed a lossy (non-UTF-8) hop.

    An unknown origin is not trusted. A chat edit executed by FlowGate for an API run
    (``server_call_id`` set: the model's tool input reached the server as JSON) is.
    """
    kind = getattr(origin, "kind", None)
    if not kind:
        return False
    if kind == "chat":
        return bool(getattr(origin, "server_call_id", None))
    return kind not in _WORKER_TRANSPORT_ORIGINS


class ProbeSplit:
    """A submitted body with the worker's encoding probe line taken off (``encoding_probe``)."""

    def __init__(self, raw, text, status, received):
        self.raw, self.text, self.status, self.received = raw, text, status, received


def split_probe(content, origin):
    """Split the probe of ``origin``'s token off ``content`` (a no-op for trusted origins).

    Done before any rule prepares the text, so the stored body never carries the probe
    line; ``raw`` (the file as sent) stays available for the fingerprint.
    """
    from modules.flow_gate.services import encoding_probe
    if not isinstance(content, str) or transport_trusted(origin):
        return ProbeSplit(content, content, encoding_probe.PROBE_ABSENT, None)
    token_id = getattr(origin, "token_id", None)
    text, status, received = encoding_probe.split_document_probe(content, encoding_probe.probe_for(token_id))
    return ProbeSplit(content, text, status, received)


def encoding_guard(text, doc, extras=None, locale="ko", *, legacy=False, origin=None, probe=None):
    from modules.flow_gate.api import inbox_routes as inbox
    extras = extras or {}
    try:
        text.encode("utf-8")
    except UnicodeError as exc:
        raise invalid("invalid_content", "content", "invalid_format", doc,
                      validation={"reason": "encoding"}) from exc
    probe_kwargs = {}
    if probe is not None and probe.status != "absent":
        probe_kwargs = {"probe_status": probe.status, "probe_received": probe.received}
        if probe.status == "matched":
            probe_kwargs["fingerprint_texts"] = [probe.raw, probe.text, text]
    result = inbox._encoding_guard(fields={"body": text, **extras.get("fields", {})}, fingerprint_field="body",
        body_sha256=extras.get("body_sha256"), body_chars=extras.get("body_chars"),
        force_encoding_reason=extras.get("force_encoding_reason"), locale=locale,
        trusted_transport=origin is not None and transport_trusted(origin), **probe_kwargs)
    if result is not None:
        if legacy:
            from .legacy_edit import reject
            reject(result)
        payload = json.loads(result.body)
        raise invalid("invalid_content", "content", "invalid_format", doc, validation=payload)
