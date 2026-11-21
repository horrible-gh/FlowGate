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

@pytest.mark.parametrize("who", ("P", "M", "N", "R", "D"))
def test_v24_observation_non_admins(env, who):
    seen = []
    for name, url in CALLS:
        r = env.get(url, who)
        seen.append((name, r.status_code, _scope(name, r)))
    print(f"V24 {who}: {seen}")
    lists = [s for s in seen if s[0].startswith("list")]
    full_list = [s for s in lists if s[1] == 200 and P1 in (s[2] or []) and (s[0] == "list_active" or P2 in (s[2] or []))]
    if full_list:
        print(f"V24 {who}: UNRESOLVED 0075 defect reproduced - lists unscoped by membership are returned to a "
              f"non-administrator (status=active: every active project; status=all: every project): {full_list}")
    if who in ("N", "R", "D") and any(s[1] == 200 for s in seen if s[0].startswith("p1_")):
        print(f"V24 {who}: a non-member read p1's storage-root / git config (system-role path; recorded, not resolved)")
    assert seen == OBSERVED[who], seen
def test_v25_reads_change_nothing(env):
    before_db = _snapshot(env.db_path)
    before_sessions = _session_rows(env.db_path)
    before_files = _manifest(env.env_root)
    # The fixture really holds every V20 category, and the key file is the key in use.
    per_category = _categories(before_files)
    assert all(per_category.values()), per_category
    assert os.environ.get("FLOWGATE_GIT_ENCRYPT_KEY") is None
    before_key = (env.env_root / KEY_FILE).read_bytes()
    worktree_status = _git(env.env_root / WORKTREE, "status", "--porcelain")
    statuses = []
    for who in ACCOUNTS + UNAUTHENTICATED:
        for _name, url in CALLS + (("p2_root", f"{API}/projects/{P2}/storage-root"), ("p2_git", f"{API}/projects/{P2}/git/config")):
            statuses.append(env.get(url, who).status_code)
    after_db = _snapshot(env.db_path)
    after_files = _manifest(env.env_root)
    after_key = (env.env_root / KEY_FILE).read_bytes()
    changed = sorted(name for name in after_db if after_db[name] != before_db.get(name))
    # get_current_user's own session bookkeeping is the one write a signed-in GET makes; it is the
    # auth layer's, not a project / settings / Git value. Recorded with the columns that moved.
    moved, session_ids = _session_columns_changed(env.db_path, before_sessions)
    print(f"V25 {len(statuses)} GETs, statuses {sorted(set(statuses))}; tables changed: {changed}; "
          f"auth_sessions columns moved: {moved}")
    files = [entry for entry in after_files if entry[1] != "dir"]
    print(f"V25/V20 manifest of {env.env_root.name}: {len(after_files)} entries ({len(files)} files, "
          f"{len(after_files) - len(files)} dirs); non-empty files per category {per_category}; "
          f"identical before/after: {after_files == before_files}")
    assert [name for name in changed if name != "auth_sessions"] == []
    assert session_ids == sorted(before_sessions)  # no session created or removed
    assert after_files == before_files
    assert after_key == before_key
    assert _git(env.env_root / WORKTREE, "status", "--porcelain") == worktree_status == "?? app/wip.py\n"
    git_row = sqlite3.connect(env.db_path).execute(
        "SELECT secret_enc, translate_url, tr_scope_stage, username FROM project_git_config WHERE project_id = ?", [P1]).fetchone()
    assert git_row[1:] == ("http://translate.local", "warn", "bot-0076") and git_row[0]
def test_v20_git_config_read_uses_the_key_file(env):
    """The fixture's key file is the key in use: A's git/config decrypts the stored secret with it."""
    from modules.flow_gate.services.git import credentials

    assert credentials._load_key_material(credentials.SECRET_ENV_KEY) is None  # no env / settings key
    assert credentials._key_file_path() == env.env_root / KEY_FILE
    assert credentials._get_current_key() == b"k" * 32
    r = env.get(f"{API}/projects/{P1}/git/config", "A")
    assert r.status_code == 200, r.text
    config = r.json()["config"]
    assert config["has_secret"] is True
    assert config["secret_masked"] and config["secret_masked"] != SECRET
    assert config["secret_masked"][:4] == SECRET[:4] and config["secret_masked"][-4:] == SECRET[-4:]
