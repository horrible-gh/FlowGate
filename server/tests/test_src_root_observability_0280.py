"""flowgate.default.0280 T0005 — test-run execution-location observability.

B0001 kept reporting "the tests run in main, not on the work branch". NR0003 found
the runner already resolves the group worktree correctly, and that two separate
defects made that impossible to see:

  A. the TSR 실행 환경 block printed ``doc['branch']`` — the *document's* branch,
     which is always "main" — plus a hardcoded "프로젝트 소스 루트 (src_root)" string,
     so a correct worktree run was reported as a main run;
  B. every fallback to the base tree was silent: no log, no column, no UI trace, so
     a real mis-run and a correct run left identical evidence (none).

These tests pin the fix: the root the runner used is classified, persisted and
rendered, and each fallback reason is distinguishable.
"""
from __future__ import annotations

from pathlib import Path


# ── A. TSR renders the recorded root, never the document branch ──────────────


def _tsr_env_line(run: dict) -> str:
    from modules.flow_gate.services import test_run_service

    content = test_run_service._tsr_content(
        {"doc_id": "flowgate.default.0280.9001-TS", "project_id": "flowgate", "branch": "main"},
        run,
        [],
        "TSR",
    )
    lines = [ln for ln in content.splitlines() if ln.startswith("- 실행 위치:")]
    assert len(lines) == 1, f"expected exactly one 실행 위치 line, got {lines}"
    return lines[0]


def test_tsr_reports_worktree_root_not_document_branch():
    line = _tsr_env_line(
        {
            "run_id": "trun_0280_1",
            "source_root": "src/FlowGate Live/fg-0280",
            "source_root_kind": "worktree",
        }
    )

    assert "src/FlowGate Live/fg-0280" in line
    assert "워크트리" in line
    # The old misreport: doc['branch'] is "main" for every document in a group, so
    # printing it beside the root is what produced the false "ran in main" reading.
    assert "main" not in line


def test_tsr_names_the_fallback_reason_for_a_base_run():
    line = _tsr_env_line(
        {
            "run_id": "trun_0280_2",
            "source_root": "src/FlowGate Live/main",
            "source_root_kind": "worktree_unregistered",
        }
    )

    assert "base" in line
    assert "src/FlowGate Live/main" in line
    # The post-merge re-run case must be readable as such, not as a bare path.
    assert "워크트리" in line and "등록" in line


def test_tsr_admits_no_record_for_runs_predating_the_column():
    line = _tsr_env_line({"run_id": "trun_old", "source_root": None, "source_root_kind": None})

    assert "기록 없음" in line
    # Must not fabricate a location for a run that never recorded one.
    assert "워크트리" not in line


# ── B. Fallbacks are classified, not silent ──────────────────────────────────


class _FakeGitDb:
    def __init__(self, cfg, state):
        self._cfg, self._state = cfg, state

    def get_config(self, _project_id):
        return self._cfg

    def get_state(self, _group_id):
        return self._state


def _install_git(monkeypatch, *, cfg, state, project_name="FlowGate Live"):
    from modules.flow_gate.services import git_service

    fake = _FakeGitDb(cfg, state)
    monkeypatch.setattr(git_service, "db_git", fake)
    monkeypatch.setattr(git_service, "_project_name", lambda _pid: project_name)
    return git_service


def test_effective_src_root_ex_distinguishes_every_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))

    cases = [
        (None, None, "git_integration_off"),
        ({"enabled": 0}, None, "git_integration_off"),
        ({"enabled": 1}, None, "no_group_git_state"),
        ({"enabled": 1}, {"worktree_registered": 0, "branch": "fg-0280"}, "worktree_unregistered"),
        ({"enabled": 1}, {"worktree_registered": 1, "branch": "  "}, "state_branch_empty"),
        ({"enabled": 1}, {"worktree_registered": 1, "branch": "fg-0280"}, "worktree_dir_missing"),
    ]
    for cfg, state, expected in cases:
        git_service = _install_git(monkeypatch, cfg=cfg, state=state)
        path, reason = git_service.effective_src_root_ex("flowgate", "flowgate.default.0280")
        assert path is None
        assert reason == expected, f"cfg={cfg} state={state}"

    # No group context at all — the caller never asked for a worktree.
    git_service = _install_git(monkeypatch, cfg={"enabled": 1}, state=None)
    assert git_service.effective_src_root_ex("flowgate", None) == (None, "no_group_context")


def test_effective_src_root_ex_returns_the_worktree_when_it_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    worktree = tmp_path / "src" / "FlowGate Live" / "fg-0280"
    worktree.mkdir(parents=True)
    # 0287 NR0004: a worktree is a directory WITH its `.git` link; a bare directory
    # is the corpse an interrupted teardown leaves, and no resolver accepts it now.
    (worktree / ".git").write_text("gitdir: ../main/.git/worktrees/x", encoding="utf-8")
    git_service = _install_git(
        monkeypatch,
        cfg={"enabled": 1},
        state={"worktree_registered": 1, "branch": "fg-0280"},
    )

    path, reason = git_service.effective_src_root_ex("flowgate", "flowgate.default.0280")

    assert path == worktree.resolve()
    assert reason == "worktree"
    # The legacy wrapper must keep its exact contract.
    assert git_service.effective_src_root("flowgate", "flowgate.default.0280") == worktree.resolve()


def test_unregistered_worktree_is_logged_not_swallowed(monkeypatch, tmp_path, caplog):
    """The post-merge re-run case (NR0003 §4-B) must leave a trace in the log."""
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    git_service = _install_git(
        monkeypatch,
        cfg={"enabled": 1},
        state={"worktree_registered": 0, "branch": "fg-0280", "status": "merged"},
    )

    with caplog.at_level("WARNING"):
        git_service.effective_src_root_ex("flowgate", "flowgate.default.0280")

    assert any("worktree_unregistered" in rec.getMessage() for rec in caplog.records)


def test_classify_src_root_reports_worktree_and_fallback(monkeypatch, tmp_path):
    from modules.flow_gate.storage import paths as storage_paths

    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    worktree = tmp_path / "src" / "FlowGate Live" / "fg-0280"
    worktree.mkdir(parents=True)
    # 0287 NR0004: a worktree is a directory WITH its `.git` link; a bare directory
    # is the corpse an interrupted teardown leaves, and no resolver accepts it now.
    (worktree / ".git").write_text("gitdir: ../main/.git/worktrees/x", encoding="utf-8")
    _install_git(
        monkeypatch,
        cfg={"enabled": 1},
        state={"worktree_registered": 1, "branch": "fg-0280"},
    )

    assert storage_paths.classify_src_root("flowgate", "flowgate.default.0280", worktree) == "worktree"
    # A root that is NOT the resolved worktree must not be labelled as one.
    other = tmp_path / "src" / "FlowGate Live" / "main"
    assert storage_paths.classify_src_root("flowgate", "flowgate.default.0280", other) == "unknown"
    assert storage_paths.classify_src_root("flowgate", "flowgate.default.0280", None) == "unknown"


def test_classify_src_root_never_raises(monkeypatch, tmp_path):
    """Bookkeeping must not be able to fail a run."""
    from modules.flow_gate.services import git_service
    from modules.flow_gate.storage import paths as storage_paths

    def boom(*_a, **_kw):
        raise RuntimeError("git backend down")

    monkeypatch.setattr(git_service, "effective_src_root_ex", boom)

    assert storage_paths.classify_src_root("flowgate", "flowgate.default.0280", tmp_path) == "unknown"


# ── The runner persists what it resolved ─────────────────────────────────────


def test_execute_run_records_the_root_it_used(monkeypatch, tmp_path):
    from modules.flow_gate.services import test_run_service

    root = tmp_path / "src" / "FlowGate Live" / "fg-0280"
    root.mkdir(parents=True)
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(
        test_run_service.db_docs,
        "get_by_id",
        lambda _id: {
            "doc_id": _id,
            "project_id": "flowgate",
            "branch": "main",
            "group_id": "flowgate.default.0280",
        },
    )
    monkeypatch.setattr(
        test_run_service.storage_paths, "resolve_project_src_root", lambda *_a, **_k: root
    )
    monkeypatch.setattr(
        test_run_service.storage_paths, "classify_src_root", lambda *_a, **_k: "worktree"
    )
    recorded: dict = {}
    monkeypatch.setattr(
        test_run_service.db_test_runs,
        "set_run_source_root",
        lambda run_id, source_root, source_root_kind: recorded.update(
            run_id=run_id, source_root=source_root, kind=source_root_kind
        ),
    )
    # Stop the run right after the root is recorded — this test is about the record.
    monkeypatch.setattr(
        test_run_service, "_allocate_port", lambda: (_ for _ in ()).throw(RuntimeError("stop"))
    )

    try:
        test_run_service._execute_run_inner({"run_id": "trun_0280", "doc_id": "doc-1"})
    except RuntimeError:
        pass

    assert recorded["run_id"] == "trun_0280"
    assert recorded["kind"] == "worktree"
    # Stored storage-root-relative (B0054), so the value survives a host move.
    assert not Path(recorded["source_root"]).is_absolute()
    assert recorded["source_root"] == "src/FlowGate Live/fg-0280"


def test_record_source_root_failure_does_not_fail_the_run(monkeypatch, tmp_path):
    from modules.flow_gate.services import test_run_service

    def boom(*_a, **_kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(test_run_service.db_test_runs, "set_run_source_root", boom)

    # Must return normally: a run is not invalidated by its own bookkeeping.
    test_run_service._record_source_root(
        {"project_id": "flowgate", "group_id": "flowgate.default.0280"},
        {"run_id": "trun_0280"},
        tmp_path,
    )
