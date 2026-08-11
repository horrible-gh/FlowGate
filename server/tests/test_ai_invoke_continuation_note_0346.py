"""flowgate.default.0346 (R0001 -> N0002 -> NR0003 -> D0004 -> T0005 구현): the [전달멘트] tab's
common/per-step notes, prepended to each continuous hop's prompt as a "사용자 메시지" section.

Covers:
  • _resolve_continuation_hop_note: the SAME _hop_worker_item_seq fold + str/int dual-key
    lookup as _resolve_continuation_hop_override — the two tables must resolve the same
    item_seq for the same hop, or the row a user sees in ContinuousWorkDialog would carry a
    provider meant for one hop and a note meant for another (0317 T0013 재발 방지).
  • start_run prompt injection, end to end: individual-only / common-only / both / neither,
    verified against the LITERAL bytes a spawned worker receives on stdin (no notes ⇒
    byte-identical to the pre-feature prompt — D0004 §3-3 / T0005 §3 제약 5).
  • _maybe_auto_resume_hop / _spawn_auto_resume: the note bundle rides the run forward across
    a hop re-spawn, mirroring test_ai_provider_doctype_map_0317.py's provider-override coverage
    (the "first hop only" shape is the easiest way to get this feature wrong — T0005 §3 제약 4).
  • a note-resolution failure never stalls the hop.

Uses TESTING=1; DB/settings/token layers monkeypatched exactly like test_ai_invoke_0187.py —
the real-subprocess CLI adapter measures exact stdin bytes to prove full/exact prompt delivery.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import invoke_mention_service  # noqa: E402

PY = sys.executable
ROOT_DOC = "flowgate.default.0346.0001-R"


def _provider(pid="aip_test01", cmd=None):
    return {
        "id": pid, "name": "cli-1", "exec_type": "cli", "kind": "claude",
        "enabled": True, "cli_command": cmd,
        "api_base_url": None, "api_model": None, "api_key_set": False, "api_key_hint": None,
    }


class FakeWfseq:
    """Stand-in for db.workflow_sequences with an item_seq-addressable effective head, so
    _hop_worker_item_seq's instruction->report fold resolves exactly like the live sequence
    would. Default items: N/NR realized, T (head) pending, its paired TR pending — the worker
    fills TR@4, which is the row ContinuousWorkDialog shows and keys overrides on."""

    def __init__(self, head_item_seq=3, items=None):
        self.sequence = {"id": 1}
        self.head_item_seq = head_item_seq
        self.items = items if items is not None else [
            {"item_seq": 1, "type": "N", "result_doc_id": "d-0002-N"},
            {"item_seq": 2, "type": "NR", "result_doc_id": "d-0003-NR"},
            {"item_seq": 3, "type": "T", "result_doc_id": None},
            {"item_seq": 4, "type": "TR", "result_doc_id": None},
        ]

    def get_sequence_for_member_doc(self, doc_id):
        return self.sequence

    def get_sequence_by_doc_id(self, doc_id):
        return self.sequence

    def get_sequence_items(self, seq_id):
        return list(self.items)

    def get_effective_head(self, seq_id):
        return next((dict(i) for i in self.items if i["item_seq"] == self.head_item_seq), None)


@pytest.fixture
def note_env(monkeypatch, tmp_path):
    """Patch every collaborator start_run touches for a mode='continuous' run — same shape as
    test_ai_invoke_0187.py's fake_env, with get_effective_head added so the note/provider hop
    resolvers actually resolve instead of degrading on a missing collaborator."""
    wfseq = FakeWfseq()
    chain_holder = {"providers": [], "source": "system", "registered_count": 0}

    monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda group_id: 4)
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", lambda group_id: [])
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: {"doc_id": doc_id, "branch": "main"})
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", wfseq.get_sequence_for_member_doc)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", wfseq.get_sequence_by_doc_id)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", wfseq.get_sequence_items)
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", wfseq.get_effective_head)
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_name": "testproj"})
    monkeypatch.setattr(
        svc.ai_settings_service, "resolve_effective", lambda pid: {"ok": True, **chain_holder}
    )
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: None)
    monkeypatch.setattr(
        svc.token_service, "issue",
        lambda **kw: {
            "raw_token": "tok_raw_test", "token_id": "tok_20260729_000001",
            "expires_at": "2026-07-30T00:00:00+00:00",
            "scratch_dir": str(tmp_path / "tokwork"),
        },
    )
    monkeypatch.setattr(svc.token_service, "revoke", lambda *a, **kw: None)
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *a, **kw: tmp_path / "storage")
    src_root = tmp_path / "srcroot"
    src_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        svc.storage_paths, "resolve_project_src_root", lambda pid, branch, *, group_id: src_root
    )
    monkeypatch.setattr(svc.storage_paths, "to_storage_relative", lambda path, project=None: str(path))
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc, "_broadcast", lambda run, event_type, payload: None)
    return {"wfseq": wfseq, "chain": chain_holder, "tmp": tmp_path}


_counter = {"n": 0}


def _capture_cmd(tmp_path):
    """A CLI provider that writes its FULL stdin to a file, so the test can assert the exact
    bytes the worker received (not just a length) — stdin is how the prompt is delivered
    (never argv, cp932 truncation risk)."""
    _counter["n"] += 1
    outfile = tmp_path / f"captured_{_counter['n']}.txt"
    posix = outfile.as_posix()
    cmd = f'"{PY}" -c "import sys,pathlib; pathlib.Path(\'{posix}\').write_bytes(sys.stdin.buffer.read())"'
    return cmd, outfile


def _start(
    env,
    mention,
    *,
    note_overrides=None,
    default_note=None,
    target_seq=4,
    instruction_mode="auto_approved",
):
    cmd, outfile = _capture_cmd(env["tmp"])
    env["chain"]["providers"] = [_provider(cmd=cmd)]
    env["chain"]["registered_count"] = 1
    res = svc.start_run(
        project_id="flowgate",
        module="default",
        group_id="flowgate.default.0346",
        doc_ref=ROOT_DOC,
        action_scope="new",
        mode="continuous",
        continuation_target_seq=target_seq,
        continuation_review_mode=False,
        continuation_instruction_mode=instruction_mode,
        continuation_locale="ko",
        issued_to="usr_admin",
        api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda raw, scratch: mention,
        continuation_note_overrides=note_overrides,
        continuation_default_note=default_note,
    )
    return res, outfile


def _wait_finished(run_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = svc.get_run_record(run_id)
        if run and run["status"] == "finished":
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def _read(outfile, timeout=30.0) -> bytes:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if outfile.exists():
            return outfile.read_bytes()
        time.sleep(0.05)
    raise AssertionError(f"{outfile} was never written")


MENTION = "## 지시\n문서를 작성하세요.\n"


class TestResolveHopNote:
    """Unit coverage for _resolve_continuation_hop_note — the note-table twin of
    _resolve_continuation_hop_override's item_seq fold + str/int key lookup."""

    def _wire(self, monkeypatch, head_item_seq, items=None):
        wfseq = FakeWfseq(head_item_seq=head_item_seq, items=items)
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", wfseq.get_sequence_for_member_doc)
        monkeypatch.setattr(svc.db_wfseq, "get_effective_head", wfseq.get_effective_head)
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", wfseq.get_sequence_items)
        return wfseq

    def test_auto_approved_report_row_note_is_found_via_head_fold(self, monkeypatch):
        # head = T@3 -> worker fills TR@4; keyed on the visible TR row (string key, JSON body shape).
        self._wire(monkeypatch, head_item_seq=3)
        assert svc._resolve_continuation_hop_note(
            ROOT_DOC,
            {"4": "TR용 멘트"},
            continuation_instruction_mode="auto_approved",
        ) == "TR용 멘트"

    def test_auto_approved_instruction_row_note_does_not_resolve(self, monkeypatch):
        self._wire(monkeypatch, head_item_seq=3)
        assert svc._resolve_continuation_hop_note(
            ROOT_DOC,
            {"3": "T용 멘트"},
            continuation_instruction_mode="auto_approved",
        ) is None

    def test_ai_direct_instruction_row_note_resolves_without_report_fold(self, monkeypatch):
        self._wire(monkeypatch, head_item_seq=3)
        assert svc._resolve_continuation_hop_note(
            ROOT_DOC,
            {"3": "T용 멘트", "4": "TR용 멘트"},
            continuation_instruction_mode="ai_direct",
        ) == "T용 멘트"

    def test_int_key_also_resolves(self, monkeypatch):
        # Same-process callers (never JSON-decoded) may hand an int key.
        self._wire(monkeypatch, head_item_seq=3)
        assert svc._resolve_continuation_hop_note(ROOT_DOC, {4: "TR용 멘트"}) == "TR용 멘트"

    def test_blank_note_is_treated_as_absent(self, monkeypatch):
        self._wire(monkeypatch, head_item_seq=3)
        assert svc._resolve_continuation_hop_note(ROOT_DOC, {"4": "   "}) is None

    def test_no_sequence_returns_none(self, monkeypatch):
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _d: None)
        assert svc._resolve_continuation_hop_note(ROOT_DOC, {"4": "x"}) is None

    def test_lookup_failure_degrades_to_none(self, monkeypatch):
        def _boom(_s):
            raise RuntimeError("boom")
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _d: {"id": 1})
        monkeypatch.setattr(svc.db_wfseq, "get_effective_head", _boom)
        assert svc._resolve_continuation_hop_note(ROOT_DOC, {"4": "x"}) is None


class TestPromptInjectionEndToEnd:
    """Full start_run runs (real subprocess) asserting the EXACT bytes the worker receives."""

    def test_individual_note_only(self, note_env):
        res, outfile = _start(note_env, MENTION, note_overrides={"4": "이 단계는 결제 실패 케이스도 다뤄줘"})
        _wait_finished(res["run_id"])
        expected = invoke_mention_service.prepend_messages_section(
            MENTION, ["이 단계는 결제 실패 케이스도 다뤄줘"], "ko",
        )
        assert _read(outfile).decode("utf-8") == expected

    def test_ai_direct_instruction_note_reaches_spawned_worker(self, note_env):
        res, outfile = _start(
            note_env,
            MENTION,
            note_overrides={"3": "T 직접 작성 멘트", "4": "TR 멘트"},
            instruction_mode="ai_direct",
        )
        _wait_finished(res["run_id"])
        expected = invoke_mention_service.prepend_messages_section(
            MENTION, ["T 직접 작성 멘트"], "ko",
        )
        assert _read(outfile).decode("utf-8") == expected

    def test_common_note_only(self, note_env):
        res, outfile = _start(note_env, MENTION, default_note="이 그룹은 결제 모듈 리팩터링입니다")
        _wait_finished(res["run_id"])
        expected = invoke_mention_service.prepend_messages_section(
            MENTION, ["이 그룹은 결제 모듈 리팩터링입니다"], "ko",
        )
        assert _read(outfile).decode("utf-8") == expected

    def test_common_note_reaches_every_hop(self, note_env):
        # A second hop (different head item_seq) must ALSO receive the common note — it is not
        # a one-shot, first-hop-only value.
        note_env["wfseq"].head_item_seq = 1
        note_env["wfseq"].items = [
            {"item_seq": 1, "type": "TR", "result_doc_id": None},
        ]
        res, outfile = _start(
            note_env, MENTION, default_note="공통 멘트", target_seq=1,
        )
        _wait_finished(res["run_id"])
        expected = invoke_mention_service.prepend_messages_section(MENTION, ["공통 멘트"], "ko")
        assert _read(outfile).decode("utf-8") == expected

    def test_both_notes_are_adopted_not_replaced(self, note_env):
        # D0004 §3-3: an individual note does NOT push out the common one — both are carried.
        res, outfile = _start(
            note_env, MENTION,
            default_note="공통 멘트",
            note_overrides={"4": "개별 멘트"},
        )
        _wait_finished(res["run_id"])
        expected = invoke_mention_service.prepend_messages_section(
            MENTION, ["공통 멘트", "개별 멘트"], "ko",
        )
        got = _read(outfile).decode("utf-8")
        assert got == expected
        assert "공통 멘트" in got and "개별 멘트" in got

    def test_no_notes_is_byte_identical_to_the_pre_feature_prompt(self, note_env):
        # T0005 §3 제약 5: no notes supplied ⇒ the worker's prompt must be untouched, byte
        # for byte — no empty section, no stray blank line.
        res, outfile = _start(note_env, MENTION)
        _wait_finished(res["run_id"])
        assert _read(outfile) == MENTION.encode("utf-8")

    def test_notes_ignored_on_a_single_run(self, note_env):
        # Even if a caller mistakenly forwarded note fields on a single run, the injection
        # branch is gated on mode == 'continuous' — a single run's prompt stays untouched.
        cmd, outfile = _capture_cmd(note_env["tmp"])
        note_env["chain"]["providers"] = [_provider(cmd=cmd)]
        note_env["chain"]["registered_count"] = 1
        res = svc.start_run(
            project_id="flowgate", module="default", group_id="flowgate.default.0346",
            doc_ref=ROOT_DOC, action_scope="new", mode="single",
            continuation_target_seq=None, continuation_review_mode=False,
            continuation_instruction_mode=None, continuation_locale=None,
            issued_to="usr_admin", api_base_url="http://127.0.0.1:1/flowgate/api/v1",
            mention_builder=lambda raw, scratch: MENTION,
            continuation_note_overrides={"4": "무시되어야 함"},
            continuation_default_note="무시되어야 함",
        )
        _wait_finished(res["run_id"])
        assert _read(outfile) == MENTION.encode("utf-8")


class TestNoteInjectionFailureIsSwallowed:
    def test_note_resolution_exception_does_not_stall_the_hop(self, note_env, monkeypatch):
        def _boom(_s):
            raise RuntimeError("boom")
        monkeypatch.setattr(svc.db_wfseq, "get_effective_head", _boom)
        res, outfile = _start(note_env, MENTION, note_overrides={"4": "x"}, default_note="y")
        run = _wait_finished(res["run_id"])
        assert run["end_reason"] == "exited"
        # The default note still applies (its own strip/append never touches db_wfseq); only
        # the per-step lookup — the one that failed — is skipped.
        expected = invoke_mention_service.prepend_messages_section(MENTION, ["y"], "ko")
        assert _read(outfile).decode("utf-8") == expected


class TestNotesStayInsideThePause:
    """The notes are per-run content, and the ONE place they may be stored is a paused chain.

    D0004 / T0005 완료 기준 '공통' originally said the feature adds no schema and no
    persistence: the notes were to live in the in-memory run dict and the start request,
    nowhere else. This class asserted that by searching all of `server/sql` and
    `server/modules/flow_gate/db` for the two field names and demanding zero hits.

    0394 T0004 (NR0003 §13.1-1) is where that had to be decided rather than repaired.
    Pause/resume arrived afterwards and cannot work without keeping them: a paused chain
    survives a server restart, so whatever the resumed hop must be told has to be on
    disk. `076a_ai_invoke_paused_provider.sql` adds the two columns to
    `ai_invoke_paused_chains` and `db/ai_invoke_paused_chains.py` writes them — which is
    what turned this guard red. Deleting the guard would give the field names free rein;
    keeping "zero hits" would mean deleting pause/resume. So the rule is re-stated with
    the boundary the original was really drawing: the notes may be stored in the paused
    chain and NOWHERE else. A migration that put them on documents, tokens, or the run
    history — where they would outlive the run and become user content nobody scheduled
    for deletion — still fails here, which was the point.

    Two things make that boundary safe, and both are asserted below:

      * the only table involved is `ai_invoke_paused_chains`, and
      * a row there is deleted when the chain is resumed, cancelled or swept
        (`delete_and_return` / `delete_by_group` / `delete_system_stop`), so retention is
        bounded by the pause itself rather than being open-ended.

    The retention review the original wording asked for therefore has an answer: the
    notes live exactly as long as the pause the user created, and go with it.
    """

    FIELDS = ("continuation_default_note", "continuation_note_overrides")

    # The paused-chain snapshot is the sanctioned home. Anything else is a finding.
    ALLOWED_SQL = {"076a_ai_invoke_paused_provider.sql"}
    ALLOWED_DB_MODULES = {"ai_invoke_paused_chains.py"}

    def _hits(self, root: Path, patterns=("*.sql", "*.py")) -> list[str]:
        found = []
        for pattern in patterns:
            for path in root.rglob(pattern):
                if "__pycache__" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if any(field in text for field in self.FIELDS):
                    found.append(str(path.relative_to(_SERVER_DIR)))
        return sorted(found)

    def test_only_the_paused_chain_migration_stores_the_note_fields(self):
        sql_root = _SERVER_DIR / "sql"
        assert sql_root.is_dir(), "server/sql must exist for this guard to mean anything"

        unexpected = [h for h in self._hits(sql_root) if Path(h).name not in self.ALLOWED_SQL]
        assert unexpected == [], (
            "전달멘트가 일시정지 스냅샷 밖의 표에 저장된다: "
            f"{unexpected}. 이 값은 해당 실행에만 속하는 사용자 입력이라, 일시정지가 "
            "풀리면 함께 사라져야 한다 — 다른 표에 넣으려면 보관 기간을 먼저 정하라."
        )

    def test_only_the_paused_chain_module_writes_the_note_fields(self):
        db_root = _SERVER_DIR / "modules" / "flow_gate" / "db"
        assert db_root.is_dir(), "server/modules/flow_gate/db must exist for this guard to mean anything"

        unexpected = [
            h for h in self._hits(db_root) if Path(h).name not in self.ALLOWED_DB_MODULES
        ]
        assert unexpected == [], (
            f"전달멘트를 일시정지 스냅샷 밖에서 읽거나 쓰는 db 모듈이 있다: {unexpected}."
        )

    def test_the_paused_row_is_deleted_when_the_pause_ends(self):
        """Bounded retention: the row carrying the notes has a delete path out of it."""
        from modules.flow_gate.db import ai_invoke_paused_chains as paused

        for remover in ("delete_and_return", "delete_by_group", "delete_system_stop"):
            assert callable(getattr(paused, remover, None)), (
                f"{remover} 가 없다. 일시정지 행이 지워지지 않으면 전달멘트가 무기한 남는다."
            )


class TestNoteRunDictAndAutoResume:
    """The note bundle rides the run dict and the auto-resume handoff exactly like
    continuation_provider_overrides / continuation_base_provider_id (0317 T0013 결함 재발
    방지 shape: 첫 홉엔 반영되는데 두 번째 홉부터 사라지는 누락)."""

    @pytest.fixture(autouse=True)
    def _clean_registries(self):
        for _lock, _reg in ((svc._runs_lock, svc._runs), (svc._auto_resume_lock, svc._auto_resume)):
            with _lock:
                _reg.clear()
        yield
        for _lock, _reg in ((svc._runs_lock, svc._runs), (svc._auto_resume_lock, svc._auto_resume)):
            with _lock:
                _reg.clear()

    def test_run_dict_carries_the_note_bundle_only_when_continuous(self, note_env):
        res, outfile = _start(
            note_env, MENTION, default_note="공통", note_overrides={"4": "개별"},
        )
        _wait_finished(res["run_id"])
        run = svc.get_run_record(res["run_id"])
        assert run["continuation_default_note"] == "공통"
        assert run["continuation_note_overrides"] == {"4": "개별"}

    def test_maybe_resume_carries_note_bundle_forward(self, monkeypatch):
        gid = "flowgate.default.0346"
        calls = []
        monkeypatch.setattr(svc, "_spawn_auto_resume", lambda g, p: calls.append((g, p)))
        svc.request_auto_resume(gid, {
            "doc_ref": ROOT_DOC, "target_seq": 4,
            "review_mode": False, "instruction_mode": "auto_approved",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
        })
        run = {
            "group_id": gid, "end_reason": "exited", "cancel_event": None,
            "continuation_provider_overrides": None,
            "continuation_note_overrides": {"4": "개별"},
            "continuation_default_note": "공통",
        }
        svc._maybe_auto_resume_hop(run)
        assert len(calls) == 1
        _g, pending = calls[0]
        assert pending["note_overrides"] == {"4": "개별"}
        assert pending["default_note"] == "공통"

    def test_spawn_passes_note_bundle_to_start_run(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw))
        svc._spawn_auto_resume("flowgate.default.0346", {
            "doc_ref": ROOT_DOC, "target_seq": 4,
            "review_mode": False, "instruction_mode": "auto_approved",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
            "note_overrides": {"4": "개별"}, "default_note": "공통",
        })
        assert captured["continuation_note_overrides"] == {"4": "개별"}
        assert captured["continuation_default_note"] == "공통"

    def test_spawn_without_notes_forwards_none(self, monkeypatch):
        # A pending hop queued before this feature (or with no notes chosen) must not crash
        # _spawn_auto_resume — pending.get() degrades cleanly to None.
        captured: dict = {}
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw))
        svc._spawn_auto_resume("flowgate.default.0346", {
            "doc_ref": ROOT_DOC, "target_seq": 4,
            "review_mode": False, "instruction_mode": "auto_approved",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
        })
        assert captured["continuation_note_overrides"] is None
        assert captured["continuation_default_note"] is None
