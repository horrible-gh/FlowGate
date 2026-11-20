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

from test_git_integration_0115 import TestGitEndToEnd as _Scenario


class TestGitEndToEnd(_Scenario):
    def test_review_gate_write_plan_structure_validation(self, origin_repo):
        # 0481 TR0009 rev2: structural validation (L0007 §2.5, the Q&A-bound
        # schema) rejects a malformed plan and gates test-path operations behind
        # allow_test_edits, independent of any live tree state.
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError) as exc:
            svc._validate_write_plan_structure({"schema_version": "wrong"}, allow_test_edits=False)
        assert exc.value.code == "invalid_write_plan"

        base_plan = {
            "schema_version": "flowgate.write-plan.v1",
            "base_fingerprint": "0" * 64,
            "operations": [{
                "operation_id": "op1", "kind": "create_file", "path": "server/tests/new_test.py",
                "absent": True, "content_bytes_base64": "eA==", "mode": "100644", "purpose": "x",
            }],
        }
        with pytest.raises(svc.GitServiceError) as exc2:
            svc._validate_write_plan_structure(base_plan, allow_test_edits=False)
        assert exc2.value.code == "invalid_write_plan"

        # the SAME test-path operation in held_test_operations (with a benign
        # non-test operation actually in operations[], which must stay
        # non-empty) passes without allow_test_edits; so does
        # allow_test_edits=True with the test-path operation in operations[].
        benign_op = {
            "operation_id": "op2", "kind": "create_file", "path": "server/app.py",
            "absent": True, "content_bytes_base64": "eA==", "mode": "100644", "purpose": "x",
        }
        svc._validate_write_plan_structure(
            {**base_plan, "operations": [benign_op], "held_test_operations": base_plan["operations"]},
            allow_test_edits=False,
        )
        svc._validate_write_plan_structure(base_plan, allow_test_edits=True)

        # 0009-TR rev3 (AI review finding 2): a plan whose AI proposed ONLY
        # test-path edits is legitimate — operations[] is empty and everything
        # lands in held_test_operations[]. That must pass structural validation
        # (L0007 §2.7 requires the held proposals be SHOWN, not rejected), while
        # a plan with BOTH arrays empty is still meaningless and rejected.
        svc._validate_write_plan_structure(
            {**base_plan, "operations": [], "held_test_operations": base_plan["operations"]},
            allow_test_edits=False,
        )
        with pytest.raises(svc.GitServiceError) as exc3:
            svc._validate_write_plan_structure(
                {**base_plan, "operations": [], "held_test_operations": []}, allow_test_edits=False,
            )
        assert exc3.value.code == "invalid_write_plan"


