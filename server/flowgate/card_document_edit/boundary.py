"""L#1 §2.3: Stop hold (transport) -> type lock -> Flow guard -> document row."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from modules.flow_gate.db.connection import in_transaction
from modules.flow_gate.graph_workflow.services import flow_guard

TYPE_LOCK_WAIT_SEC = 30
OWN, JOIN, CREATE = "OWN", "JOIN", "CREATE"
_owned_types = ContextVar("card_document_edit_type_locks", default=())


def type_lock_of(type_code):
    return type_code if type_code in {"WP", "TR2"} else None


def _lock(doc):
    if doc.get("type_code") == "WP":
        from modules.flow_gate.services.work_plan_locks import plan_save_lock
        return plan_save_lock(doc["doc_id"])
    if doc.get("type_code") == "TR2":
        from modules.flow_gate.documents.tr2_service import _lock as tr2_lock
        return tr2_lock(doc["doc_id"])
    return None


@contextmanager
def type_lock(doc, *, wait=True):
    """May be shared by an outer bridge; JOIN never waits in reverse lock order."""
    key = (doc["doc_id"], type_lock_of(doc.get("type_code")))
    if key[1] is None or key in _owned_types.get():
        yield
        return
    lock = _lock(doc)
    acquired = lock.acquire(timeout=TYPE_LOCK_WAIT_SEC) if wait else lock.acquire(blocking=False)
    if not acquired:
        raise flow_guard.FlowLockTimeout(doc["doc_id"])
    token = _owned_types.set((*_owned_types.get(), key))
    try:
        yield
    finally:
        _owned_types.reset(token)
        lock.release()


@dataclass(frozen=True)
class Context:
    doc_id: str
    locked_type: object
    guard_key: tuple
    mode: str

    def assert_held(self):
        assert in_transaction(), "revision storage requires its transaction"
        if self.mode != CREATE and flow_guard.held_guard() != self.guard_key:
            raise flow_guard.GuardNotHeld(self.guard_key, flow_guard.held_guard())


def enter(key_doc, mode, body, *, flow_id=None, new_document=False):
    doc_id = key_doc["doc_id"]
    key = ("flow", flow_id) if flow_id else flow_guard.document_guard_key(key_doc, doc_id)
    ctx = Context(doc_id, type_lock_of(key_doc.get("type_code")), key, mode)
    if mode == OWN:
        if in_transaction():
            raise flow_guard.GuardNotHeld(key, flow_guard.held_guard())
        with type_lock(key_doc):
            if key[0] == "flow":
                def locked():
                    flow_guard.lock_document_row(doc_id)  # second SQL, immediately after guard bump
                    return body(ctx)
                return flow_guard.with_flow_guard(key[1], locked)
            return flow_guard.with_document_lock(doc_id, lambda: body(ctx))
    if mode == JOIN:
        if not in_transaction() or flow_guard.held_guard() != key:
            raise flow_guard.GuardNotHeld(key, flow_guard.held_guard())
        with type_lock(key_doc, wait=False):
            flow_guard.lock_document_row(doc_id)
            return body(ctx)
    if mode == CREATE:
        if not in_transaction() or not new_document or key_doc.get("revision_no", 0) != 0:
            raise flow_guard.GuardNotHeld(key, flow_guard.held_guard())
        with type_lock(key_doc, wait=False):
            return body(ctx)
    raise flow_guard.GuardNotHeld(key, flow_guard.held_guard())
