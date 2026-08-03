"""원격 삭제 도구 — 삽을 고친다 (0382 B0001 §3 / NR0003 제안 5).

질의에 적힌 503 의 정체가 여기다. 261개를 지우려던 작업자는 세 가지 벽에 부딪혔다.

* ``remove`` 는 파일 하나만 지운다 → 폴더를 주면 404, 261번 불러야 한다.
* git 이 만든 개체 파일은 읽기 전용이라 ``os.remove`` 가 PermissionError 를 낸다.
* 그 오류가 503 "서버가 **일시적으로** 요청을 처리할 수 없습니다"로 나갔다. 작업자는
  "일시적"이라는 말을 믿고 3번 재시도했고, 영원히 안 될 일이었다.

세 가지를 각각 고정한다. 재귀 삭제는 열되 **도구가 남긴 흔적으로 판정되는 경로에만**
연다(제안 5-c 의 안전한 쪽) — 화면·제출 검사와 같은 규칙을 본다.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from scratch_support import remove_tree, session_scratch

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import remote_tool_service as rts  # noqa: E402

_SCRATCH = session_scratch("remote-cleanup-0382")


@pytest.fixture
def root():
    path = _SCRATCH / f"root-{os.urandom(6).hex()}"
    path.mkdir(parents=True)
    yield path
    remove_tree(path)


# ── 읽기 전용 파일도 지워진다 (제안 5-a) ─────────────────────────────────────

def test_remove_clears_the_readonly_bit_and_succeeds(root):
    target = root / "locked.txt"
    target.write_text("x", encoding="utf-8")
    os.chmod(target, stat.S_IREAD)

    extra, _ = rts._exec_remove({"path": "locked.txt"}, root)

    assert extra["removed"] is True
    assert not target.exists()


# ── 폴더째 지울 수단 (제안 5-c) ──────────────────────────────────────────────

def test_directory_without_recursive_keeps_the_old_404(root):
    (root / "server" / ".test-tmp-0313").mkdir(parents=True)
    with pytest.raises(rts._OpError) as caught:
        rts._exec_remove({"path": "server/.test-tmp-0313"}, root)
    assert caught.value.status == 404


def test_recursive_removes_a_tool_artifact_tree_and_counts_it(root):
    debris = root / "server" / ".test-tmp-0313" / "case-a" / ".git" / "objects"
    debris.mkdir(parents=True)
    for index in range(3):
        blob = debris / f"obj{index}"
        blob.write_bytes(b"z")
        os.chmod(blob, stat.S_IREAD)  # git 개체와 같은 읽기 전용 상태

    extra, _ = rts._exec_remove(
        {"path": "server/.test-tmp-0313", "recursive": True}, root
    )

    assert extra["removed"] is True
    assert extra["recursive"] is True
    assert extra["removed_file_count"] == 3
    assert not (root / "server" / ".test-tmp-0313").exists()


def test_recursive_refuses_a_real_source_directory(root):
    (root / "server" / "modules").mkdir(parents=True)
    (root / "server" / "modules" / "app.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(rts._OpError) as caught:
        rts._exec_remove({"path": "server/modules", "recursive": True}, root)

    assert caught.value.status == 422
    assert caught.value.details["reason"] == "not_tool_artifact"
    # 거절했으면 아무것도 안 지운다.
    assert (root / "server" / "modules" / "app.py").exists()


@pytest.mark.parametrize("path", [".", "./", "./server", ".git", "server/./modules"])
def test_recursive_normalizes_dot_segments_and_protects_root_source_and_git(root, path):
    (root / "server" / "modules").mkdir(parents=True)
    source = root / "server" / "modules" / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")
    (root / ".git").mkdir()
    git_config = root / ".git" / "config"
    git_config.write_text("[core]\n", encoding="utf-8")

    with pytest.raises(rts._OpError) as caught:
        rts._exec_remove({"path": path, "recursive": True}, root)

    assert caught.value.status == 422
    assert caught.value.details["reason"] == "not_tool_artifact"
    assert source.exists(), f"{path!r} must not delete source"
    assert git_config.exists(), f"{path!r} must not delete repository metadata"


def test_recursive_must_be_a_boolean_and_string_false_never_recurses(root):
    (root / "server" / ".test-tmp-0313").mkdir(parents=True)

    with pytest.raises(rts._OpError) as caught:
        rts._validate_required(
            "remove", {"path": "server/.test-tmp-0313", "recursive": "false"}
        )
    assert caught.value.status == 422

    # 실행 함수를 직접 부르는 내부 경로도 문자열을 참으로 승격하지 않는다.
    with pytest.raises(rts._OpError) as caught:
        rts._exec_remove(
            {"path": "server/.test-tmp-0313", "recursive": "false"}, root
        )
    assert caught.value.status == 404
    assert (root / "server" / ".test-tmp-0313").exists()


# ── "재시도하면 될 일"과 "재시도해도 안 될 일"이 다른 답을 낸다 (제안 5-b) ───

def test_locked_path_answers_409_not_a_temporary_503(root, monkeypatch):
    target = root / "held.txt"
    target.write_text("x", encoding="utf-8")

    def _always_denied(_path):
        raise PermissionError(13, "held by another process")

    monkeypatch.setattr(rts.os, "remove", _always_denied)
    monkeypatch.setattr(rts.os, "chmod", lambda *_a, **_kw: None)

    with pytest.raises(rts._OpError) as caught:
        rts._exec_remove({"path": "held.txt"}, root)

    assert caught.value.status == 409, "503(일시적)로 답하면 AI 가 헛되이 재시도한다"
    assert caught.value.details["reason"] == "path_locked"


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
@pytest.mark.parametrize("reason", ["path_locked", "not_tool_artifact"])
def test_new_reasons_have_a_message_in_every_locale(reason, locale):
    assert rts._CUSTOM_ERROR_MESSAGES[reason][locale].strip()


def test_locked_message_says_retrying_will_not_help():
    exc = rts._OpError(409, details={"reason": "path_locked"})
    assert "재시도해도" in rts._op_error_message(exc, "ko")
    assert "not help" in rts._op_error_message(exc, "en")


def test_not_tool_artifact_message_names_the_path():
    exc = rts._OpError(422, details={"reason": "not_tool_artifact", "path": "server/modules"})
    assert "server/modules" in rts._op_error_message(exc, "ko")
