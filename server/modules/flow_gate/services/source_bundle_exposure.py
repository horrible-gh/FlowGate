"""The one Source Bundle exposure judgment (0672 T0004 stage 1, NR0003 §2.1/§7).

Source Bundle is no longer a general source-access path: live source tools are the
canonical source truth for N/NR/T/TR/review/chat/workflow_decide/resolve_conflict.
Only the TS/TSR steps, which have not been reworked yet, keep the Bundle tools they
receive today. Every advertiser (API tool list, CLI prompt/env, mention, help/notices)
and every Bundle entry point (API tool handlers, CLI routes) asks this module, so what
is advertised and what is allowed cannot drift apart.

Input is ``(action_scope, doc_ref type, head type)``. ``edit``/``review`` tokens point
at the document itself, so the doc_ref type decides. A ``new`` token's doc_ref is the
spine/predecessor, so the workflow head type (the same lookup permission uses) decides
whether the step is TS, and the doc_ref type keeps deciding the run tools exactly as the
kind-based advertisement did before. This judgment never changes ``kind_for_step`` /
``kind_for_token`` or the live source scopes.
"""
from __future__ import annotations

import logging
from typing import Optional

from modules.flow_gate.db import documents as db_documents

_logger = logging.getLogger(__name__)

NONE = "none"
ACCESS = "access"  # access_source_bundle
RUN = "run"  # access_source_bundle + run_source_bundle + run_test

# The preserved set. TSR(edit/review) is kept as conditional compatibility only (no
# automatic issuance path exists); TSR(new) is refused or moved to test_run at issuance.
_PRESERVED_TYPES = frozenset({"TS", "TSR"})
# kind_for_step's read_write set: the doc_ref types for which TS(new) was advertised run_*.
_RUN_DOC_TYPES = frozenset({"TR", "TS", "TSR"})


def _norm(value: Optional[str]) -> str:
    return str(value or "").strip().upper()


def exposure(action_scope: Optional[str], doc_ref_type: Optional[str], head_type: Optional[str]) -> str:
    """``none`` | ``access`` | ``run`` for one step."""
    scope, doc_type, head = str(action_scope or ""), _norm(doc_ref_type), _norm(head_type)
    if scope == "new":
        if head != "TS" or not doc_type:
            return NONE
        return RUN if doc_type in _RUN_DOC_TYPES else ACCESS
    if scope == "edit":
        return RUN if doc_type in _PRESERVED_TYPES else NONE
    if scope == "review":
        return ACCESS if doc_type in _PRESERVED_TYPES else NONE
    return NONE


def exposed(level: str) -> bool:
    return level in (ACCESS, RUN)


def doc_type(doc_ref: Optional[str]) -> Optional[str]:
    if not doc_ref:
        return None
    doc = db_documents.get_by_id(doc_ref)
    if not doc:
        return None
    return _norm(doc.get("type_code") or doc.get("type")) or None


def head_type(doc_ref: Optional[str]) -> Optional[str]:
    """The workflow head type, through the same lookup the permission judgment uses."""
    from modules.flow_gate.services import remote_tool_service  # lazy -- import cycle

    head, _failed = remote_tool_service._worker_token_step_type_result({"doc_ref": doc_ref})
    return head


def for_doc_ref(action_scope: Optional[str], doc_ref: Optional[str], *, doc_ref_type: Optional[str] = None) -> str:
    """Resolve the types for a run/token and judge. Any lookup failure exposes nothing."""
    scope = str(action_scope or "")
    if scope not in ("new", "edit", "review") or not doc_ref:
        return NONE
    try:
        own = doc_ref_type if doc_ref_type is not None else doc_type(doc_ref)
        head = head_type(doc_ref) if scope == "new" else own
    except Exception:
        _logger.warning("source bundle exposure lookup failed for %s", doc_ref, exc_info=True)
        return NONE
    return exposure(scope, own, head)


def for_token(token_rec: dict) -> str:
    return for_doc_ref(token_rec.get("action_scope"), token_rec.get("doc_ref"))
