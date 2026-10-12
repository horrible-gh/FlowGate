"""Card memo rules (flowgate.v02.0056 T#3): the memo fields, what a create / change request may carry, and the
"User memos" section the card's AI is given.

- A memo has a title (required, one line, at most ``TITLE_MAX_LEN``), a body (text, may be empty, at most
  ``BODY_MAX_LEN``) and ``ai_ref`` (the card's AI reads it; off unless the person turns it on). The author and the
  times are the server's.
- A create carries ``title`` and optionally ``body`` / ``ai_ref``; a change carries any of the three (at least
  one). Any other key is refused (422 ``unknown_field``), as the card settings PATCH refuses one.
- AI reading (0002-CH turn 6 §8): only the memos turned on go to the AI, every time the card's AI starts — there
  is no "read once then locked" rule. They are not documents, so they are not reference links (``ref_doc_ids``):
  the section carries each memo's title and body, in the order the memos were written, before the mention (as
  ``prepend_messages_section`` places the user messages). The whole section — its header, every title, body and
  separator, and the truncation note — is at most ``SECTION_MAX_CHARS`` characters. When every memo fits, the
  section has them all and no note. Otherwise the section ends with one ``OMITTED_NOTE`` (its room is kept first):
  the titles come first — every title while they all fit, else the titles that fit in order and the rest left
  out — and the bodies are then taken in order while they fit; a memo whose body does not fit shows its title
  only. The note counts the memos shown by title only and the memos left out.
"""
from __future__ import annotations

from typing import Any, Optional

TITLE_MAX_LEN = 200
BODY_MAX_LEN = 20000           # characters; up to 4 bytes each in utf8mb4: the MySQL body is MEDIUMTEXT (149 / 150)
SECTION_HEADER = "User memos"
SECTION_MAX_CHARS = 8000
OMITTED_NOTE = ("[Truncated: the memos are over the {limit}-character limit. "
                "{title_only} memo(s) shown by title only, {left_out} memo(s) left out.]")
_SEPARATOR = "\n\n"
FIELDS = ("title", "body", "ai_ref")


def _bad_text(value: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


def _title(value: Any, errors: list[dict]) -> Optional[str]:
    if not isinstance(value, str):
        errors.append({"field": "title", "reason": "not_a_text"})
        return None
    title = value.strip()
    if not title:
        errors.append({"field": "title", "reason": "required"})
    elif len(title) > TITLE_MAX_LEN:
        errors.append({"field": "title", "reason": "too_long", "max": TITLE_MAX_LEN})
    elif _bad_text(title):
        errors.append({"field": "title", "reason": "invalid_text"})
    return title


def _body(value: Any, errors: list[dict]) -> Optional[str]:
    if not isinstance(value, str):
        errors.append({"field": "body", "reason": "not_a_text"})
        return None
    if len(value) > BODY_MAX_LEN:
        errors.append({"field": "body", "reason": "too_long", "max": BODY_MAX_LEN})
    return value


def _ai_ref(value: Any, errors: list[dict]) -> Optional[bool]:
    if not isinstance(value, bool):
        errors.append({"field": "ai_ref", "reason": "not_a_bool"})
        return None
    return value


def _checked(body: Any, *, create: bool) -> tuple[dict, list[dict]]:
    if not isinstance(body, dict):
        return {}, [{"field": "body", "reason": "not_an_object"}]
    errors = [{"field": key, "reason": "unknown_field"} for key in sorted(set(body) - set(FIELDS))]
    fields: dict = {}
    if "title" in body:
        fields["title"] = _title(body["title"], errors)
    elif create:
        errors.append({"field": "title", "reason": "required"})
    if "body" in body:
        fields["body"] = _body(body["body"], errors)
    if "ai_ref" in body:
        fields["ai_ref"] = _ai_ref(body["ai_ref"], errors)
    if not create and not errors and not fields:
        errors.append({"field": "body", "reason": "no_change"})
    return fields, errors


def check_create(body: Any) -> tuple[dict, list[dict]]:
    """``(fields, errors)`` of a create: title, body ("" when absent), ai_ref (False when absent)."""
    fields, errors = _checked(body, create=True)
    return {"title": fields.get("title"), "body": fields.get("body") or "",
            "ai_ref": bool(fields.get("ai_ref"))}, errors


def check_change(body: Any) -> tuple[dict, list[dict]]:
    """``(fields, errors)`` of a change: only the fields the request carries."""
    return _checked(body, create=False)


def view(row: dict) -> dict:
    """A memo as the API shows it."""
    return {"memo_id": row["memo_id"], "title": row["title"], "body": row.get("body") or "",
            "ai_ref": bool(row.get("ai_ref")), "created_by": row.get("created_by"),
            "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
            "updated_by": row.get("updated_by")}


def section(memos: list[dict], limit: int = SECTION_MAX_CHARS) -> str:
    """The "User memos" section of the memos the AI reads (``ai_ref`` on), or "" when there is none; never longer
    than ``limit`` characters, whatever the number and size of the memos."""
    picked = [m for m in memos if m.get("ai_ref")]
    if not picked:
        return ""
    head = f"## {SECTION_HEADER}\n---\n"
    heads = [f"### {m['title']}" for m in picked]
    bodies = [(m.get("body") or "").strip() for m in picked]
    full = head + _SEPARATOR.join(f"{h}\n{b}" if b else h for h, b in zip(heads, bodies))
    if len(full) <= limit:
        return full
    # one note at the end; its longest form (every count as large as the memos) is set aside first
    room = limit - len(head) - len(_note(limit, len(picked), len(picked)))
    shown: list[str] = []
    for h in heads:                                     # the titles first, in order, each with its separator
        if len(h) + len(_SEPARATOR) > room:
            break
        shown.append(h)
        room -= len(h) + len(_SEPARATOR)
    title_only = 0
    for i, (h, b) in enumerate(zip(heads[:len(shown)], bodies)):
        if not b:
            continue
        if len(shown) == len(heads) and len(b) + 1 <= room:   # the bodies only once every title is in
            shown[i] = f"{h}\n{b}"
            room -= len(b) + 1
        else:
            title_only += 1
    text = head + _SEPARATOR.join(shown + [_note(limit, title_only, len(heads) - len(shown))])
    return text[:limit]                                 # only a limit shorter than the header and the note cuts


def _note(limit: int, title_only: int, left_out: int) -> str:
    return OMITTED_NOTE.format(limit=limit, title_only=title_only, left_out=left_out)


def memo_section(flow_id: str, node_key: Optional[str], doc_id: Optional[str]) -> str:
    """``card_memo_hooks.memo_section`` handler: the section of the card ``node_key``'s memos, or — for an AI
    working on one document (the per-document AI invoke, a Q&A answer) — of the cards whose lines show ``doc_id``."""
    from flowgate.card_documents import lines as card_lines   # noqa: PLC0415
    from flowgate.card_memos import repository                 # noqa: PLC0415

    if not repository.available():
        return ""
    keys = [node_key] if node_key else card_lines.node_keys_showing(flow_id, doc_id)
    if not keys:
        return ""
    return section(repository.memos(flow_id, keys, ai_ref_only=True))
