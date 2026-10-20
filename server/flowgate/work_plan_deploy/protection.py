"""Which card documents the deploy may write or remove (flowgate.v02.0074 T#4, D0006 §3.5).

A document is the card's **untouched empty draft** only when all of these hold; anything else protects it:

1. owned: the card's own line, holding the document it shows (the end of its supersede chain)
2. state: a draft with no review state and not superseded (``slots.is_disposable_draft``)
3. body: no body after the front matter (the v0.1 approval's rule, ``card_lines.has_body``)
4. attachments: none
5. history: no AI review, no rejection, no approval / rejection / submission / review event, revision 0 — a past
   history protects even when the current review state is empty
6. body save evidence: none
7. the card never started: no node run past pending, no attempt, not open / in review / running now
8. every fact above was read: a read that fails protects (``protection_unknown``); the display counts that hide
   their failures are not used here

``allow_deploy_body`` (the cleanup's judgement only): a line whose every body save came from the deploy and whose
file still has the digest the deploy recorded in the card's ``wp_source`` counts as untouched for 3 and 6 — the
deploy may take back what only it wrote. A person's edit, any other save or an unreadable file protects it.

The existing ``compose._untouched_draft`` / ``slots.is_disposable_draft`` judge 1 ~ 3 only; they are not changed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from flowgate.work_plan_deploy import legacy_adapter as legacy

ABSENT, UNTOUCHED, PROTECTED = "absent", "untouched", "protected"
NOT_OWNED, STARTED, HAS_BODY, HAS_ATTACHMENT, HAS_HISTORY, UNKNOWN = (
    "not_owned", "started", "has_body", "has_attachment", "has_history", "protection_unknown")


@dataclass
class Verdict:
    state: str                         # ABSENT (the line shows no live document) | UNTOUCHED | PROTECTED
    reason: Optional[str] = None       # the protection reason
    doc: Optional[dict] = None         # the live document the line shows
    path: object = None                # its file (UNTOUCHED)
    identity: Optional[tuple] = None   # the file's identity when judged (UNTOUCHED)
    text: Optional[str] = None         # the file's text when judged (UNTOUCHED)


def shown_document(flow_id: str, row: dict, docs_by_id: dict) -> Optional[dict]:
    """The live document a line row shows (the end of its supersede chain), or None."""
    doc = docs_by_id.get(row["doc_id"])
    if doc is None:
        return None
    end = legacy.live_end(flow_id, doc, docs_by_id)[0]
    return end or (doc if legacy.is_alive(doc, flow_id) else None)


def judge(flow_id: str, row: dict, docs_by_id: dict, *, started: bool,
          deploy_mark: Optional[dict] = None) -> Verdict:
    """The verdict on one card line (``row``: its ``graph_node_documents`` row)."""
    try:
        shown = shown_document(flow_id, row, docs_by_id)
        if shown is None:
            return Verdict(ABSENT)
        if not row.get("owned") or shown["doc_id"] != row["doc_id"]:
            return Verdict(PROTECTED, NOT_OWNED, shown)
        if started:
            return Verdict(PROTECTED, STARTED, shown)
        path = legacy.file_of(shown)
        identity = legacy.file_identity(path) if path is not None else None
        state, text = legacy.body_text(shown)
        if state != "readable" or text is None or identity is None:
            return Verdict(PROTECTED, UNKNOWN, shown)
        saves = legacy.body_save_sources(shown["doc_id"])
        written = legacy.approvable_text(shown, text)
        if written or saves:
            if not _deploy_wrote_only(shown, path, saves, deploy_mark):
                return Verdict(PROTECTED, HAS_BODY, shown)
        if legacy.attachment_count(shown["doc_id"]) > 0:
            return Verdict(PROTECTED, HAS_ATTACHMENT, shown)
        if (not legacy.is_disposable_draft(shown) or int(shown.get("revision_no") or 0) != 0
                or legacy.review_count(shown["doc_id"]) > 0 or legacy.rejection_count(shown) > 0
                or legacy.review_event_count(shown) > 0):
            return Verdict(PROTECTED, HAS_HISTORY, shown)
        return Verdict(UNTOUCHED, None, shown, path, identity, text)
    except Exception as exc:  # noqa: BLE001 — D0006 §3.5-8: a fact that cannot be read protects the document
        legacy.log_warning("work plan deploy: protection facts unreadable for %s: %s", row.get("doc_id"), exc)
        return Verdict(PROTECTED, UNKNOWN, docs_by_id.get(row.get("doc_id")))


def _deploy_wrote_only(doc: dict, path, saves: list, mark: Optional[dict]) -> bool:
    if not mark or mark.get("instruction_doc_id") != doc["doc_id"] or not mark.get("instruction_digest"):
        return False
    if not saves or any(source != legacy.DEPLOY_SOURCE for source in saves):
        return False
    with open(path, "rb") as stream:
        return legacy.digest(stream.read()) == mark["instruction_digest"]


def card_verdict(flow_id: str, node_key: str, rows: list[dict], docs_by_id: dict,
                 deploy_mark: Optional[dict]) -> Optional[str]:
    """None when every line of the card is free to go with it (its documents untouched or absent), else the first
    protection reason (the cleanup removes a card only then)."""
    try:
        started = legacy.node_started(flow_id, node_key)
    except Exception as exc:  # noqa: BLE001
        legacy.log_warning("work plan deploy: run facts unreadable for %s: %s", node_key, exc)
        return UNKNOWN
    if started:
        return STARTED
    for row in rows:
        verdict = judge(flow_id, row, docs_by_id, started=False, deploy_mark=deploy_mark)
        if verdict.state == PROTECTED:
            return verdict.reason
    return None
