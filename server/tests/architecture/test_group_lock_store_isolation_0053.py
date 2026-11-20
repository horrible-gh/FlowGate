"""Lock fixtures expose the same database to eager and late store callers."""
from group_lock_stub import group_store  # noqa: F401
from modules.flow_gate.db.connection import get_store as original_get_store


def test_original_get_store_sees_the_lock_fixture_database(group_store):
    # A function imported after setup would also retain connection.py's globals.
    # Patching only already-imported module attributes misses that caller.
    assert original_get_store() is group_store
    assert original_get_store()._fetch_one(
        "SELECT project_id FROM projects WHERE project_id = ?", ["p1"]
    )["project_id"] == "p1"
