"""Legacy service boundary for the account/role application rules (0113 T#2)."""
from modules.flow_gate.db import ai_providers
from modules.flow_gate.db.connection import get_store, in_transaction
from modules.flow_gate.settings import workspace_service
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.graph_workflow.services.flow_guard import held_guard


def isolated_read(read, savepoint):
    """A recoverable optional read must not abort its caller's write transaction."""
    if not in_transaction():
        return read()
    from modules.flow_gate.db import connection
    if not connection._SAVEPOINT_NAME.fullmatch(savepoint):
        raise ValueError("invalid role read savepoint")
    store = get_store()
    connection._open_sqlite_transaction(getattr(connection._tx_local, "txn", None))
    store._execute("SAVEPOINT " + savepoint)
    try:
        result = read()
    except Exception:
        store._execute("ROLLBACK TO SAVEPOINT " + savepoint)
        store._execute("RELEASE SAVEPOINT " + savepoint)
        raise
    store._execute("RELEASE SAVEPOINT " + savepoint)
    return result
