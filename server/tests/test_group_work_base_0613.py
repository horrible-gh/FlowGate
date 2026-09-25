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
    result = workflow.list_groups_endpoint(project_id="p", current_user={})
    assert [row["effective_work_base_ref"] for row in result["groups"]] == [
        "feature", "main"
    ]


def test_migration_is_additive_and_present_for_all_dialects(migrated_sqlite_db):
    db_path = migrated_sqlite_db("group-work-base-0613.db")
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(groups)")}
    assert "work_base_ref" in columns

    root = Path(__file__).resolve().parents[1] / "sql" / "migrations"
    for dialect in ("sqlite", "postgres", "mysql"):
        text = (root / dialect / "118_group_work_base_ref.sql").read_text(encoding="utf-8")
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


def test_outbox_create_existing_group_rejects_work_base_ref(monkeypatch):
    monkeypatch.setattr(process_service.db, "get_allowed_projects", lambda: [])
    monkeypatch.setattr(
        git_service,
        "validate_group_work_base_ref",
        lambda *_args: pytest.fail("an existing group ref must be rejected before validation"),
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
        "code": "group_work_base_existing_group",
        "message": "work_base_ref is only accepted when creating a new group",
    }]


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