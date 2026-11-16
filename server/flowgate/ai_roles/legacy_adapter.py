"""Legacy service boundary for the account/role application rules (0113 T#2)."""
from modules.flow_gate.db import ai_providers, ai_groups, ai_group_role_profiles
from modules.flow_gate.db.connection import get_store, in_transaction
from modules.flow_gate.db.ai_group_role_profiles import effective_review_count, stored_review_count  # noqa: F401 - re-exported boundary
from modules.flow_gate.settings import ai_execution_policy_service
from modules.flow_gate.settings import workspace_service
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.graph_workflow.services.flow_guard import held_guard


def creation_group_profiles():
    """Raw global role defaults, without filtering unavailable provider IDs."""
    group = ai_groups.get_role_group()
    return ai_group_role_profiles.list_profiles(group["ai_group_id"]) if group else []


def card_review_count_choices():
    """The card 검수 횟수 values a stored card setting may hold (-1, 0, 1..the effective max), as the card PATCH.
    The same policy call as ``ai_group_role_profile_service.card_review_count_choices`` (the API's effective value)."""
    return ai_execution_policy_service.repeat_count_choices(allow_zero=True)


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
