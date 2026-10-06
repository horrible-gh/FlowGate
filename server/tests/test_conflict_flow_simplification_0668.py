"""Git 충돌 처리 프로세스 간략화 — 서버 계약 (flowgate.default.0668 T0004).

0668 NR0003 이 정한 정상 경로는 ``승인 → 충돌 → AI/직접 해결 → 검토 및 승인 → 완료`` 다.
화면 쪽 단계는 클라이언트가 줄이지만, 그 단계가 서버 상태 하나로 이어지려면 서버가 세 가지를
약속해야 한다. 이 스위트가 그 셋을 고정한다.

  * **TR 충돌도 같은 검토 화면에서 승인된다.** 해결이 끝나면 병합처럼 후보가 얼고
    (``review_fingerprint``), ``GET …/review`` 가 같은 모양으로 답하고, ``POST …/approve``
    가 그 지문으로 TR 커밋을 끝낸다. 본 것과 다른 트리는 커밋하지 않는다(``stale_review``).
    별도 ``tr-commit`` 은 호환 경로로 그대로 남는다.
  * **검토 대기는 화면이 아니라 서버 상태다.** 검토를 열면 세션 활동으로 친다(TTL 연장).
    베이스를 쥐지 않은 검토 대기는 TTL 로 죽지 않고, 베이스를 쥔 것은 활동 TTL 에 생성 시점
    기준 상한(``REVIEW_PENDING_MAX_HOURS``)이 걸린다.
  * **청소가 닫은 시도는 abort 와 똑같이 정리된다.** TTL·고아 종료도 승인 intent 와
    rerere checkpoint 를 버려, 이후 재승인이 ``final_approval_retry_mismatch`` 로 막히지 않는다.

TR 쪽은 진짜 git 저장소와 마이그레이션된 sqlite 로 돌린다(0332 스위트와 같은 방식).
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
from datetime import datetime, timedelta, timezone
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
    "FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-conflict-flow-0668-")
)

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import git_routes  # noqa: E402
from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import git_integration as db_git  # noqa: E402
from modules.flow_gate.db import tr_commit_ledger as db_ledger  # noqa: E402
from modules.flow_gate.services import git_service as svc  # noqa: E402
from modules.flow_gate.services import tr_commit_service as trc  # noqa: E402
from modules.flow_gate.services.git import cleanup  # noqa: E402
from modules.flow_gate.services.git_service import GitServiceError  # noqa: E402
from group_lock_stub import stub_group_lock  # noqa: E402

_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not _GIT, reason="git binary unavailable")

_SCRATCH = session_scratch("conflict-flow-0668")

_PROJECT = "flowgate"
_GROUP = "flowgate.default.0668"
_TR_A = "flowgate.default.0668.0009-TR"

_SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
    VALUES('{_PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'default', '충돌 간략화', 'OPEN',
           datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
        doc_id, project_id, module, group_id, type_code, seq, title, status,
        doc_review_status, created_at, updated_at)
    VALUES('{_TR_A}', '{_PROJECT}', 'default', '{_GROUP}', 'TR', 9,
           '커밋 포인트 작업레포트', 'open', 'approved', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO group_git_state(
        group_id, project_id, branch, worktree_registered, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'work', 1, 'waiting', datetime('now'), datetime('now'));
"""


class _SqliteStore:
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
    db_path = migrated_sqlite_db("conflict_flow_0668.db", seed_sql=_SEED_SQL)
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
    stub_group_lock(monkeypatch)
    return repo


def _parked_cancel(repo):
    """0009-TR 이 f.txt 를 A 로 만들고 그 위에 사람이 C 를 얹었다. 취소는 충돌한다."""
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    sha_a = _commit(repo, "0009-TR: A")
    row = db_ledger.record_commit(
        group_id=_GROUP, doc_id=_TR_A, commit_sha=sha_a, commit_subject="0009-TR: A",
    )
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    _commit(repo, "manual fix")
    parked = trc.cancel_tr_commits(_GROUP, [_TR_A])["conflict_session"]
    assert parked, "이 시나리오는 반드시 충돌로 끝나야 한다"
    return row, parked


def _resolved_cancel(repo, content="resolved\n"):
    row, parked = _parked_cancel(repo)
    out = svc.resolve_conflicts(
        _GROUP, parked["merge_id"], [{"path": "f.txt", "content": content}], True,
    )
    return row, parked, out


def _iso(delta_hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=delta_hours)).isoformat()


# ── 1. TR 충돌도 같은 검토 화면에서 승인된다 ─────────────────────────────────────

@needs_git
def test_resolving_a_tr_conflict_freezes_a_review_candidate(real_store, git_active, repo):
    """해결 응답이 병합과 같은 ``review_fingerprint`` 를 싣고, 세션은 TR 어휘(resolved)를
    유지한 채 검토 화면이 읽을 후보를 들고 있다. 커밋은 여전히 없다."""
    _row, parked, out = _resolved_cancel(repo)
    head = _git(["rev-parse", "HEAD"], repo).strip()

    assert out["result"]["status"] == "resolved_pending_review"
    fingerprint = out["result"]["review_fingerprint"]
    assert isinstance(fingerprint, str) and len(fingerprint) == 64
    context = db_git.session_context(db_git.get_session(parked["merge_id"]))
    assert context["review_state"] == "resolved"
    assert context["review_fingerprint"] == fingerprint
    assert context["base_head"] == head
    assert [c["path"] for c in context["changes"]] == ["f.txt"]


@needs_git
def test_the_common_review_payload_serves_a_tr_conflict(real_store, git_active, repo):
    """``GET …/review`` 가 병합 검토와 같은 모양으로 답한다. 없는 기능(대화·반려)은
    다른 화면이 아니라 capability 플래그로 말한다."""
    _row, parked, out = _resolved_cancel(repo)

    review = svc.get_merge_review(_GROUP, parked["merge_id"])["result"]

    assert review["review_state"] == "resolved_pending_review"
    assert review["review_fingerprint"] == out["result"]["review_fingerprint"]
    assert review["kind"] == "tr_revert"
    assert (review["can_approve"], review["can_reject"], review["can_send"]) == (True, False, False)
    assert [c["path"] for c in review["changes"]] == ["f.txt"]
    assert review["tr_conflict"]["doc_code"] == "0009-TR"

    diff = svc.read_merge_review_file_diff(_GROUP, parked["merge_id"], "f.txt")["data"]
    assert diff["old"]["content"] == "C\n"
    assert diff["new"]["content"] == "resolved\n"


@needs_git
def test_opening_a_tr_review_before_resolution_is_not_ready(real_store, git_active, repo):
    _row, parked = _parked_cancel(repo)

    with pytest.raises(GitServiceError) as exc:
        svc.get_merge_review(_GROUP, parked["merge_id"])

    assert exc.value.code == "review_not_ready"


@needs_git
def test_approving_with_the_shown_fingerprint_commits_the_tr(real_store, git_active, repo):
    """검토 화면의 [승인] 한 번이 TR 커밋과 원장 기록까지 끝낸다(별도 커밋 단추 없음)."""
    row, parked, out = _resolved_cancel(repo)

    done = trc.commit_conflict_resolution(
        _GROUP, parked["merge_id"], review_fingerprint=out["result"]["review_fingerprint"],
    )

    assert done["result"]["status"] == "committed"
    assert done["result"]["review_state"] == "completed"
    assert _git(["log", "-1", "--format=%s"], repo).strip() == 'Revert "0009-TR: A"'
    assert (repo / "f.txt").read_text(encoding="utf-8") == "resolved\n"
    assert db_ledger.get_by_id(row["id"])["state"] == "canceled"
    assert db_git.get_session(parked["merge_id"])["status"] == "done"


@needs_git
def test_a_fingerprint_that_was_not_shown_commits_nothing(real_store, git_active, repo):
    row, parked, _out = _resolved_cancel(repo)
    head = _git(["rev-parse", "HEAD"], repo).strip()

    with pytest.raises(GitServiceError) as exc:
        trc.commit_conflict_resolution(_GROUP, parked["merge_id"], review_fingerprint="0" * 64)

    assert exc.value.code == "stale_review"
    assert _git(["rev-parse", "HEAD"], repo).strip() == head
    assert db_ledger.get_by_id(row["id"])["state"] == "live"
    assert db_git.get_session(parked["merge_id"])["status"] == "open"


@needs_git
def test_a_tree_changed_after_review_is_refrozen_not_committed(real_store, git_active, repo):
    """검토 뒤에 누군가 워크트리를 바꿨다. 본 지문은 맞지만 지금 트리는 본 트리가 아니다 —
    커밋하지 않고, 새 지문으로 다시 얼려 다시 보게 한다(병합의 identity_mismatch 와 같은 계약)."""
    _row, parked, out = _resolved_cancel(repo)
    shown = out["result"]["review_fingerprint"]
    head = _git(["rev-parse", "HEAD"], repo).strip()
    (repo / "f.txt").write_text("changed later\n", encoding="utf-8")
    _git(["add", "f.txt"], repo)

    with pytest.raises(GitServiceError) as exc:
        trc.commit_conflict_resolution(_GROUP, parked["merge_id"], review_fingerprint=shown)

    assert exc.value.code == "stale_review"
    assert _git(["rev-parse", "HEAD"], repo).strip() == head
    context = db_git.session_context(db_git.get_session(parked["merge_id"]))
    assert context["review_fingerprint"] != shown
    assert exc.value.details["review_fingerprint"] == context["review_fingerprint"]
    # 새 지문으로는 승인된다.
    done = trc.commit_conflict_resolution(
        _GROUP, parked["merge_id"], review_fingerprint=context["review_fingerprint"],
    )
    assert done["result"]["status"] == "committed"
    assert (repo / "f.txt").read_text(encoding="utf-8") == "changed later\n"


@needs_git
def test_the_legacy_tr_commit_route_still_commits_without_a_fingerprint(
    real_store, git_active, repo,
):
    """``tr-commit`` 은 호환 alias 로 남는다 — 예전 화면이 눌러도 같은 결과다."""
    _row, parked, _out = _resolved_cancel(repo)

    done = trc.commit_conflict_resolution(_GROUP, parked["merge_id"])

    assert done["result"]["status"] == "committed"


@needs_git
def test_the_approve_route_dispatches_a_tr_session_to_the_tr_commit(
    real_store, git_active, repo, monkeypatch,
):
    """같은 ``POST …/approve`` 가 TR 세션이면 TR 커밋으로 간다. 병합 승인 함수는 불리지
    않는다 — TR 커밋과 병합 검토 적용은 한 잠금 구간으로 합쳐지지 않는다."""
    _row, parked, out = _resolved_cancel(repo)
    monkeypatch.setattr(git_routes, "_check_group_permission", lambda *a, **k: None)

    def _never(*_a, **_k):
        raise AssertionError("a TR session must not reach approve_merge_review")

    monkeypatch.setattr(git_routes.git_service, "approve_merge_review", _never)
    body = git_routes.ApproveBody(
        attempt_id="1b4e28ba-2fa1-11d2-883f-0016d3cca427",
        review_fingerprint=out["result"]["review_fingerprint"],
    )

    resp = git_routes.post_merge_review_approve(_GROUP, parked["merge_id"], body, user={})

    assert resp["ok"] is True
    assert resp["result"]["status"] == "committed"


# ── 2. 검토 대기의 TTL ─────────────────────────────────────────────────────────

def _session(created_h: float, touched_h: float) -> dict:
    return {"merge_id": 1, "created_at": _iso(created_h), "touched_at": _iso(touched_h)}


def test_a_review_wait_that_holds_no_base_never_expires(monkeypatch):
    monkeypatch.setattr(cleanup.merge_target, "holds_base_checkout", lambda s: False)

    assert cleanup._review_pending_ttl_expired(_session(500, 400)) is False


def test_a_base_holding_review_wait_lives_while_it_is_looked_at(monkeypatch):
    monkeypatch.setattr(cleanup.merge_target, "holds_base_checkout", lambda s: True)

    assert cleanup._review_pending_ttl_expired(_session(30, 1)) is False
    assert cleanup._review_pending_ttl_expired(_session(30, 25)) is True


def test_a_base_holding_review_wait_has_a_hard_cap(monkeypatch):
    monkeypatch.setattr(cleanup.merge_target, "holds_base_checkout", lambda s: True)
    cap = cleanup.REVIEW_PENDING_MAX_HOURS

    assert cleanup._review_pending_ttl_expired(_session(cap - 1, 1)) is False
    assert cleanup._review_pending_ttl_expired(_session(cap + 1, 1)) is True


@needs_git
def test_the_sweep_keeps_a_resolved_tr_conflict_under_the_review_ttl(
    real_store, git_active, repo,
):
    """해결된 TR 충돌은 검토 TTL 을 따른다: 최근에 열어 봤으면 하루가 지나도 남고,
    상한을 넘으면 예전 TTL 과 같은 포기 경로로 정리된다."""
    _row, parked, _out = _resolved_cancel(repo)
    merge_id = parked["merge_id"]
    real_store._execute(
        "UPDATE git_merge_session SET created_at = ?, touched_at = ? WHERE merge_id = ?",
        [_iso(30), _iso(1), merge_id],
    )

    svc.merge_session_sweep()
    assert db_git.get_session(merge_id)["status"] == "open"

    real_store._execute(
        "UPDATE git_merge_session SET created_at = ? WHERE merge_id = ?",
        [_iso(cleanup.REVIEW_PENDING_MAX_HOURS + 1), merge_id],
    )
    svc.merge_session_sweep()
    assert db_git.get_session(merge_id)["status"] == "aborted"


@needs_git
def test_opening_the_tr_review_counts_as_activity(real_store, git_active, repo):
    _row, parked, _out = _resolved_cancel(repo)
    merge_id = parked["merge_id"]
    stale = _iso(23)
    real_store._execute(
        "UPDATE git_merge_session SET touched_at = ? WHERE merge_id = ?", [stale, merge_id],
    )

    svc.get_merge_review(_GROUP, merge_id)

    assert db_git.get_session(merge_id)["touched_at"] != stale


# ── 3. 청소가 닫은 시도는 abort 와 같이 정리된다 ───────────────────────────────

def test_a_sweep_close_discards_the_intent_and_the_checkpoint(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        svc.db_git, "invalidate_resolution_checkpoint_for_merge",
        lambda merge_id: calls.append(("checkpoint", merge_id)),
    )
    monkeypatch.setattr(
        cleanup.approval_intent, "discard_intent",
        lambda merge_id, reason: calls.append(("intent", merge_id, reason)),
    )

    cleanup._discard_abandoned_attempt(7, "ttl_expired")

    assert calls == [("checkpoint", 7), ("intent", 7, "ttl_expired")]


def test_a_failing_cleanup_never_breaks_the_sweep(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(svc.db_git, "invalidate_resolution_checkpoint_for_merge", _boom)
    monkeypatch.setattr(cleanup.approval_intent, "discard_intent", _boom)

    cleanup._discard_abandoned_attempt(7, "orphan_recovered")   # must not raise


def test_an_orphan_close_discards_the_attempt(monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(cleanup.merge_target, "close_session_attempt", lambda *a, **k: True)
    monkeypatch.setattr(svc, "_set_status", lambda *a, **k: None)
    monkeypatch.setattr(cleanup, "_emit_auto_aborted", lambda *a, **k: None)
    monkeypatch.setattr(
        cleanup, "_discard_abandoned_attempt", lambda merge_id, reason: seen.append((merge_id, reason)),
    )

    cleanup._close_orphan({"merge_id": 11, "group_id": _GROUP}, _PROJECT)

    assert seen == [(11, "orphan_recovered")]
