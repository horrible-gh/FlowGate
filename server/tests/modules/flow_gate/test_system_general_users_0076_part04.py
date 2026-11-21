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

def test_v14_lowering_the_ceiling_keeps_stored_picks_and_existing_reads_degrade(env):
    from modules.flow_gate.services.ai_invoke import admission, review

    a = env.h("A")
    assert env.client.patch(f"{API}/system/settings", json={"updates": {"ai_repeat_count_max": "10"}}, headers=a).status_code == 200
    # A paused chain whose review and restart picks are 8 (stored under ceiling 10).
    conn = sqlite3.connect(env.db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO ai_invoke_paused_chains (group_id, doc_ref, mode, paused_by, paused_at, docs_reached, "
        "created_at, updated_at, continuation_review_count_overrides, continuation_restart_max_attempts) "
        "VALUES ('g_0076', 'd_0076', 'continuous', 'usr_a0076', '2026-10-20T00:00:00+09:00', 0, "
        "'2026-10-20T00:00:00+09:00', '2026-10-20T00:00:00+09:00', '{\"1\": 8}', 8)")
    conn.commit()
    conn.close()
    assert review.resolve_review_count({1: 8}, 1) == 8
    assert admission._resolve_restart_max_attempts(8) == 9

    paused_before = _snapshot(env.db_path, ["ai_invoke_paused_chains"])
    everything_before = _snapshot(env.db_path)
    r = env.client.patch(f"{API}/system/settings", json={"updates": {"ai_repeat_count_max": "3"}}, headers=a)
    assert r.status_code == 200
    assert [(row["setting_key"], row["setting_value"]) for row in r.json()["updated"]] == [("ai_repeat_count_max", "3")]
    assert _settings_map(env.client.get(f"{API}/system/settings", headers=a).json())["ai_repeat_count_max"] == "3"
    assert _snapshot(env.db_path, ["ai_invoke_paused_chains"]) == paused_before
    everything_after = _snapshot(env.db_path)
    changed = sorted(name for name in everything_after if everything_after[name] != everything_before.get(name))
    print(f"V14 tables changed by the PATCH (and its auth): {changed}")
    assert set(changed) <= {"system_settings", "auth_sessions"}
    # The existing read paths now degrade: review → 0 (not reviewed), restart → default 1 (2 attempts).
    review_now = review.resolve_review_count({1: 8}, 1)
    restart_now = admission._resolve_restart_max_attempts(8)
    print(f"V14 after ceiling 3: resolve_review_count(8)={review_now}, _resolve_restart_max_attempts(8)={restart_now}")
    assert review_now == 0
    assert restart_now == 2
def test_v15_ceiling_bounds_and_codes(env):
    a = env.h("A")
    results = []
    for value, expected in (("1", 200), ("30", 200), ("0", 422), ("31", 422), ("2.5", 422)):
        r = env.client.patch(f"{API}/system/settings", json={"updates": {"ai_repeat_count_max": value}}, headers=a)
        results.append((value, r.status_code, r.json().get("detail") if r.status_code != 200 else r.json()["updated"][0]["setting_value"]))
        assert r.status_code == expected, (value, r.text)
        if expected == 422:
            assert r.json()["detail"] == {"code": "ai_repeat_count_out_of_range", "params": {"min": 1, "max": 30}}
    print(f"V15: {results}")
    assert _settings_map(env.client.get(f"{API}/system/settings", headers=a).json())["ai_repeat_count_max"] == "30"
def test_v20_settings_and_user_saves_touch_only_their_own_rows_and_no_file(env, monkeypatch, tmp_path):
    """The saves 시스템 › 일반 / 사용자 can make — the Scratch pair, the repeat ceiling and the user
    actions — leave every other table and every file under the (isolated) storage root as they
    were. Async GC is not part of this: the settings write does not run it."""
    import hashlib

    storage = tmp_path / "storage"
    for rel, data in (("documents/p1/0001-R.md", b"doc"), ("src/p1/app.py", b"print(1)"),
                      ("scratch/run_1/out.txt", b"tmp"), ("keys/secret.key", b"k" * 32),
                      ("worktrees/p1/g1/README.md", b"wt")):
        path = storage / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(storage))

    def manifest():
        return sorted((str(p.relative_to(storage)), hashlib.sha256(p.read_bytes()).hexdigest())
                      for p in storage.rglob("*") if p.is_file())

    a = env.h("A")
    env.client.get(f"{API}/system/users", headers=a)  # the list GET may create the default workspace
    files_before, db_before = manifest(), _snapshot(env.db_path)
    calls = [
        ("PATCH", f"{API}/system/settings", {"updates": {"scratch_ttl_value": "6", "scratch_ttl_unit": "hour"}}),
        ("PATCH", f"{API}/system/settings", {"updates": {"ai_repeat_count_max": "4"}}),
        ("POST", f"{API}/system/users", {"name": "v20 new", "email": "v20new@example.com", "start": "invite", "is_admin": False}),
        ("POST", f"{API}/system/users/usr_m0076/disable", {}),
        ("POST", f"{API}/system/users/usr_m0076/enable", {}),
        ("PUT", f"{API}/system/users/usr_n0076/admin", {"is_admin": True}),
        ("PUT", f"{API}/system/users/usr_n0076/admin", {"is_admin": False}),
        ("POST", f"{API}/system/users/usr_n0076/tfa-reset", {}),
        ("GET", f"{API}/system/storage-root", None),
        ("GET", f"{API}/system/settings", None),
        ("GET", f"{API}/system/users", None),
    ]
    for method, url, body in calls:
        assert _send(env.client, method, url, body, a).status_code in (200, 201)
    db_after = _snapshot(env.db_path)
    changed = sorted(name for name in db_after if db_after[name] != db_before.get(name))
    removed = {name: sorted(set(db_before[name]) - set(db_after[name])) for name in ("workspace_members", "workspaces", "workspace_project_access", "workspace_ai_allowlist")}
    print(f"V20 tables changed: {changed}; workspace rows removed: {removed}; files unchanged: {manifest() == files_before}")
    assert manifest() == files_before
    assert set(changed) <= {"system_settings", "users", "user_project_roles", "auth_sessions", "refresh_tokens", "totp_backup_codes", "workspace_members"}  # disable ends sessions
    assert all(not rows for rows in removed.values())
    settings_before = {r for r in db_before["system_settings"] if "scratch_ttl" not in r and "ai_repeat_count_max" not in r}
    settings_after = {r for r in db_after["system_settings"] if "scratch_ttl" not in r and "ai_repeat_count_max" not in r}
    assert settings_after == settings_before  # storage_root, source_mode, … untouched
def test_v19_last_admin_and_self_disable_are_refused_other_changes_apply(env):
    a = env.h("A")
    users = lambda: {u["user_id"]: u for u in env.client.get(f"{API}/system/users", headers=a).json()["users"]}
    users()  # the list GET may create the default workspace (existing behaviour); start after it
    relations_before = _snapshot(env.db_path, ["workspace_members", "workspaces"])

    # A disabling A while another administrator (X) is active → the self guard.
    r = env.client.post(f"{API}/system/users/usr_a0076/disable", headers=a, json={})
    print(f"V19 A disables self (2 admins): {r.status_code} {r.json()}")
    assert r.status_code == 409 and r.json()["detail"] == {"code": "cannot_disable_self"}
    assert users()["usr_a0076"]["state"] == "active"

    # Leave A as the only active administrator.
    assert env.client.post(f"{API}/system/users/usr_x0076/disable", headers=a, json={}).status_code == 200
    r = env.client.put(f"{API}/system/users/usr_a0076/admin", headers=a, json={"is_admin": False})
    print(f"V19 last admin demoted: {r.status_code} {r.json()}")
    assert r.status_code == 409 and r.json()["detail"] == {"code": "last_admin"}
    r = env.client.post(f"{API}/system/users/usr_a0076/disable", headers=a, json={})
    print(f"V19 last admin disables self: {r.status_code} {r.json()}")
    assert r.status_code == 409 and r.json()["detail"] == {"code": "last_admin"}
    now = users()["usr_a0076"]
    assert now["is_admin"] is True and now["state"] == "active"

    # Another user's state changes are applied and read back.
    assert env.client.post(f"{API}/system/users/usr_m0076/disable", headers=a, json={}).json()["state"] == "disabled"
    assert users()["usr_m0076"]["state"] == "disabled"
    assert env.client.post(f"{API}/system/users/usr_m0076/enable", headers=a, json={}).json()["state"] == "active"
    assert users()["usr_m0076"]["state"] == "active"

    # Create without workspace_id: no membership row is created or removed.
    created = env.client.post(f"{API}/system/users", headers=a, json={"name": "v19 new", "email": "v19new@example.com", "start": "password", "is_admin": False})
    assert created.status_code == 201
    assert created.json()["user"]["workspaces"] == [] and created.json()["temp_password"]
    users()
    relations_after = _snapshot(env.db_path, ["workspace_members", "workspaces"])
    removed = {t: sorted(set(relations_before[t]) - set(relations_after[t])) for t in relations_before}
    added = {t: sorted(set(relations_after[t]) - set(relations_before[t])) for t in relations_before}
    print(f"V19 workspace rows removed={removed} added={added}")
    assert all(not rows for rows in removed.values())
