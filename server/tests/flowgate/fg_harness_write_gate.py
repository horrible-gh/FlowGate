"""flowgate.v02.0146 T#2 — the document write gate for the Flow Gate test harnesses that seed documents directly.

The server startup registers ``flowgate.card_document_edit.write_gate.dispatch`` as the document write gate
(``card_document_ports.DOCUMENT_WRITE_GATE``). A harness that builds its app without the startup used to inherit
whatever an earlier test module had left in that process-wide table and, run on its own (or in another order, as
``--dist loadfile`` does), failed on setup with ``409 card_document_edit_required``. ``install`` registers the real
gate for one test through ``monkeypatch`` (restored afterwards). Only the harness's own direct row writes are let
through -- the seed's ``file_path`` (``docs/<id>.md``) and review status, and the ``revision_no`` helpers that stand
in for the engine's result registration; paths, stages, creations, project scopes and any row write that touches
another column or path still go through the real gate.
"""
from __future__ import annotations

SEED_ROW_KEYS = frozenset({"file_path", "doc_review_status", "revision_no"})


def harness_write_gate(operation, *args, **kwargs):
    from flowgate.card_document_edit import write_gate

    if operation == "row":
        doc_id, updates, apply = args[:3]
        if set(updates) <= SEED_ROW_KEYS and updates.get("file_path", f"docs/{doc_id}.md") == f"docs/{doc_id}.md":
            return apply()
    return write_gate.dispatch(operation, *args, **kwargs)


def install(monkeypatch, *, dedicated=False):
    """Register the gate for this test; ``dedicated=True`` adds the other document-write ports the startup registers
    (author evidence, dedicated writes such as the root conversion, write-released flush)."""
    from modules.flow_gate.graph_workflow.services import card_document_ports as ports

    monkeypatch.setitem(ports._handlers, ports.DOCUMENT_WRITE_GATE, harness_write_gate)
    if dedicated:
        from flowgate.card_document_edit import author_evidence, integration
        from flowgate.card_document_edit.pending import flush

        for name, handler in ((ports.DOCUMENT_AUTHOR_EVIDENCE, author_evidence.current),
                              (ports.DEDICATED_DOCUMENT_WRITE, integration.dedicated),
                              (ports.DOCUMENT_WRITE_RELEASED, flush)):
            monkeypatch.setitem(ports._handlers, name, handler)
