"""Designer (user JWT) read / revise of a standalone document by doc_id (flowgate.v02.0157).

A document that no card line holds (``target.NON_CARD``) has no card coordinates, so the card
transport (``routes.BASE``) cannot reach it. These two functions give the Designer the same
doc_id contract the chat path already has, without any chat run, ticket or token:

* ``user_read_document``   – consistent body/revision read plus ``edit_path`` (``card`` /
  ``non_card`` / ``unavailable``). The client starts a standalone edit session only for
  ``non_card``; it never guesses the classification itself.
* ``user_revise_document`` – full-body save of an EXISTING certain non-card document. It keeps the
  doc_id and adds one revision through ``chat_documents._revise_non_card`` -> ``legacy_store``:
  in-boundary ``require_non_card`` re-check (a document bound to a card meanwhile, or a failed
  classification, is refused), ``NonCardRule`` (terminal / final-approved / not editable /
  unsupported type / Flow lock), stale-revision conflict and idempotent replay.
  A card-bound document is refused here: its edits keep the card contract (coordinates, line,
  connection-change detection) of ``routes.post_revision``.

The actor is the authenticated user (``Actor(user_id)``, kind ``person``) and the origin the
Designer (``Origin()``), the same pair as the card user routes; the revision event records it.
"""
from __future__ import annotations

from modules.flow_gate.db import documents
from . import contacts, guards, read, target
from .chat_adapter import IDENTITY_FREE_TYPES
from .chat_documents import (_edit_path, _expected_revision, _read_payload, _revise_non_card, _split_frontmatter,
                             with_current_frontmatter)
from .contracts import Actor, Origin
from .errors import GraphApiError, internal_error, invalid
from .validation import KEY_RE, required_text

REVISE_FIELDS = frozenset({"expected_revision", "content", "idempotency_key"})


def _document(doc_id):
    required_text(doc_id, "doc_id", "invalid_request")
    doc = documents.get_by_id(doc_id.strip())
    if doc is None:
        raise GraphApiError(404, "document_not_found", {"doc_id": doc_id})
    return doc


def _body(raw):
    if not isinstance(raw, dict):
        raise invalid("invalid_request", "body", "invalid_format")
    unknown = sorted(set(raw) - REVISE_FIELDS)
    if unknown:
        raise GraphApiError(422, "invalid_request", {"errors": [
            {"field": k, "reason": "unknown_field"} for k in unknown]})
    return raw


def _idempotency_key(value):
    # Required: without a key ``legacy_store`` skips the replay judgement and a resend would add a revision.
    required_text(value, "idempotency_key", "invalid_request")
    if not KEY_RE.fullmatch(value):
        raise invalid("invalid_request", "idempotency_key", "invalid_format")
    return value


def _kept_frontmatter(doc, content):
    """flowgate.v02gui.0003: a Designer save sent WITHOUT front matter (an uploaded plain ``.md``) keeps the document's
    current front matter, as the chat paths do (``chat_documents.with_current_frontmatter``). A body that brings its own
    front matter is passed as sent, so ``common_checks.identity_frontmatter`` still refuses another document's file;
    an empty body, an identity-free type (WP / TR2) and an unreadable current body are left to the existing checks."""
    if not isinstance(content, str) or not content.strip() or _split_frontmatter(content)[0] is not None:
        return content
    if doc.get("type_code") in IDENTITY_FREE_TYPES:
        return content
    _current, body = read._snapshot_parts(doc["doc_id"])
    if body.state != "readable":
        return content
    return with_current_frontmatter(content, body.text)


def keep_card_frontmatter(req):
    """The card route's request with ``_kept_frontmatter`` applied. Only the request's own Flow document is read;
    ``revise`` still checks that it is the line's document, its revision, the permission and the identity."""
    from dataclasses import replace
    doc_id = req.expected_doc_id
    doc = documents.get_by_id(doc_id) if isinstance(doc_id, str) and doc_id else None
    if not doc or doc.get("group_id") != req.flow_id:
        return req
    content = _kept_frontmatter(doc, req.content)
    return req if content is req.content else replace(req, content=content)


def user_read_document(actor: Actor, doc_id: str) -> dict:
    doc = _document(doc_id)
    guards.authorize(actor, doc["project_id"], "perm_document_read", flow_id=doc.get("group_id"))
    fresh, body = read._snapshot_parts(doc["doc_id"])
    return _read_payload(fresh, body, target.classify_card_slot(fresh))


def user_revise_document(actor: Actor, doc_id: str, raw_body, locale: str = "ko") -> dict:
    """``raw_body`` is the decoded JSON object (authentication already happened in the route)."""
    body = _body(raw_body)
    expected = _expected_revision(body.get("expected_revision"))
    content = body.get("content")
    required_text(content, "content", "invalid_content", nonempty=False)
    key = _idempotency_key(body.get("idempotency_key"))
    doc = _document(doc_id)
    guards.authorize(actor, doc["project_id"], "perm_document_update", flow_id=doc.get("group_id"))
    classification = target.classify_card_slot(doc)
    if classification == target.CARD:
        raise invalid("card_binding_missing", "card_binding", "invalid_format", doc,
                      validation={"reason": "card_document"})
    if classification != target.NON_CARD:
        raise invalid("card_binding_missing", "card_binding", "invalid_format", doc,
                      validation={"reason": "card_edit_unavailable"})
    content = _kept_frontmatter(doc, content)
    kind, result, plan, _line = _revise_non_card(doc, expected, content, actor, Origin(), key, None, None, locale,
                                                 edit_reason=None)
    if kind == "SAVED":
        contacts.run(plan)
    elif kind == "REPLAYED":
        contacts.run_replay(plan)
    else:
        raise internal_error()
    return {**result, "edit_path": _edit_path(target.NON_CARD)}
