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

from test_git_integration_0115 import TestProvision0161 as _Scenario


class TestProvision0161(_Scenario):
    def test_judge_occupied_before(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        assert svc._judge_base_slot(adopt_origin["base"], "main") == "occupied"
        view = svc.provision_view("adoptprj")
        assert view["configured"] is True
        assert view["base_path_state"] == "occupied"
        assert view["base_checkout_exists"] is False
        assert view["last_attempt"] is None  # no attempts recorded yet


    def test_adopt_is_lossless_and_records(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        base = adopt_origin["base"]
        out = svc.provision_base("adoptprj", "manual")
        assert out["status"] == "ok"
        assert out["mode"] == "adopt"
        assert out["snapshot_commit"]  # local↔remote difference existed
        # lossless: pre-existing bytes are untouched (local content wins)
        assert (base / "crud.py").read_text(encoding="utf-8") == "local only\n"
        assert (base / "shared.txt").read_text(encoding="utf-8") == "local version\n"
        # remote-only file restored, NOT recorded as a deletion
        assert (base / "README.md").read_text(encoding="utf-8") == "remote readme\n"
        # completion: marker gone, branch ref established
        assert not (base / ".git" / "flowgate_adopt_pending").exists()
        assert svc._judge_base_slot(base, "main") == "checkout"
        # ledger + view
        view = svc.provision_view("adoptprj")
        assert view["base_checkout_exists"] is True
        assert view["last_attempt"]["result"] == "ok"
        assert view["last_attempt"]["trigger"] == "manual"
        assert view["adopt_snapshot"]["commit"] == out["snapshot_commit"]
        # the base checkout is clean → finalize's base_dirty guard passes
        assert svc._dirty(base) is False


    def test_reprovision_is_noop_without_record_update(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        before = svc.provision_view("adoptprj")["last_attempt"]
        out = svc.provision_base("adoptprj", "manual")
        assert out["status"] == "ok"
        assert out["mode"] == "none"
        assert svc.provision_view("adoptprj")["last_attempt"] == before


    def test_worktree_after_adopt_and_finalize_carries_snapshot(self, adopt_origin):
        """B0001 end-to-end: the hook path succeeds on an occupied slot, and the
        adopt snapshot rides along with the finalize push (D0003 §3)."""
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        group = "adoptprj.default.0001"
        assert svc.ensure_worktree("adoptprj", "default", group) == "ok"
        wt = src_root("AdoptProj", "adoptprj_default_0001")
        # 0296 T0004: the worktree now forks from LOCAL main, which already
        # contains origin/main plus the adopt snapshot. This assertion used to
        # read "remote version" — the group saw stale remote content while the
        # operator's base held the adopted local content, the same invisible-file
        # class as B0001 (NR flowgate.default.0296.0003 §C1/§C2). It pinned the
        # then-current behaviour, not an intent: the snapshot is expected to
        # survive all the way to origin (asserted below), so a worktree that
        # ignores it was only ever a way to lose work.
        assert (wt / "shared.txt").read_text(encoding="utf-8") == "local version\n"
        (wt / "work.txt").write_text("group work\n", encoding="utf-8")
        _seed_wf_done_root(group, project_id=group.split(".", 1)[0])
        db_git.set_status(group, "awaiting_choice")
        out = svc.finalize(group, "merge")
        assert out["result"]["status"] == "merged"
        # origin main now holds the group work AND the adopt snapshot
        files = _git(["ls-tree", "--name-only", "main"], cwd=adopt_origin["bare"]).split()
        assert "work.txt" in files
        assert "crud.py" in files
        # local-content-wins snapshot survived the merge
        assert _git(["show", "main:shared.txt"], cwd=adopt_origin["bare"]) == "local version\n"
        # once the snapshot reached the remote, the view stops advertising it
        assert svc.provision_view("adoptprj")["adopt_snapshot"] is None


    def test_manual_provision_response_shape(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        out = svc.provision_manual("adoptprj")
        assert out["ok"] is True
        assert out["result"]["status"] == "ok"
        assert out["result"]["mode"] == "none"
        assert out["result"]["provision"]["base_checkout_exists"] is True


    def test_manual_not_enabled_409(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError) as exc:
            svc.provision_manual("plainprj")
        assert exc.value.status == 409
        assert exc.value.code == "not_enabled"
        # status view for a non-integrated project is the fixed scenario-7 shape
        view = svc.provision_view("plainprj")
        assert view == {"configured": False, "enabled": False, "base_branch": None,
                        "base_path_state": "empty", "base_checkout_exists": False,
                        "adopt_snapshot": None, "last_attempt": None}


    def test_provision_view_unknown_project_404(self, adopt_origin):
        from modules.flow_gate.services import git_service as svc

        with pytest.raises(svc.GitServiceError) as exc:
            svc.provision_view("ghost")
        assert exc.value.status == 404


    def test_failed_attempt_recorded_then_reentry_completes(self, adopt_origin):
        """Interrupted adopt leaves debris + marker; a re-run resumes and
        finishes (L0005 §2.3 idempotent re-entry, P0004 scenario 5)."""
        from modules.flow_gate.db import projects
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.storage.paths import src_root

        projects.create({"project_id": "retryprj", "project_name": "RetryProj"})
        base = src_root("RetryProj", "main")
        base.mkdir(parents=True, exist_ok=True)
        (base / "keep.txt").write_text("keep me\n", encoding="utf-8")
        # unreachable origin → fetch fails mid-adopt
        svc.save_config("retryprj", {
            "repo_url": (Path(adopt_origin["tmp"]) / "missing.git").as_uri(),
            "base_branch": "main",
            "enabled": True,
        })
        out = svc.provision_base("retryprj", "manual")
        assert out["status"] == "failed"
        assert out["mode"] == "adopt"
        # debris: .git + pending marker stay; the slot still reports occupied
        assert (base / ".git" / "flowgate_adopt_pending").exists()
        assert svc._judge_base_slot(base, "main") == "occupied"
        view = svc.provision_view("retryprj")
        assert view["base_checkout_exists"] is False
        assert view["last_attempt"]["result"] == "failed"
        # fix the config → the re-run resumes from the debris and completes
        svc.save_config("retryprj", {
            "repo_url": adopt_origin["bare"].as_uri(),
            "base_branch": "main",
            "enabled": True,
        })
        out = svc.provision_base("retryprj", "manual")
        assert out["status"] == "ok"
        assert out["mode"] == "adopt"
        assert (base / "keep.txt").read_text(encoding="utf-8") == "keep me\n"
        assert svc._judge_base_slot(base, "main") == "checkout"
        svc.delete_config("retryprj")


