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


def test_untracked_merge_blockers_parsing():
    """R5's stderr parser — pure text, so it needs no git binary.

    None (not []) when the failure was something else: the caller only swaps in
    the dedicated 409 on a positive identification, so unrelated git errors keep
    their honest 500 instead of being mislabelled as an untracked collision.
    """
    from modules.flow_gate.services import git_service as svc

    stderr = (
        "error: The following untracked working tree files would be "
        "overwritten by merge:\n"
        "\tdocs/new.md\n"
        "\tsrc/added.py\n"
        "Please move or remove them before you merge.\n"
        "Aborting\n"
    )
    assert svc._untracked_merge_blockers(stderr) == ["docs/new.md", "src/added.py"]
    # the delete-side wording of the same refusal
    assert svc._untracked_merge_blockers(
        "error: Untracked working tree file would be removed by merge:\n\tx.txt\n"
    ) == ["x.txt"]
    # unrelated failures / empty input → None, never a bogus empty diagnosis
    assert svc._untracked_merge_blockers("fatal: refusing to merge unrelated histories") is None
    assert svc._untracked_merge_blockers("") is None
    assert svc._untracked_merge_blockers(None) is None
    # header recognized but no parsable list → still a positive identification
    assert svc._untracked_merge_blockers(
        "error: The following untracked working tree files would be overwritten by merge:\n"
    ) == []

