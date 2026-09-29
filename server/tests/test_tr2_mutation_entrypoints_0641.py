"""flowgate.default.0641 T2#2 — behavior tests for every ordinary mutation entrypoint."""
from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from modules.flow_gate import process_service
from modules.flow_gate.api import inbox_routes
from modules.flow_gate.api.v1 import file_transfer_routes, tree_routes
from modules.flow_gate.documents.attachments import service as attachment_service
from modules.flow_gate.services import git_service
from modules.flow_gate.services import remote_tool_service
from modules.flow_gate.services import tr2_file_policy as policy


PROJECT = "flowgate"
GROUP_A = "flowgate.default.0641"
GROUP_B = "flowgate.default.0641-other"


def _policy_error(code: str, path: str = "a.txt"):
    return policy.Tr2FilePolicyError(code, path, details={"required_mutation": "TR2"})


def _guard_factory(root: Path, *, managed_by_group=None, alias_paths=None, calls=None):
    managed_by_group = managed_by_group or {}
    alias_paths = set(alias_paths or ())
    calls = calls if calls is not None else []

    @contextmanager
    def guard(project_id, group_id, *, exact_paths=(), recursive_paths=(), allow_missing_leaf=False):
        exact = list(exact_paths)
        recursive = list(recursive_paths)
        calls.append({
            "project_id": project_id, "group_id": group_id,
            "exact": exact, "recursive": recursive,
            "allow_missing_leaf": allow_missing_leaf,
        })
        owned = set(managed_by_group.get(group_id, set()))
        for rel in [*exact, *recursive]:
            normalized = rel.replace("\\", "/")
            if normalized in alias_paths:
                raise _policy_error(policy.SOURCE_PATH_ALIAS_NOT_ALLOWED, normalized)
        for rel in exact:
            normalized = rel.replace("\\", "/")
            if normalized in owned:
                raise _policy_error(policy.TR2_MANAGED_FILE, normalized)
        for rel in recursive:
            normalized = rel.replace("\\", "/").rstrip("/")
            prefix = normalized + "/"
            hit = next((p for p in owned if p == normalized or p.startswith(prefix)), None)
            if hit:
                raise _policy_error(policy.TR2_MANAGED_FILE, hit)
        yield SimpleNamespace(
            project_id=project_id,
            group_id=group_id,
            root=root,
            holder="test-holder",
            exact_targets=tuple(
                (p.replace("\\", "/"), root.joinpath(*p.replace("\\", "/").split("/")))
                for p in exact
            ),
            recursive_targets=tuple(
                (p.replace("\\", "/"), root.joinpath(*p.replace("\\", "/").split("/")))
                for p in recursive
            ),
        )

    return guard


def test_direct_edit_managed_reject_and_unmanaged_success(monkeypatch, tmp_path):
    root = tmp_path / "wt"
    root.mkdir()
    target = root / "a.txt"
    target.write_text("before", encoding="utf-8")
    monkeypatch.setattr(
        policy, "general_source_mutation",
        _guard_factory(root, managed_by_group={GROUP_A: {"a.txt"}}),
    )

    with pytest.raises(HTTPException) as blocked:
        tree_routes.update_src_file_content(
            PROJECT, tree_routes.SrcContentUpdate(content="blocked"),
            path="a.txt", group_id=GROUP_A,
        )
    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == policy.TR2_MANAGED_FILE
    assert target.read_text(encoding="utf-8") == "before"

    monkeypatch.setattr(policy, "general_source_mutation", _guard_factory(root))
    result = tree_routes.update_src_file_content(
        PROJECT, tree_routes.SrcContentUpdate(content="after"),
        path="a.txt", group_id=GROUP_A,
    )
    assert result["group_id"] == GROUP_A
    assert target.read_text(encoding="utf-8") == "after"


def _enable_delete_auth(monkeypatch):
    monkeypatch.setattr(tree_routes, "_check_project_auth", lambda *_: {"issued_to": "u"})
    monkeypatch.setattr(tree_routes, "has_permission", lambda *_: True)


def test_file_and_folder_delete_managed_reject(monkeypatch, tmp_path):
    root = tmp_path / "wt"
    (root / "folder").mkdir(parents=True)
    (root / "file.txt").write_text("x", encoding="utf-8")
    (root / "folder" / "owned.txt").write_text("x", encoding="utf-8")
    _enable_delete_auth(monkeypatch)
    monkeypatch.setattr(
        policy, "general_source_mutation",
        _guard_factory(root, managed_by_group={GROUP_A: {"file.txt", "folder/owned.txt"}}),
    )

    file_response = tree_routes.delete_src_path(
        object(), PROJECT,
        tree_routes.SrcDeleteRequest(path="file.txt", type="file", group_id=GROUP_A),
    )
    assert file_response.status_code == 409
    assert json.loads(file_response.body)["error"]["code"] == policy.TR2_MANAGED_FILE
    assert (root / "file.txt").exists()

    folder_response = tree_routes.delete_src_path(
        object(), PROJECT,
        tree_routes.SrcDeleteRequest(path="folder", type="folder", group_id=GROUP_A),
    )
    assert folder_response.status_code == 409
    assert json.loads(folder_response.body)["error"]["code"] == policy.TR2_MANAGED_FILE
    assert (root / "folder" / "owned.txt").exists()


class _Upload:
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data

    async def read(self):
        return self._data


class _Form:
    def __init__(self, values, files):
        self.values = values
        self.files = files

    def get(self, key, default=None):
        return self.values.get(key, default)

    def getlist(self, key):
        return list(self.files) if key == "files[]" else []


class _Request:
    def __init__(self, form):
        self._form = form
        self.headers = {}

    async def form(self):
        return self._form


def test_upload_batch_managed_rejects_before_first_write(monkeypatch, tmp_path):
    root = tmp_path / "wt"
    root.mkdir()
    writes = []
    monkeypatch.setattr(file_transfer_routes, "_check_project_auth", lambda *_: {"issued_to": "u"})
    monkeypatch.setattr(file_transfer_routes, "_save_file", lambda path, data: writes.append(path))
    monkeypatch.setattr(
        policy, "general_source_mutation",
        _guard_factory(root, managed_by_group={GROUP_A: {"managed.txt"}}),
    )
    request = _Request(_Form(
        {"group_id": GROUP_A, "target_path": ""},
        [_Upload("free.txt", b"free"), _Upload("managed.txt", b"managed")],
    ))

    response = asyncio.run(file_transfer_routes.upload_files(request, PROJECT))
    assert response.status_code == 409
    assert json.loads(response.body)["error"]["code"] == policy.TR2_MANAGED_FILE
    assert writes == []
    assert not (root / "free.txt").exists()


def test_deleted_managed_recreate_reject_and_other_group_same_path_allowed(monkeypatch, tmp_path):
    root = tmp_path / "wt"
    root.mkdir()
    target = root / "gone.txt"
    monkeypatch.setattr(
        process_service, "_storage_create_target",
        lambda project_id, parent, name, group_id: (root / name, None),
    )
    monkeypatch.setattr(
        policy, "general_source_mutation",
        _guard_factory(root, managed_by_group={GROUP_A: {"gone.txt"}}),
    )
    blocked = process_service.create_storage_file(PROJECT, "", "gone.txt", group_id=GROUP_A)
    assert blocked["status"] == "error"
    assert blocked["code"] == policy.TR2_MANAGED_FILE
    assert not target.exists()

    allowed = process_service.create_storage_file(PROJECT, "", "gone.txt", group_id=GROUP_B)
    assert allowed == {"status": "success"}
    assert target.exists()


def test_create_file_managed_reject_and_unmanaged_success(monkeypatch, tmp_path):
    root = tmp_path / "wt"
    root.mkdir()
    monkeypatch.setattr(
        process_service, "_storage_create_target",
        lambda project_id, parent, name, group_id: (root / name, None),
    )
    monkeypatch.setattr(
        policy, "general_source_mutation",
        _guard_factory(root, managed_by_group={GROUP_A: {"owned.txt"}}),
    )
    blocked = process_service.create_storage_file(PROJECT, "", "owned.txt", group_id=GROUP_A)
    assert blocked["code"] == policy.TR2_MANAGED_FILE
    assert not (root / "owned.txt").exists()

    monkeypatch.setattr(policy, "general_source_mutation", _guard_factory(root))
    assert process_service.create_storage_file(
        PROJECT, "", "free.txt", group_id=GROUP_A
    ) == {"status": "success"}
    assert (root / "free.txt").is_file()


def test_deleted_restore_managed_reject_and_unmanaged_success(monkeypatch, tmp_path):
    root = tmp_path / "wt"
    (root / "docs").mkdir(parents=True)
    target = root / "docs" / "gone.txt"
    monkeypatch.setattr(inbox_routes, "_group_path_is_deleted", lambda *_: True)

    monkeypatch.setattr(
        policy, "general_source_mutation",
        _guard_factory(root, managed_by_group={GROUP_A: {"docs/gone.txt"}}),
    )
    with pytest.raises(HTTPException) as blocked:
        inbox_routes.restore_deleted_group_file(
            PROJECT, GROUP_A, inbox_routes.DeletedGroupFileRestore(path="docs/gone.txt"),
            user={"user_id": "u"},
        )
    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == policy.TR2_MANAGED_FILE
    assert not target.exists()

    monkeypatch.setattr(policy, "general_source_mutation", _guard_factory(root))

    def checkout(args, cwd):
        target.write_text("restored", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(git_service, "_run_git", checkout)
    monkeypatch.setattr(inbox_routes, "_emit_source_edit_refresh", lambda *_: None)
    result = inbox_routes.restore_deleted_group_file(
        PROJECT, GROUP_A, inbox_routes.DeletedGroupFileRestore(path="docs/gone.txt"),
        user={"user_id": "u"},
    )
    assert result["data"]["restored"] is True
    assert target.read_text(encoding="utf-8") == "restored"


def _attachment_setup(monkeypatch, tmp_path):
    root = tmp_path / "wt"
    root.mkdir(exist_ok=True)
    source = tmp_path / "attachment.bin"
    source.write_bytes(b"payload")
    row = {
        "filename": "attachment.bin",
        "file_path": "storage/attachment.bin",
        "content_sha256": hashlib.sha256(b"payload").hexdigest(),
    }
    monkeypatch.setattr(
        attachment_service, "load_document",
        lambda _doc: {"doc_id": "d", "project_id": PROJECT, "group_id": GROUP_A},
    )
    monkeypatch.setattr(attachment_service, "assert_mutable", lambda *_: None)
    monkeypatch.setattr(
        attachment_service, "resolve_registered_attachment",
        lambda *_: (row, source),
    )
    return root


def test_attachment_copy_managed_reject_and_unmanaged_success(monkeypatch, tmp_path):
    root = _attachment_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(
        policy, "general_source_mutation",
        _guard_factory(root, managed_by_group={GROUP_A: {"copied.bin"}}),
    )
    with pytest.raises(attachment_service.AttachmentError) as blocked:
        attachment_service.copy_to_source(
            "d", "attachment.bin", "copied.bin", GROUP_A, {"user_id": "u"}
        )
    assert blocked.value.code == policy.TR2_MANAGED_FILE
    assert not (root / "copied.bin").exists()

    monkeypatch.setattr(policy, "general_source_mutation", _guard_factory(root))
    result = attachment_service.copy_to_source(
        "d", "attachment.bin", "copied.bin", GROUP_A, {"user_id": "u"}
    )
    assert result["destination"]["target_path"] == "copied.bin"
    assert (root / "copied.bin").read_bytes() == b"payload"


def _remote_common(monkeypatch, tmp_path, *, git_enabled=True):
    root = tmp_path / "remote"
    root.mkdir(exist_ok=True)
    grant = {
        "grant_id": "g1",
        "project": PROJECT,
        "group_id": GROUP_A,
        "module": "default",
    }
    monkeypatch.setattr(remote_tool_service, "_authenticate", lambda _token: grant)
    monkeypatch.setattr(remote_tool_service, "_locale_for_grant", lambda _grant: "en")
    monkeypatch.setattr(
        remote_tool_service.db_grants, "get_scopes",
        lambda _grant_id: {"read", "write", "remove", "grep"},
    )
    monkeypatch.setattr(remote_tool_service, "_validate_paths", lambda *_: None)
    monkeypatch.setattr(remote_tool_service, "_validate_allowed_fields", lambda *_: None)
    monkeypatch.setattr(remote_tool_service, "_validate_required", lambda *_: None)
    monkeypatch.setattr(remote_tool_service, "_log", lambda *_a, **_k: None)
    monkeypatch.setattr(remote_tool_service, "_emit_explorer_refresh", lambda *_: None)
    monkeypatch.setattr(remote_tool_service, "_continuation", lambda *_: {})
    monkeypatch.setattr(remote_tool_service, "_resolve_root_for_mutation", lambda *_: root)
    monkeypatch.setattr(remote_tool_service, "_resolve_src_root", lambda *_: root)

    from modules.flow_gate.db import git_integration as db_git
    monkeypatch.setattr(
        db_git, "get_config", lambda _project: {"enabled": 1} if git_enabled else {"enabled": 0}
    )
    return root


@pytest.mark.parametrize("op", ["write", "patch", "remove"])
def test_ai_write_patch_remove_managed_reject_with_top_level_policy_code(
    monkeypatch, tmp_path, op
):
    root = _remote_common(monkeypatch, tmp_path, git_enabled=True)
    monkeypatch.setattr(
        policy, "general_source_mutation",
        _guard_factory(root, managed_by_group={GROUP_A: {"owned.txt"}}),
    )
    monkeypatch.setattr(
        remote_tool_service, "_execute",
        lambda *_: pytest.fail("managed mutation reached executor"),
    )

    status, payload = remote_tool_service.handle(op, "token", {"path": "owned.txt"})
    assert status == 409
    assert payload["error"]["code"] == policy.TR2_MANAGED_FILE
    assert payload["error"]["details"]["code"] == policy.TR2_MANAGED_FILE


def test_ai_alias_reject_has_distinct_top_level_code(monkeypatch, tmp_path):
    root = _remote_common(monkeypatch, tmp_path, git_enabled=True)
    monkeypatch.setattr(
        policy, "general_source_mutation",
        _guard_factory(root, alias_paths={"alias.txt"}),
    )
    monkeypatch.setattr(remote_tool_service, "_execute", lambda *_: pytest.fail("alias executed"))

    status, payload = remote_tool_service.handle(
        "write", "token", {"path": "alias.txt", "content": "x"}
    )
    assert status == 422
    assert payload["error"]["code"] == policy.SOURCE_PATH_ALIAS_NOT_ALLOWED
    assert payload["error"]["code"] != policy.TR2_MANAGED_FILE


@pytest.mark.parametrize("op", ["read", "grep", "glob", "stat"])
def test_ai_read_family_remains_allowed(monkeypatch, tmp_path, op):
    root = _remote_common(monkeypatch, tmp_path, git_enabled=True)
    calls = []

    def must_not_guard(*_a, **_k):
        pytest.fail("read-only operation entered mutation guard")

    monkeypatch.setattr(policy, "general_source_mutation", must_not_guard)
    monkeypatch.setattr(
        remote_tool_service, "_execute",
        lambda seen_op, body, seen_root, grant: (
            calls.append((seen_op, seen_root)) or {"allowed": True}, None
        ),
    )
    status, payload = remote_tool_service.handle(op, "token", {"path": "a.txt"})
    assert status == 200
    assert payload["allowed"] is True
    assert calls == [(op, root)]


def test_non_git_remote_group_mutation_keeps_existing_base_fallback(monkeypatch, tmp_path):
    root = _remote_common(monkeypatch, tmp_path, git_enabled=False)
    calls = []

    def must_not_guard(*_a, **_k):
        pytest.fail("non-Git fallback entered TR2 ownership guard")

    monkeypatch.setattr(policy, "general_source_mutation", must_not_guard)
    monkeypatch.setattr(
        remote_tool_service, "_execute",
        lambda op, body, seen_root, grant: (
            calls.append((op, seen_root)) or {"path": body["path"], "ok_write": True}, 1
        ),
    )

    status, payload = remote_tool_service.handle(
        "write", "token", {"path": "legacy.txt", "content": "x"}
    )
    assert status == 200
    assert payload["ok_write"] is True
    assert calls == [("write", root)]


def test_generic_remote_409_keeps_legacy_conflict_code(monkeypatch, tmp_path):
    _remote_common(monkeypatch, tmp_path, git_enabled=False)
    monkeypatch.setattr(
        remote_tool_service, "_execute",
        lambda *_: (_ for _ in ()).throw(
            remote_tool_service._OpError(409, details={"reason": "ordinary_conflict"})
        ),
    )
    status, payload = remote_tool_service.handle(
        "write", "token", {"path": "x.txt", "content": "x"}
    )
    assert status == 409
    assert payload["error"]["code"] == "conflict"


def test_file_and_folder_delete_unmanaged_success(monkeypatch, tmp_path):
    root = tmp_path / "wt-delete-ok"
    (root / "folder").mkdir(parents=True)
    file_target = root / "file.txt"
    folder_target = root / "folder"
    file_target.write_text("file", encoding="utf-8")
    (folder_target / "child.txt").write_text("child", encoding="utf-8")

    _enable_delete_auth(monkeypatch)
    monkeypatch.setattr(policy, "general_source_mutation", _guard_factory(root))
    monkeypatch.setattr(
        git_service, "base_checkout_dirty_status", lambda _project_id: {"dirty": False}
    )

    file_result = tree_routes.delete_src_path(
        object(), PROJECT,
        tree_routes.SrcDeleteRequest(path="file.txt", type="file", group_id=GROUP_A),
    )
    assert file_result["deleted"] == "file.txt"
    assert file_result["type"] == "file"
    assert not file_target.exists()

    folder_result = tree_routes.delete_src_path(
        object(), PROJECT,
        tree_routes.SrcDeleteRequest(path="folder", type="folder", group_id=GROUP_A),
    )
    assert folder_result["deleted"] == "folder"
    assert folder_result["type"] == "folder"
    assert not folder_target.exists()


def test_upload_unmanaged_success_writes_destination_bytes(monkeypatch, tmp_path):
    root = tmp_path / "wt-upload-ok"
    root.mkdir()
    monkeypatch.setattr(file_transfer_routes, "_check_project_auth", lambda *_: {"issued_to": "u"})
    monkeypatch.setattr(policy, "general_source_mutation", _guard_factory(root))

    request = _Request(_Form(
        {"group_id": GROUP_A, "target_path": "nested"},
        [_Upload("one.txt", b"one"), _Upload("two.bin", b"\x00\x01two")],
    ))
    result = asyncio.run(file_transfer_routes.upload_files(request, PROJECT))

    assert result["skipped"] == []
    assert result["uploaded"] == [
        {"path": "nested/one.txt", "size": 3},
        {"path": "nested/two.bin", "size": 5},
    ]
    assert (root / "nested" / "one.txt").read_bytes() == b"one"
    assert (root / "nested" / "two.bin").read_bytes() == b"\x00\x01two"


def test_ai_git_integrated_unmanaged_write_patch_remove_success(monkeypatch, tmp_path):
    root = _remote_common(monkeypatch, tmp_path, git_enabled=True)
    guard_calls = []
    monkeypatch.setattr(
        policy,
        "general_source_mutation",
        _guard_factory(root, calls=guard_calls),
    )

    status, payload = remote_tool_service.handle(
        "write",
        "token",
        {"path": "ai.txt", "content": "before", "mode": "overwrite", "encoding": "utf-8"},
    )
    assert status == 200
    assert payload["ok"] is True
    assert (root / "ai.txt").read_text(encoding="utf-8") == "before"

    status, payload = remote_tool_service.handle(
        "patch",
        "token",
        {
            "path": "ai.txt",
            "old_string": "before",
            "new_string": "after",
            "replace_all": False,
            "encoding": "utf-8",
        },
    )
    assert status == 200
    assert payload["ok"] is True
    assert (root / "ai.txt").read_text(encoding="utf-8") == "after"

    status, payload = remote_tool_service.handle(
        "remove",
        "token",
        {"path": "ai.txt", "recursive": False},
    )
    assert status == 200
    assert payload["ok"] is True
    assert not (root / "ai.txt").exists()

    assert [call["exact"] for call in guard_calls] == [
        ["ai.txt"],
        ["ai.txt"],
        ["ai.txt"],
    ]
