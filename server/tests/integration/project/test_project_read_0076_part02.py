"""Independently selectable scenarios; shared fixtures remain in test_project_read_0076.py."""
from __future__ import annotations

from tests.integration.project.test_project_read_0076 import (
    ACCOUNTS,
    API,
    CALLS,
    CATEGORIES,
    Env,
    KEY_FILE,
    OBSERVED,
    P1,
    P1_NAME,
    P1_OVERRIDE,
    P2,
    PASSWORD,
    Path,
    SECRET,
    SRC_MAIN,
    SYSTEM_ROOT,
    UNAUTHENTICATED,
    WORKTREE,
    _DENIED,
    _LISTS,
    _build_storage,
    _categories,
    _git,
    _manifest,
    _put,
    _scope,
    _session_columns_changed,
    _session_rows,
    _snapshot,
    base64,
    env,
    hashlib,
    os,
    pytest,
    sqlite3,
    subprocess,
    sys,
)

def test_v24_a_git_config_contract(env):
    r = env.get(f"{API}/projects/{P1}/git/config", "A")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["configured"] is True
    config = body["config"]
    for key, value in (("project_id", P1), ("repo_url", "https://git.example.com/team/p1.git"), ("provider", "gitea"),
                       ("enabled", True), ("base_branch", "develop"), ("default_finalize_action", "push"),
                       ("author_name", "P1 Bot"), ("author_email", "p1-bot@example.com"), ("has_secret", True)):
        assert config[key] == value, key
    assert SECRET not in r.text  # only a masked form travels; the screen shows only has_secret
    assert config["secret_masked"] and config["secret_masked"] != SECRET
    unconfigured = env.get(f"{API}/projects/{P2}/git/config", "A").json()
    assert unconfigured == {"ok": True, "configured": False, "config": None}
    print(f"V24 A p1 git/config keys: {sorted(config)}; p2: {unconfigured}")
@pytest.mark.parametrize("who", UNAUTHENTICATED)
def test_v24_unauthenticated_401(env, who):
    seen = [(name, env.get(url, who).status_code) for name, url in CALLS]
    print(f"V24 {who}: {seen}")
    assert [status for _, status in seen] == [401, 401, 401, 401], seen
