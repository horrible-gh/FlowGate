"""Independently selectable scenarios; shared fixtures remain in test_system_general_users_0076.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_system_general_users_0076 import (
    API,
    DELEGATED,
    D_ADMIN_FLAG_OBSERVED,
    D_OBSERVED,
    Env,
    PASSWORD,
    SCRATCH_KEYS,
    WATCHED,
    _delegate,
    _fail_second_settings_write,
    _raw_settings,
    _send,
    _settings_map,
    _snapshot,
    _system_calls,
    contextmanager,
    env,
    os,
    pytest,
    sqlite3,
)

def test_v13_second_write_failure_rolls_both_keys_back(env, monkeypatch):
    a = env.h("A")
    before = _raw_settings(env.db_path)
    assert before == ("7", "day")
    with monkeypatch.context() as patch:
        count = _fail_second_settings_write(env.store, patch)
        r = env.unsafe_client.patch(f"{API}/system/settings", json={"updates": {"scratch_ttl_value": "6", "scratch_ttl_unit": "hour"}}, headers=a)
    print(f"V13 second write fails: HTTP {r.status_code}, writes attempted={count['n']}, raw after={_raw_settings(env.db_path)}")
    assert count["n"] == 2
    assert r.status_code == 500
    assert _raw_settings(env.db_path) == before
    reread = _settings_map(env.client.get(f"{API}/system/settings", headers=a).json())
    assert (reread["scratch_ttl_value"], reread["scratch_ttl_unit"]) == before
def test_v13_exception_in_the_transaction_body_after_both_writes_rolls_both_keys_back(env, monkeypatch):
    """Both keys are written, then an exception is raised INSIDE the adapter's transaction block,
    right after its body yields — so the original context exits through its error/rollback path
    and never reaches its normal exit (where it would commit). This is "an exception in the
    transaction body before commit", not a failure of the commit call itself; a real commit-call
    failure is not exercised here. The V13 release evidence is the second-write failure above."""
    a = env.h("A")
    before = _raw_settings(env.db_path)
    adapter = env.store._db
    original = adapter.begin_transaction

    @contextmanager
    def raise_before_commit():
        with original() as txn:
            yield txn
            raise sqlite3.OperationalError("injected body exception after both writes, before commit")

    with monkeypatch.context() as patch:
        patch.setattr(adapter, "begin_transaction", raise_before_commit)
        r = env.unsafe_client.patch(f"{API}/system/settings", json={"updates": {"scratch_ttl_value": "30", "scratch_ttl_unit": "minute"}}, headers=a)
    print(f"V13 body exception before commit: HTTP {r.status_code}, raw after={_raw_settings(env.db_path)}")
    assert r.status_code == 500
    assert _raw_settings(env.db_path) == before
    reread = _settings_map(env.client.get(f"{API}/system/settings", headers=a).json())
    assert (reread["scratch_ttl_value"], reread["scratch_ttl_unit"]) == before
def test_v13_store_without_transaction_is_not_atomic(env, monkeypatch):
    """The nullcontext branch D §2.5 refuses to count as atomic: on a store that has no
    transaction() the first key survives a failed second write. The deployment store is not
    such a store (test above), which is what lets the Scratch editor ship."""
    from modules.flow_gate.settings import system_settings_service

    class NoTxnStore:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            if name in ("transaction", "with_transaction"):
                raise AttributeError(name)
            return getattr(self._inner, name)

    no_txn = NoTxnStore(env.store)
    assert getattr(no_txn, "transaction", None) is None
    with monkeypatch.context() as patch:
        _fail_second_settings_write(env.store, patch)
        patch.setattr(system_settings_service._connection, "get_store", lambda: no_txn)
        with pytest.raises(sqlite3.OperationalError):
            system_settings_service.set_values({"scratch_ttl_value": "6", "scratch_ttl_unit": "hour"}, updated_by="usr_a0076")
    print(f"V13 no-transaction store after failed 2nd write: raw={_raw_settings(env.db_path)}")
    assert _raw_settings(env.db_path) == ("6", "day")  # half written: not atomic
def test_v13_other_backends_transaction_api_presence():
    """Information only: whether sqloader's other adapters expose begin_transaction. The
    deployment DB type is sqlite3 (.env.sample); MySQL/PostgreSQL are not exercised here."""
    found = {}
    for module_name in ("sqloader.mysql", "sqloader.postgresql"):
        try:
            module = __import__(module_name, fromlist=["*"])
        except Exception as exc:  # driver not installed in this environment
            found[module_name] = f"import failed: {type(exc).__name__}"
            continue
        classes = [obj for obj in vars(module).values() if isinstance(obj, type) and obj.__module__ == module.__name__]
        found[module_name] = {cls.__name__: hasattr(cls, "begin_transaction") for cls in classes}
    print(f"V13 other adapters: {found}")
