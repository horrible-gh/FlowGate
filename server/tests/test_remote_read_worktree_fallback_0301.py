"""flowgate.fileforge.0001 TR0005 — remote-read worktree fallback regression tests."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from modules.flow_gate.services import git_service
from modules.flow_gate.services import remote_tool_service as remote


@pytest.fixture()
def grant() -> dict:
    return {
        "project": "flowgate",
        "module": "fileforge",
        "group_id": "flowgate.fileforge.0001",
    }


def _base(monkeypatch) -> Path:
    path = Path("/base-checkout")
    monkeypatch.setattr(remote, "_fallback_project_root", lambda _grant: path)
    return path


def test_expected_fallback_self_heals_once_and_returns_worktree(monkeypatch, grant):
    """An anomalous integrated-group fallback retries provisioning exactly once."""
    base = _base(monkeypatch)
    worktree = Path("/group-worktree")
    ensure_calls: list[tuple] = []

    monkeypatch.setattr(
        git_service,
        "effective_src_root_ex",
        lambda project, group: (None, git_service.SRC_ROOT_NO_STATE),
    )
    monkeypatch.setattr(
        git_service,
        "ensure_worktree",
        lambda *args, **kwargs: ensure_calls.append((args, kwargs)) or "ok",
    )
    monkeypatch.setattr(
        git_service, "effective_src_root", lambda project, group: worktree
    )

    resolved = remote._resolve_src_root(grant, "grep")
    assert resolved == worktree
    assert resolved != base
    assert ensure_calls == [
        (
            ("flowgate", "fileforge", "flowgate.fileforge.0001"),
            {"trigger": "remote_read_retry"},
        )
    ]


def test_unresolved_expected_fallback_warns_and_still_serves_base(
    monkeypatch, grant, caplog
):
    """Reads remain available, but an unresolved expected worktree is traceable."""
    base = _base(monkeypatch)
    ensure_calls: list[int] = []
    monkeypatch.setattr(
        git_service,
        "effective_src_root_ex",
        lambda project, group: (None, git_service.SRC_ROOT_DIR_MISSING),
    )
    monkeypatch.setattr(
        git_service,
        "ensure_worktree",
        lambda *args, **kwargs: ensure_calls.append(1) or "failed",
    )

    with caplog.at_level(logging.WARNING, logger=remote.__name__):
        resolved = remote._resolve_src_root(grant, "glob")

    assert resolved == base
    assert ensure_calls == [1]
    assert "flowgate.fileforge.0001" in caplog.text
    assert "op=glob" in caplog.text
    assert git_service.SRC_ROOT_DIR_MISSING in caplog.text


@pytest.mark.parametrize(
    "reason",
    [git_service.SRC_ROOT_INTEGRATION_OFF, git_service.SRC_ROOT_NO_GROUP],
)
def test_benign_fallback_does_not_provision_or_warn(
    monkeypatch, grant, caplog, reason
):
    """Non-integrated and no-group-context outcomes preserve silent fallback-first."""
    base = _base(monkeypatch)
    monkeypatch.setattr(
        git_service,
        "effective_src_root_ex",
        lambda project, group: (None, reason),
    )

    def unexpected(*args, **kwargs):
        pytest.fail("benign read fallback must not call ensure_worktree")

    monkeypatch.setattr(git_service, "ensure_worktree", unexpected)
    with caplog.at_level(logging.WARNING, logger=remote.__name__):
        assert remote._resolve_src_root(grant, "read") == base
    assert not caplog.records


def test_legacy_group_less_grant_uses_base_without_loading_git(monkeypatch):
    """Legacy grants remain a direct base-root fallback."""
    base = _base(monkeypatch)
    assert remote._resolve_src_root({"project": "flowgate"}, "read") == base