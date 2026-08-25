"""TR 승인이 그룹 워크트리에 커밋을 남기는가 (0332 T#1 — R0001 앞 절).

세 층을 각각 진짜로 돌린다. 어느 하나라도 대역으로 때우면 이 기능이 "호출은 됐다"까지만
증명되기 때문이다.

1. **원장(085)** — 마이그레이션을 전부 적용한 sqlite 위에서 운영 INSERT/SELECT 를 그대로
   돌린다. CHECK 제약(DB0008 §2-1)이 실제로 잘못된 조합을 막는지도 여기서 본다.
2. **커밋(git)** — 저장소 밖의 진짜 git 워크트리에 ``create_tr_commit`` 을 걸어 커밋이
   정말 생기는지, 도구 흔적이 빠지는지(0382 규칙 공유), 못 하는 상황마다 P0006 §5-2 의
   닫힌 코드 중 무엇이 나오는지 본다.
3. **훅(승인)** — TR 이 아니면 아무 것도 하지 않고, TR 이면 원장 한 줄과 P0006 §1 모양의
   응답이 남는지 본다.

한 줄로 요약하면 이 시험이 고정하는 계약은 **"승인은 git 이 무엇을 하든 선다"** 이다.
"""
from __future__ import annotations

import base64
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
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-tr-commit-0332-")
)

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import tr_commit_ledger as db_ledger  # noqa: E402
from modules.flow_gate.services import git_service as svc  # noqa: E402
from modules.flow_gate.services import tr_commit_service as trc  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")

_SCRATCH = session_scratch("tr-commit-0332")

_PROJECT = "flowgate"
_GROUP = "flowgate.default.0332"
_TR_DOC = "flowgate.default.0332.0009-TR"
_TR_DOC_2 = "flowgate.default.0332.0011-TR"
_D_DOC = "flowgate.default.0332.0005-D"

_SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
    VALUES('{_PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'default', 'TR 커밋 포인트', 'OPEN',
           datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
        doc_id, project_id, module, group_id, type_code, seq, title, status,
        created_at, updated_at)
    VALUES('{_D_DOC}', '{_PROJECT}', 'default', '{_GROUP}', 'D', 5,
           'TR 커밋 포인트 기본설계', 'open', datetime('now'), datetime('now')),
          ('{_TR_DOC}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 9,
           '커밋 포인트 생성 작업레포트', 'open', datetime('now'), datetime('now')),
          ('{_TR_DOC_2}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 11,
           '되돌리기 확인 창 결과 화면', 'open', datetime('now'), datetime('now'));
"""


class _SqliteStore:
    """운영 FlowGateStore 와 같은 최소 계약. ``_sql`` 은 일부러 두지 않는다 — 등록된
    진짜 SQL 본문이 쓰이게 하려는 것이다(0393 의 교훈)."""

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
    """085 를 포함해 마이그레이션 전부를 적용한 sqlite 위의 진짜 저장소.

    ``get_store`` 함수가 아니라 connection 의 STORE 객체를 바꾼다 — 이미 ``get_store`` 를
    임포트해 둔 모듈도 같은 객체를 보게 하기 위해서다.
    """
    db_path = migrated_sqlite_db("tr_commit_ledger_0332.db", seed_sql=_SEED_SQL)
    store = _SqliteStore(db_path)
    previous = db_connection.STORE
    db_connection.STORE = store
    try:
        yield store
    finally:
        db_connection.STORE = previous
        store._conn.close()


# ── 1. 원장(마이그레이션 085) ────────────────────────────────────────────────

def test_a_commit_round_writes_one_live_row(real_store):
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC,
        commit_sha="a" * 40, commit_subject="0009-TR: 커밋 포인트 생성",
    )

    assert row["state"] == "live"
    assert row["commit_sha"] == "a" * 40
    assert row["cancel_attempt_log"] == "[]"
    assert row["skip_reason"] is None


def test_a_skipped_round_writes_one_no_commit_row_carrying_why(real_store):
    """K3 의 이유: "소스를 안 건드렸다"와 "커밋을 시도조차 못 했다"가 구별돼야 한다."""
    row = db_ledger.record_no_commit(
        group_id=_GROUP, doc_id=_TR_DOC, skip_reason="git_busy",
    )

    assert row["state"] == "no_commit"
    assert row["commit_sha"] is None
    assert row["skip_reason"] == "git_busy"


def test_the_schema_refuses_a_live_row_without_a_commit(real_store):
    """CHECK ck_trl_commit_sha_matches_state — DB0008 §5-1 의 첫 불변식."""
    with pytest.raises(sqlite3.IntegrityError):
        real_store._execute(
            "INSERT INTO tr_commit_ledger"
            "(group_id, doc_id, state, cancel_attempt_log, created_at, updated_at) "
            "VALUES (?, ?, 'live', '[]', 'now', 'now')",
            [_GROUP, _TR_DOC],
        )


def test_the_schema_refuses_a_skip_reason_outside_the_closed_set(real_store):
    """P0006 §5-2 가 닫아 둔 여섯 코드 — 일곱 번째를 DB 가 막는다."""
    with pytest.raises(sqlite3.IntegrityError):
        real_store._execute(
            "INSERT INTO tr_commit_ledger"
            "(group_id, doc_id, state, skip_reason, cancel_attempt_log, created_at, updated_at) "
            "VALUES (?, ?, 'no_commit', 'because_i_said_so', '[]', 'now', 'now')",
            [_GROUP, _TR_DOC],
        )


def test_re_approval_adds_a_row_and_the_newest_one_wins(real_store):
    """되돌렸다가 다시 승인하면 새 행이다 — 앞 행은 지우지 않는다(D0005 K9)."""
    first = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC, commit_sha="1" * 40, commit_subject="first",
    )
    second = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC, commit_sha="2" * 40, commit_subject="second",
    )

    assert second["id"] > first["id"]
    latest = db_ledger.latest_by_doc([_TR_DOC])
    assert latest[_TR_DOC]["commit_sha"] == "2" * 40
    assert len(db_ledger.list_by_group(_GROUP)) == 2


def test_live_rows_are_the_cancel_targets_newest_first(real_store):
    """L0007 §2.4 — 정렬 키는 seq 가 아니라 원장 행 번호(삽입 순서)다."""
    db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC_2, commit_sha="b" * 40, commit_subject="seq 11",
    )
    db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC, commit_sha="c" * 40, commit_subject="seq 9, 나중 커밋",
    )
    db_ledger.record_no_commit(
        group_id=_GROUP, doc_id=_D_DOC, skip_reason="no_changes",
    )

    targets = db_ledger.live_rows(_GROUP, [_TR_DOC, _TR_DOC_2, _D_DOC])

    # seq 9 가 나중에 커밋됐으므로 seq 11 보다 먼저 취소돼야 한다.
    assert [t["doc_id"] for t in targets] == [_TR_DOC, _TR_DOC_2]
    # no_commit 행은 취소 대상이 아니다.
    assert _D_DOC not in [t["doc_id"] for t in targets]


# ── 2. 화면이 읽는 모양 ───────────────────────────────────────────────────────

def test_the_strip_marks_only_steps_that_actually_committed(real_store):
    db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC, commit_sha="d" * 40, commit_subject="0009-TR: 제목",
    )
    db_ledger.record_no_commit(
        group_id=_GROUP, doc_id=_TR_DOC_2, skip_reason="no_changes",
    )

    marks = trc.slot_commit_states([_TR_DOC, _TR_DOC_2, _D_DOC])

    assert marks[_TR_DOC] == {
        "state": "live", "commit": "d" * 7,
        "subject": "0009-TR: 제목", "cancel_commit": None,
        # 0332 T0018 K11 — 처음 승인이 남긴 커밋이지 되살린 커밋이 아니다.
        "restored": False,
    }
    # 소스를 바꾸지 않은 단계는 조용하다 — 표식 자체가 없다(D0005 §6.1).
    assert _TR_DOC_2 not in marks
    assert _D_DOC not in marks


def test_a_failed_re_approval_does_not_blank_out_an_existing_commit(real_store):
    """재승인이 커밋에 실패해도 살아 있는 커밋의 표식은 그대로 있어야 한다.

    가장 새 행만 보면 그 위에 얹힌 ``no_commit`` 이 칸을 비워 버린다 — 브랜치에는 그 커밋이
    멀쩡히 있는데 화면만 없다고 말하는 상태가 된다.
    """
    db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC, commit_sha="f" * 40, commit_subject="살아 있는 커밋",
    )
    db_ledger.record_no_commit(
        group_id=_GROUP, doc_id=_TR_DOC, skip_reason="git_busy",
    )

    marks = trc.slot_commit_states([_TR_DOC])

    assert marks[_TR_DOC]["commit"] == "f" * 7
    assert marks[_TR_DOC]["state"] == "live"


def test_the_panel_summary_counts_every_row_and_never_truncates_silently(real_store):
    for index in range(trc.PANEL_COMMIT_LIST_MAX + 3):
        db_ledger.record_commit(
            group_id=_GROUP, doc_id=_TR_DOC,
            commit_sha=f"{index:040d}", commit_subject=f"commit {index}",
        )
    db_ledger.record_no_commit(
        group_id=_GROUP, doc_id=_TR_DOC_2, skip_reason="artifacts_only",
    )

    summary = trc.group_commit_summary(_GROUP)

    assert summary["live"] == trc.PANEL_COMMIT_LIST_MAX + 3
    assert summary["no_commit"] == 1
    assert len(summary["commits"]) == trc.PANEL_COMMIT_LIST_MAX
    assert summary["more"] == 4          # 접힌 줄 수를 화면이 "N개 더"로 말한다
    # 최신순이므로 맨 위는 마지막에 쓴 no_commit 줄이다.
    assert summary["commits"][0]["doc_code"] == "0011-TR"
    # doc_code 는 저장된 값이 아니라 지금의 documents.seq/type_code 에서 조립된다.
    assert {c["doc_code"] for c in summary["commits"] if c["state"] == "live"} == {"0009-TR"}


def test_summaries_answer_for_several_groups_in_one_call(real_store):
    db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_DOC, commit_sha="e" * 40, commit_subject="x",
    )

    summaries = trc.group_commit_summaries([_GROUP, "flowgate.default.9999"])

    assert summaries[_GROUP]["live"] == 1
    # 원장 행이 없는 그룹은 아예 빠진다 — 호출자가 빈 요약을 채운다.
    assert "flowgate.default.9999" not in summaries


# ── 3. 승인 훅 ────────────────────────────────────────────────────────────────

def test_a_non_tr_approval_is_not_touched_at_all(real_store, monkeypatch):
    """P0006 §1-8 — TR 이 아닌 승인 응답은 이 기능 이전과 바이트 단위로 같다."""
    def _never(*args, **kwargs):
        raise AssertionError("non-TR 승인이 git 을 건드렸다")

    monkeypatch.setattr(svc, "create_tr_commit", _never)

    assert trc.on_document_approved(_D_DOC) is None
    assert db_ledger.list_by_group(_GROUP) == []


def test_a_tr_approval_commits_and_leaves_one_live_row(real_store, monkeypatch):
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": True, "commit": "a1b2c3d", "commit_sha": "a1b2c3d" + "0" * 33,
        "subject": subject, "skipped_reason": None,
        "excluded_artifacts": [], "committed_paths": ["server/x.py"],
    })

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["committed"] is True
    assert payload["commit"] == "a1b2c3d"
    # flowgate.default.0462 T0005 §4-2 — L0007 §2.6 을 대체: 초안이 없으면(이 시드 문서의
    # commit_message 는 NULL) ASCII 폴백이고, 번역은 여전히 타지 않는다.
    assert payload["subject"] == "chore: approve 0009-TR"
    rows = db_ledger.list_by_group(_GROUP)
    assert [r["state"] for r in rows] == ["live"]
    assert rows[0]["commit_sha"] == "a1b2c3d" + "0" * 33


def test_a_tr_approval_that_could_not_commit_still_leaves_the_reason(real_store, monkeypatch):
    """승인은 선다. 못 한 이유는 원장과 응답 양쪽에 남는다(P0006 §1-6)."""
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "git_busy", "excluded_artifacts": [], "committed_paths": [],
    })

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["committed"] is False
    assert payload["skipped_reason"] == "git_busy"
    rows = db_ledger.list_by_group(_GROUP)
    assert [(r["state"], r["skip_reason"]) for r in rows] == [("no_commit", "git_busy")]


def test_the_hook_never_lets_a_git_failure_reach_the_approval(real_store, monkeypatch):
    def _boom(group_id, subject):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(svc, "create_tr_commit", _boom)

    # 예외가 아니라 None 이다 — 호출자는 tr_commit 키를 빼고 승인을 그대로 돌려준다.
    assert trc.on_document_approved(_TR_DOC) is None


def test_the_reported_list_is_compared_never_used_as_a_filter(real_store, monkeypatch, tmp_path):
    """D0005 K2 — 신고 목록은 대조에만 쓴다. 커밋 범위를 좁히지 않는다."""
    body = tmp_path / "0009-TR_document.md"
    body.write_text(
        "# 작업레포트\n\n## 변경 파일\n\n- server/reported.py\n- client/forgotten.ts\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        trc.storage_paths, "resolve_storage_path",
        lambda *args, **kwargs: body,
    )
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": True, "commit": "9f8e7d6", "commit_sha": "9f8e7d6" + "0" * 33,
        "subject": subject, "skipped_reason": None, "excluded_artifacts": [],
        "committed_paths": ["server/reported.py", "server/never_declared.py"],
    })
    real_store._execute(
        "UPDATE documents SET file_path = ? WHERE doc_id = ?",
        ["documents/x/0009-TR_document.md", _TR_DOC],
    )

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["reported_diff"] == {
        "unreported": ["server/never_declared.py"],   # 신고 없이 담긴 것
        "missing": ["client/forgotten.ts"],           # 신고했는데 안 담긴 것
    }
    # 그래도 커밋은 그대로 성공이다 — 경고이지 차단이 아니다.
    assert payload["committed"] is True


# ── 3b. 승인 커밋 제목 규칙 (flowgate.default.0462 T0005) ─────────────────────

def _draft_doc(commit_message=None, seq=9, title="커밋 포인트 생성 작업레포트"):
    """``commit_subject()`` 는 dict 만 보므로 DB 없이도 결정 로직을 시험할 수 있다."""
    return {
        "doc_id": _TR_DOC, "seq": seq, "type_code": "TR", "title": title,
        "commit_message": commit_message, "group_id": _GROUP,
    }


def test_a_conventional_ascii_draft_is_used_verbatim():
    doc = _draft_doc(commit_message="fix(git): preserve finalized commit subject")
    assert trc.commit_subject(doc) == "fix(git): preserve finalized commit subject"


def test_a_non_conventional_ascii_draft_gets_the_derived_type_prefixed(real_store):
    """`derive_commit_type()` 이 실제로 DB 를 읽고 돌려준 타입이 쓰인다 — 대역이 아니다."""
    real_store._execute(
        "INSERT INTO documents(doc_id, project_id, module, group_id, type_code, seq, "
        "title, status, created_at, updated_at) VALUES (?, ?, 'default', ?, 'B', 1, "
        "'버그', 'open', datetime('now'), datetime('now'))",
        [f"{_GROUP}.0001-B", _PROJECT, _GROUP],
    )
    doc = _draft_doc(commit_message="preserve finalized commit subject")
    assert trc.commit_subject(doc) == "fix: preserve finalized commit subject"


def test_a_korean_draft_is_ignored_and_the_ascii_fallback_is_used():
    doc = _draft_doc(commit_message="복구 API 오류 코드 정리")
    subject = trc.commit_subject(doc)
    assert subject == "chore: approve 0009-TR"
    assert subject.isascii()


def test_no_draft_falls_back_to_the_ascii_chore_subject():
    doc = _draft_doc(commit_message=None)
    assert trc.commit_subject(doc) == "chore: approve 0009-TR"


def test_an_oversized_draft_falls_back_instead_of_being_clipped():
    doc = _draft_doc(commit_message="x" * (svc.COMMIT_SUBJECT_MAX + 1))
    assert trc.commit_subject(doc) == "chore: approve 0009-TR"


def test_missing_commit_message_key_triggers_a_refetch_that_finds_the_draft(
    real_store, monkeypatch,
):
    """T0005 §4-3 — RPC 승인 경로가 넘기는 dict 에는 commit_message 키 자체가 없다.
    재조회가 실제로 도는지 본다."""
    real_store._execute(
        "UPDATE documents SET commit_message = ? WHERE doc_id = ?",
        ["fix(git): preserve finalized commit subject", _TR_DOC],
    )
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": True, "commit": "abc1234", "commit_sha": "abc1234" + "0" * 33,
        "subject": subject, "skipped_reason": None,
        "excluded_artifacts": [], "committed_paths": [],
    })
    partial = {
        "doc_id": _TR_DOC, "type_code": "TR", "group_id": _GROUP,
        "title": "커밋 포인트 생성 작업레포트", "seq": 9,
        # commit_message 키 자체가 없다 — RPC 경로(workflow.py:766)의 result dict 재현.
    }

    payload = trc.on_document_approved(_TR_DOC, partial)

    assert payload["subject"] == "fix(git): preserve finalized commit subject"


def test_a_commit_message_key_present_as_none_skips_the_refetch(real_store, monkeypatch):
    """키가 있고 값이 None 이면 "초안 없음"이 확정된 정보다 — 재조회하지 않는다."""
    real_store._execute(
        "UPDATE documents SET commit_message = ? WHERE doc_id = ?",
        ["fix(git): preserve finalized commit subject", _TR_DOC],
    )
    monkeypatch.setattr(svc, "create_tr_commit", lambda group_id, subject: {
        "committed": True, "commit": "abc1234", "commit_sha": "abc1234" + "0" * 33,
        "subject": subject, "skipped_reason": None,
        "excluded_artifacts": [], "committed_paths": [],
    })
    partial = {
        "doc_id": _TR_DOC, "type_code": "TR", "group_id": _GROUP,
        "title": "커밋 포인트 생성 작업레포트", "seq": 9,
        "commit_message": None,
    }

    payload = trc.on_document_approved(_TR_DOC, partial)

    # DB 에는 영문 초안이 있어도, 넘겨받은 dict 가 "키 있음+None" 이므로 그 정보를 믿는다.
    assert payload["subject"] == "chore: approve 0009-TR"


def test_cancel_and_reapply_subjects_quote_the_new_style_subject():
    original = "fix(git): preserve finalized commit subject"
    assert svc.cancel_subject(original) == (
        'Revert "fix(git): preserve finalized commit subject"'
    )
    assert svc.reapply_subject(original) == (
        'Reapply "fix(git): preserve finalized commit subject"'
    )


# ── 4. 진짜 git 워크트리 ─────────────────────────────────────────────────────

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
    path = _SCRATCH / f"wt-{os.urandom(6).hex()}"
    path.mkdir(parents=True)
    _git(["init", "-b", "work"], path)
    (path / "kept.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], path)
    _git(["-c", "user.name=T", "-c", "user.email=t@t", "commit", "-m", "base"], path)
    yield path
    remove_tree(path)


@pytest.fixture
def git_active(monkeypatch, repo):
    """그룹이 git 활성이고 워크트리가 이 repo 라고 서버에 알려 준다."""
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


@needs_git
def test_the_commit_really_lands_and_debris_stays_out(git_active, repo):
    (repo / "server").mkdir()
    (repo / "server" / "real.py").write_text("y = 2\n", encoding="utf-8")
    debris = repo / "server" / ".test-tmp-0332"
    debris.mkdir(parents=True)
    (debris / "junk.txt").write_text("junk\n", encoding="utf-8")
    before = _git(["rev-parse", "HEAD"], repo).strip()

    result = svc.create_tr_commit(_GROUP, "0009-TR: 커밋 포인트 생성")

    head = _git(["rev-parse", "HEAD"], repo).strip()
    assert result["committed"] is True
    assert head != before
    assert result["commit_sha"] == head
    assert result["commit"] == head[:7]
    assert _git(["log", "-1", "--pretty=%s"], repo).strip() == "0009-TR: 커밋 포인트 생성"
    assert result["committed_paths"] == ["server/real.py"]
    assert result["excluded_artifacts"] == ["server/.test-tmp-0332/junk.txt"]
    # 뺐다고 지우지 않는다 — 흔적 파일은 워크트리에 그대로 있다(타임머신 원칙).
    assert (debris / "junk.txt").exists()


@needs_git
def test_a_tr_that_changed_nothing_gets_no_commit_and_says_so(git_active, repo):
    before = _git(["rev-parse", "HEAD"], repo).strip()

    result = svc.create_tr_commit(_GROUP, "0009-TR: 문서만 쓴 작업")

    assert result["committed"] is False
    assert result["skipped_reason"] == "no_changes"
    # 빈 앵커 커밋을 남기지 않는다(D0005 K3) — 마무리의 자동 폐기 판단이 죽는다.
    assert _git(["rev-parse", "HEAD"], repo).strip() == before


@needs_git
def test_a_worktree_dirty_only_with_debris_is_not_no_changes(git_active, repo):
    """P0006 §1-5 — 화면 문구가 다르므로 코드도 다르다."""
    debris = repo / "server" / ".test-tmp-0332"
    debris.mkdir(parents=True)
    (debris / "junk.txt").write_text("junk\n", encoding="utf-8")

    result = svc.create_tr_commit(_GROUP, "0009-TR: 흔적만 남은 작업")

    assert result["committed"] is False
    assert result["skipped_reason"] == "artifacts_only"
    assert result["excluded_artifacts"] == ["server/.test-tmp-0332/junk.txt"]


@needs_git
def test_a_held_lock_gives_up_immediately_instead_of_stalling_the_approval(
    git_active, repo, monkeypatch,
):
    """L0007 §1 tr_commit_lock_wait_sec = 0 — 마무리 한 번이 승인 전부를 멈추면 안 된다."""
    monkeypatch.setattr(svc.db_git, "try_acquire_lock", lambda project_id, holder: False)
    (repo / "server").mkdir()
    (repo / "server" / "real.py").write_text("y = 2\n", encoding="utf-8")

    result = svc.create_tr_commit(_GROUP, "0009-TR: 잠금 경합")

    assert result["committed"] is False
    assert result["skipped_reason"] == "git_busy"
    # 잃는 것은 없다 — 변경분은 워크트리에 그대로 남아 다음 커밋이 가져간다.
    assert (repo / "server" / "real.py").exists()


@needs_git
def test_a_group_without_a_worktree_reports_no_worktree(git_active, monkeypatch):
    monkeypatch.setattr(svc.db_git, "get_state", lambda group_id: {
        "worktree_registered": 0, "branch": "work", "status": "merged",
    })

    result = svc.create_tr_commit(_GROUP, "0009-TR: 이미 병합된 그룹")

    assert result["committed"] is False
    assert result["skipped_reason"] == "no_worktree"


@needs_git
def test_git_disabled_project_is_reported_not_raised(git_active, monkeypatch):
    monkeypatch.setattr(svc.db_git, "get_config", lambda project_id: {"enabled": 0})

    result = svc.create_tr_commit(_GROUP, "0009-TR: git 꺼진 프로젝트")

    assert result["committed"] is False
    assert result["skipped_reason"] == "git_inactive"


@needs_git
def test_the_approval_hook_produces_a_conventional_english_commit_in_real_git(
    real_store, git_active, repo,
):
    """flowgate.default.0462 T0005 완료 기준 3 — 실제 승인 훅을 태워 커밋 제목을 실측한다
    (초안이 있는 경우)."""
    real_store._execute(
        "UPDATE documents SET commit_message = ? WHERE doc_id = ?",
        ["fix(git): preserve finalized commit subject", _TR_DOC],
    )
    (repo / "server").mkdir()
    (repo / "server" / "real.py").write_text("y = 2\n", encoding="utf-8")

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["committed"] is True
    subject = _git(["log", "-1", "--pretty=%s"], repo).strip()
    assert subject == "fix(git): preserve finalized commit subject"
    assert subject.isascii()


@needs_git
def test_the_approval_hook_falls_back_to_ascii_chore_when_no_draft(
    real_store, git_active, repo,
):
    """완료 기준 3의 두 번째 실측 — 초안이 없는 경우."""
    (repo / "server").mkdir()
    (repo / "server" / "real.py").write_text("y = 3\n", encoding="utf-8")

    payload = trc.on_document_approved(_TR_DOC)

    assert payload["committed"] is True
    subject = _git(["log", "-1", "--pretty=%s"], repo).strip()
    assert subject == "chore: approve 0009-TR"
    assert subject.isascii()


@needs_git
def test_the_finalize_absorb_still_behaves_exactly_as_before(repo):
    """공유한 것은 stage 단계뿐 — 0382 가 고정한 흡수 커밋의 계약은 그대로다."""
    (repo / "server").mkdir()
    (repo / "server" / "real.py").write_text("y = 2\n", encoding="utf-8")
    debris = repo / "server" / ".test-tmp-0332"
    debris.mkdir(parents=True)
    (debris / "junk.txt").write_text("junk\n", encoding="utf-8")

    excluded = svc._absorb_worker_edits(repo, "feat: real work", None)

    tracked = {line for line in _git(["ls-files"], repo).splitlines() if line}
    assert "server/real.py" in tracked
    assert not [p for p in tracked if ".test-tmp-0332" in p]
    assert excluded == ["server/.test-tmp-0332/junk.txt"]
