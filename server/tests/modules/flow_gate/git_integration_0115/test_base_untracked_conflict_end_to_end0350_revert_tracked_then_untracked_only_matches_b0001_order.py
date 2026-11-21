"""Git integration 0115: independently selectable scenario module."""
from __future__ import annotations

from test_git_integration_0115 import (
    Path,
    _API_0594_OURS,
    _API_0594_PATHS,
    _API_0594_THEIRS,
    _FILE_CLONE,
    _GIT,
    _MockDB,
    _MockTxn,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _ScriptedConflictModel0608,
    _TMP_STORAGE,
    _api_conflict_run_0608,
    _checkout_0608,
    _chunk_sides_0608,
    _git,
    _git_can_clone_local_file_url,
    _history_blob_0608,
    _ident_of,
    _index_conflict_stages_0608,
    _make_untracked_collision,
    _migration_applied,
    _node_syntax_checker_available,
    _real_reads_for_conflict_0608,
    _resolve_token_over_urlopen_0608,
    _seed_wf_done_root,
    _session_json_0608,
    _unmerged_0608,
    act_origin,
    adopt_origin,
    api_conflict_origin_0608,
    author_origin,
    base64,
    base_origin,
    contextmanager,
    grpexp_origin,
    held_locks,
    hold_lock,
    lm,
    needs_git,
    needs_node_syntax,
    noop_origin,
    nowork_surface_origin,
    origin_repo,
    os,
    patch_store,
    project_scoped_admission_origin,
    pytest,
    resolve_base_dirty_origin,
    seed,
    shutil,
    sqlite3,
    subprocess,
    sys,
    tempfile,
    terminal_reopen_origin,
    tmp_db,
    untracked_conflict_origin,
    untracked_origin,
    untracked_remove_origin,
)

from test_git_integration_0115 import TestBaseUntrackedConflictEndToEnd0350 as _Scenario


class TestBaseUntrackedConflictEndToEnd0350(_Scenario):
    def test_revert_tracked_then_untracked_only_matches_b0001_order(self, untracked_conflict_origin):
        """NR §7 item 3: B0001's actual sequence — a tracked base edit AND an
        untracked collision file both present, revert clears only the tracked
        one, and merge THEN fails on the untracked-only leftover (not the E3
        guard, which the first finalize hit instead)."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        base = untracked_conflict_origin["base"]
        group = "utcprj.default.0350d"
        _make_untracked_collision(svc, db_git, src_root, group, "clash3.txt")
        (base / "README.md").write_text("hotfixed\n", encoding="utf-8")   # tracked
        (base / "clash3.txt").write_text("base pre-existing\n", encoding="utf-8")  # untracked collision
        try:
            with pytest.raises(svc.GitServiceError) as exc:
                svc.finalize(group, "merge_only")
            assert exc.value.code == "base_dirty"          # tracked guard fires first
            assert exc.value.details == {"files": ["README.md"]}

            rev = svc.base_revert("utcprj", ["README.md"])
            assert rev["result"]["remaining"] == []

            with pytest.raises(svc.GitServiceError) as exc2:
                svc.finalize(group, "merge_only")
            assert exc2.value.code == "base_untracked_conflict"
            assert exc2.value.details == {"files": ["clash3.txt"], "stage": "merge", "cause": "conflict_other"}
        finally:
            (base / "clash3.txt").unlink()

