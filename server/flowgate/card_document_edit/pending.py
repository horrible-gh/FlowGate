"""Committed contacts wait until the outer Flow and type locks have been released."""
from contextvars import ContextVar
from modules.flow_gate.db.connection import after_commit, in_transaction
from modules.flow_gate.graph_workflow.services.flow_guard import held_guard

_queue = ContextVar("document_pending_contacts", default=())


def enqueue(pending):
    guarded = held_guard() is not None
    def committed():
        _queue.set((*_queue.get(), pending))
        if not guarded:
            flush()
    assert after_commit(committed)


def flush():
    from .boundary import _owned_types
    if in_transaction() or held_guard() is not None or _owned_types.get():
        return
    queue = _queue.get()
    _queue.set(())
    for pending in queue:
        pending.run_if_committed()


def enqueue_action(call):
    class Action:
        def run_if_committed(self):
            from .contacts import _best_effort
            _best_effort("committed action", call)
    enqueue(Action())
