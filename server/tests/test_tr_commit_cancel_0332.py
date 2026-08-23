"""되돌리기가 TR 커밋을 취소 커밋으로 되돌리는가 (0332 T#2 — R0001 뒤 절).

T#1 과 같은 원칙으로 세 층을 진짜로 돌린다. 특히 되돌림은 **git 이 실제로 하는 일**이라
대역으로 흉내 내면 "revert 를 불렀다"까지만 증명된다 — 충돌이 정말 충돌로 끝나는지,
빈 되돌림이 정말 빈 커밋을 안 남기는지는 진짜 저장소에서만 나온다.

1. **원장(085 취소 열)** — ``mark_canceled`` / ``record_cancel_attempt`` / ``record_block``
   을 마이그레이션 전부 적용한 sqlite 위에서 돌린다. 행은 지워지지 않는다(D0005 K5).
2. **되돌림(git)** — 진짜 워크트리에 취소를 걸어 취소 커밋이 생기는지, 충돌이 워크트리를
   원상으로 되돌리고 거기서 멈추는지, 도구 흔적이 취소를 막지 않는지(L0007 §2.5) 본다.
3. **판정표(L0007 §4.1)** — G1~G11 이 각각 어느 코드로 나오는지, 그리고 그 코드가
   P0006 §5-3 의 닫힌 집합과 `retryable` 표를 벗어나지 않는지 본다.

이 시험이 고정하는 계약 한 줄: **"되감기는 언제나 서고, 취소는 최선을 다하되 무엇을 못
했는지 반드시 말한다"** (D0005 K8).
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from scratch_support import remove_tree, session_scratch

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault(
    "FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode()
)
os.environ.setdefault(
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-tr-cancel-0332-")
)

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import git_integration as db_git  # noqa: E402
from modules.flow_gate.db import tr_commit_ledger as db_ledger  # noqa: E402
from modules.flow_gate.services import git_service as svc  # noqa: E402
from modules.flow_gate.services import tr_commit_service as trc  # noqa: E402
from modules.flow_gate.services import workflow_rework_service as rework  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")

_SCRATCH = session_scratch("tr-cancel-0332")

_PROJECT = "flowgate"
_GROUP = "flowgate.default.0332"
_TR_A = "flowgate.default.0332.0009-TR"
_TR_B = "flowgate.default.0332.0011-TR"
_D_DOC = "flowgate.default.0332.0005-D"

_SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
    VALUES('{_PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'default', 'TR 커밋 취소', 'OPEN',
           datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
        doc_id, project_id, module, group_id, type_code, seq, title, status,
        created_at, updated_at)
    VALUES('{_D_DOC}', '{_PROJECT}', 'default', '{_GROUP}', 'D', 5,
           'TR 커밋 취소 기본설계', 'open', datetime('now'), datetime('now')),
          ('{_TR_A}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 9,
           '커밋 포인트 생성 작업레포트', 'open', datetime('now'), datetime('now')),
          ('{_TR_B}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 11,
           '되돌리기 취소 작업레포트', 'open', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO group_git_state(
        group_id, project_id, branch, worktree_registered, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'work', 1, 'none', datetime('now'), datetime('now'));
"""


class _SqliteStore:
    """T#1 과 같은 최소 계약. ``_sql`` 은 일부러 두지 않는다 — 등록된 진짜 SQL 본문이
    쓰이게 하려는 것이다(0393 의 교훈)."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def transaction(self):
        yield self


@pytest.fixture
def real_store(migrated_sqlite_db):
    db_path = migrated_sqlite_db("tr_commit_cancel_0332.db", seed_sql=_SEED_SQL)
    store = _SqliteStore(db_path)
    previous = db_connection.STORE
    db_connection.STORE = store
    try:
        yield store
    finally:
        db_connection.STORE = previous
        store._conn.close()


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


def _commit(repo: Path, message: str) -> str:
    _git(["add", "-A"], repo)
    _git(["-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", message], repo)
    return _git(["rev-parse", "HEAD"], repo).strip()


@pytest.fixture
def repo():
    """저장소 **밖**의 진짜 git 워크트리 (0382 재발 방지 규칙을 스스로 지킨다)."""
    path = _SCRATCH / f"wt-{os.urandom(6).hex()}"
    path.mkdir(parents=True)
    _git(["init", "-b", "work"], path)
    (path / "f.txt").write_text("base\n", encoding="utf-8")
    _commit(path, "base")
    yield path
    remove_tree(path)


@pytest.fixture
def git_active(monkeypatch, repo):
    """이 그룹이 git 활성이고 그 워크트리가 이 repo 라고 서버에 알려 준다."""
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: {
        "enabled": 1, "base_branch": "main", "author_name": None, "author_email": None,
    })
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 1, "branch": "work", "status": "none",
    })
    monkeypatch.setattr(svc, "_project_name", lambda project_id: "flowgate")
    monkeypatch.setattr(svc, "src_root", lambda project_name, branch: repo)
    monkeypatch.setattr(svc.db_git, "try_acquire_lock", lambda project_id, holder: True)
    monkeypatch.setattr(svc.db_git, "release_lock", lambda project_id, holder: None)
    return repo


# ── 1. 원장의 취소 열 (DB0008 §4.3~§4.5) ─────────────────────────────────────

def test_a_cancel_writes_onto_the_row_it_cancels_and_never_deletes_it(real_store):
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="0009-TR: 제목",
    )

    assert db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40) is True

    after = db_ledger.get_by_id(row["id"])
    assert after["state"] == "canceled"
    assert after["cancel_commit"] == "c" * 40
    assert after["canceled_at"]
    # 원래 커밋 해시는 그대로 남는다 — "이 커밋이 있었고 취소됐다"가 기록의 내용이다.
    assert after["commit_sha"] == "a" * 40


def test_a_second_cancel_of_the_same_row_reports_it_did_not_win(real_store):
    """DB0008 §4-3 의 ``WHERE state='live'``. ``_execute`` 는 rowcount 를 주지 않으므로
    서비스는 되읽어 판정한다 — 이 값이 False 라야 호출자가 ``already_canceled`` 로
    다시 분류하고, 되돌림의 되돌림이 생기지 않는다."""
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="x",
    )
    db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)

    assert db_ledger.mark_canceled(row["id"], cancel_commit="d" * 40) is False
    assert db_ledger.get_by_id(row["id"])["cancel_commit"] == "c" * 40
    assert db_ledger.is_canceled(row["id"]) is True


def test_an_empty_revert_is_canceled_without_a_cancel_commit(real_store):
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="x",
    )

    assert db_ledger.mark_canceled(
        row["id"], cancel_commit=None, reason="empty_revert"
    ) is True

    after = db_ledger.get_by_id(row["id"])
    assert (after["state"], after["cancel_commit"], after["cancel_reason"]) == (
        "canceled", None, "empty_revert",
    )


def test_a_failed_attempt_is_logged_and_the_row_stays_live(real_store):
    """응답은 실패를 ``conflict`` 하나로 묶는다(P0006 §5-4 닫힌 집합). 무엇이 실제로
    실패했는지는 여기 남아야 나중에 읽을 수 있다."""
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="x",
    )

    db_ledger.record_cancel_attempt(row["id"], failed_reason="revert_conflict")
    db_ledger.record_cancel_attempt(row["id"], failed_reason="timeout")

    after = db_ledger.get_by_id(row["id"])
    assert after["state"] == "live"
    log = json.loads(after["cancel_attempt_log"])
    assert [entry["reason"] for entry in log] == ["revert_conflict", "timeout"]
    assert all(entry["at"] for entry in log)


def test_a_group_level_block_writes_only_the_three_diagnostic_columns(real_store):
    db_ledger.record_block(_GROUP, "dirty_worktree", "dirty_worktree")

    state = real_store._fetch_one(
        "SELECT * FROM group_git_state WHERE group_id = ?", [_GROUP]
    )
    assert state["last_cancel_block_reason"] == "dirty_worktree"
    assert state["last_cancel_block_sub"] == "dirty_worktree"
    assert state["last_cancel_block_at"]
    # 그룹의 git 상태 자체는 취소 진단을 남긴다고 움직이지 않는다.
    assert state["status"] == "none"


def test_a_block_on_a_group_without_git_state_is_silent(real_store):
    """git 을 켠 적 없는 그룹에는 진단을 남길 자리가 없다(DB0008 §5-2). 조용히 0행."""
    db_ledger.record_block("flowgate.default.9999", "git_inactive", "integration_disabled")


def test_the_preview_query_carries_commits_only_in_workflow_order(real_store):
    db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_B, commit_sha="b" * 40, commit_subject="11번",
    )
    db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="a" * 40, commit_subject="9번",
    )
    db_ledger.record_no_commit(group_id=_GROUP, doc_id=_TR_A, skip_reason="no_changes")

    rows = db_ledger.commit_rows_by_group(_GROUP)

    # 커밋을 남긴 행만, 그리고 문서 seq 오름차순으로 (창은 단계 순서로 그린다).
    assert [(r["seq"], r["commit_sha"][:1]) for r in rows] == [(9, "a"), (11, "b")]


# ── 2. 진짜 워크트리에서의 되돌림 ────────────────────────────────────────────

def _ledger_commit(group_id, doc_id, sha, subject="s"):
    return db_ledger.record_commit(
        group_id=group_id, doc_id=doc_id, commit_sha=sha, commit_subject=subject,
    )


@needs_git
def test_the_cancel_lays_one_revert_commit_per_tr_newest_first(real_store, git_active, repo):
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: a")
    (repo / "b.py").write_text("b = 1\n", encoding="utf-8")
    sha_b = _commit(repo, "0011-TR: b")
    _ledger_commit(_GROUP, _TR_B, sha_b, "0011-TR: b")

    result = trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])

    assert result["attempted"] is True
    assert result["blocked_reason"] is None
    assert result["stopped_reason"] is None
    assert result["skipped"] == []
    # 최신부터 역순으로. 덩어리 짓지 않으므로 취소 커밋도 TR 마다 하나씩이다.
    assert [line["doc_code"] for line in result["canceled"]] == ["0011-TR", "0009-TR"]
    assert not (repo / "a.py").exists() and not (repo / "b.py").exists()
    subjects = _git(["log", "-2", "--pretty=%s"], repo).split("\n")
    assert subjects[0] == 'Revert "0009-TR: a"'
    assert subjects[1] == 'Revert "0011-TR: b"'
    # 본문이 되돌린 커밋을 전체 해시로 지목한다 — 짝을 어느 쪽에서도 찾을 수 있다.
    assert sha_a in _git(["log", "-1", "--pretty=%b"], repo)
    assert f"(group {_GROUP})" in _git(["log", "-1", "--pretty=%b"], repo)
    # 원장은 두 줄 다 취소로 바뀌고 어느 줄도 사라지지 않았다.
    rows = db_ledger.list_by_group(_GROUP)
    assert [r["state"] for r in rows] == ["canceled", "canceled"]
    assert all(r["cancel_commit"] for r in rows)


@needs_git
def test_a_conflict_is_parked_as_a_session_and_leaves_the_rest_untried(
    real_store, git_active, repo,
):
    """TR0019 로 이 자리의 결말이 바뀌었다.

    예전에는 충돌이 나면 그 자리에서 ``revert --quit`` + ``reset --hard`` 로 증거를 지우고
    "충돌했다, 손으로 정리해라"만 남겼다. 지금은 충돌 세션 한 행으로 남는다 — 그룹은
    ``status='conflict'`` 에 ``merge_id`` 를 달고, 병합 충돌이 쓰던 인라인 편집기와 AI
    호출이 그대로 이 행을 연다. 멈추는 것과 나머지를 건드리지 않는 것은 그대로다.
    """
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: A")
    (repo / "f.txt").write_text("B\n", encoding="utf-8")
    sha_b = _commit(repo, "0011-TR: B")
    row_b = _ledger_commit(_GROUP, _TR_B, sha_b, "0011-TR: B")
    # 원장 밖의 손질이 같은 줄을 다시 건드린다 — B 의 되돌림이 여기서 충돌한다.
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])

    assert result["attempted"] is True
    assert result["canceled"] == []
    assert result["stopped_reason"] == "conflict"
    assert [(s["doc_code"], s["reason"]) for s in result["skipped"]] == [
        ("0011-TR", "conflict"), ("0009-TR", "not_attempted"),
    ]
    # 같은 요청을 다시 보내는 [다시 시도] 는 여전히 주지 않는다 — 답이 같기 때문이고,
    # 이제 답이 다를 수 있는 경로(해결 후 커밋)가 따로 생겼다(L0007 §4.2).
    assert result["retryable"] is False

    # 충돌은 살아 있다: 커밋은 안 됐지만 트리에는 표식이 있고 시퀀서가 열려 있다.
    parked = result["conflict_session"]
    assert parked and parked["kind"] == "tr_revert"
    assert parked["files"] == ["f.txt"]
    assert parked["doc_code"] == "0011-TR"
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    assert "<<<<<<<" in (repo / "f.txt").read_text(encoding="utf-8")
    assert svc._revert_in_flight(repo) is True

    # 세션은 병합 충돌과 같은 표에 있고, 그룹은 같은 방식으로 conflict 를 가리킨다.
    # group_git_state 는 저장된 행을 직접 읽는다 — ``git_active`` 가 ``db_git.get_state``
    # 를 고정 dict 로 갈아끼워 두었으므로 그 함수로는 방금 쓴 값이 안 보인다.
    session = db_git.get_session(parked["merge_id"])
    assert (session["status"], db_git.session_kind(session)) == ("open", "tr_revert")
    state = real_store._fetch_one(
        "SELECT status, merge_id FROM group_git_state WHERE group_id = ?", [_GROUP]
    )
    assert (state["status"], state["merge_id"]) == ("conflict", parked["merge_id"])

    # 실패한 행은 live 그대로이고, 무엇이 실패했는지는 원장에 남는다.
    after = db_ledger.get_by_id(row_b["id"])
    assert after["state"] == "live"
    assert json.loads(after["cancel_attempt_log"])[0]["reason"] == "revert_conflict"


@needs_git
def test_giving_up_on_a_parked_conflict_restores_the_worktree_exactly_as_before(
    real_store, git_active, repo,
):
    """예전의 파괴는 사라진 게 아니라 단추 뒤로 갔다.

    [되돌리기 중단] 은 그때의 ``revert --quit`` + ``reset --hard HEAD`` 그대로이고
    ``git clean`` 은 여전히 이 경로에 없다(0382). 원장 행도 그대로 — 포기한 취소는
    커밋을 live 로 남긴다.
    """
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    row_a = _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: A")
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    parked = trc.cancel_tr_commits(_GROUP, [_TR_A])["conflict_session"]
    assert parked
    # 세션이 열린 뒤 워크트리에 놓인 추적 밖 파일. 포기가 이것을 지우면 0382 재발이다.
    (repo / "debris.tmp").write_text("x\n", encoding="utf-8")

    trc.abort_conflict_resolution(_GROUP, parked["merge_id"])

    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    assert (repo / "f.txt").read_text(encoding="utf-8") == "C\n"
    assert svc._revert_in_flight(repo) is False
    assert (repo / "debris.tmp").exists()
    assert db_git.get_session(parked["merge_id"])["status"] == "aborted"
    state = real_store._fetch_one(
        "SELECT status FROM group_git_state WHERE group_id = ?", [_GROUP]
    )
    assert state["status"] != "conflict"
    after = db_ledger.get_by_id(row_a["id"])
    assert after["state"] == "live"
    assert json.loads(after["cancel_attempt_log"])[-1]["reason"] == "conflict_abandoned"


@needs_git
def test_an_empty_revert_is_reported_as_already_canceled_without_a_new_commit(
    real_store, git_active, repo,
):
    """같은 내용이 다른 경로로 이미 되돌려졌다. 빈 커밋을 얹지 않는다(D0005 K3와 같은 이유)."""
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    row = _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: A")
    (repo / "f.txt").write_text("base\n", encoding="utf-8")
    _commit(repo, "manual undo")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert result["canceled"] == []
    assert [(s["doc_code"], s["reason"]) for s in result["skipped"]] == [
        ("0009-TR", "already_canceled"),
    ]
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    after = db_ledger.get_by_id(row["id"])
    assert (after["state"], after["cancel_commit"], after["cancel_reason"]) == (
        "canceled", None, "empty_revert",
    )


@needs_git
def test_an_already_canceled_tr_is_skipped_not_reverted_again(real_store, git_active, repo):
    """D0005 K6 — 되돌리기를 두 번 눌렀다고 되돌림의 되돌림이 일어나서는 안 된다."""
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: a")

    first = trc.cancel_tr_commits(_GROUP, [_TR_A])
    head_after_first = _git(["rev-parse", "HEAD"], repo).strip()
    second = trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert len(first["canceled"]) == 1
    # 두 번째는 원장이 이미 canceled 라 대상 자체가 비고, G1 로 조용히 끝난다.
    assert second["attempted"] is True
    assert second["canceled"] == [] and second["skipped"] == []
    assert second["blocked_reason"] is None
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_after_first


@needs_git
def test_tool_debris_does_not_block_a_cancel(real_store, git_active, repo):
    """L0007 §2.5 — 흔적은 TR 커밋이 일부러 남겨 둔 파일이라 정상 상태에 늘 있다.
    마무리의 ``_dirty()`` 를 그대로 썼다면 이 그룹은 영영 취소되지 않는다."""
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: a")
    debris = repo / "server" / ".test-tmp-0332"
    debris.mkdir(parents=True)
    (debris / "junk.txt").write_text("junk\n", encoding="utf-8")

    result = trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert len(result["canceled"]) == 1
    # 취소가 흔적을 지우지도 담지도 않는다.
    assert (debris / "junk.txt").exists()


@needs_git
def test_an_uncommitted_edit_blocks_the_cancel_and_asks_for_a_retry(
    real_store, git_active, repo,
):
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: a")
    (repo / "f.txt").write_text("hand edit\n", encoding="utf-8")   # 추적 파일의 미커밋 변경
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert result["attempted"] is False
    assert result["blocked_reason"] == "dirty_worktree"
    assert result["retryable"] is True          # 정리한 뒤 [다시 시도]
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    state = real_store._fetch_one(
        "SELECT * FROM group_git_state WHERE group_id = ?", [_GROUP]
    )
    assert state["last_cancel_block_sub"] == "dirty_worktree"


@needs_git
def test_commits_missing_from_this_worktree_cancel_nothing_at_all(
    real_store, git_active, repo,
):
    """G11 fail-closed — 부분 취소보다 무취소가 안전하다(L0007 §5)."""
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_B, "b" * 40, "0011-TR: 이 트리에 없는 커밋")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])

    assert result["attempted"] is False
    assert result["blocked_reason"] == "no_worktree"
    assert result["retryable"] is False
    # 이 트리에 있는 a 커밋조차 건드리지 않았다.
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    assert [r["state"] for r in db_ledger.list_by_group(_GROUP)] == ["live", "live"]


# ── 3. 판정표 (L0007 §4.1 / P0006 §5-3) ──────────────────────────────────────

def test_a_group_that_never_committed_ends_quietly_not_on_an_error(real_store):
    """G1 이 게이트보다 앞이다. 문서만 쓴 그룹을 되감으면 "취소할 커밋이 없었습니다"로
    끝나야 하고, ``git_inactive`` 결과 화면이 떠서는 안 된다(L0007 §4.1 주 1)."""
    result = trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])

    assert result == {
        "attempted": True, "blocked_reason": None,
        "canceled": [], "skipped": [], "stopped_reason": None, "retryable": False,
        "conflict_session": None,
    }


@pytest.mark.parametrize(
    "state, expect_reason, expect_sub, expect_retryable",
    [
        ({"worktree_registered": 1, "branch": "work", "status": "merged"},
         "already_merged", "already_merged", False),
        ({"worktree_registered": 1, "branch": "work", "status": "pushed"},
         "already_merged", "already_merged", False),
        ({"worktree_registered": 1, "branch": "work", "status": "merging"},
         "git_busy", "merge_in_flight", True),
        ({"worktree_registered": 0, "branch": "work", "status": "none"},
         "no_worktree", "worktree_unregistered", False),
    ],
)
def test_the_group_state_gates_answer_in_the_documented_order(
    real_store, git_active, repo, monkeypatch,
    state, expect_reason, expect_sub, expect_retryable,
):
    """G5 가 G7 보다 앞이라야 병합된 그룹이 ``no_worktree`` 로 뭉개지지 않는다 — 그러면
    화면이 "[병합 되돌리기]로만 취소할 수 있습니다"를 말할 기회를 잃는다."""
    _ledger_commit(_GROUP, _TR_A, "a" * 40, "0009-TR: a")
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: state)

    result = trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert result["attempted"] is False
    assert result["blocked_reason"] == expect_reason
    assert result["retryable"] is expect_retryable
    row = real_store._fetch_one(
        "SELECT * FROM group_git_state WHERE group_id = ?", [_GROUP]
    )
    assert row["last_cancel_block_sub"] == expect_sub
    # 어떤 게이트도 원장 행을 건드리지 않는다.
    assert [r["state"] for r in db_ledger.list_by_group(_GROUP)] == ["live"]


def test_a_git_disabled_project_reports_git_inactive(real_store, git_active, monkeypatch):
    _ledger_commit(_GROUP, _TR_A, "a" * 40, "0009-TR: a")
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: {"enabled": 0})

    result = trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert (result["blocked_reason"], result["retryable"]) == ("git_inactive", False)


def test_a_busy_project_lock_is_retryable_and_never_waits_forever(
    real_store, git_active, monkeypatch,
):
    _ledger_commit(_GROUP, _TR_A, "a" * 40, "0009-TR: a")
    monkeypatch.setattr(svc.db_git, "try_acquire_lock", lambda project_id, holder: False)
    monkeypatch.setattr(svc, "CANCEL_LOCK_WAIT_SEC", 0)

    result = trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert (result["blocked_reason"], result["retryable"]) == ("git_busy", True)


def test_every_block_code_stays_inside_the_closed_protocol_set(real_store):
    """P0006 §5-3 이 닫아 둔 다섯 코드. 여기 없는 코드를 새로 만들면 화면이 그것을 그릴
    문구를 갖고 있지 않다."""
    assert set(trc.CANCEL_BLOCK_RETRYABLE) == {
        "already_merged", "no_worktree", "git_inactive", "dirty_worktree", "git_busy",
    }


@needs_git
def test_the_lock_is_released_before_the_rearm_can_ask_for_it(real_store, git_active, repo):
    """L0007 §2.1 ③ — 잠금은 재진입이 되지 않는다. 취소가 쥔 채로 끝나면 재무장이 5초를
    기다린 뒤 조용히 실패한다."""
    held: list[str] = []
    svc.db_git.try_acquire_lock = lambda project_id, holder: (held.append(holder), True)[1]
    svc.db_git.release_lock = lambda project_id, holder: held.remove(holder)
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_A, sha_a, "0009-TR: a")

    trc.cancel_tr_commits(_GROUP, [_TR_A])

    assert held == []


# ── 4. 미리보기 (P0006 §2) ───────────────────────────────────────────────────

def test_the_preview_shows_live_and_canceled_rows_with_the_group_status(
    real_store, git_active,
):
    row_a = _ledger_commit(_GROUP, _TR_A, "a" * 40, "0009-TR: a")
    _ledger_commit(_GROUP, _TR_B, "b" * 40, "0011-TR: b")
    db_ledger.mark_canceled(row_a["id"], cancel_commit="c" * 40)

    preview = trc.commit_preview(_GROUP)

    assert preview["group_status"] == "active"
    assert preview["commits"] == [
        {"seq": 9, "doc_id": _TR_A, "doc_code": "0009-TR", "commit": "a" * 7,
         "subject": "0009-TR: a", "status": "canceled", "cancel_commit": "c" * 7},
        {"seq": 11, "doc_id": _TR_B, "doc_code": "0011-TR", "commit": "b" * 7,
         "subject": "0011-TR: b", "status": "live", "cancel_commit": None},
    ]


def test_the_preview_calls_a_merged_group_merged_not_worktree_less(
    real_store, git_active, monkeypatch,
):
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 0, "branch": "work", "status": "merged",
    })

    assert trc.commit_preview(_GROUP)["group_status"] == "already_merged"


def test_a_merge_in_flight_previews_as_active(real_store, git_active, monkeypatch):
    """L0007 §3 — 창이 열린 몇 초 뒤엔 이미 틀린 값이 되는 조건은 미리보기에 싣지 않는다.
    확인을 누른 순간 G6 이 ``git_busy``(재시도 가능)로 답한다."""
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 1, "branch": "work", "status": "merging",
    })

    assert trc.commit_preview(_GROUP)["group_status"] == "active"


def test_a_git_inactive_group_previews_empty(real_store, monkeypatch):
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: None)

    assert trc.commit_preview(_GROUP) == {"group_status": "git_inactive", "commits": []}


# ── 5. 되돌리기 경로에 붙는 자리 (L0007 §2.1) ────────────────────────────────

def test_the_rearm_cancels_first_and_re_arms_second(monkeypatch):
    """순서가 결과를 바꾼다. 재무장은 워크트리를 base HEAD 에서 새로 만들므로, 뒤에
    취소하면 취소할 대상이 이미 사라져 있다."""
    order: list[str] = []
    monkeypatch.setattr(
        rework.tr_commit_service, "cancel_for_reopen",
        lambda group_id, doc_ids: (order.append(f"cancel:{sorted(doc_ids)}"), {"ok": 1})[1],
    )
    monkeypatch.setattr(
        rework.git_service, "reopen_group_git",
        lambda project_id, group_id: order.append("rearm"),
    )

    out = rework._rearm_git(_PROJECT, _GROUP, [_TR_B, _TR_A])

    assert order == [f"cancel:{sorted([_TR_B, _TR_A])}", "rearm"]
    assert out == {"ok": 1}


def test_a_cancel_that_blows_up_drops_the_key_but_still_re_arms(monkeypatch):
    """L0007 §5 — 되돌리기는 200 으로 성공하고, 반쪽짜리 객체 대신 키 자체가 빠진다."""
    def _boom(group_id, doc_ids):
        raise RuntimeError("ledger table missing")

    rearmed: list[str] = []
    monkeypatch.setattr(rework.tr_commit_service, "cancel_for_reopen", _boom)
    monkeypatch.setattr(
        rework.git_service, "reopen_group_git",
        lambda project_id, group_id: rearmed.append(group_id),
    )

    assert rework._rearm_git(_PROJECT, _GROUP, [_TR_A]) is None
    assert rearmed == [_GROUP]


def test_a_rearm_failure_does_not_swallow_the_cancel_result(monkeypatch):
    """취소와 재무장은 서로의 성공 조건이 아니다(L0007 §5)."""
    def _boom(project_id, group_id):
        raise RuntimeError("worktree gone")

    monkeypatch.setattr(
        rework.tr_commit_service, "cancel_for_reopen",
        lambda group_id, doc_ids: {"attempted": True},
    )
    monkeypatch.setattr(rework.git_service, "reopen_group_git", _boom)

    assert rework._rearm_git(_PROJECT, _GROUP, [_TR_A]) == {"attempted": True}


def test_the_reopen_result_carries_the_cancel_object(monkeypatch):
    """P0006 §3 — 기존 응답(``ok``/``reopened``/``return_point``)은 그대로이고 키가 하나
    늘 뿐이다. 0142·0381 의 단언이 이 위에서 그대로 서야 한다."""
    payload = {"ok": True, "reopened": [_TR_A], "return_point": {"exists": False}}
    monkeypatch.setattr(
        rework, "_rearm_git",
        lambda project_id, group_id, doc_ids: {"attempted": True, "canceled": []},
    )

    result = dict(payload)
    cancel = rework._rearm_git(_PROJECT, _GROUP, result["reopened"])
    if cancel is not None:
        result["tr_commit_cancel"] = cancel

    assert result["ok"] is True and result["reopened"] == [_TR_A]
    assert result["return_point"] == {"exists": False}
    assert result["tr_commit_cancel"]["attempted"] is True


# ── 6. 취소만 재실행 (P0006 §4 / L0007 §2.7) ─────────────────────────────────

def test_the_retry_spares_the_reapproved_commit_but_not_the_document(
    real_store, git_active, monkeypatch,
):
    """되감긴 뒤 다시 승인된 단계는 새 커밋을 만들었다. 그것을 되돌리면 사람이 방금 다시
    한 일을 지운다 — 그래서 그 **행**은 빠진다.

    0332 T0018 §3-4 로 계약이 바뀐 자리다. 예전에는 재승인된 **문서**를 통째로 뺐고,
    그 바람에 취소에 실패해 아직 live 로 남아 있던 옛 행까지 같이 빠져 어떤 경로로도
    다시 잡히지 않았다. 문서는 대상에 남고, 재승인이 방금 만든 행 하나만 제외된다.
    (옛 행이 실제로 대상에 남는지는 test_tr_commit_reapply_0332.py §7 이 본다.)"""
    calls: list[dict] = []
    monkeypatch.setattr(
        trc, "cancel_tr_commits",
        lambda group_id, doc_ids, exclude_row_ids=None: (
            calls.append({"docs": sorted(doc_ids),
                          "exclude": sorted(exclude_row_ids or [])}),
            trc.empty_cancel_result(),
        )[1],
    )
    fresh = _ledger_commit(_GROUP, _TR_A, "n" * 40, "재승인이 만든 커밋")
    real_store._execute(
        "INSERT INTO workflow_return_points(group_id, front_seq, created_at, updated_at) "
        "VALUES(?, 11, datetime('now'), datetime('now'))", [_GROUP],
    )
    rp_id = real_store._fetch_one(
        "SELECT id FROM workflow_return_points WHERE group_id = ?", [_GROUP]
    )["id"]
    for doc_id, seq in ((_TR_A, 9), (_TR_B, 11)):
        real_store._execute(
            "INSERT INTO workflow_return_point_docs"
            "(return_point_id, doc_id, seq, prev_status, fingerprint) "
            "VALUES(?, ?, ?, 'approved', 'fp')",
            [rp_id, doc_id, seq],
        )
    # 0009-TR 은 그 사이 다시 승인됐다.
    real_store._execute(
        "UPDATE documents SET doc_review_status = 'approved' WHERE doc_id = ?", [_TR_A]
    )

    trc.cancel_retry(_GROUP)

    assert calls == [{"docs": sorted([_TR_A, _TR_B]), "exclude": [fresh["id"]]}]


def test_a_retry_without_a_return_point_is_a_quiet_no_op(real_store):
    result = trc.cancel_retry(_GROUP)

    assert result["attempted"] is True
    assert result["blocked_reason"] is None
    assert result["canceled"] == [] and result["skipped"] == []


# ── 7. 두 라우트 (P0006 §2·§4) ───────────────────────────────────────────────

def test_the_return_point_read_carries_the_preview(real_store, git_active):
    """미리보기는 새 엔드포인트가 아니라 창이 이미 부르는 조회에 얹힌다(P0006 §2 서두)."""
    from modules.flow_gate.documents.routers import documents as doc_routes

    _ledger_commit(_GROUP, _TR_A, "a" * 40, "0009-TR: a")

    payload = doc_routes.get_workflow_return_point(_TR_A, {"user_id": "u"})

    assert payload["ok"] is True
    # 기존 항목은 그대로다 — 키가 하나 늘 뿐이다.
    assert payload["return_point"]["exists"] is False
    assert payload["tr_commit_preview"]["group_status"] == "active"
    assert [c["doc_code"] for c in payload["tr_commit_preview"]["commits"]] == ["0009-TR"]


def test_a_broken_preview_is_omitted_and_the_read_still_answers(
    real_store, git_active, monkeypatch,
):
    """미리보기 실패가 되돌림 지점 본문까지 깨뜨리지 않는다. HTTP 200 그대로."""
    from modules.flow_gate.documents.routers import documents as doc_routes

    def _boom(group_id):
        raise RuntimeError("ledger table missing")

    monkeypatch.setattr(trc, "commit_preview", _boom)

    payload = doc_routes.get_workflow_return_point(_TR_A, {"user_id": "u"})

    assert payload["ok"] is True
    assert "tr_commit_preview" not in payload


def test_the_retry_route_answers_with_the_same_cancel_object(real_store, monkeypatch):
    from modules.flow_gate.documents.routers import documents as doc_routes

    monkeypatch.setattr(
        trc, "cancel_retry",
        lambda group_id: {**trc.empty_cancel_result(), "attempted": True},
    )

    payload = doc_routes.retry_cancel_tr_commits(_TR_A, {"user_id": "u"})

    assert payload["ok"] is True
    assert payload["tr_commit_cancel"]["attempted"] is True
    # 되감기 경로가 아니다 — 문서 상태에는 손대지 않는다.
    assert "reopened" not in payload


def test_the_retry_route_404s_on_an_unknown_document(real_store):
    from fastapi import HTTPException

    from modules.flow_gate.documents.routers import documents as doc_routes

    with pytest.raises(HTTPException) as excinfo:
        doc_routes.retry_cancel_tr_commits("flowgate.default.0332.9999-TR", {"user_id": "u"})

    assert excinfo.value.status_code == 404


def test_a_retry_that_blows_up_reports_nothing_attempted_not_a_500(real_store, monkeypatch):
    from modules.flow_gate.documents.routers import documents as doc_routes

    def _boom(group_id):
        raise RuntimeError("ledger exploded")

    monkeypatch.setattr(trc, "cancel_retry", _boom)

    payload = doc_routes.retry_cancel_tr_commits(_TR_A, {"user_id": "u"})

    assert payload == {"ok": True, "tr_commit_cancel": trc.empty_cancel_result()}


# ── 8. 감사 이벤트 (D0005 §4) ────────────────────────────────────────────────

def test_a_cancel_outcome_reaches_the_audit_trail(monkeypatch):
    """되돌리기 감사에 취소 결과가 남는다. 되감기 이벤트를 고쳐 쓰지 않고 한 줄을 더한다 —
    이미 커밋된 감사 행을 나중에 덧칠하는 것이 바로 이 그룹이 거부하는 이력 편집이다."""
    logged: list[dict] = []
    monkeypatch.setattr(
        rework.tr_commit_service, "cancel_for_reopen",
        lambda group_id, doc_ids: {
            "attempted": True, "blocked_reason": None, "stopped_reason": None,
            "retryable": False,
            "canceled": [{"doc_id": _TR_A, "doc_code": "0009-TR",
                          "commit": "a" * 7, "cancel_commit": "c" * 7}],
            "skipped": [],
        },
    )
    monkeypatch.setattr(rework.git_service, "reopen_group_git", lambda *_: None)
    monkeypatch.setattr(
        rework.event_logger, "log_event",
        lambda **kwargs: (logged.append(kwargs), {})[1],
    )

    rework._rearm_git(_PROJECT, _GROUP, [_TR_A], "usr_1")

    assert len(logged) == 1
    assert logged[0]["event_type"] == rework.EVT_TR_COMMIT_CANCEL
    # 되감기 이벤트 타입이 아니다 — 그것을 읽는 코드(중복 되감기 판정 등)는 그대로다.
    assert logged[0]["event_type"] != "workflow_reopen"
    assert logged[0]["group_id"] == _GROUP
    assert logged[0]["metadata"]["canceled"][0]["cancel_commit"] == "c" * 7


def test_a_rewind_with_nothing_to_cancel_writes_no_audit_row(monkeypatch):
    """모든 되감기마다 "아무 일도 없었음" 한 줄을 남기면 뜻 있는 줄이 파묻힌다."""
    logged: list[dict] = []
    monkeypatch.setattr(
        rework.tr_commit_service, "cancel_for_reopen",
        lambda group_id, doc_ids: trc.empty_cancel_result() | {"attempted": True},
    )
    monkeypatch.setattr(rework.git_service, "reopen_group_git", lambda *_: None)
    monkeypatch.setattr(
        rework.event_logger, "log_event",
        lambda **kwargs: (logged.append(kwargs), {})[1],
    )

    rework._rearm_git(_PROJECT, _GROUP, [_TR_A], "usr_1")

    assert logged == []
