"""Node identity rematch after a v0.1 sequence changed (D0004 §3.6, L0009 §2.3.4~§2.3.6).

Pure: the caller passes the recorded mapping rows (``prev``), the current v0.1 rows
(``cur``), the protected row ids, a slot-key generator and a supersede-chain predicate.

prev row keys: node_key, identity_source, item_id, item_seq, item_type, label, sort_order,
               source_wp_card_id, result_doc_id
cur  row keys: id, item_seq, type, label, sort_order, source_wp_card_id, result_doc_id
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

REMATCH_LCS_MAX_CELLS = 250_000

DOC_PREFIX = "doc:"
ROW_PREFIX = "row:"


def doc_key(doc_id: str) -> str:
    return f"{DOC_PREFIX}{doc_id}"


def row_key(item_id) -> str:
    return f"{ROW_PREFIX}{int(item_id)}"


@dataclass
class Assignment:
    row: dict                     # the current v0.1 row
    node_key: str
    identity_source: str          # document | sequence_row | sequence_slot
    prev_key: Optional[str]       # recorded key it was paired with (None = no pair)


@dataclass
class RematchResult:
    assignments: list[Assignment] = field(default_factory=list)
    retired_keys: list[str] = field(default_factory=list)

    @property
    def handoffs(self) -> list[tuple[str, str]]:
        """(from_key, to_key) for every pair whose key changed (L0009 §2.3.4 handoff rule)."""
        return [
            (a.prev_key, a.node_key) for a in self.assignments
            if a.prev_key is not None and a.prev_key != a.node_key
        ]


def _upper(value) -> str:
    return str(value or "").upper()


def _trim(value) -> str:
    return str(value or "").strip()


def same_physical_row(p: dict, c: dict) -> bool:
    return (
        p.get("item_id") == c.get("id")
        and p.get("item_seq") == c.get("item_seq")
        and _upper(p.get("item_type")) == _upper(c.get("type"))
    )


def same_type_and_label(p: dict, c: dict) -> bool:
    return _upper(p.get("item_type")) == _upper(c.get("type")) and _trim(p.get("label")) == _trim(c.get("label"))


def _cur_order(c: dict):
    return (c.get("sort_order") or 0, c.get("id") or 0)


def _prev_order(p: dict):
    return (p.get("sort_order") or 0, p.get("item_seq") or 0)


def lcs_pairs(ps: Sequence[dict], cs: Sequence[dict], eq) -> list[tuple[dict, dict]]:
    n, m = len(ps), len(cs)
    if n * m > REMATCH_LCS_MAX_CELLS:
        return linear_pairs(ps, cs, eq)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if eq(ps[i], cs[j]):
                table[i][j] = 1 + table[i + 1][j + 1]
            else:
                table[i][j] = max(table[i + 1][j], table[i][j + 1])
    result = []
    i = j = 0
    while i < n and j < m:
        if eq(ps[i], cs[j]) and table[i][j] == 1 + table[i + 1][j + 1]:
            result.append((ps[i], cs[j]))
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1          # tie: skip the recorded (deleted) row first
        else:
            j += 1
    return result


def linear_pairs(ps: Sequence[dict], cs: Sequence[dict], eq) -> list[tuple[dict, dict]]:
    result = []
    j = 0
    for p in ps:
        for k in range(j, len(cs)):
            if eq(p, cs[k]):
                result.append((p, cs[k]))
                j = k + 1
                break
    return result


def rematch(
    prev: Sequence[dict],
    cur: Sequence[dict],
    protected_ids: set[int],
    *,
    new_slot_key: Callable[[], str],
    supersede_reaches: Callable[[str, str], bool] = lambda _old, _new: False,
) -> RematchResult:
    P = sorted(prev, key=_prev_order)
    C = sorted(cur, key=_cur_order)
    used_p: set[int] = set()
    used_c: set[int] = set()
    out = RematchResult()

    def remaining_p():
        return [p for p in P if id(p) not in used_p]

    def remaining_c():
        return [c for c in C if id(c) not in used_c]

    def pair(p: Optional[dict], c: dict, key: str, source: str) -> None:
        if p is not None:
            used_p.add(id(p))
        used_c.add(id(c))
        out.assignments.append(Assignment(c, key, source, p.get("node_key") if p else None))

    def first(pred) -> Optional[dict]:
        return next((p for p in remaining_p() if pred(p)), None)

    # 1) rows holding a result document -> paired by the document
    for c in [c for c in C if c.get("result_doc_id") is not None]:
        rid = c["result_doc_id"]
        p = first(lambda p: p.get("result_doc_id") == rid)
        if p is None:
            p = first(lambda p: same_physical_row(p, c))
        if p is None:
            p = first(lambda p: p.get("result_doc_id") is not None and supersede_reaches(p["result_doc_id"], rid))
        pair(p, c, doc_key(rid), "document")

    # 2) protected rows before their result -> paired by the row itself
    for c in [c for c in remaining_c() if int(c["id"]) in protected_ids]:
        p = first(lambda p: p.get("item_id") == c.get("id") and p.get("item_seq") == c.get("item_seq"))
        pair(p, c, row_key(c["id"]), "sequence_row")

    # 3) rows a work plan poured in -> paired by (card, type), relative order kept
    groups: list[tuple[str, str]] = []
    for c in remaining_c():
        card = c.get("source_wp_card_id")
        if card and (card, _upper(c.get("type"))) not in groups:
            groups.append((card, _upper(c.get("type"))))
    for card, typ in groups:
        cs = [c for c in remaining_c() if c.get("source_wp_card_id") == card and _upper(c.get("type")) == typ]
        ps = [
            p for p in remaining_p()
            if p.get("identity_source") == "sequence_slot"
            and p.get("source_wp_card_id") == card and _upper(p.get("item_type")) == typ
        ]
        for p, c in zip(ps, cs):
            pair(p, c, p["node_key"], "sequence_slot")

    # 4) remaining pending rows -> order comparison (LCS on type + label)
    ps = [p for p in remaining_p() if p.get("identity_source") == "sequence_slot"]
    for p, c in lcs_pairs(ps, remaining_c(), same_type_and_label):
        pair(p, c, p["node_key"], "sequence_slot")

    # 5) unpaired new rows -> new identities
    for c in remaining_c():
        if c.get("result_doc_id") is not None:
            pair(None, c, doc_key(c["result_doc_id"]), "document")
        elif int(c["id"]) in protected_ids:
            pair(None, c, row_key(c["id"]), "sequence_row")
        else:
            pair(None, c, new_slot_key(), "sequence_slot")

    # 6) unpaired recorded identities -> retired
    in_use = {a.node_key for a in out.assignments}
    out.retired_keys = [p["node_key"] for p in remaining_p() if p["node_key"] not in in_use]
    out.assignments.sort(key=lambda a: _cur_order(a.row))
    return out
