"""앞으로 복원이 취소한 소스를 되살리는가 (0332 T0018 — R0001 의 남은 세 번째 절).

0012-TR 이 "승인 때 커밋"을, 0014-TR 이 "되감을 때 되돌리기"를 만들었고 이 스위트는
"앞으로 갈 때 다시 얹기"(D0005 K11)를 본다. 셋이 한 세트라는 것이 R0001 의 요구였고,
둘만 있는 상태는 "되감으면 코드가 따라오는데 앞으로 오면 안 따라오는 타임머신"이라
일관되게 못 하는 것보다 나쁘다 — 사람은 "되감았다 다시 왔으니 원래대로겠지"라고 믿는데
워크트리는 아니기 때문이다.

앞 스위트와 같은 이유로 세 층을 진짜로 돌린다. 특히 되살리기는 **되돌림의 되돌림**이라
대역으로는 "revert 를 한 번 더 불렀다"까지만 증명된다. 순서가 정말 역순인지, 사람이 손으로
다시 한 단계를 정말로 건드리지 않는지, 충돌이 워크트리를 원상으로 되돌리는지는 진짜
저장소에서만 나온다.

이 시험이 고정하는 계약 두 줄.
  * **앞으로 복원은 문서와 소스를 같이 데려온다. 못 하면 왜 못 했는지 말한다** (D0005 K8·K11).
  * **사람이 이미 다시 한 일은 두 번 얹지 않는다** — 되살리기의 유일한 파괴적 실패 모드다.

그리고 T0018 §3-4 의 구멍 하나: 되돌리기가 막힌 뒤 그 단계를 다시 승인하면, 취소하지
못한 **옛 live 행**이 어떤 경로로도 다시 잡히지 않았다. 문서 단위로 거르던 필터를 행
단위로 고쳤고, 그 계약이 §7 에 있다.
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
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-tr-reapply-0332-")
)

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import git_integration as db_git  # noqa: E402
from modules.flow_gate.db import tr_commit_ledger as db_ledger  # noqa: E402
from modules.flow_gate.services import git_service as svc  # noqa: E402
from modules.flow_gate.services import tr_commit_service as trc  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")

_SCRATCH = session_scratch("tr-reapply-0332")

_PROJECT = "flowgate"
_GROUP = "flowgate.default.0332"
_TR_A = "flowgate.default.0332.0009-TR"
_TR_B = "flowgate.default.0332.0011-TR"
_D_DOC = "flowgate.default.0332.0005-D"

_SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
    VALUES('{_PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'default', 'TR 커밋 되살리기', 'OPEN',
           datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
        doc_id, project_id, module, group_id, type_code, seq, title, status,
        doc_review_status, created_at, updated_at)
    VALUES('{_D_DOC}', '{_PROJECT}', 'default', '{_GROUP}', 'D', 5,
           'TR 커밋 취소 기본설계', 'open', 'approved', datetime('now'), datetime('now')),
          ('{_TR_A}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 9,
           '커밋 포인트 생성 작업레포트', 'open', 'approved', datetime('now'), datetime('now')),
          ('{_TR_B}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 11,
           '되돌리기 취소 작업레포트', 'open', 'approved', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO group_git_state(
        group_id, project_id, branch, worktree_registered, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'work', 1, 'none', datetime('now'), datetime('now'));
"""


class _SqliteStore:
    """앞 두 스위트와 같은 최소 계약. ``_sql`` 은 일부러 두지 않는다 — 등록된 진짜 SQL
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
    db_path = migrated_sqlite_db("tr_commit_reapply_0332.db", seed_sql=_SEED_SQL)
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
        "worktree_registered": 1, "branch": "work", "status": "none",
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


def _two_committed_steps(repo):
    """0009-TR 이 a.py 를, 0011-TR 이 b.py 를 남긴 상태."""
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: a")
    _ledger_commit(_TR_A, sha_a, "0009-TR: a")
    (repo / "b.py").write_text("b = 1\n", encoding="utf-8")
    sha_b = _commit(repo, "0011-TR: b")
    _ledger_commit(_TR_B, sha_b, "0011-TR: b")
    return sha_a, sha_b


# ── 1. 원장의 되살리기 열 (086) ───────────────────────────────────────────────

def test_a_reapply_adds_a_new_live_row_and_leaves_the_canceled_one_alone(real_store):
    """D0005 K5 — 되살림은 취소 행을 되돌려 놓는 것이 아니라 새 행이다. 취소를 지우면
    되감았다는 사실 자체가 기록에서 사라진다."""
    row = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)

    new_row = db_ledger.record_reapply(
        group_id=_GROUP, doc_id=_TR_A, commit_sha="r" * 40,
        commit_subject="0009-TR: a", restored_from_id=row["id"],
    )

    assert new_row["state"] == "live"
    assert new_row["commit_sha"] == "r" * 40
    assert new_row["restored_from_id"] == row["id"]
    # 취소 행은 글자 하나 안 바뀐다.
    old = db_ledger.get_by_id(row["id"])
    assert (old["state"], old["cancel_commit"]) == ("canceled", "c" * 40)
    # 두 줄 다 남는다 — 지우개가 아니다.
    assert [r["state"] for r in db_ledger.list_by_group(_GROUP)] == ["live", "canceled"]


def test_the_reappliable_query_orders_the_peel_the_inverse_of_the_cancel(real_store):
    """취소는 ``id DESC`` 로 최신 커밋부터 얹었으므로, 마지막에 얹힌 취소 커밋(= 가장
    작은 id 의 행)이 git 에서 가장 위에 있다. 되살리기는 그것부터 벗겨야 한다."""
    row_a = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    row_b = _ledger_commit(_TR_B, "b" * 40, "0011-TR: b")
    # 취소가 실제로 도는 순서: B(최신) 먼저, A 나중.
    db_ledger.mark_canceled(row_b["id"], cancel_commit="cb" + "0" * 38)
    db_ledger.mark_canceled(row_a["id"], cancel_commit="ca" + "0" * 38)

    rows = db_ledger.reappliable_rows(_GROUP, [_TR_A, _TR_B])

    assert [r["id"] for r in rows] == [row_a["id"], row_b["id"]]
    assert all(int(r["newer_live"] or 0) == 0 for r in rows)


def test_a_row_with_a_newer_live_row_is_flagged_not_dropped(real_store):
    """사람이 손으로 다시 한 단계. 조용히 빼면 화면이 "왜 이 단계만 안 돌아왔지"에
    답할 말을 잃는다 — 걸러 내되 이유를 달고 돌려준다(L0007 §5)."""
    row = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)
    _ledger_commit(_TR_A, "n" * 40, "0009-TR: 다시 한 작업")

    rows = db_ledger.reappliable_rows(_GROUP, [_TR_A])

    assert [r["id"] for r in rows] == [row["id"]]
    assert int(rows[0]["newer_live"]) == 1


def test_newest_live_id_by_doc_names_only_the_freshest_row(real_store):
    stale = _ledger_commit(_TR_A, "a" * 40, "옛 커밋")
    fresh = _ledger_commit(_TR_A, "n" * 40, "새 커밋")

    newest = db_ledger.newest_live_id_by_doc([_TR_A, _TR_B])

    assert newest == {_TR_A: fresh["id"]}
    assert newest[_TR_A] != stale["id"]


# ── 2. 왕복 (T0018 §4 시나리오 A·B) ──────────────────────────────────────────

@needs_git
def test_a_forward_restore_reapplies_the_canceled_commits_newest_cancel_first(
    real_store, git_active, repo,
):
    """되감았다 앞으로 오면 소스가 그대로 돌아와 있어야 한다. 그리고 되돌림을 벗기는
    순서는 취소가 얹은 순서의 정확한 역순이다."""
    sha_a, sha_b = _two_committed_steps(repo)
    trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    assert not (repo / "a.py").exists() and not (repo / "b.py").exists()

    result = trc.restore_for_return(_GROUP, [_TR_A, _TR_B])

    assert result["attempted"] is True
    assert result["blocked_reason"] is None and result["stopped_reason"] is None
    assert result["skipped"] == []
    assert [line["doc_code"] for line in result["reapplied"]] == ["0009-TR", "0011-TR"]
    # 소스가 트리에 그대로 돌아왔다 — 이게 K11 이 없어서 안 되던 바로 그 일이다.
    assert (repo / "a.py").read_text(encoding="utf-8") == "a = 1\n"
    assert (repo / "b.py").read_text(encoding="utf-8") == "b = 1\n"
    # 시나리오 B: C1·C2·revert C2·revert C1·reapply C1·reapply C2 (오래된 것부터).
    subjects = _git(["log", "-6", "--pretty=%s"], repo).strip().split("\n")
    assert subjects == [
        'Reapply "0011-TR: b"',
        'Reapply "0009-TR: a"',
        'Revert "0009-TR: a"',
        'Revert "0011-TR: b"',
        "0011-TR: b",
        "0009-TR: a",
    ]
    # 되살림 커밋 본문은 양쪽 끝을 다 지목한다 — 하나의 grep 으로 세 커밋이 꿰인다.
    body = _git(["log", "-1", "--pretty=%b"], repo)
    assert sha_b in body                      # 원본 TR 커밋
    assert f"(group {_GROUP})" in body
    assert "reapply" in body.lower()
    assert sha_a not in body                  # 남의 단계는 섞이지 않는다
    # 원장은 취소 2행 + 되살림 live 2행. 아무 줄도 사라지지 않는다.
    rows = db_ledger.list_by_group(_GROUP)
    assert sorted(r["state"] for r in rows) == ["canceled", "canceled", "live", "live"]
    restored = [r for r in rows if r["state"] == "live"]
    assert all(r["restored_from_id"] for r in restored)


@needs_git
def test_the_strip_marker_comes_back_to_committed_after_a_restore(
    real_store, git_active, repo,
):
    """D0005 §6.1 의 "가장 새 행이 이긴다" 규칙 그대로 표식이 돌아온다. 되살리기용 표식
    경로를 새로 만들지 않았다는 것을 여기서 고정한다."""
    _two_committed_steps(repo)
    trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    assert trc.slot_commit_states([_TR_A])[_TR_A]["state"] == "canceled"

    trc.restore_for_return(_GROUP, [_TR_A, _TR_B])

    mark = trc.slot_commit_states([_TR_A])[_TR_A]
    assert mark["state"] == "live"
    # 처음 승인이 남긴 커밋과 되살린 커밋은 화면에서 구분돼야 한다.
    assert mark["restored"] is True


# ── 3. 이중 적용 금지 (시나리오 C) ───────────────────────────────────────────

@needs_git
def test_a_step_redone_by_hand_is_never_double_applied(real_store, git_active, repo):
    """되돌린 뒤 사람이 그 단계를 다시 작업해 재승인했다. 앞으로 복원이 옛 커밋을 위에
    다시 얹으면 같은 일이 두 번 적용된다 — 되살리기의 유일한 파괴적 실패 모드다."""
    sha_a, _sha_b = _two_committed_steps(repo)
    trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    # 0009-TR 을 손으로 다시 작업해 재승인 — 새 커밋과 새 live 행이 생긴다.
    (repo / "a.py").write_text("a = 2   # 다시 한 작업\n", encoding="utf-8")
    redone = _commit(repo, "0009-TR: a (redone)")
    _ledger_commit(_TR_A, redone, "0009-TR: a (redone)")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.restore_for_return(_GROUP, [_TR_A, _TR_B])

    assert [(s["doc_code"], s["reason"]) for s in result["skipped"]] == [
        ("0009-TR", "superseded"),
    ]
    assert [line["doc_code"] for line in result["reapplied"]] == ["0011-TR"]
    # 다시 한 작업이 그대로다 — 옛 내용이 위에 덮이지 않았다.
    assert (repo / "a.py").read_text(encoding="utf-8") == "a = 2   # 다시 한 작업\n"
    assert (repo / "b.py").read_text(encoding="utf-8") == "b = 1\n"
    # 0011-TR 하나만 새 커밋을 얹었다.
    assert _git(["rev-list", "--count", f"{head_before}..HEAD"], repo).strip() == "1"
    # 재작업 행은 손대지 않았고, 옛 취소 행도 취소인 채다.
    assert db_ledger.get_by_id(
        [r for r in db_ledger.list_by_group(_GROUP) if r["commit_sha"] == redone][0]["id"]
    )["restored_from_id"] is None


@needs_git
def test_an_empty_cancel_has_no_commit_to_peel_back_off(real_store, git_active, repo):
    """빈 되돌림으로 취소된 행에는 되돌릴 취소 커밋이 없다. 조용히 빠지지 않고 이유가
    붙어 나온다 — ``superseded`` 와 다음 할 일이 다르기 때문이다."""
    row = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    db_ledger.mark_canceled(row["id"], cancel_commit=None, reason="empty_revert")

    result = trc.restore_for_return(_GROUP, [_TR_A])

    assert result["attempted"] is True
    assert result["reapplied"] == []
    assert [(s["doc_code"], s["reason"]) for s in result["skipped"]] == [
        ("0009-TR", "no_cancel_commit"),
    ]


@needs_git
def test_a_restore_run_twice_puts_nothing_back_a_second_time(
    real_store, git_active, repo,
):
    """되살리기가 만든 live 행 자체가 두 번째 실행의 방어다(K6 와 같은 자리)."""
    _two_committed_steps(repo)
    trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    trc.restore_for_return(_GROUP, [_TR_A, _TR_B])
    head_after_first = _git(["rev-parse", "HEAD"], repo).strip()

    second = trc.restore_for_return(_GROUP, [_TR_A, _TR_B])

    assert second["attempted"] is True
    assert second["reapplied"] == []
    assert [s["reason"] for s in second["skipped"]] == ["superseded", "superseded"]
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_after_first


# ── 4. 차단과 충돌 (시나리오 D·E) ────────────────────────────────────────────

@needs_git
def test_a_blocked_reapply_leaves_the_rows_canceled_and_says_why(
    real_store, git_active, repo,
):
    """워크트리가 더러우면 아무것도 하지 않는다 — 되살림을 사람의 미커밋 변경과 섞지
    않으려는 것이고, 취소가 막히는 이유와 같은 자리(G10)다. 문서 복원은 그대로 서고
    ``retryable=True`` 로 재시도 경로가 열린다."""
    _two_committed_steps(repo)
    trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    (repo / "f.txt").write_text("hand edit\n", encoding="utf-8")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.restore_for_return(_GROUP, [_TR_A, _TR_B])

    assert result["attempted"] is False
    assert result["blocked_reason"] == "dirty_worktree"
    assert result["retryable"] is True
    assert result["reapplied"] == []
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    # 원장은 취소인 채다 — 되살렸다고 말하지 않는다.
    assert [r["state"] for r in db_ledger.list_by_group(_GROUP)] == ["canceled", "canceled"]
    state = real_store._fetch_one(
        "SELECT * FROM group_git_state WHERE group_id = ?", [_GROUP]
    )
    assert state["last_cancel_block_sub"] == "dirty_worktree"


@needs_git
def test_a_reapply_conflict_is_parked_as_a_session_and_leaves_the_rest_untried(
    real_store, git_active, repo,
):
    """되살리기 충돌도 막다른 길이 아니다(TR0019).

    T0018 §2-2 는 이 자리를 다음 그룹으로 미뤘었다. 지금은 되돌리기와 **같은 함수**로
    같은 표에 세션을 남기고, 방향만 ``tr_reapply`` 로 다르다. 멈추는 것, 나머지를
    ``not_attempted`` 로 두는 것, 같은 요청을 다시 보내는 버튼을 주지 않는 것은 그대로다.
    """
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    row_a = _ledger_commit(_TR_A, sha_a, "0009-TR: A")
    trc.cancel_tr_commits(_GROUP, [_TR_A])
    # 취소 뒤 같은 줄을 사람이 다시 건드렸다 — 되살림이 여기서 충돌한다.
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")
    head_before = _git(["rev-parse", "HEAD"], repo).strip()

    result = trc.restore_for_return(_GROUP, [_TR_A])

    assert result["attempted"] is True
    assert result["reapplied"] == []
    assert result["stopped_reason"] == "conflict"
    assert [(s["doc_code"], s["reason"]) for s in result["skipped"]] == [
        ("0009-TR", "conflict"),
    ]
    assert result["retryable"] is False
    # 커밋은 없지만 충돌은 살아 있다 — 해결할 것이 남아 있어야 해결할 수 있다.
    parked = result["conflict_session"]
    assert parked and parked["kind"] == "tr_reapply"
    assert parked["files"] == ["f.txt"]
    assert _git(["rev-parse", "HEAD"], repo).strip() == head_before
    assert "<<<<<<<" in (repo / "f.txt").read_text(encoding="utf-8")
    assert svc._revert_in_flight(repo) is True
    # 행은 취소인 채로 남고, 무엇이 실패했는지는 원장에 남는다.
    after = db_ledger.get_by_id(row_a["id"])
    assert after["state"] == "canceled"
    assert json.loads(after["cancel_attempt_log"])[-1]["reason"] == "revert_conflict"


@needs_git
def test_a_conflict_leaves_the_older_cancels_untouched(real_store, git_active, repo):
    """순서 있는 작업이다. 충돌한 되살림 아래의 것을 계속 벗기면 더 세게 충돌한다."""
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    _ledger_commit(_TR_A, sha_a, "0009-TR: A")
    (repo / "b.py").write_text("b = 1\n", encoding="utf-8")
    sha_b = _commit(repo, "0011-TR: b")
    _ledger_commit(_TR_B, sha_b, "0011-TR: b")
    trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    # 가장 먼저 벗겨질 것은 0009-TR 의 취소다. 그 자리를 사람이 막아 둔다.
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")

    result = trc.restore_for_return(_GROUP, [_TR_A, _TR_B])

    assert [(s["doc_code"], s["reason"]) for s in result["skipped"]] == [
        ("0009-TR", "conflict"), ("0011-TR", "not_attempted"),
    ]
    assert not (repo / "b.py").exists()


def test_a_group_with_nothing_canceled_ends_quietly_not_on_an_error(real_store):
    """되감은 적 없는 그룹을 앞으로 복원해도 git 오류 화면이 뜨면 안 된다 — 게이트보다
    앞에서 조용히 끝나야 한다(L0007 §4.1 주 1과 같은 이유)."""
    result = trc.restore_for_return(_GROUP, [_TR_A, _TR_B])

    assert result == {
        "attempted": True, "blocked_reason": None,
        "reapplied": [], "skipped": [], "stopped_reason": None, "retryable": False,
        "conflict_session": None,
    }


@pytest.mark.parametrize(
    "state, expect_reason, expect_retryable",
    [
        ({"worktree_registered": 1, "branch": "work", "status": "merged"},
         "already_merged", False),
        ({"worktree_registered": 1, "branch": "work", "status": "merging"},
         "git_busy", True),
        ({"worktree_registered": 0, "branch": "work", "status": "none"},
         "no_worktree", False),
    ],
)
def test_the_reapply_reuses_the_cancel_gate_ladder_verbatim(
    real_store, git_active, repo, monkeypatch, state, expect_reason, expect_retryable,
):
    """게이트를 한 벌 더 만들지 않았다는 것이 이 시험의 내용이다. 병합된 그룹은 취소도
    되살림도 fail-closed 이고(K7-①), 판정표는 ``CANCEL_BLOCK_RETRYABLE`` 하나뿐이다."""
    row = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: state)

    result = trc.restore_for_return(_GROUP, [_TR_A])

    assert result["attempted"] is False
    assert result["blocked_reason"] == expect_reason
    assert result["retryable"] is expect_retryable
    assert result["retryable"] is trc.CANCEL_BLOCK_RETRYABLE[expect_reason]
    # 어떤 게이트도 원장 행을 건드리지 않는다.
    assert db_ledger.get_by_id(row["id"])["state"] == "canceled"


def test_every_reapply_skip_reason_stays_inside_the_closed_set(real_store):
    """화면이 그릴 문구를 갖고 있지 않은 코드를 만들어 보내면 사유가 빈칸으로 보인다."""
    assert set(trc.REAPPLY_SKIP_REASONS) == {
        "superseded", "no_cancel_commit", "empty_revert", "conflict", "not_attempted",
    }


@needs_git
def test_the_lock_is_released_even_when_the_reapply_conflicts(
    real_store, git_active, repo,
):
    """L0007 §2.1 ③ — 잠금은 재진입이 안 된다. 되살리기가 쥔 채 끝나면 다음 git 작업이
    5초를 기다린 뒤 조용히 실패한다."""
    held: list[str] = []
    svc.db_git.try_acquire_lock = lambda project_id, holder: (held.append(holder), True)[1]
    svc.db_git.release_lock = lambda project_id, holder: held.remove(holder)
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    _ledger_commit(_TR_A, sha_a, "0009-TR: A")
    trc.cancel_tr_commits(_GROUP, [_TR_A])
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")

    trc.restore_for_return(_GROUP, [_TR_A])

    assert held == []


# ── 5. 커밋 메시지 (T0018 §3-3) ──────────────────────────────────────────────

def test_the_reapply_subject_names_the_original_tr_not_the_cancel():
    """``Revert "Revert "0009-TR: ...""`` 는 git 이 알아서 쓰는 문장이고 읽는 사람에게
    아무것도 말하지 않는다."""
    assert svc.reapply_subject("0009-TR: 제목") == 'Reapply "0009-TR: 제목"'


def test_a_very_long_subject_is_clipped_inside_the_quotes():
    long_title = "x" * (svc.COMMIT_SUBJECT_MAX + 50)

    subject = svc.reapply_subject(long_title)

    assert len(subject) <= svc.COMMIT_SUBJECT_MAX
    assert subject.startswith('Reapply "') and subject.endswith('…"')


def test_the_reapply_body_names_both_ends_of_the_round_trip():
    body = svc.reapply_body("c" * 40, "a" * 40, "0009-TR", _GROUP)

    # 첫 줄은 git 자신의 도구가 찾는 형식이고, 실제로 되돌리는 것은 취소 커밋이다.
    assert body.splitlines()[0] == f"This reverts commit {'c' * 40}."
    # 원본 TR 커밋은 그 아래에 따로 — 어느 해시로 grep 해도 세 커밋이 다 잡힌다.
    assert "a" * 40 in body
    assert f"(group {_GROUP})" in body


# ── 6. 라우트와 재시도 (시나리오 D 후반) ─────────────────────────────────────

def test_the_restore_route_adds_one_key_and_renames_nothing(real_store, monkeypatch):
    """P0006 의 원칙 그대로 — 기존 응답 키는 이름도 뜻도 그대로이고 키가 하나 늘 뿐이다.
    0142·0381 의 단언이 이 위에서 그대로 서야 한다."""
    from modules.flow_gate.documents.routers import documents as doc_routes

    payload: dict = {"ok": True, "restored": [_TR_A]}
    monkeypatch.setattr(
        trc, "restore_for_return",
        lambda group_id, doc_ids: {**trc.empty_restore_result(), "attempted": True},
    )

    doc_routes._attach_tr_commit_restore(payload, _PROJECT, _GROUP, [_TR_A], "usr_1")

    assert payload["ok"] is True and payload["restored"] == [_TR_A]
    assert payload["tr_commit_restore"]["attempted"] is True


def test_a_reapply_that_blows_up_drops_the_key_instead_of_half_filling_it(
    real_store, monkeypatch,
):
    """L0007 §5 — 반쪽짜리 객체는 화면이 "소스가 돌아왔다"고 말하게 만든다. 키를 통째로
    뺀다. 문서 복원은 그대로 200 이다(D0005 K8)."""
    from modules.flow_gate.documents.routers import documents as doc_routes

    def _boom(group_id, doc_ids):
        raise RuntimeError("ledger table missing")

    monkeypatch.setattr(trc, "restore_for_return", _boom)
    payload: dict = {"ok": True, "restored": [_TR_A]}

    doc_routes._attach_tr_commit_restore(payload, _PROJECT, _GROUP, [_TR_A], "usr_1")

    assert "tr_commit_restore" not in payload
    assert payload == {"ok": True, "restored": [_TR_A]}


def test_a_restore_outcome_reaches_the_audit_trail(real_store, monkeypatch):
    from modules.flow_gate.documents.routers import documents as doc_routes
    from modules.flow_gate.workflow import event_logger

    logged: list[dict] = []
    monkeypatch.setattr(
        trc, "restore_for_return",
        lambda group_id, doc_ids: {
            **trc.empty_restore_result(), "attempted": True,
            "reapplied": [{"doc_id": _TR_A, "doc_code": "0009-TR",
                           "commit": "a" * 7, "reapply_commit": "r" * 7}],
        },
    )
    monkeypatch.setattr(
        event_logger, "log_event", lambda **kwargs: (logged.append(kwargs), {})[1],
    )

    doc_routes._attach_tr_commit_restore({}, _PROJECT, _GROUP, [_TR_A], "usr_1")

    assert len(logged) == 1
    assert logged[0]["event_type"] == doc_routes.EVT_TR_COMMIT_REAPPLY
    assert logged[0]["metadata"]["reapplied"][0]["reapply_commit"] == "r" * 7


def test_a_restore_with_nothing_to_put_back_writes_no_audit_row(real_store, monkeypatch):
    from modules.flow_gate.documents.routers import documents as doc_routes
    from modules.flow_gate.workflow import event_logger

    logged: list[dict] = []
    monkeypatch.setattr(
        trc, "restore_for_return",
        lambda group_id, doc_ids: {**trc.empty_restore_result(), "attempted": True},
    )
    monkeypatch.setattr(
        event_logger, "log_event", lambda **kwargs: (logged.append(kwargs), {})[1],
    )

    doc_routes._attach_tr_commit_restore({}, _PROJECT, _GROUP, [_TR_A], "usr_1")

    assert logged == []


@needs_git
def test_the_reapply_retry_works_after_the_return_point_is_gone(
    real_store, git_active, repo,
):
    """완료된 앞으로 복원은 반환점을 지운다(``return_point_cleared``). 재시도를 반환점에
    매달았다면 바로 그때 "할 일 없음"이라고 답했을 것이다 — 문서는 앞으로 왔는데 소스는
    안 왔고 반환점은 사라진, 정확히 답이 필요한 상태에서."""
    _two_committed_steps(repo)
    trc.cancel_tr_commits(_GROUP, [_TR_A, _TR_B])
    # 반환점 표에는 이 그룹의 줄이 하나도 없다.
    assert real_store._fetch_one(
        "SELECT id FROM workflow_return_points WHERE group_id = ?", [_GROUP]
    ) is None

    result = trc.reapply_retry(_GROUP)

    assert [line["doc_code"] for line in result["reapplied"]] == ["0009-TR", "0011-TR"]
    assert (repo / "a.py").exists() and (repo / "b.py").exists()


def test_the_reapply_retry_ignores_steps_whose_approval_is_still_revoked(
    real_store, monkeypatch,
):
    """되감긴 채로 남아 있는 단계는 되살릴 대상이 아니다 — 사람이 아직 앞으로 오지
    않았다는 뜻이다."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        trc, "reapply_tr_commits",
        lambda group_id, doc_ids: (calls.append(sorted(doc_ids)),
                                   trc.empty_restore_result())[1],
    )
    for doc_id in (_TR_A, _TR_B):
        row = _ledger_commit(doc_id, doc_id[-2:] * 20, "s")
        db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)
    real_store._execute(
        "UPDATE documents SET doc_review_status = 'pending_review' WHERE doc_id = ?",
        [_TR_B],
    )

    trc.reapply_retry(_GROUP)

    assert calls == [[_TR_A]]


def test_the_reapply_retry_route_answers_with_the_restore_object(real_store, monkeypatch):
    from modules.flow_gate.documents.routers import documents as doc_routes

    monkeypatch.setattr(
        trc, "reapply_retry",
        lambda group_id: {**trc.empty_restore_result(), "attempted": True},
    )

    payload = doc_routes.retry_reapply_tr_commits(_TR_A, {"user_id": "u"})

    assert payload["ok"] is True
    assert payload["tr_commit_restore"]["attempted"] is True
    # 되감기도 앞으로 복원도 아니다 — 문서 상태에는 손대지 않는다.
    assert "restored" not in payload


def test_the_reapply_retry_route_404s_on_an_unknown_document(real_store):
    from fastapi import HTTPException

    from modules.flow_gate.documents.routers import documents as doc_routes

    with pytest.raises(HTTPException) as excinfo:
        doc_routes.retry_reapply_tr_commits("flowgate.default.0332.9999-TR", {"user_id": "u"})

    assert excinfo.value.status_code == 404


def test_a_reapply_retry_that_blows_up_reports_nothing_attempted_not_a_500(
    real_store, monkeypatch,
):
    from modules.flow_gate.documents.routers import documents as doc_routes

    def _boom(group_id):
        raise RuntimeError("ledger exploded")

    monkeypatch.setattr(trc, "reapply_retry", _boom)

    payload = doc_routes.retry_reapply_tr_commits(_TR_A, {"user_id": "u"})

    assert payload == {"ok": True, "tr_commit_restore": trc.empty_restore_result()}


# ── 7. 재승인 뒤 남는 옛 live 행 (시나리오 F — T0018 §3-4) ────────────────────

def test_cancel_retry_still_targets_the_old_live_row_after_a_reapproval(
    real_store, monkeypatch,
):
    """구멍 7. 되돌리기가 ``dirty_worktree`` 로 막힌 뒤 그 단계를 재승인하면 그 문서에는
    live 행이 둘이 된다 — 취소하지 못한 옛 행과, 재승인이 방금 만든 새 행. 옛 규칙은
    "재승인된 문서는 건드리지 마라"를 **문서**에 걸어 둘 다 대상에서 뺐고, 그래서 옛 행은
    어떤 경로로도 다시 잡히지 않고 영원히 live 로 남았다(스트립은 가장 새 행만 보므로
    화면에서도 사라지고, 패널 배지 숫자로만 드러난다). 규칙은 **행**에 걸려야 한다."""
    captured: list[dict] = []
    monkeypatch.setattr(
        trc, "cancel_tr_commits",
        lambda group_id, doc_ids, exclude_row_ids=None: (
            captured.append({"docs": sorted(doc_ids),
                             "exclude": sorted(exclude_row_ids or [])}),
            trc.empty_cancel_result(),
        )[1],
    )
    stale = _ledger_commit(_TR_A, "a" * 40, "0009-TR: 취소 못 한 커밋")
    fresh = _ledger_commit(_TR_A, "n" * 40, "0009-TR: 재승인이 만든 커밋")
    other = _ledger_commit(_TR_B, "b" * 40, "0011-TR: b")
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
    # 0009-TR 은 그 사이 다시 승인됐고, 0011-TR 은 아직 되감긴 채다.
    real_store._execute(
        "UPDATE documents SET doc_review_status = 'pending_review' WHERE doc_id = ?",
        [_TR_B],
    )

    trc.cancel_retry(_GROUP)

    # 문서는 둘 다 대상이다 — 재승인됐다고 문서째 빠지지 않는다.
    assert captured[0]["docs"] == sorted([_TR_A, _TR_B])
    # 빠지는 것은 재승인이 방금 만든 행 하나뿐이다.
    assert captured[0]["exclude"] == [fresh["id"]]
    assert stale["id"] not in captured[0]["exclude"]
    assert other["id"] not in captured[0]["exclude"]


def test_the_freshly_reapproved_commit_is_the_one_row_the_retry_leaves_alone(real_store):
    """행 단위 제외가 실제로 목록에서 무엇을 빼는지 — 대역 없이 진짜 루프의 입구에서."""
    stale = _ledger_commit(_TR_A, "a" * 40, "옛 커밋")
    fresh = _ledger_commit(_TR_A, "n" * 40, "새 커밋")

    result = trc.cancel_tr_commits(_GROUP, [_TR_A], exclude_row_ids={fresh["id"]})

    # 게이트까지 가지 못하고 막히든 어떻든, 남은 대상은 옛 행 하나다. git 을 켜지 않은
    # 상태이므로 여기서는 게이트가 답하고, 그 자체가 "대상이 비지 않았다"는 증거다.
    assert result["blocked_reason"] is not None
    assert db_ledger.get_by_id(stale["id"])["state"] == "live"
    assert db_ledger.get_by_id(fresh["id"])["state"] == "live"


def test_excluding_every_row_ends_quietly_instead_of_opening_a_session(real_store):
    """재승인이 유일한 행이었다면 취소할 것이 남지 않는다 — 조용히 끝나야 한다."""
    fresh = _ledger_commit(_TR_A, "n" * 40, "새 커밋")

    result = trc.cancel_tr_commits(_GROUP, [_TR_A], exclude_row_ids=[fresh["id"]])

    assert result["attempted"] is True
    assert result["blocked_reason"] is None
    assert result["canceled"] == [] and result["skipped"] == []


# ── 8. 패널이 단추를 그릴 근거 (T0018 §3-5) ──────────────────────────────────

def test_the_panel_summary_says_whether_anything_can_be_restored(real_store):
    row = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    assert trc.group_commit_summary(_GROUP)["reapply_pending"] is False

    db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)

    assert trc.group_commit_summary(_GROUP)["reapply_pending"] is True


def test_a_step_redone_by_hand_stops_offering_the_restore(real_store):
    """이미 사람이 다시 만든 단계에 "되살리기" 단추를 주면 이중 적용을 권하는 것이다."""
    row = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)
    _ledger_commit(_TR_A, "n" * 40, "0009-TR: 다시 한 작업")

    assert trc.group_commit_summary(_GROUP)["reapply_pending"] is False


def test_the_summary_carries_the_last_gate_refusal_with_its_verdict(real_store):
    """재시도 단추를 줄지는 화면이 다시 판단하지 않는다 — 서버의 한 표에서 온다."""
    row = _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")
    db_ledger.mark_canceled(row["id"], cancel_commit="c" * 40)
    db_ledger.record_block(_GROUP, "dirty_worktree", "dirty_worktree")

    summary = trc.group_commit_summary(_GROUP)

    assert summary["last_block"]["reason"] == "dirty_worktree"
    assert summary["last_block"]["retryable"] is True

    db_ledger.record_block(_GROUP, "already_merged", "already_merged")

    assert trc.group_commit_summary(_GROUP)["last_block"]["retryable"] is False


def test_a_group_that_never_blocked_carries_no_block_object(real_store):
    _ledger_commit(_TR_A, "a" * 40, "0009-TR: a")

    assert trc.group_commit_summary(_GROUP)["last_block"] is None


def test_the_empty_summary_answers_the_new_questions_too(real_store):
    """원장 행이 없는 그룹은 호출자가 EMPTY_SUMMARY 로 채운다. 새 키가 빠져 있으면
    화면이 undefined 를 참으로 읽을 자리가 생긴다."""
    assert trc.EMPTY_SUMMARY["reapply_pending"] is False
    assert trc.EMPTY_SUMMARY["last_block"] is None
    assert set(trc.EMPTY_SUMMARY) == set(trc.group_commit_summary("flowgate.default.9999"))
