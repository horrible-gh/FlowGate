"""TR-1 registry policy and localized catalog tests for group 0349."""
import pytest

from modules.flow_gate import template_provision
from modules.flow_gate.services import remote_tool_service, tool_registry
from modules.flow_gate.settings import source_mode_service


@pytest.fixture(autouse=True)
def remote_mode(monkeypatch):
    monkeypatch.setattr(source_mode_service, "resolve_effective_mode", lambda _project: "remote")


@pytest.mark.parametrize("action_scope", ["new", "edit"])
@pytest.mark.parametrize("step_type", ["TR", "TSR"])
def test_kind_read_write_for_new_or_edit_tr_tsr(monkeypatch, action_scope, step_type):
    monkeypatch.setattr(
        remote_tool_service,
        "_worker_token_step_type_result",
        lambda _rec: (step_type, False),
    )
    result = tool_registry.resolve_registry(
        {"action_scope": action_scope, "doc_ref": "d1"}, "flowgate", "ko"
    )
    assert result["kind"] == "read_write"
    assert result["reason"] is None
    assert [item["name"] for item in result["tools"]] == [
        "read", "grep", "glob", "stat", "write", "patch", "remove"
    ]


@pytest.mark.parametrize("action_scope", ["new", "edit"])
def test_kind_read_only_for_t_after_write_recall(monkeypatch, action_scope):
    """0427 T0004: T lost write/patch/remove -- it now advertises the same
    read-only catalog as an investigation-only step (N/NR/D/DS/...)."""
    monkeypatch.setattr(
        remote_tool_service,
        "_worker_token_step_type_result",
        lambda _rec: ("T", False),
    )
    result = tool_registry.resolve_registry(
        {"action_scope": action_scope, "doc_ref": "d1"}, "flowgate", "ko"
    )
    assert result["kind"] == "read"
    assert result["reason"] is None
    assert [item["name"] for item in result["tools"]] == ["read", "grep", "glob", "stat"]


@pytest.mark.parametrize("action_scope", ["review", "workflow_decide", "chat"])
def test_kind_read_for_review_workflow_decide_and_chat_without_step_lookup(monkeypatch, action_scope):
    """0431 T0004: chat joins review/workflow_decide at the same immediate
    read-only return -- none of the three look up a step type or reach the
    TR/TSR/TS write-kind branch."""
    def unexpected(_rec):
        raise AssertionError("read-only scopes must not look up a workflow step")
    monkeypatch.setattr(remote_tool_service, "_worker_token_step_type_result", unexpected)
    result = tool_registry.resolve_registry({"action_scope": action_scope}, "flowgate", "ja")
    assert result["kind"] == "read"
    assert result["reason"] is None
    assert [item["name"] for item in result["tools"]] == ["read", "grep", "glob", "stat"]


def test_kind_none_for_unassigned_scopes_and_user_jwt():
    unassigned = tool_registry.resolve_registry({"action_scope": "test_run"}, "flowgate", "en")
    assert unassigned["kind"] == "none"
    assert unassigned["reason"] == "token_scope_none"
    assert unassigned["tools"] == []

    user = tool_registry.resolve_registry({"_is_user_jwt": True}, None, "en")
    assert user["kind"] == "none"
    assert user["source_mode"] is None
    assert user["reason"] is None
    assert user["notes"] == [tool_registry.NOTES["en"]["none_user"]]


def test_lookup_exception_degrades_to_read_with_reason(monkeypatch):
    monkeypatch.setattr(
        remote_tool_service,
        "_worker_token_step_type_result",
        lambda _rec: (None, True),
    )
    result = tool_registry.resolve_registry(
        {"action_scope": "new", "doc_ref": "d1"}, "flowgate", "ko"
    )
    assert result["kind"] == "read"
    assert result["reason"] == "step_lookup_failed"
    assert [item["name"] for item in result["tools"]] == ["read", "grep", "glob", "stat"]


def test_local_mode_overrides_kind_to_none(monkeypatch):
    monkeypatch.setattr(
        remote_tool_service,
        "_worker_token_step_type_result",
        lambda _rec: ("TR", False),
    )
    monkeypatch.setattr(source_mode_service, "resolve_effective_mode", lambda _project: "local")
    result = tool_registry.resolve_registry({"action_scope": "edit"}, "flowgate", "ko")
    assert result["kind"] == "none"
    assert result["source_mode"] == "local"
    assert result["reason"] == "source_mode_local"
    assert result["notes"] == [tool_registry.NOTES["ko"]["none_local"]]


def test_source_mode_exception_falls_back_remote(monkeypatch):
    monkeypatch.setattr(
        remote_tool_service,
        "_worker_token_step_type_result",
        lambda _rec: ("TSR", False),
    )
    def fail(_project):
        raise RuntimeError("settings unavailable")
    monkeypatch.setattr(source_mode_service, "resolve_effective_mode", fail)
    result = tool_registry.resolve_registry({"action_scope": "new"}, "flowgate", "ko")
    assert result["source_mode"] == "remote"
    assert result["kind"] == "read_write"


def test_locale_normalization_catalog_order_and_exact_notes(monkeypatch):
    monkeypatch.setattr(
        remote_tool_service,
        "_worker_token_step_type_result",
        lambda _rec: ("TR", False),
    )
    assert template_provision.normalize_locale(None) == "ko"
    assert template_provision.normalize_locale(" ") == "ko"
    assert template_provision.normalize_locale("zh") == "ko"
    assert template_provision.normalize_locale("en") == "en"

    result = tool_registry.resolve_registry({"action_scope": "edit"}, "flowgate", "ko")
    assert [item["name"] for item in result["tools"]] == list(tool_registry.DISPLAY_ORDER)
    assert result["tools"][0]["summary"] == "원격 프로젝트 소스의 파일 하나를 읽는다."
    assert result["notes"] == [
        tool_registry.NOTES["ko"]["path_rule"],
        tool_registry.NOTES["ko"]["auth_rule"],
        tool_registry.NOTES["ko"]["no_disk_edit"],
        tool_registry.NOTES["ko"]["report_changes"],
        tool_registry.NOTES["ko"]["see_detail"],
    ]