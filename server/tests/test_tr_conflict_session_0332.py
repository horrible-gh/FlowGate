"""되돌리기·되살리기 충돌이 막다른 길이 아닌가 (0332 TR0019 — 남겨두려던 마지막 절).

앞 세 스위트가 "승인 때 커밋 / 되감을 때 되돌리기 / 앞으로 갈 때 다시 얹기"를 각각
고정했다. 셋 다 초록인데도 한 자리가 남아 있었다: **되돌림이 충돌하면 그걸로 끝**이었다.
루프는 그 자리에서 ``revert --quit`` + ``reset --hard HEAD`` 로 충돌한 인덱스를 지우고
"충돌했다, 손으로 정리해라"만 남겼다. 눌러도 같은 답이 나오니 [다시 시도] 조차 주지
않는 것이 맞았고 — 정말로 도움이 될 단 하나(파일을 열어 보는 것)는 방금 지워진 뒤였다.

그런데 FlowGate 는 이미 충돌을 해결한다. 마무리 병합이 충돌하면 세션 한 행이 남고,
Git 상태 패널이 그 행 위에 인라인 편집기를 열고, 그 merge_id 에 묶인 토큰으로 AI 를
불러 자동 해결까지 한다. 그 기계가 전부 **세션 행 하나**에 걸려 있으므로, 이번 변경의
전부는 "TR 충돌도 그 행이 되게 하라"다(088: ``kind`` + ``context`` 두 열).

이 스위트가 고정하는 계약 셋.

  * **충돌은 보존된다.** 커밋은 없지만 표식과 시퀀서는 살아 있고, 세션·그룹 상태가
    병합 충돌과 똑같은 모양으로 그것을 가리킨다.
  * **해결은 커밋이 아니다.** 표식이 다 사라져도 ``resolved_pending_review`` 에서 멈춘다.
    병합은 양쪽 다 사람이 쓴 코드라 "둘 다 살린다"가 대체로 정답이지만, 되돌림의 한쪽은
    "이 TR 이 한 일을 지워라"이고 반대쪽은 그 위에 얹힌 뒷작업이다 — 표식이 없는 파일이
    곧 옳은 되돌림이라는 뜻이 아니다. 자신 있게 틀린 해결을 그대로 커밋하면 화면은
    "취소 완료"라고 쓰는데 트리는 옛 상태도 새 상태도 아닌 것이 된다.
  * **포기는 예전 그대로다.** [되돌리기 중단]이 옛 파괴를 그대로 하고, ``git clean`` 은
    이 경로에 여전히 없다(0382).

세 층 모두 진짜로 돌린다. 진짜 git 저장소에서 실제로 충돌을 만들고, 실제 sqlite 에
마이그레이션을 적용한 뒤 세션 행을 쓴다 — 대역으로는 "함수를 불렀다"까지만 나온다.
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
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault(
    "FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode()
)
os.environ.setdefault(
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-tr-conflict-0332-")
)

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import git_integration as db_git  # noqa: E402
from modules.flow_gate.db import tr_commit_ledger as db_ledger  # noqa: E402
from modules.flow_gate.services import git_service as svc  # noqa: E402
from modules.flow_gate.services import tr_commit_service as trc  # noqa: E402
from modules.flow_gate.services.git_service import GitServiceError  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")

_SCRATCH = session_scratch("tr-conflict-0332")

_PROJECT = "flowgate"
_GROUP = "flowgate.default.0332"
_TR_A = "flowgate.default.0332.0009-TR"
_TR_B = "flowgate.default.0332.0011-TR"

_SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
    VALUES('{_PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'default', 'TR 커밋 충돌', 'OPEN',
           datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
        doc_id, project_id, module, group_id, type_code, seq, title, status,
        doc_review_status, created_at, updated_at)
    VALUES('{_TR_A}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 9,
           '커밋 포인트 생성 작업레포트', 'open', 'approved', datetime('now'), datetime('now')),
          ('{_TR_B}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 11,
           '되돌리기 취소 작업레포트', 'open', 'approved', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO group_git_state(
        group_id, project_id, branch, worktree_registered, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'work', 1, 'waiting', datetime('now'), datetime('now'));
"""


class _SqliteStore:
    """앞 세 스위트와 같은 최소 계약. ``_sql`` 은 일부러 두지 않는다 — 등록된 진짜 SQL
    본문이 쓰이게 하려는 것이다(0393 의 교훈)."""

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
    db_path = migrated_sqlite_db("tr_conflict_session_0332.db", seed_sql=_SEED_SQL)
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
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: {
        "enabled": 1, "base_branch": "main", "author_name": None, "author_email": None,
    })
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 1, "branch": "work", "status": "waiting",
    })
    monkeypatch.setattr(svc, "_project_name", lambda project_id: "flowgate")
    monkeypatch.setattr(svc, "src_root", lambda project_name, branch: repo)
    monkeypatch.setattr(svc.db_git, "try_acquire_lock", lambda project_id, holder: True)
    monkeypatch.setattr(svc.db_git, "release_lock", lambda project_id, holder: None)
    return repo


def _ledger_commit(doc_id, sha, subject="s"):
    return db_ledger.record_commit(
        group_id=_GROUP, doc_id=doc_id, commit_sha=sha, commit_subject=subject,
    )


def _seed_return_point(store, docs):
    """되감기가 남기는 반환점 한 벌. `cancel_retry` 가 이어받을 구간을 정하는 스냅샷이다."""
    front = max(seq for _doc_id, seq in docs)
    store._execute(
        "INSERT INTO workflow_return_points(group_id, front_seq, created_at, updated_at) "
        "VALUES(?, ?, datetime('now'), datetime('now'))", [_GROUP, front],
    )
    rp_id = store._fetch_one(
        "SELECT id FROM workflow_return_points WHERE group_id = ?", [_GROUP]
    )["id"]
    for doc_id, seq in docs:
        store._execute(
            "INSERT INTO workflow_return_point_docs"
            "(return_point_id, doc_id, seq, prev_status, fingerprint) "
            "VALUES(?, ?, ?, 'approved', 'fp')",
            [rp_id, doc_id, seq],
        )
    return rp_id


def _stored_status(store):
    return store._fetch_one(
        "SELECT status, merge_id FROM group_git_state WHERE group_id = ?", [_GROUP]
    )


def _parked_cancel(repo):
    """0009-TR 이 f.txt 를 A 로 만들고, 그 위에 사람이 C 를 얹었다. 취소는 충돌한다."""
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    row = _ledger_commit(_TR_A, sha_a, "0009-TR: A")
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")
    result = trc.cancel_tr_commits(_GROUP, [_TR_A])
    parked = result["conflict_session"]
    assert parked, "이 시나리오는 반드시 충돌로 끝나야 한다"
    return row, parked


def _parked_reapply(repo):
    """취소까지 끝난 뒤 사람이 같은 줄을 건드렸다. 되살리기가 충돌한다."""
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    row = _ledger_commit(_TR_A, sha_a, "0009-TR: A")
    assert trc.cancel_tr_commits(_GROUP, [_TR_A])["canceled"]
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")
    result = trc.restore_for_return(_GROUP, [_TR_A])
    parked = result["conflict_session"]
    assert parked, "이 시나리오는 반드시 충돌로 끝나야 한다"
    return row, parked


# ── 1. 충돌은 보존된다 ────────────────────────────────────────────────────────

@needs_git
def test_a_parked_conflict_serves_the_payload_the_merge_editor_already_reads(
    real_store, git_active, repo,
):
    """병합 충돌이 쓰던 조회가 그대로 답한다 — 화면·도구·AI 가 공짜로 따라오는 이유다.

    다른 것은 두 가지뿐이고 둘 다 **더해진** 것이다: 이게 어느 종류인지(``kind``), 그리고
    무엇을 되돌리는 중인지(``tr_conflict``). 병합 세션에서는 후자가 None 이다.
    """
    _row, parked = _parked_cancel(repo)

    payload = svc.list_conflicts(_GROUP, parked["merge_id"])

    assert payload["kind"] == "tr_revert"
    assert [f["path"] for f in payload["files"]] == ["f.txt"]
    assert payload["files"][0]["conflict_count"] == 1
    assert "<<<<<<<" in payload["files"][0]["content"]
    assert payload["tr_conflict"]["doc_code"] == "0009-TR"
    assert payload["tr_conflict"]["review_state"] == "open"
    # 되돌리기 충돌은 그룹 워크트리에 산다. 베이스 체크아웃을 읽었다면 이 파일은 없다.
    assert payload["files"][0]["content"] == (repo / "f.txt").read_text(encoding="utf-8")


@needs_git
def test_the_group_points_at_the_parked_conflict_the_same_way_a_merge_does(
    real_store, git_active, repo,
):
    """``status='conflict'`` + ``merge_id``. 패널이 이 두 값만 보고 편집기를 열기 때문에,
    새 상태값을 발명했다면 화면을 통째로 새로 만들어야 했다."""
    _row, parked = _parked_cancel(repo)

    state = _stored_status(real_store)
    assert (state["status"], state["merge_id"]) == ("conflict", parked["merge_id"])
    session = db_git.get_session(parked["merge_id"])
    assert session["status"] == "open"
    assert db_git.session_kind(session) == "tr_revert"
    # 되돌아갈 자리를 세션이 들고 있다 — 되감던 중이었지 병합 중이 아니었다.
    assert db_git.session_context(session)["prev_status"] == "waiting"


@needs_git
def test_the_panel_summary_carries_the_parked_conflict(real_store, git_active, repo):
    """패널이 "이 그룹은 왜 막혔나"를 추측하지 않게 한다. ``git_busy`` 는 남의 git 작업과
    자기 충돌을 구분하지 못하므로, 세션 자체를 실어 보낸다."""
    _row, parked = _parked_cancel(repo)

    summary = trc.group_commit_summary(_GROUP)

    assert summary["conflict_session"]["merge_id"] == parked["merge_id"]
    assert summary["conflict_session"]["kind"] == "tr_revert"
    assert summary["conflict_session"]["remaining"] == ["f.txt"]
    assert summary["conflict_session"]["review_state"] == "open"


# ── 2. 해결은 커밋이 아니다 ───────────────────────────────────────────────────

@needs_git
def test_resolving_every_file_stops_at_review_and_makes_no_commit(
    real_store, git_active, repo,
):
    """이 스위트의 핵심 한 줄.

    표식이 다 사라졌다는 사실은 "이 되돌림이 옳다"는 주장이 아니다. 병합은 마지막에
    사람이 [병합]을 누르지만, 되돌리기는 여기서 곧장 커밋까지 가 버릴 수 있었다.
    """
    row, parked = _parked_cancel(repo)
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    out = svc.resolve_conflicts(
        _GROUP, parked["merge_id"],
        [{"path": "f.txt", "content": "resolved\n"}], True,
    )

    assert out["result"]["status"] == "resolved_pending_review"
    assert out["result"]["merge_commit"] is None
    # 커밋은 없다. 세션도 살아 있다.
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    session = db_git.get_session(parked["merge_id"])
    assert session["status"] == "open"
    assert db_git.session_context(session)["review_state"] == "resolved"
    # 원장도 아직 아무 주장을 하지 않는다 — 취소는 커밋이 생겨야 취소다.
    assert db_ledger.get_by_id(row["id"])["state"] == "live"


@needs_git
def test_a_marker_left_behind_is_refused_before_anything_is_written(
    real_store, git_active, repo,
):
    """E12 의 전부-아니면-전무는 이 종류에도 그대로다."""
    _row, parked = _parked_cancel(repo)
    before = (repo / "f.txt").read_text(encoding="utf-8")

    with pytest.raises(GitServiceError) as exc:
        svc.resolve_conflicts(
            _GROUP, parked["merge_id"],
            [{"path": "f.txt", "content": "<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> z\n"}],
            True,
        )

    assert exc.value.code == "conflict_markers_remain"
    assert (repo / "f.txt").read_text(encoding="utf-8") == before


@needs_git
def test_the_commit_is_refused_while_the_session_is_still_open(
    real_store, git_active, repo,
):
    """검토 대기가 아닌 세션에 커밋 단추가 눌리면 거절이다 — 순서가 계약이다."""
    _row, parked = _parked_cancel(repo)

    with pytest.raises(GitServiceError) as exc:
        trc.commit_conflict_resolution(_GROUP, parked["merge_id"])

    assert exc.value.code == "conflict_markers_remain"
    assert db_git.get_session(parked["merge_id"])["status"] == "open"


# ── 3. 사람이 누르면 끝난다 ───────────────────────────────────────────────────

@needs_git
def test_the_commit_button_finishes_the_cancel_and_writes_the_ledger(
    real_store, git_active, repo,
):
    """해결 → 검토 → 커밋. 커밋 제목은 충돌이 나기 전에 정해 둔 그 제목이고, 원장은
    그제서야 취소가 된다."""
    row, parked = _parked_cancel(repo)
    svc.resolve_conflicts(
        _GROUP, parked["merge_id"], [{"path": "f.txt", "content": "resolved\n"}], True,
    )

    out = trc.commit_conflict_resolution(_GROUP, parked["merge_id"])

    assert out["result"]["status"] == "committed"
    assert out["result"]["ledger_written"] is True
    sha = out["result"]["commit"]
    assert _git(["log", "-1", "--format=%s"], repo).strip() == 'Revert "0009-TR: A"'
    assert _git(["rev-parse", "HEAD"], repo).strip() == sha
    # 해결한 내용이 그대로 커밋됐다. 시퀀서는 닫혔다.
    assert (repo / "f.txt").read_text(encoding="utf-8") == "resolved\n"
    assert svc._revert_in_flight(repo) is False
    assert _git(["status", "--porcelain"], repo).strip() == ""
    # 원장은 이 커밋을 취소 커밋으로 들고, 원래 커밋 해시는 그대로 남는다(D0005 K5).
    after = db_ledger.get_by_id(row["id"])
    assert (after["state"], after["cancel_commit"]) == ("canceled", sha)
    assert after["commit_sha"] == row["commit_sha"]
    # 세션은 닫히고 그룹은 원래 상태로 돌아간다.
    assert db_git.get_session(parked["merge_id"])["status"] == "done"
    assert _stored_status(real_store)["status"] == "waiting"


@needs_git
def test_the_reapply_direction_takes_the_same_road_and_ends_in_a_new_live_row(
    real_store, git_active, repo,
):
    """되살리기도 같은 세션·같은 편집·같은 단추다. 다른 것은 끝에 쓰는 원장 행뿐 —
    되살림은 취소 행을 되돌려 놓지 않고 새 live 행을 얹는다(D0005 K5)."""
    row, parked = _parked_reapply(repo)
    assert parked["kind"] == "tr_reapply"
    svc.resolve_conflicts(
        _GROUP, parked["merge_id"], [{"path": "f.txt", "content": "restored\n"}], True,
    )

    out = trc.commit_conflict_resolution(_GROUP, parked["merge_id"])

    assert out["result"]["ledger_written"] is True
    assert _git(["log", "-1", "--format=%s"], repo).strip() == 'Reapply "0009-TR: A"'
    assert (repo / "f.txt").read_text(encoding="utf-8") == "restored\n"
    # 취소 행은 취소인 채로 남고, 그 위에 되살림 행이 새로 생겼다.
    rows = db_ledger.list_by_group(_GROUP)
    assert [r["state"] for r in rows] == ["live", "canceled"]
    assert rows[0]["restored_from_id"] == row["id"]
    assert rows[0]["commit_sha"] == out["result"]["commit"]


@needs_git
def test_a_resolution_that_changes_nothing_is_recorded_not_treated_as_a_git_error(
    real_store, git_active, repo,
):
    """"양쪽 중 우리 것을 그대로 둔다"는 멀쩡한 해결이고, AI 도 종종 그렇게 답한다.

    그러면 인덱스에 올릴 것이 없다. 빈 커밋은 이력의 소음이고(D0005 K3), 그렇다고
    git 오류도 아니다 — 사람이 원한 결과가 이미 트리에 있는 상태다. 취소 루프가 빈
    되돌림을 적어 두는 방식 그대로 원장에 남는다: 커밋 없이 canceled.
    """
    row, parked = _parked_cancel(repo)
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    svc.resolve_conflicts(
        _GROUP, parked["merge_id"], [{"path": "f.txt", "content": "C\n"}], True,
    )
    out = trc.commit_conflict_resolution(_GROUP, parked["merge_id"])

    assert out["result"]["status"] == "empty"
    assert out["result"]["commit"] is None
    assert out["result"]["ledger_written"] is True
    # 커밋은 없고 트리는 그대로, 시퀀서는 닫혔다.
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    assert svc._revert_in_flight(repo) is False
    assert _git(["status", "--porcelain"], repo).strip() == ""
    after = db_ledger.get_by_id(row["id"])
    assert (after["state"], after["cancel_commit"], after["cancel_reason"]) == (
        "canceled", None, "empty_revert",
    )
    assert db_git.get_session(parked["merge_id"])["status"] == "done"


@needs_git
def test_committing_the_resolution_carries_on_with_the_rest_of_the_run(
    real_store, git_active, repo,
):
    """충돌 하나를 풀었다고 나머지를 사람이 다시 시켜야 하는 것은 아니다.

    되감기는 커밋 **한 줄기**를 벗겨 달라는 요청이고, 충돌은 그 자리에서 멈추면서
    아래 것들을 ``not_attempted`` 로 둔다(순서 있는 작업이라 그게 맞다). 문제는 그
    결과가 ``retryable=False`` 라 어떤 화면도 두 번째 누름을 권하지 않는다는 것이었다 —
    풀고 나면 반쯤 되감긴 트리를 두고 아무 단추도 그 사실을 말하지 않는다. 그래서
    커밋이 끝나면 남은 줄기를 여기서 이어서 처리한다.
    """
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    row_a = _ledger_commit(_TR_A, sha_a, "0009-TR: a")
    (repo / "f.txt").write_text("X\n", encoding="utf-8")
    sha_b = _commit(repo, "0011-TR: X")
    row_b = _ledger_commit(_TR_B, sha_b, "0011-TR: X")
    # 최신 커밋이 건드린 줄을 사람이 다시 건드렸다 — 0011-TR 의 취소가 충돌한다.
    (repo / "f.txt").write_text("Y\n", encoding="utf-8")
    _commit(repo, "manual fix")
    # 되감기가 남기는 상태 그대로: 반환점 한 벌 + 되감긴 두 문서는 pending_review.
    # 이어받기는 이 스냅샷이 정한 구간만 건드린다 — 구간 밖의 살아 있는 커밋까지 벗기면
    # 사람이 시키지 않은 되감기가 되고, 그 사이 다시 승인된 단계는 건너뛴다.
    _seed_return_point(real_store, ((_TR_A, 9), (_TR_B, 11)))
    real_store._execute(
        "UPDATE documents SET doc_review_status = 'pending_review' "
        "WHERE doc_id IN (?, ?)", [_TR_A, _TR_B],
    )

    result = trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    parked = result["conflict_session"]
    assert parked and parked["doc_code"] == "0011-TR"
    # 아래 줄은 손대지 않은 채 남아 있다 — 이것이 이어받을 대상이다.
    assert [(s["doc_code"], s["reason"]) for s in result["skipped"]] == [
        ("0011-TR", "conflict"), ("0009-TR", "not_attempted"),
    ]
    assert db_ledger.get_by_id(row_a["id"])["state"] == "live"

    svc.resolve_conflicts(
        _GROUP, parked["merge_id"], [{"path": "f.txt", "content": "Y resolved\n"}], True,
    )
    out = trc.commit_conflict_resolution(_GROUP, parked["merge_id"])

    # 충돌한 줄은 사람이 확인한 대로 커밋됐고,
    assert db_ledger.get_by_id(row_b["id"])["state"] == "canceled"
    # 아래 줄은 같은 호출 안에서 이어서 취소됐다.
    continued = out["result"]["continued"]
    assert [c["doc_code"] for c in continued["canceled"]] == ["0009-TR"]
    after_a = db_ledger.get_by_id(row_a["id"])
    assert after_a["state"] == "canceled"
    assert after_a["cancel_commit"]
    # 트리에서도 실제로 벗겨졌다 — 원장만 그렇게 말하는 것이 아니다.
    assert not (repo / "a.py").exists()
    assert svc._revert_in_flight(repo) is False


@needs_git
def test_giving_up_on_a_reapply_leaves_the_row_canceled_and_the_tree_clean(
    real_store, git_active, repo,
):
    """포기의 결말은 "아무 일도 없었다"여야 한다 — 되살리기를 그만두면 그 단계는
    취소된 채로 남는다."""
    row, parked = _parked_reapply(repo)
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    trc.abort_conflict_resolution(_GROUP, parked["merge_id"])

    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    assert svc._revert_in_flight(repo) is False
    assert _git(["status", "--porcelain"], repo).strip() == ""
    after = db_ledger.get_by_id(row["id"])
    assert after["state"] == "canceled"
    assert json.loads(after["cancel_attempt_log"])[-1]["reason"] == "conflict_abandoned"
    assert db_git.get_session(parked["merge_id"])["status"] == "aborted"
    assert _stored_status(real_store)["status"] == "waiting"


# ── 4. 버려진 세션을 청소가 회수한다 ──────────────────────────────────────────

@needs_git
def test_the_sweep_closes_a_session_whose_revert_is_no_longer_in_flight(
    real_store, git_active, repo,
):
    """청소는 베이스 체크아웃의 MERGE_HEAD 로 판단해 왔다. TR 충돌에는 그런 것이 없고
    워크트리도 다르므로, 갈라 두지 않았다면 열려 있는 세션을 전부 고아로 닫았을 것이다.

    반대 방향도 같이 고정한다: 되돌림이 아직 살아 있으면 손대지 않는다.
    """
    _row, parked = _parked_cancel(repo)

    svc.merge_session_sweep()
    assert db_git.get_session(parked["merge_id"])["status"] == "open"

    # 사람이 FlowGate 밖에서 되돌림을 끝냈다 — 이제 가리킬 것이 없다.
    _git(["revert", "--quit"], repo)
    _git(["reset", "--hard", "HEAD"], repo)

    svc.merge_session_sweep()

    assert db_git.get_session(parked["merge_id"])["status"] == "aborted"
    assert _stored_status(real_store)["status"] == "waiting"


# ── 5. AI 에게 어느 질문인지 말해 준다 ────────────────────────────────────────

def test_the_ai_mention_tells_a_revert_apart_from_a_merge():
    """같은 충돌 표식이라도 물음이 다르다.

    병합은 "두 갈래를 합쳐라"라서 양쪽을 다 남기면 대체로 맞는다. 되돌리기에 그 습관을
    들고 오면 지우라고 한 커밋을 조용히 되살려 놓고 "해결 완료"라고 답한다. 멘트에서
    이 문단만이 종류별로 갈리며, 나머지(청크 payload·엔드포인트·완료 판정)는 공유한다.
    """
    # 지연 임포트: 이 모듈은 앱 설정(ALLOWED_ORIGIN/CONTEXT)을 끌고 오므로 수집
    # 시점에 올리면 스위트 전체가 collection error 로 죽는다.
    from modules.flow_gate.api import token_routes

    merge = token_routes._conflict_task_section("merge", {})
    revert = token_routes._conflict_task_section(
        "tr_revert", {"doc_code": "0009-TR", "subject": "0009-TR: A"}
    )
    reapply = token_routes._conflict_task_section(
        "tr_reapply", {"doc_code": "0009-TR", "subject": "0009-TR: A"}
    )

    assert "NOT a branch merge" not in merge
    for text in (revert, reapply):
        assert "NOT a branch merge" in text
        assert "Do not resolve it by keeping both sides." in text
        assert "0009-TR" in text
        # 커밋은 사람이 누른다는 사실을 AI 가 알아야 한다 — 모르면 완료 보고가 틀린다.
        assert "resolved_pending_review" in text
    assert "UNDOING" in revert
    assert "PUTTING BACK" in reapply
