"""마무리 커밋이 테스트 찌꺼기를 삼키지 못하게 하는 게이트 (0382 B0001 / NR0003 제안 1·3).

사건: 커밋 ``0f502ce`` 는 파일 266개를 바꿨는데 그중 261개가 ``server/.test-tmp-*`` 였다.
마무리(finalize)가 워크트리가 더러우면 ``git add -A`` 로 남은 편집을 통째로 흡수하는데
거기에 필터가 없었고, 화면은 그 경로를 감추도록 되어 있어 아무도 볼 기회가 없었다.

여기서 고정하는 계약은 셋이다.

1. 흡수 커밋은 **새로 생긴 흔적을 담지 않는다.**
2. 흡수 커밋은 **추적 중인 파일의 삭제는 그대로 담는다.** 이미 커밋된 261개를 나중에
   지우는 작업이 바로 이 경로로 커밋되어야 하므로, 삭제까지 거르면 그 파일들은 영원히
   못 지우게 된다.
3. 뺀 것은 **조용히 버리지 않는다.** 목록이 결과로 돌아오고, 호출부가 그걸 화면에 싣는다.

그리고 제외 규칙은 이제 화면과 검사가 같은 함수를 본다(제안 3). 두 벌이었던 것이
"화면엔 안 보이는데 제출은 막히는" 모순을 만들었다.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from scratch_support import remove_tree, session_scratch

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault(
    "FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode()
)
os.environ.setdefault(
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-artifact-gate-0382-")
)

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import git_service as svc  # noqa: E402
from modules.flow_gate.services import path_exclusion_rules as rules  # noqa: E402
from modules.flow_gate.services import tr_scope_service as trs  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")

_SCRATCH = session_scratch("artifact-gate-0382")


def _git(args, cwd):
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t",
    })
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"git {args} failed: {proc.stderr}"
    return proc.stdout


@pytest.fixture
def repo():
    """저장소 **밖**의 진짜 git 워크트리 하나 (0382 의 재발 방지 규칙을 스스로 지킨다)."""
    path = _SCRATCH / f"repo-{os.urandom(6).hex()}"
    path.mkdir(parents=True)
    _git(["init", "-b", "main"], path)
    (path / "kept.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], path)
    _git(["-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "base"], path)
    yield path
    remove_tree(path)


def _committed_paths(repo: Path) -> set[str]:
    return {line for line in _git(["ls-files"], repo).splitlines() if line}


# ── 계약 1: 새 흔적은 흡수 커밋에 들어가지 않는다 ────────────────────────────

@needs_git
def test_absorb_commits_real_work_but_not_tool_debris(repo):
    (repo / "server").mkdir()
    (repo / "server" / "real.py").write_text("y = 2\n", encoding="utf-8")
    debris = repo / "server" / ".test-tmp-0313" / "storage"
    debris.mkdir(parents=True)
    (debris / "junk.txt").write_text("junk\n", encoding="utf-8")

    excluded = svc._absorb_worker_edits(repo, "feat: real work", None)

    tracked = _committed_paths(repo)
    assert "server/real.py" in tracked
    assert not [p for p in tracked if ".test-tmp-0313" in p]
    assert excluded == ["server/.test-tmp-0313/storage/junk.txt"]
    # 뺐다고 지우지는 않는다 — 파일은 워크트리에 그대로 있다(타임머신 원칙).
    assert (debris / "junk.txt").exists()


# ── 계약 2: 추적 중인 파일의 삭제는 그대로 커밋된다 ──────────────────────────

@needs_git
def test_absorb_still_commits_deletions_of_tracked_files(repo):
    """이미 커밋된 261개를 나중에 지우는 작업이 이 경로로 커밋되어야 한다.

    필터를 ``add -A`` 전체에 걸면 삭제까지 걸러져 그 파일들이 영구히 남는다. 게이트는
    **새로 추가되는** 흔적만 막는다.
    """
    junk_dir = repo / "server" / ".test-tmp-0313"
    junk_dir.mkdir(parents=True)
    (junk_dir / "old.txt").write_text("committed junk\n", encoding="utf-8")
    _git(["add", "-A", "-f"], repo)
    _git(["-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "junk"], repo)
    assert "server/.test-tmp-0313/old.txt" in _committed_paths(repo)

    remove_tree(junk_dir)
    excluded = svc._absorb_worker_edits(repo, "chore: drop test debris", None)

    assert excluded == []
    assert "server/.test-tmp-0313/old.txt" not in _committed_paths(repo)


# ── 계약 3: 흔적뿐인 워크트리는 빈 커밋도 500 도 만들지 않는다 ───────────────

@needs_git
def test_debris_only_worktree_produces_no_commit_and_no_error(repo):
    before = _git(["rev-parse", "HEAD"], repo).strip()
    debris = repo / "server" / ".test-tmp-0318"
    debris.mkdir(parents=True)
    (debris / "junk.txt").write_text("junk\n", encoding="utf-8")

    excluded = svc._absorb_worker_edits(repo, "feat: nothing real", None)

    assert excluded == ["server/.test-tmp-0318/junk.txt"]
    assert _git(["rev-parse", "HEAD"], repo).strip() == before


# ── 계약 3': 뺀 목록이 결과 모양에 실린다 ────────────────────────────────────

def test_artifact_payload_reports_count_and_list():
    payload = svc._artifact_payload(["a/.test-tmp-1/x", "b/.test-tmp-1/y"])
    assert payload["excluded_artifact_count"] == 2
    assert payload["excluded_artifacts"] == ["a/.test-tmp-1/x", "b/.test-tmp-1/y"]


def test_artifact_payload_caps_the_list_but_not_the_count():
    many = [f"server/.test-tmp-0313/f{i}" for i in range(svc.FINALIZE_ARTIFACT_LIST_MAX + 50)]
    payload = svc._artifact_payload(many)
    assert payload["excluded_artifact_count"] == len(many)
    assert len(payload["excluded_artifacts"]) == svc.FINALIZE_ARTIFACT_LIST_MAX


# ── 제외 규칙: 화면과 검사가 같은 함수를 본다 (제안 3) ───────────────────────

@pytest.mark.parametrize("path", [
    "server/.test-tmp-0313/storage/x.json",
    "server/.test-tmp-0318/case-abc/.git/objects/aa/bb",
    "server/.test-tmp-0313",
    ".git/config",
    "client/node_modules/pkg/index.js",
    "server/app.db",
])
def test_shared_rule_excludes_tool_debris(path):
    assert rules.is_excluded_path(path) is True
    # NR 이 정본으로 지목한 이름은 그대로 살아 있고, 같은 판정을 준다.
    assert trs.is_excluded_path(path) is True


@pytest.mark.parametrize("path", [
    "server/modules/flow_gate/services/git_service.py",
    "client/src/main/components/MainPanel.vue",
    # 0299 의 판단을 유지한다 — 정말 고친 설정 파일이 조용히 사라지면 안 된다.
    "client/src/.eslintrc.json",
])
def test_shared_rule_keeps_real_work(path):
    assert rules.is_excluded_path(path) is False
    assert trs.is_excluded_path(path) is False


def test_the_two_rules_no_longer_disagree_about_the_bug_path():
    """사고의 본체: 화면은 감추는데 검사는 잡던 그 경로."""
    path = "server/.test-tmp-0313/storage/x.json"
    assert svc._is_hidden_source_path(path) is True
    assert trs.is_excluded_path(path) is True


def test_exposure_rule_still_hides_a_nested_dotfile():
    """규칙을 합치면서 중첩 비밀 파일이 탐색기로 새어 나가지 않아야 한다."""
    assert svc._is_hidden_source_path("server/.env.local") is True


def test_exclusion_reason_names_why():
    assert rules.exclusion_reason("server/.test-tmp-0313/x") == rules.REASON_DOT_DIRECTORY
    assert rules.exclusion_reason(".env") == rules.REASON_DOT_TOPLEVEL
    assert rules.exclusion_reason("client/node_modules/a.js") == rules.REASON_TOOL_DIRECTORY
    assert rules.exclusion_reason("server/app.db") == rules.REASON_GENERATED_FILE
    assert rules.exclusion_reason("server/app.py") is None


def test_partition_keeps_order_and_separates_debris():
    kept, artifacts = rules.partition_paths([
        "server/a.py", "server/.test-tmp-0313/x", "client/b.vue", ".env",
    ])
    assert kept == ["server/a.py", "client/b.vue"]
    assert artifacts == ["server/.test-tmp-0313/x", ".env"]
