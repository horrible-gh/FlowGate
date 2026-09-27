from __future__ import annotations

import inspect
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from modules.flow_gate import process_service
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.services import git_service
from modules.flow_gate.services.git import group_work_base, worktree as worktree_service
from modules.flow_gate.services.git.credentials import GitServiceError
from modules.flow_gate.workflow.routers import workflow


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    (root / "README.md").write_text("base\n", encoding="utf-8")
    assert _git(root, "add", "README.md").returncode == 0
    assert _git(root, "commit", "-m", "base").returncode == 0
    assert _git(root, "branch", "flowgate-v0.2").returncode == 0
    sha = _git(root, "rev-parse", "main").stdout.strip()
    assert _git(root, "update-ref", "refs/remotes/origin/remote-base", sha).returncode == 0

    monkeypatch.setattr(git_service, "_require_enabled_config", lambda _pid: {"enabled": 1})
    monkeypatch.setattr(git_service, "_base_root_of", lambda _pid: root)
    monkeypatch.setattr(git_service.db_git, "get_state_by_branch", lambda _pid, _ref: None)
    return root


def test_validation_accepts_local_and_rejects_remote_only_branches(repo):
    assert git_service.validate_group_work_base_ref("p", "flowgate-v0.2") == "flowgate-v0.2"
    with pytest.raises(GitServiceError) as caught:
        git_service.validate_group_work_base_ref("p", "remote-base")
    assert caught.value.status == 409
    assert caught.value.code == "group_work_base_remote_only"


def test_validation_rejects_invalid_missing_and_internal_refs(repo, monkeypatch):
    with pytest.raises(GitServiceError) as caught:
        git_service.validate_group_work_base_ref("p", "bad..name")
    assert caught.value.status == 422
    assert caught.value.code == "group_work_base_invalid"

    with pytest.raises(GitServiceError) as caught:
        git_service.validate_group_work_base_ref("p", "deleted-branch")
    assert caught.value.status == 404
    assert caught.value.code == "group_work_base_not_found"

    monkeypatch.setattr(
        git_service.db_git,
        "get_state_by_branch",
        lambda _pid, ref: {"group_id": "p.default.1", "branch": ref},
    )
    with pytest.raises(GitServiceError) as caught:
        git_service.validate_group_work_base_ref("p", "flowgate-v0.2")
    assert caught.value.status == 409
    assert caught.value.code == "group_work_base_internal"
    assert caught.value.details["connected_group_id"] == "p.default.1"


def test_validation_requires_enabled_git(repo, monkeypatch):
    def disabled(_project_id):
        raise GitServiceError(409, "invalid_state", "git integration is not enabled")

    monkeypatch.setattr(git_service, "_require_enabled_config", disabled)
    with pytest.raises(GitServiceError) as caught:
        git_service.validate_group_work_base_ref("p", "main")
    assert caught.value.code == "invalid_state"


def test_resolution_prefers_group_value_and_legacy_falls_back_to_project_base():
    cfg = {
        "enabled": 1,
        "base_branch": "main",
        "default_merge_target": "integration",
    }
    assert group_work_base.resolve_group_work_base_ref(
        "p", "p.default.1", group={"work_base_ref": "feature-base"}, config=cfg
    ) == "feature-base"
    assert group_work_base.resolve_group_work_base_ref(
        "p", "p.default.legacy", group={"work_base_ref": None}, config=cfg
    ) == "main"
    assert group_work_base.resolve_group_work_base_ref(
        "p", "p.default.nogit", group={"work_base_ref": "ignored"},
        config={"enabled": 0, "base_branch": "main"},
    ) is None


def test_group_create_service_persists_validated_ref(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        git_service, "validate_group_work_base_ref", lambda _pid, ref: ref
    )
    monkeypatch.setattr(
        git_service, "resolve_group_work_base_ref",
        lambda _pid, _gid, **kwargs: kwargs["group"].get("work_base_ref") or "main",
    )
    monkeypatch.setattr(
        process_service.numbering_service, "reserve_group", lambda _pid, _module: "0042"
    )
    monkeypatch.setattr(process_service.db, "now_iso", lambda: "2026-09-25T00:00:00")

    def create(data):
        captured.update(data)
        return dict(data)

    monkeypatch.setattr(db_groups, "create", create)
    result = process_service.create_group(
        "p", "title", module="default", work_base_ref="flowgate-v0.2"
    )
    assert result["status"] == "success"
    assert captured["work_base_ref"] == "flowgate-v0.2"
    assert result["work_base_ref"] == "flowgate-v0.2"
    assert result["effective_work_base_ref"] == "flowgate-v0.2"


def test_group_create_service_preserves_non_git_legacy_path(monkeypatch):
    monkeypatch.setattr(
        git_service,
        "validate_group_work_base_ref",
        lambda *_args, **_kwargs: pytest.fail("omitted ref must not be validated"),
    )
    monkeypatch.setattr(
        git_service, "resolve_group_work_base_ref", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        process_service.numbering_service, "reserve_group", lambda _pid, _module: "0043"
    )
    monkeypatch.setattr(process_service.db, "now_iso", lambda: "2026-09-25T00:00:00")
    monkeypatch.setattr(db_groups, "create", lambda data: dict(data))

    result = process_service.create_group("plain", "title")
    assert result["status"] == "success"
    assert result["work_base_ref"] is None
    assert result["effective_work_base_ref"] is None


def test_group_create_endpoint_surfaces_validation_error(monkeypatch):
    monkeypatch.setattr(
        workflow, "_get_user_permissions", lambda _user: {"project.group.manage"}
    )
    monkeypatch.setattr(
        process_service,
        "create_group",
        lambda **_kwargs: {
            "status": "error",
            "http_status": 404,
            "code": "group_work_base_not_found",
            "message": "missing",
            "details": {"work_base_ref": "gone"},
        },
    )
    with pytest.raises(HTTPException) as caught:
        workflow.create_group_endpoint(
            workflow.GroupCreateRequest(
                project_id="p", title="x", work_base_ref="gone"
            ),
            current_user={},
        )
    assert caught.value.status_code == 404
    assert caught.value.detail["code"] == "group_work_base_not_found"


def test_group_list_exposes_stored_and_effective_values(monkeypatch):
    monkeypatch.setattr(
        workflow, "_get_user_permissions", lambda _user: {"document.read"}
    )
    monkeypatch.setattr(
        db_groups,
        "list_groups",
        lambda **_kwargs: [
            {"group_id": "p.default.1", "project_id": "p", "work_base_ref": "feature"},
            {"group_id": "p.default.2", "project_id": "p", "work_base_ref": None},
        ],
    )
    monkeypatch.setattr(
        git_service.db_git,
        "get_config",
        lambda _pid: {"enabled": 1, "base_branch": "main"},
    )
    monkeypatch.setattr(
        git_service.db_git,
        "list_states_of_project_any",
        lambda _pid: [{"group_id": "p.default.1", "worktree_registered": 1}],
    )
    result = workflow.list_groups_endpoint(project_id="p", current_user={})
    assert [row["effective_work_base_ref"] for row in result["groups"]] == [
        "feature", "main"
    ]
    assert [row["work_base_locked"] for row in result["groups"]] == [True, False]


def test_migration_is_additive_and_present_for_all_dialects(migrated_sqlite_db):
    db_path = migrated_sqlite_db("group-work-base-0613.db")
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(groups)")}
    assert "work_base_ref" in columns

    root = Path(__file__).resolve().parents[1] / "sql" / "migrations"
    for dialect in ("sqlite", "postgres", "mysql"):
        text = (root / dialect / "119_group_work_base_ref.sql").read_text(encoding="utf-8")
        assert "ALTER TABLE groups ADD COLUMN work_base_ref TEXT" in text


def test_project_choices_expose_git_state_without_branch_catalog_calls(monkeypatch):
    monkeypatch.setattr(
        process_service.db,
        "get_allowed_projects",
        lambda: [
            {"project": "git-project", "module": "default"},
            {"project": "git-project", "module": "admin"},
            {"project": "plain-project", "module": "core"},
        ],
    )
    configs = {
        "git-project": {"enabled": 1, "base_branch": "release"},
        "plain-project": None,
    }
    monkeypatch.setattr(
        git_service.db_git, "get_config", lambda project_id: configs[project_id]
    )

    assert process_service.get_projects_with_modules() == [
        {
            "project": "git-project",
            "modules": ["admin", "default"],
            "git_enabled": True,
            "base_branch": "release",
        },
        {
            "project": "plain-project",
            "modules": ["core"],
            "git_enabled": False,
            "base_branch": None,
        },
    ]


def test_existing_contracts_remain_separate():
    worktree_params = inspect.signature(git_service.ensure_worktree).parameters
    resolver_params = inspect.signature(
        group_work_base.resolve_group_work_base_ref
    ).parameters
    assert "start_point" in worktree_params
    assert "start_point" not in resolver_params
    assert "default_merge_target" not in resolver_params


def _stub_outbox_workflow_root_io(monkeypatch, tmp_path, captured):
    storage_file = tmp_path / "documents" / "0001-requirement.md"
    monkeypatch.setattr(process_service.db, "get_allowed_projects", lambda: [])
    monkeypatch.setattr(
        process_service.numbering_service,
        "reserve_group",
        lambda _project, _module: "0044",
    )
    monkeypatch.setattr(
        process_service.db,
        "insert_group",
        lambda *args: captured.setdefault("group_args", args),
    )
    monkeypatch.setattr(
        process_service.db, "get_documents_by_group_id", lambda _group_id: []
    )
    monkeypatch.setattr(
        process_service.numbering_service,
        "reserve_document",
        lambda _group_id, _doc_type, module: "0001-R",
    )
    monkeypatch.setattr(
        process_service.db, "outbox_dir", lambda _project: str(tmp_path / "outbox")
    )
    monkeypatch.setattr(
        process_service.storage_paths,
        "document_path",
        lambda **_kwargs: storage_file,
    )
    monkeypatch.setattr(process_service.db, "insert_document", lambda **_kwargs: None)
    monkeypatch.setattr(
        process_service.db, "insert_event", lambda *_args, **_kwargs: None
    )
    return storage_file


def test_outbox_create_new_group_persists_validated_work_base_ref(
    repo, monkeypatch, tmp_path
):
    captured = {}
    storage_file = _stub_outbox_workflow_root_io(monkeypatch, tmp_path, captured)

    result = process_service.create_requirement(
        project="p",
        module="default",
        title="Requirement",
        slug="",
        priority="medium",
        body="body",
        new_group_name="New group",
        work_base_ref="flowgate-v0.2",
    )

    assert result["status"] == "success"
    assert result["group_id"] == "p.default.0044"
    assert captured["group_args"][-1] == "flowgate-v0.2"
    assert storage_file.exists()


def test_outbox_create_existing_group_applies_work_base_ref_when_unlocked(
    repo, tmp_path, monkeypatch
):
    """flowgate.default.0613 TR0014 rev2: the rejection removed the blanket
    "existing group = readonly" rule. Before real Git work has locked the group,
    picking a Base Branch for it is ordinary group setup."""
    storage_file = _stub_outbox_workflow_root_io(monkeypatch, tmp_path, {})
    monkeypatch.setattr(
        process_service.db, "get_group",
        lambda gid: {"group_id": gid, "project_id": "p", "module": "default"},
    )
    monkeypatch.setattr(git_service.db_git, "get_state", lambda _gid: None)
    applied = {}
    monkeypatch.setattr(
        db_groups, "update_work_base_ref",
        lambda gid, ref: applied.update(group_id=gid, work_base_ref=ref),
    )

    result = process_service.create_requirement(
        project="p",
        module="default",
        title="Requirement",
        slug="",
        priority="medium",
        body="body",
        group_id="p.default.0001",
        work_base_ref="flowgate-v0.2",
    )

    assert result["status"] == "success", result
    assert applied == {"group_id": "p.default.0001", "work_base_ref": "flowgate-v0.2"}
    assert storage_file.exists()


def test_outbox_create_existing_group_rejects_work_base_ref_once_locked(monkeypatch):
    monkeypatch.setattr(process_service.db, "get_allowed_projects", lambda: [])
    monkeypatch.setattr(
        process_service.db, "get_group",
        lambda gid: {"group_id": gid, "project_id": "p", "module": "default"},
    )
    monkeypatch.setattr(
        git_service.db_git, "get_state",
        lambda _gid: {"worktree_registered": 1},
    )
    monkeypatch.setattr(
        db_groups, "update_work_base_ref",
        lambda *_a: pytest.fail("a locked group's work base must not be written"),
    )

    result = process_service.create_requirement(
        project="p",
        module="default",
        title="Requirement",
        slug="",
        priority="medium",
        body="body",
        group_id="p.default.0001",
        work_base_ref="flowgate-v0.2",
    )

    assert result["status"] == "error"
    assert result["errors"] == [{
        "code": "group_work_base_locked",
        "message": "group work base cannot change once its Git worktree has started",
    }]


@pytest.mark.parametrize(
    ("state", "locked"),
    [
        (None, False),
        ({}, False),
        ({"worktree_registered": 0, "status": "none"}, False),
        ({"worktree_registered": 0, "status": "none", "provision_error": "boom"}, False),
        ({"worktree_registered": 1}, True),
        ({"worktree_registered": 0, "initial_source_sync_at": "2026-09-25"}, True),
        ({"worktree_registered": 0, "status": "merged"}, True),
    ],
)
def test_group_work_base_locked_reads_permanent_git_history_not_just_current_worktree(
    monkeypatch, state, locked
):
    """flowgate.default.0613 TR0014 rev2: worktree.unregister_worktree() clears
    worktree_registered on slot cleanup while status/initial_source_sync_at stay
    as permanent group history, and worktree.py's terminal-reopen path resolves
    C1 ancestry against resolve_group_work_base_ref again. A provisioning
    *failure* alone (worktree never created) must stay unlocked."""
    monkeypatch.setattr(git_service.db_git, "get_state", lambda _gid: state)
    assert group_work_base.group_work_base_locked("p", "p.default.1") is locked


@pytest.mark.parametrize(
    ("work_base_ref", "expected_code"),
    [
        ("bad..name", "group_work_base_invalid"),
        ("remote-base", "group_work_base_remote_only"),
        ("flowgate-v0.2", "group_work_base_internal"),
    ],
)
def test_outbox_create_rejects_disallowed_work_base_refs(
    repo, monkeypatch, work_base_ref, expected_code
):
    monkeypatch.setattr(process_service.db, "get_allowed_projects", lambda: [])
    if expected_code == "group_work_base_internal":
        monkeypatch.setattr(
            git_service.db_git,
            "get_state_by_branch",
            lambda _project, ref: {"group_id": "p.default.0001", "branch": ref},
        )

    result = process_service.create_requirement(
        project="p",
        module="default",
        title="Requirement",
        slug="",
        priority="medium",
        body="body",
        new_group_name="New group",
        work_base_ref=work_base_ref,
    )

    assert result["status"] == "error"
    assert result["errors"][0]["code"] == expected_code


def test_outbox_create_route_forwards_work_base_ref(monkeypatch):
    from starlette.requests import Request

    from modules.flow_gate.api.v1 import legacy_misc_routes

    captured = {}
    monkeypatch.setattr(
        legacy_misc_routes.process_service,
        "create_requirement",
        lambda **kwargs: captured.update(kwargs) or {"status": "success"},
    )
    request = Request({"type": "http", "headers": [(b"x-locale", b"ko")]})

    response = legacy_misc_routes.api_outbox_create(
        request=request,
        project="p",
        module="default",
        title="Requirement",
        slug="",
        priority="medium",
        body="",
        owner="admin",
        group_id="",
        new_group_name="New group",
        work_base_ref="flowgate-v0.2",
        doc_type="R",
        template="default",
    )

    assert response.status_code == 200
    assert captured["work_base_ref"] == "flowgate-v0.2"
    assert captured["new_group_name"] == "New group"


def test_connected_worktree_lifecycle_uses_durable_group_base(
    repo, monkeypatch, tmp_path
):
    """A-H connected Git contract: retry/restart stay pinned and mutations stay local."""
    # Give the selected source branch content absent from project main, and expose all
    # refs through a real origin so the production fetch/worktree commands run unchanged.
    assert _git(repo, "switch", "flowgate-v0.2").returncode == 0
    (repo / "selected-marker.txt").write_text("selected\n", encoding="utf-8")
    assert _git(repo, "add", "selected-marker.txt").returncode == 0
    assert _git(repo, "commit", "-m", "selected base marker").returncode == 0
    selected_at_creation = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert _git(repo, "switch", "main").returncode == 0
    main_at_creation = _git(repo, "rev-parse", "HEAD").stdout.strip()

    origin = tmp_path / "origin.git"
    assert _git(tmp_path, "init", "--bare", "-b", "main", str(origin)).returncode == 0
    assert _git(repo, "remote", "add", "origin", str(origin)).returncode == 0
    assert _git(repo, "push", "origin", "--all").returncode == 0

    project_id = "connected"
    project_name = "Connected"
    selected_group = f"{project_id}.default.0001"
    fallback_group = f"{project_id}.default.0002"
    cfg = {
        "enabled": 1,
        "base_branch": "main",
        "default_merge_target": "integration",
    }
    rows = {
        selected_group: {"group_id": selected_group, "work_base_ref": "flowgate-v0.2"},
        fallback_group: {"group_id": fallback_group, "work_base_ref": None},
    }
    states = {}
    slots = tmp_path / "slots"
    slots.mkdir()

    monkeypatch.setattr(db_groups, "get_by_id", lambda gid: rows.get(gid))
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _pid: cfg)
    monkeypatch.setattr(git_service, "_project_name", lambda _pid: project_name)
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(git_service, "_acquire_lock", lambda *_args: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda *_args: None)
    monkeypatch.setattr(git_service, "_load_secret_for", lambda _cfg: "")
    monkeypatch.setattr(
        git_service, "_provision_base_locked",
        lambda *_args: {"status": "ready", "reason": None},
    )
    monkeypatch.setattr(
        git_service, "src_root",
        lambda _name, branch: repo if branch == "main" else slots / branch,
    )
    monkeypatch.setattr(git_service.db_git, "get_state", lambda gid: states.get(gid))

    def register(gid, pid, branch):
        states[gid] = {
            "group_id": gid, "project_id": pid, "branch": branch,
            "worktree_registered": 1, "initial_source_sync_at": None,
        }

    monkeypatch.setattr(git_service.db_git, "register_worktree", register)
    monkeypatch.setattr(git_service.db_git, "clear_provision_failure", lambda _gid: None)
    monkeypatch.setattr(git_service, "_fail_worktree", lambda *_args: None)
    monkeypatch.setattr(git_service, "_emit", lambda *_args: None)
    monkeypatch.setattr(git_service, "_emit_worktree_ready", lambda *_args, **_kwargs: None)

    # H1 can fail before creation; the later H2/source-access retry resolves the same
    # durable row and starts from the selected branch, never from request-local state.
    attempts = {"count": 0}

    def provision(*_args):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return {"status": "failed", "reason": "injected"}
        return {"status": "ready", "reason": None}

    monkeypatch.setattr(git_service, "_provision_base_locked", provision)
    assert git_service.ensure_worktree(project_id, "default", selected_group) == "failed"
    assert git_service.ensure_worktree(project_id, "default", selected_group) == "ok"
    selected_branch = git_service.worktree_branch_name(project_id, "default", selected_group)
    selected_wt = slots / selected_branch
    assert (selected_wt / "selected-marker.txt").read_text(encoding="utf-8") == "selected\n"
    assert _git(selected_wt, "rev-parse", "HEAD").stdout.strip() == selected_at_creation
    assert _git(selected_wt, "rev-parse", "HEAD").stdout.strip() != main_at_creation

    # Moving the source branch later cannot move an existing group.  The first-source
    # clean/reset also targets the group's own frozen HEAD rather than the moving source.
    assert _git(repo, "switch", "flowgate-v0.2").returncode == 0
    (repo / "later-source.txt").write_text("later\n", encoding="utf-8")
    assert _git(repo, "add", "later-source.txt").returncode == 0
    assert _git(repo, "commit", "-m", "advance selected source").returncode == 0
    assert _git(repo, "switch", "main").returncode == 0
    monkeypatch.setattr(
        worktree_service.db_tr_ledger, "commit_rows_by_group", lambda _gid: [],
    )
    monkeypatch.setattr(
        git_service.db_git,
        "set_initial_source_sync",
        lambda gid, sha: states[gid].update(initial_source_sync_at="now", initial_source_sync_sha=sha),
    )
    sync = git_service.ensure_initial_group_source_sync(
        project_id, "default", selected_group
    )
    assert sync == {"performed": True, "reason": "ok", "sha": selected_at_creation}
    assert not (selected_wt / "later-source.txt").exists()

    # Simulated restart/reprovision: delete only the registered worktree; the retained
    # group branch is reattached at its frozen commit, independent of the moved source.
    assert _git(repo, "worktree", "remove", "--force", str(selected_wt)).returncode == 0
    states[selected_group]["worktree_registered"] = 1
    assert git_service.ensure_worktree(project_id, "default", selected_group) == "ok"
    assert _git(selected_wt, "rev-parse", "HEAD").stdout.strip() == selected_at_creation
    assert not (selected_wt / "later-source.txt").exists()

    # Representative mutation is confined to the group worktree.  Neither project main,
    # the selected source branch, nor the independent finalize target is touched.
    (selected_wt / "mutation.txt").write_text("group only\n", encoding="utf-8")
    assert not (repo / "mutation.txt").exists()
    assert _git(repo, "show", "flowgate-v0.2:mutation.txt").returncode != 0
    assert cfg["default_merge_target"] == "integration"
    assert rows[selected_group]["work_base_ref"] == "flowgate-v0.2"

    # Legacy/main fallback is still main, and Git-disabled projects remain a no-op.
    assert git_service.ensure_worktree(project_id, "default", fallback_group) == "ok"
    fallback_branch = git_service.worktree_branch_name(project_id, "default", fallback_group)
    fallback_wt = slots / fallback_branch
    assert _git(fallback_wt, "rev-parse", "HEAD").stdout.strip() == main_at_creation
    assert not (fallback_wt / "selected-marker.txt").exists()
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _pid: {"enabled": 0})
    assert git_service.ensure_worktree("plain", "default", "plain.default.0001") == "skipped"


def test_h1_async_and_h2_share_the_same_durable_resolver(monkeypatch):
    calls = []
    monkeypatch.setattr(
        git_service, "ensure_worktree",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "ok",
    )

    class ImmediateThread:
        def __init__(self, *, target, args, daemon):
            self.target, self.args, self.daemon = target, args, daemon

        def start(self):
            self.target(*self.args)

    import threading
    monkeypatch.setattr(threading, "Thread", ImmediateThread)
    git_service.ensure_worktree_async("p", "default", "p.default.1")
    git_service.ensure_worktree("p", "default", "p.default.1", "remote_access")
    assert calls == [
        (("p", "default", "p.default.1", "workflow_decide"), {}),
        (("p", "default", "p.default.1", "remote_access"), {}),
    ]

# ── T0013 follow-ups: migration ordinal, Base Branch delete guard, options boundary ──

_MIGRATIONS = Path(__file__).resolve().parents[1] / "sql" / "migrations"
_DIALECTS = ("sqlite", "postgres", "mysql")


def test_work_base_migration_owns_ordinal_119_in_every_dialect():
    """T0013 §1: 118 is held by the unmerged sibling 0517 (118_snapshot_lineage.sql).

    The work-base migration therefore takes the next ordinal free on both sides, with
    one identical file name per dialect, and the ledger carries any database that
    already applied the branch under its old 118 name.
    """
    from modules.flow_gate.db.migration_renames import RENAMES

    for dialect in _DIALECTS:
        directory = _MIGRATIONS / dialect
        assert not (directory / "118_group_work_base_ref.sql").exists()
        assert (directory / "119_group_work_base_ref.sql").is_file()
        assert sorted(p.name for p in directory.glob("119*_*.sql")) == [
            "119_group_work_base_ref.sql"
        ]
        assert not [p.name for p in directory.glob("118*_*.sql")], (
            f"{dialect}: 118 belongs to flowgate.default.0517's snapshot lineage"
        )
    assert ("118_group_work_base_ref.sql", "119_group_work_base_ref.sql") in RENAMES


def test_work_base_rename_carries_an_already_applied_ledger(tmp_path):
    """A preview/dev database that ran this branch under 118 must skip 119 on reboot."""
    from modules.flow_gate.db.migration_renames import apply_migration_renames

    db_path = tmp_path / "applied-118.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE migrations (filename TEXT PRIMARY KEY, applied_at TEXT)")
        conn.execute(
            "INSERT INTO migrations VALUES ('118_group_work_base_ref.sql', '2026-09-25')"
        )
    apply_migration_renames("sqlite3", sqlite_path=str(db_path))
    with sqlite3.connect(db_path) as conn:
        names = {row[0] for row in conn.execute("SELECT filename FROM migrations")}
    assert names == {"119_group_work_base_ref.sql"}


class _SqliteGroupStore:
    """Just enough of the store for db_groups' read helpers, over a migrated schema."""

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def _fetch_all(self, sql, params):
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def add_group(self, group_id, project_id, work_base_ref, status="OPEN", deleted_at=None):
        self.conn.execute(
            "INSERT INTO groups (group_id, project_id, module, title, status, created_at, "
            "updated_at, work_base_ref, deleted_at) VALUES (?, ?, 'default', ?, ?, "
            "'2026-09-25', '2026-09-25', ?, ?)",
            [group_id, project_id, group_id, status, work_base_ref, deleted_at],
        )
        self.conn.commit()

    def set_status(self, group_id, status):
        self.conn.execute("UPDATE groups SET status = ? WHERE group_id = ?", [status, group_id])
        self.conn.commit()


@pytest.fixture
def group_store(migrated_sqlite_db, monkeypatch):
    store = _SqliteGroupStore(migrated_sqlite_db("group-work-base-guard-0613.db"))
    monkeypatch.setattr(db_groups, "get_store", lambda: store)
    yield store
    store.conn.close()


@pytest.fixture
def branch_repo(tmp_path, monkeypatch):
    from modules.flow_gate.services.git import branches as branch_service

    root = tmp_path / "branch-repo"
    root.mkdir()
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    (root / "README.md").write_text("base\n", encoding="utf-8")
    assert _git(root, "add", "README.md").returncode == 0
    assert _git(root, "commit", "-m", "base").returncode == 0
    assert _git(root, "branch", "release-base").returncode == 0

    cfg = {"enabled": 1, "base_branch": "main"}
    monkeypatch.setattr(branch_service, "_branch_context", lambda _pid: (cfg, root, "main"))
    monkeypatch.setattr(git_service, "_base_root_of", lambda _pid: root)
    monkeypatch.setattr(git_service, "_acquire_lock", lambda _pid, _holder: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda _pid, _holder: None)
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda _pid: [])
    monkeypatch.setattr(git_service.db_git, "list_open_sessions", lambda: [])
    return root


def test_delete_is_blocked_while_an_active_group_pins_the_branch_as_base(
    branch_repo, group_store,
):
    """T0013 §2 minimum regression, steps 1-5."""
    # 1-2. an ordinary local branch stored as an OPEN group's Base Branch.
    group_store.add_group("p.default.0001", "p", "release-base")

    # 3-4. every delete surface fails closed with a 409 "referenced" code.
    assert git_service.check_branch_delete("p", "release-base") == "branch_is_group_work_base"
    row = next(
        b for b in git_service.list_branches("p")["branches"] if b["name"] == "release-base"
    )
    assert row["can_delete"] is False
    assert row["delete_blocked_reason"] == "branch_is_group_work_base"
    with pytest.raises(GitServiceError) as caught:
        git_service.delete_branch("p", "release-base")
    assert caught.value.status == 409
    assert caught.value.code == "branch_is_group_work_base"
    assert caught.value.details == {"connected_group_ids": ["p.default.0001"]}
    assert _git(branch_repo, "show-ref", "--verify", "refs/heads/release-base").returncode == 0

    # 5. once the group's lifecycle ends the ordinary delete policy applies again.
    group_store.set_status("p.default.0001", "CLOSED")
    assert git_service.check_branch_delete("p", "release-base") is None
    assert git_service.delete_branch("p", "release-base")["deleted"] is True
    assert _git(branch_repo, "show-ref", "--verify", "refs/heads/release-base").returncode != 0


def test_only_live_groups_of_the_same_project_pin_a_branch(branch_repo, group_store):
    group_store.add_group("other.default.0001", "other", "release-base")
    group_store.add_group("p.default.0001", "p", "release-base", status="CANCELLED")
    group_store.add_group("p.default.0002", "p", "release-base", status="closed")
    group_store.add_group("p.default.0003", "p", "release-base", deleted_at="2026-09-25")
    group_store.add_group("p.default.0004", "p", None)   # legacy: follows the project base
    assert group_work_base.groups_pinning_work_base("p", "release-base") == []
    assert git_service.check_branch_delete("p", "release-base") is None

    group_store.add_group("p.default.0005", "p", "release-base", status="in_progress")
    group_store.add_group("p.default.0006", "p", "release-base")
    assert group_work_base.groups_pinning_work_base("p", "release-base") == [
        "p.default.0005", "p.default.0006",
    ]


def test_existing_delete_guards_keep_their_codes_and_order(branch_repo, group_store):
    # project base keeps branch_is_base even when a group also pins it.
    group_store.add_group("p.default.0001", "p", "main")
    assert git_service.check_branch_delete("p", "main") == "branch_is_base"

    # unmerged-commit protection is untouched once the pin is released.
    assert _git(branch_repo, "checkout", "-q", "release-base").returncode == 0
    (branch_repo / "extra.txt").write_text("x\n", encoding="utf-8")
    assert _git(branch_repo, "add", "extra.txt").returncode == 0
    assert _git(branch_repo, "commit", "-q", "-m", "extra").returncode == 0
    assert _git(branch_repo, "checkout", "-q", "main").returncode == 0
    group_store.add_group("p.default.0002", "p", "release-base", status="CLOSED")
    with pytest.raises(GitServiceError) as caught:
        git_service.delete_branch("p", "release-base")
    assert caught.value.code == "branch_unmerged_commits"


def test_work_base_options_list_exactly_what_group_creation_accepts(repo, monkeypatch):
    """T0013 §3: the requirement dialog's options are the validator's accepted set."""
    _git(repo, "branch", "slot-owned")
    monkeypatch.setattr(
        git_service.db_git, "get_config", lambda _pid: {"enabled": 1, "base_branch": "main"}
    )
    monkeypatch.setattr(
        git_service.db_git, "get_state_by_branch",
        lambda _pid, ref: {"group_id": "p.default.9"} if ref == "slot-owned" else None,
    )

    result = git_service.list_group_work_base_options("p")
    assert result == {
        "ok": True,
        "git_enabled": True,
        "base_branch": "main",
        "branches": [
            {"name": "flowgate-v0.2", "is_project_base": False},
            {"name": "main", "is_project_base": True},
        ],
    }
    for row in result["branches"]:
        assert git_service.validate_group_work_base_ref("p", row["name"]) == row["name"]
    for rejected in ("slot-owned", "remote-base"):
        with pytest.raises(GitServiceError):
            git_service.validate_group_work_base_ref("p", rejected)


def test_work_base_options_are_empty_without_git(monkeypatch):
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _pid: {"enabled": 0})
    monkeypatch.setattr(
        git_service, "_run_git", lambda *a, **k: pytest.fail("no Git call without integration")
    )
    assert git_service.list_group_work_base_options("plain") == {
        "ok": True, "git_enabled": False, "base_branch": None, "branches": [],
    }
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _pid: None)
    assert git_service.list_group_work_base_options("plain")["branches"] == []


def _git_routes_client(user: dict):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.auth.middleware import get_current_user

    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def test_requirement_author_reads_options_but_not_the_branch_manager_catalog(monkeypatch):
    """The defect: a worker-role author (perm_document_create, no project.settings.read)
    got 403 from /git/branches and the Git-project dialog could not create at all."""
    from modules.flow_gate.api.v1 import git_routes

    granted = {("p", "perm_document_create")}
    checked = []

    def has_permission(user_id, project_id, permission):
        checked.append((user_id, project_id, permission))
        return (project_id, permission) in granted

    monkeypatch.setattr(
        "modules.flow_gate.rbac.decorators._permission_service.has_permission", has_permission
    )
    monkeypatch.setattr(
        git_routes.git_service, "list_group_work_base_options",
        lambda pid: {"ok": True, "git_enabled": True, "base_branch": "main",
                     "branches": [{"name": "main", "is_project_base": True}], "pid": pid},
    )
    monkeypatch.setattr(
        git_routes.git_service, "list_branches",
        lambda pid: pytest.fail("catalog must stay behind project.settings.read"),
    )
    client = _git_routes_client({"user_id": "worker", "is_admin": False})

    ok = client.get("/api/v1/projects/p/git/work-base-options")
    assert ok.status_code == 200
    assert ok.json()["pid"] == "p"
    assert ("worker", "p", "perm_document_create") in checked
    assert client.get("/api/v1/projects/p/git/branches").status_code == 403
    # an unrelated project where the user holds no role exposes nothing.
    assert client.get("/api/v1/projects/secret/git/work-base-options").status_code == 403


def test_seeded_author_role_contract_matches_the_options_permission(migrated_sqlite_db):
    """role_worker is the seeded requirement author: it creates documents but does not
    read project settings, which is exactly why the dialog cannot use the catalog."""
    with sqlite3.connect(migrated_sqlite_db("rbac-seed-0613.db")) as conn:
        worker = {
            row[0] for row in conn.execute(
                "SELECT permission_id FROM role_permissions WHERE role_id = 'role_worker'"
            )
        }
    assert "perm_document_create" in worker
    assert "project.settings.read" not in worker
    assert "perm_project_settings_read" not in worker
