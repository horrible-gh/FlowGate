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
    @needs_node_syntax
    def test_review_gate_vue_validates_both_script_and_script_setup_blocks(self, origin_repo):
        # 0481 TR0009 rev2 (AI review finding 2): a valid SFC may carry BOTH a
        # `<script setup>` and an ordinary `<script>` block — Vue's own compiler
        # merges them at build time. `checkVue` used to pick only ONE via
        # `descriptor.scriptSetup || descriptor.script`, so a syntax error in
        # whichever block lost that `||` reached approval unnoticed. Here the
        # `<script setup>` block is syntactically valid and the ordinary
        # `<script>` block is not — the old code would have approved this.
        import uuid

        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "gitprj.default.0135"
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)

        assert svc.ensure_worktree("gitprj", "default", group) == "ok"
        wt = src_root("GitProj", "gitprj_default_0135")
        (wt / "bad.vue").write_text(
            "<script setup>\nconst groupOnly = 1\n</script>\n", encoding="utf-8",
        )
        (seedwt / "bad.vue").write_text(
            "<script setup>\nconst mainlineOnly = 1\n</script>\n", encoding="utf-8",
        )
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "mainline adds bad.vue independently"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]

        # keeps both sides' added lines verbatim (the side-drop guard, unrelated
        # to this check) while the ordinary <script> block is unclosed — invalid
        # JS the real parser must catch even though <script setup> is fine.
        out = svc.resolve_conflicts(group, merge_id, [{
            "path": "bad.vue",
            "content": (
                "<script setup>\nconst groupOnly = 1\nconst mainlineOnly = 1\n"
                "</script>\n<script>\nexport default {\n</script>\n"
            ),
        }], True)
        assert out["result"]["status"] == "resolved_pending_review"
        fingerprint = out["result"]["review_fingerprint"]

        rejected = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=fingerprint, authority="human",
        )
        assert rejected["result"]["status"] == "pre_commit_validation_failed"
        errors = rejected["result"]["errors"]
        assert any(e["path"] == "bad.vue" and e["validator"] == "vue" for e in errors)

        out2 = svc.resolve_conflicts(group, merge_id, [{
            "path": "bad.vue",
            "content": (
                "<script setup>\nconst groupOnly = 1\nconst mainlineOnly = 1\n"
                "</script>\n<script>\nexport default { inheritAttrs: false }\n</script>\n"
            ),
        }], True)
        assert out2["result"]["status"] == "resolved_pending_review"
        approved = svc.approve_merge_review(
            group, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=out2["result"]["review_fingerprint"], authority="human",
        )
        assert approved["result"]["status"] == "merged"
        assert db_git.get_session(merge_id)["status"] == "done"


