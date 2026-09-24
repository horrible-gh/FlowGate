"""flowgate.default.0554 T0014: WorkPlan pre-instruction reaches the real worker/review context.

Covers:
  • start_run prompt injection, end to end, verified against the LITERAL bytes a spawned
    worker receives on stdin (mirrors test_ai_invoke_continuation_note_0346.py's harness for
    the sibling `note` feature, since pre-instruction is injected at the exact same
    admission._inject_hop_notes convergence point, D0007 §3.5/T0014 §2).
  • WorkPlan auto-approved pre-instruction reaches its source authoring worker only.
  • a stored-but-invalid attachment reference stops the hop before any worker spawns,
    revoking the token it already minted (T0014 §5 fail-closed contract).
  • note + pre-instruction coexist as two distinct sections (T0014 §14).
  • the rework mention (issue_rework_request / build_rework_mention) re-applies the same
    slot's pre-instruction, and its own fail-closed contract (T0014 §9 / §5).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("ALLOWED_ORIGIN", "")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import invoke_mention_service  # noqa: E402
from modules.flow_gate.services import work_plan_attachment_service as wpa_svc  # noqa: E402
from modules.flow_gate.services.ai_invoke import admission  # noqa: E402

PY = sys.executable
ROOT_DOC = "flowgate.default.0554.9001-R"
GROUP_ID = "flowgate.default.0554.preinstr"
MENTION = "## 지시\n문서를 작성하세요.\n"
PRE_TEXT = "이 단계는 결제 실패 케이스도 함께 검토해라"

ATTACHMENT = {
    "doc_id": "flowgate.default.0554.9000-WP",
    "filename": "__wp_pre_instruction__T-1__abc123.txt",
    "original_filename": "brief.txt",
    "content_sha256": "a" * 64,
}


def _attachment_json(overrides=None) -> str:
    data = dict(ATTACHMENT)
    if overrides:
        data.update(overrides)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class _FakePart:
    """The slice of Starlette's UploadFile upload_pre_instruction_attachment touches."""

    def __init__(self, filename, data: bytes) -> None:
        self.filename = filename
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk, self._pos = self._data[self._pos:], len(self._data)
            return chunk
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk


def _provider(pid="aip_test01", cmd=None):
    return {
        "id": pid, "name": "cli-1", "exec_type": "cli", "kind": "claude",
        "enabled": True, "cli_command": cmd,
        "api_base_url": None, "api_model": None, "api_key_set": False, "api_key_hint": None,
    }


class FakeWfseq:
    """Stand-in for db.workflow_sequences with an item_seq-addressable effective head —
    same shape as test_ai_invoke_continuation_note_0346.py's fixture, extended with the
    pre-instruction columns migration 115 added."""

    def __init__(self, head_item_seq=1, items=None):
        self.sequence = {"id": 1}
        self.head_item_seq = head_item_seq
        self.items = items if items is not None else [
            {
                "item_seq": 1, "type": "D", "result_doc_id": None,
                "source_doc_id": ATTACHMENT["doc_id"],
                "pre_instruction_text": PRE_TEXT,
                "pre_instruction_attachment_json": None,
            },
        ]

    def get_sequence_for_member_doc(self, doc_id):
        return self.sequence

    def get_sequence_by_doc_id(self, doc_id):
        return self.sequence

    def get_sequence_items(self, seq_id):
        return [dict(i) for i in self.items]

    def get_effective_head(self, seq_id):
        return next((dict(i) for i in self.items if i["item_seq"] == self.head_item_seq), None)

    def get_item_by_result_doc_id(self, doc_id):
        return next((dict(i) for i in self.items if i.get("result_doc_id") == doc_id), None)

    # ── work_plan_apply_service.apply()'s real write surface (T0014 §15 rework) ──────────
    # A previous rejection ("project()만 호출한 뒤 값을 직접 복사") required the REAL apply()
    # to be the thing that lands values onto sequence rows, not a hand copy of project()'s
    # return value. apply() calls exactly these three db_wfseq functions inside
    # get_store().transaction() — reproducing their real column-shape contract here (see
    # workflow_sequences.py's own insert_sequence_item/update_sequence_item_plan_snapshot)
    # is what lets it run unmodified against this fixture's in-memory store.
    def insert_sequence(self, doc_id):
        self.sequence = {"id": 1, "head_advanced_at": None}

    def insert_sequence_item(
        self, *, sequence_id, item_seq, type_, label, doc_class, sort_order,
        note="", source_doc_id=None, source_revision_no=None,
        provider_id=None, provider_display_name=None, review_count=0,
        reviewer_provider_id=None, reviewer_provider_display_name=None,
        pre_instruction_text=None, pre_instruction_attachment=None,
    ):
        import json as _json

        attachment_json = (
            _json.dumps(pre_instruction_attachment, ensure_ascii=False, separators=(",", ":"))
            if isinstance(pre_instruction_attachment, dict) else pre_instruction_attachment
        )
        self._next_id = getattr(self, "_next_id", 0) + 1
        self.items.append({
            "id": self._next_id, "item_seq": item_seq, "type": type_, "label": label,
            "doc_class": doc_class, "sort_order": sort_order, "note": note or "",
            "source_doc_id": source_doc_id, "source_revision_no": source_revision_no,
            "provider_id": provider_id, "provider_display_name": provider_display_name,
            "review_count": review_count, "reviewer_provider_id": reviewer_provider_id,
            "reviewer_provider_display_name": reviewer_provider_display_name,
            "pre_instruction_text": pre_instruction_text,
            "pre_instruction_attachment_json": attachment_json,
            "result_doc_id": None, "status": "pending",
        })

    def update_sequence_item_plan_snapshot(
        self, item_id, *, note, source_doc_id, source_revision_no,
        provider_id, provider_display_name, review_count=0,
        reviewer_provider_id=None, reviewer_provider_display_name=None,
        pre_instruction_text=None, pre_instruction_attachment=None,
    ):
        import json as _json

        attachment_json = (
            _json.dumps(pre_instruction_attachment, ensure_ascii=False, separators=(",", ":"))
            if isinstance(pre_instruction_attachment, dict) else pre_instruction_attachment
        )
        for item in self.items:
            if item.get("id") == item_id:
                item.update(
                    note=note or "", source_doc_id=source_doc_id,
                    source_revision_no=source_revision_no, provider_id=provider_id,
                    provider_display_name=provider_display_name, review_count=review_count,
                    reviewer_provider_id=reviewer_provider_id,
                    reviewer_provider_display_name=reviewer_provider_display_name,
                    pre_instruction_text=pre_instruction_text,
                    pre_instruction_attachment_json=attachment_json,
                )


@pytest.fixture
def pre_env(monkeypatch, tmp_path):
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
    monkeypatch.setattr(svc.db_wfseq, "get_item_by_result_doc_id", wfseq.get_item_by_result_doc_id)
    monkeypatch.setattr(svc.db_wfseq, "insert_sequence", wfseq.insert_sequence)
    monkeypatch.setattr(svc.db_wfseq, "insert_sequence_item", wfseq.insert_sequence_item)
    monkeypatch.setattr(
        svc.db_wfseq, "update_sequence_item_plan_snapshot", wfseq.update_sequence_item_plan_snapshot,
    )
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_name": "testproj"})
    monkeypatch.setattr(
        svc.ai_settings_service, "resolve_effective", lambda pid: {"ok": True, **chain_holder}
    )
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: None)
    monkeypatch.setattr(
        svc.token_service, "issue",
        lambda **kw: {
            "raw_token": "tok_raw_test", "token_id": "tok_20260922_000001",
            "expires_at": "2026-09-23T00:00:00+00:00",
            "scratch_dir": str(tmp_path / "tokwork"),
        },
    )
    revoked = []
    monkeypatch.setattr(
        svc.token_service, "revoke",
        lambda token_id, reason=None: revoked.append((token_id, reason)),
    )
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *a, **kw: tmp_path / "storage")
    src_root = tmp_path / "srcroot"
    src_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        svc.storage_paths, "resolve_project_src_root", lambda pid, branch, *, group_id: src_root
    )
    monkeypatch.setattr(svc.storage_paths, "to_storage_relative", lambda path, project=None: str(path))
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc, "_broadcast", lambda run, event_type, payload: None)
    return {"wfseq": wfseq, "chain": chain_holder, "tmp": tmp_path, "revoked": revoked}


_counter = {"n": 0}


def _capture_cmd(tmp_path):
    _counter["n"] += 1
    outfile = tmp_path / f"captured_{_counter['n']}.txt"
    posix = outfile.as_posix()
    cmd = f'"{PY}" -c "import sys,pathlib; pathlib.Path(\'{posix}\').write_bytes(sys.stdin.buffer.read())"'
    return cmd, outfile


def _slow_capture_cmd(tmp_path, seconds=1.0):
    """Like _capture_cmd, but sleeps first — gives a pause/resume test a window to call
    svc.pause_run/mark_user_paused while the hop is still counted as running."""
    _counter["n"] += 1
    outfile = tmp_path / f"captured_{_counter['n']}.txt"
    posix = outfile.as_posix()
    cmd = (
        f'"{PY}" -c "import sys,time,pathlib; time.sleep({seconds}); '
        f'pathlib.Path(\'{posix}\').write_bytes(sys.stdin.buffer.read())"'
    )
    return cmd, outfile


def _start(
    env, mention, *,
    note_overrides=None, default_note=None, target_seq=1,
    instruction_mode="auto_approved", cmd=None, outfile=None, to_end=False,
):
    if cmd is None:
        cmd, outfile = _capture_cmd(env["tmp"])
    env["chain"]["providers"] = [_provider(cmd=cmd)]
    env["chain"]["registered_count"] = 1
    res = svc.start_run(
        project_id="flowgate",
        module="default",
        group_id=GROUP_ID,
        doc_ref=ROOT_DOC,
        action_scope="new",
        mode="continuous",
        continuation_target_seq=target_seq,
        continuation_to_end=to_end,
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


class TestPreInstructionEndToEnd:
    """Full start_run runs (real subprocess) asserting the EXACT bytes the worker receives."""

    def test_text_reaches_the_spawned_worker_exactly_once(self, pre_env):
        res, outfile = _start(pre_env, MENTION)
        _wait_finished(res["run_id"])
        got = _read(outfile).decode("utf-8")
        assert got.count("WorkPlan 사전지시") == 1
        assert PRE_TEXT in got
        expected = invoke_mention_service.prepend_pre_instruction_section(
            MENTION, text=PRE_TEXT, attachment=None, locale="ko",
        )
        assert got == expected

    def test_no_pre_instruction_is_byte_identical_to_the_pre_feature_prompt(self, pre_env):
        pre_env["wfseq"].items[0]["pre_instruction_text"] = None
        res, outfile = _start(pre_env, MENTION)
        _wait_finished(res["run_id"])
        assert _read(outfile) == MENTION.encode("utf-8")

    def test_text_and_note_are_kept_as_two_distinct_sections(self, pre_env):
        res, outfile = _start(pre_env, MENTION, default_note="공통 멘트")
        _wait_finished(res["run_id"])
        got = _read(outfile).decode("utf-8")
        assert got.count("## WorkPlan 사전지시") == 1
        assert got.count("## 사용자 메세지") == 1
        assert "공통 멘트" in got and PRE_TEXT in got

    def test_valid_attachment_advertises_the_read_target_alongside_the_text(self, pre_env, monkeypatch):
        pre_env["wfseq"].items[0]["pre_instruction_attachment_json"] = _attachment_json()
        monkeypatch.setattr(wpa_svc, "validate_reference", lambda doc_id, reference: None)
        res, outfile = _start(pre_env, MENTION)
        _wait_finished(res["run_id"])
        got = _read(outfile).decode("utf-8")
        assert got.count("## WorkPlan 사전지시") == 1
        assert ATTACHMENT["original_filename"] in got
        assert ATTACHMENT["doc_id"] in got
        assert ATTACHMENT["filename"] in got
        assert PRE_TEXT in got

    def test_valid_attachment_is_a_real_reserved_file_the_hops_own_token_can_actually_read(
        self, pre_env, monkeypatch, tmp_path,
    ):
        """0554 T0014 §4/I2 rework (rej_01M33DPHG59E1H56 finding 4): the sibling test above
        (and test_document_attachments_0060.py's older reserved-lifecycle tests) all
        monkeypatch validate_reference/resolve_registered_attachment wholesale, so none of
        them ever prove a worker can actually READ the attachment the prompt advertises.

        This uploads a REAL reserved attachment through the real
        work_plan_attachment_service.upload_pre_instruction_attachment (real file on disk,
        real `attachments` registry row, over a real temp sqlite DB swapped into
        db.connection.STORE for this test only), points the sequence item's
        pre_instruction_attachment_json at that real reference — validate_reference is NOT
        monkeypatched here — runs a real start_run hop and confirms the prompt advertises it,
        then, using a worker token shaped exactly like the one this hop's item would carry
        (same group_id/doc_ref), calls the real
        GET /api/v1/document/{doc_id}/attachments/{name}/read route over HTTP and confirms
        the content and digest actually match what was uploaded.
        """
        import sqlite3
        from contextlib import contextmanager
        from unittest.mock import patch

        from fastapi import FastAPI
        from starlette.testclient import TestClient

        from modules.flow_gate.api.v1.document_routes import router as attach_router
        from modules.flow_gate.db import connection as conn_mod
        from modules.flow_gate.db import documents as db_documents
        from modules.flow_gate.services import auth_outbound

        # Canonical shape (project.module.NNNN.NNNN-TYPE) — the real HTTP read route's
        # own id_validators.DOC_ID regex requires this; GROUP_ID's synthetic
        # "flowgate.default.0554.preinstr" shape (fine for every service-level fake in this
        # file) would 422 the real route before scope is even checked.
        wp_doc_id = "flowgate.default.0554.9000-WP"
        db_path = tmp_path / "reserved_attachment_real.db"
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        migration = _SERVER_DIR / "sql" / "migrations" / "sqlite" / "083_attachment_registry.sql"
        conn.executescript(migration.read_text(encoding="utf-8"))
        conn.commit()

        original_store = conn_mod.STORE

        class _Store:
            """Routes `attachments` table SQL to the real migrated temp DB above; everything
            else (ai_invoke_runs, project_git_config, ...) still goes to whatever store this
            test process was already using — this hop's own bookkeeping must keep working
            exactly like every OTHER test in this file that never touches connection.STORE."""

            def _execute(self, sql, params=None):
                if "attachments" in sql:
                    conn.execute(sql, params or [])
                    conn.commit()
                else:
                    original_store._execute(sql, params or [])

            def _fetch_one(self, sql, params=None):
                if "attachments" in sql:
                    row = conn.execute(sql, params or []).fetchone()
                    return dict(row) if row else None
                return original_store._fetch_one(sql, params or [])

            def _fetch_all(self, sql, params=None):
                if "attachments" in sql:
                    return [dict(r) for r in conn.execute(sql, params or []).fetchall()]
                return original_store._fetch_all(sql, params or [])

            @contextmanager
            def transaction(self):
                try:
                    yield self
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
        conn_mod.STORE = _Store()

        # A real, colocated body file — resolve_attach_dir's rule ① (body's real location
        # wins) then needs no project_settings-driven group/subgroup path arithmetic at all.
        wp_file_path = "documents/flowgate/main/default/0554/9000-WP_document.md"
        (pre_env["tmp"] / "storage" / "documents" / "flowgate" / "main" / "default" / "0554").mkdir(
            parents=True, exist_ok=True,
        )
        (pre_env["tmp"] / "storage" / wp_file_path).write_text("# wp body", encoding="utf-8")

        def _get_by_id(doc_id):
            if doc_id == wp_doc_id:
                return {
                    "doc_id": wp_doc_id, "branch": "main", "project_id": "flowgate",
                    "module": "default", "group_id": GROUP_ID, "type_code": "WP",
                    "doc_review_status": "approved", "file_path": wp_file_path,
                }
            return {"doc_id": doc_id, "branch": "main"}

        monkeypatch.setattr(svc.db_docs, "get_by_id", _get_by_id)
        monkeypatch.setattr(db_documents, "get_by_id", _get_by_id)
        monkeypatch.setattr(wpa_svc, "assert_mutable", lambda *a, **kw: None)
        # pre_env stubs storage_paths.to_storage_relative to `str(path)` (fine for the run's
        # own scratch-dir bookkeeping, which never round-trips through this jail check) — the
        # attachment registry needs a REAL storage-root-relative path or resolve_registered_
        # attachment's J3 check reads the stored absolute path as STORAGE_PATH_OUTSIDE_ROOT.
        storage_root = (pre_env["tmp"] / "storage").resolve()
        monkeypatch.setattr(
            svc.storage_paths, "to_storage_relative",
            lambda path, project=None: Path(path).resolve().relative_to(storage_root).as_posix(),
        )

        try:
            import asyncio

            saved = asyncio.run(wpa_svc.upload_pre_instruction_attachment(
                wp_doc_id, "D#1",
                _FakePart("brief.txt", b"real reserved instruction bytes"),
                {"user_id": "usr_admin"}, None,
            ))
            reference = saved["reference"]
            assert wpa_svc.is_reserved_name(reference["filename"])

            pre_env["wfseq"].items[0]["source_doc_id"] = wp_doc_id
            pre_env["wfseq"].items[0]["pre_instruction_attachment_json"] = json.dumps(
                reference, ensure_ascii=False,
            )
            res, outfile = _start(pre_env, MENTION)
            _wait_finished(res["run_id"])
            got = _read(outfile).decode("utf-8")
            assert got.count("## WorkPlan 사전지시") == 1
            assert reference["original_filename"] in got
            assert reference["filename"] in got
            assert PRE_TEXT in got

            app = FastAPI()
            app.include_router(attach_router)
            client = TestClient(app)
            worker_token = {
                "token_id": "tok_worker_pre_instr", "issued_to": "usr_admin",
                "project": "flowgate", "group_id": GROUP_ID, "doc_ref": wp_doc_id,
                "action_scope": "new", "expires_at": "2999-01-01T00:00:00+00:00",
            }
            with patch.object(auth_outbound.token_service, "verify", return_value=worker_token), \
                 patch.object(auth_outbound, "has_permission", return_value=True):
                resp = client.get(
                    f"/api/v1/document/{wp_doc_id}/attachments/{reference['filename']}/read",
                    headers={"Authorization": "Bearer x"},
                )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["content"] == "real reserved instruction bytes"
            assert body["attachment"]["content_sha256"] == reference["content_sha256"]
        finally:
            conn_mod.STORE = original_store
            conn.close()

    def test_auto_approved_instruction_pre_instruction_reaches_source_authoring_worker_only(
        self, pre_env, monkeypatch,
    ):
        # 0611 T0009: auto_approved controls approval after authoring; a WorkPlan-backed
        # T remains the worker hop and consumes its own text/file context.
        pre_env["wfseq"].head_item_seq = 3
        pre_env["wfseq"].items = [
            {"item_seq": 1, "type": "N", "result_doc_id": "d-0002-N"},
            {"item_seq": 2, "type": "NR", "result_doc_id": "d-0003-NR"},
            {
                "item_seq": 3, "type": "T", "result_doc_id": None,
                "source_doc_id": ATTACHMENT["doc_id"], "pre_instruction_text": PRE_TEXT,
                "pre_instruction_attachment_json": _attachment_json(),
            },
            {
                "item_seq": 4, "type": "TR", "result_doc_id": None,
                "source_doc_id": ATTACHMENT["doc_id"], "pre_instruction_text": None,
                "pre_instruction_attachment_json": None,
            },
        ]
        monkeypatch.setattr(wpa_svc, "validate_reference", lambda doc_id, reference: None)
        res, outfile = _start(pre_env, MENTION, target_seq=4, instruction_mode="auto_approved")
        _wait_finished(res["run_id"])
        got = _read(outfile).decode("utf-8")
        assert got.count("## WorkPlan 사전지시") == 1
        assert PRE_TEXT in got
        assert ATTACHMENT["original_filename"] in got

    def test_ai_direct_instruction_row_gets_its_own_pre_instruction(self, pre_env):
        # Contrast case: under ai_direct the worker fills T@3 itself (no fold), so T@3's own
        # pre-instruction DOES apply — proving the fold is what suppressed it above, not a
        # blanket "T never gets one" rule.
        pre_env["wfseq"].head_item_seq = 3
        pre_env["wfseq"].items = [
            {
                "item_seq": 3, "type": "T", "result_doc_id": None,
                "source_doc_id": ATTACHMENT["doc_id"], "pre_instruction_text": PRE_TEXT,
                "pre_instruction_attachment_json": None,
            },
            {
                "item_seq": 4, "type": "TR", "result_doc_id": None,
                "source_doc_id": None, "pre_instruction_text": None,
                "pre_instruction_attachment_json": None,
            },
        ]
        res, outfile = _start(pre_env, MENTION, target_seq=4, instruction_mode="ai_direct")
        _wait_finished(res["run_id"])
        got = _read(outfile).decode("utf-8")
        assert got.count("WorkPlan 사전지시") == 1
        assert PRE_TEXT in got


class TestPreInstructionFailClosed:
    def test_invalid_source_attachment_stops_before_the_worker_is_spawned(
        self, pre_env, monkeypatch,
    ):
        pre_env["wfseq"].head_item_seq = 3
        pre_env["wfseq"].items = [
            {
                "item_seq": 3, "type": "T", "result_doc_id": None,
                "source_doc_id": ATTACHMENT["doc_id"],
                "pre_instruction_text": PRE_TEXT,
                "pre_instruction_attachment_json": _attachment_json(),
            },
            {
                "item_seq": 4, "type": "TR", "result_doc_id": None,
                "source_doc_id": ATTACHMENT["doc_id"],
                "pre_instruction_text": None,
                "pre_instruction_attachment_json": None,
            },
        ]
        monkeypatch.setattr(
            wpa_svc, "validate_reference",
            lambda doc_id, reference: "pre_instruction_attachment_digest_mismatch",
        )
        with pytest.raises(HTTPException) as caught:
            _start(pre_env, MENTION, target_seq=4)
        assert caught.value.status_code == 409
        assert caught.value.detail["code"] == "pre_instruction_attachment_digest_mismatch"
        assert caught.value.detail["source_doc_id"] == ATTACHMENT["doc_id"]
        # T0014 §5: "AI 호출 시작 안 됨" — no subprocess ever ran, so no capture file exists.
        assert list(pre_env["tmp"].glob("captured_*.txt")) == []
        # The token this hop already minted must not be left behind unrevoked.
        assert pre_env["revoked"] == [
            ("tok_20260922_000001", "ai_invoke_pre_instruction_attachment_invalid")
        ]

    def test_missing_file_and_doc_mismatch_surface_their_own_stable_codes(self, pre_env, monkeypatch):
        pre_env["wfseq"].items[0]["pre_instruction_attachment_json"] = _attachment_json()
        for code in (
            "pre_instruction_attachment_file_missing",
            "pre_instruction_attachment_doc_mismatch",
            "pre_instruction_attachment_registry_missing",
        ):
            monkeypatch.setattr(wpa_svc, "validate_reference", lambda doc_id, reference, c=code: c)
            with pytest.raises(HTTPException) as caught:
                _start(pre_env, MENTION)
            assert caught.value.detail["code"] == code


class TestPreInstructionSequenceLookupFailsClosed:
    """rej_01M338WJ83A3JTZJ finding 1: admission._inject_hop_notes/_sequence_item_by_seq used
    to swallow a sequence/head/item lookup EXCEPTION into the same None a genuinely-absent row
    produces — so a real stored pre_instruction_text/attachment on the row could silently never
    be checked, and the hop would start the AI anyway. This is a distinct failure mode from
    TestPreInstructionFailClosed (a reference that resolves but fails validation): here the row
    itself is never successfully read, so the code must stop rather than guess "no instruction".
    """

    def test_head_lookup_exception_stops_the_hop_before_the_worker_is_spawned(self, pre_env, monkeypatch):
        def boom(seq_id):
            raise RuntimeError("transient db failure")

        monkeypatch.setattr(svc.db_wfseq, "get_effective_head", boom)
        with pytest.raises(HTTPException) as caught:
            _start(pre_env, MENTION)
        assert caught.value.status_code == 409
        assert caught.value.detail["code"] == "pre_instruction_sequence_lookup_failed"
        # T0014 §5: no subprocess ever ran — a lookup failure is exactly as fail-closed as a
        # validated-but-bad attachment reference.
        assert list(pre_env["tmp"].glob("captured_*.txt")) == []
        assert pre_env["revoked"] == [
            ("tok_20260922_000001", "ai_invoke_pre_instruction_attachment_invalid")
        ]

    def test_sequence_item_by_seq_propagates_instead_of_degrading_to_none(self, monkeypatch):
        # rej_01M338WJ83A3JTZJ finding 1, second seam: _sequence_item_by_seq itself used to
        # catch this and return None — indistinguishable from "item_seq 1 does not exist in
        # this sequence". Its only caller now needs the exception to tell the two apart, so
        # it must propagate out of this function unit-level, not just through the full flow.
        def boom(seq_id):
            raise RuntimeError("transient db failure")

        monkeypatch.setattr(admission.db_wfseq, "get_sequence_for_member_doc", lambda doc_id: {"id": 1})
        monkeypatch.setattr(admission.db_wfseq, "get_sequence_items", boom)
        with pytest.raises(RuntimeError):
            admission._sequence_item_by_seq("some-doc", 1)

    def test_inject_hop_notes_fails_closed_when_head_lookup_raises(self, monkeypatch):
        def boom(doc_id):
            raise RuntimeError("transient db failure")

        monkeypatch.setattr(admission.db_wfseq, "get_sequence_for_member_doc", boom)
        with pytest.raises(wpa_svc.PreInstructionAttachmentError) as caught:
            admission._inject_hop_notes(
                MENTION, "some-doc",
                default_note=None, note_overrides=None,
                instruction_mode="auto_approved", locale="ko",
            )
        assert caught.value.code == "pre_instruction_sequence_lookup_failed"

    def test_inject_hop_notes_fails_closed_when_item_relookup_raises(self, monkeypatch):
        # get_effective_head resolves item_seq=1 normally (this "D" head never folds to a
        # paired report row, so it never calls get_sequence_items during that resolution) —
        # only the SECOND, independent re-lookup inside _sequence_item_by_seq fails. This is
        # the other propagation seam _inject_hop_notes must fail closed on.
        seq = {"id": 1}
        head = {"item_seq": 1, "type": "D"}

        def boom(seq_id):
            raise RuntimeError("transient db failure")

        monkeypatch.setattr(admission.db_wfseq, "get_sequence_for_member_doc", lambda doc_id: seq)
        monkeypatch.setattr(admission.db_wfseq, "get_effective_head", lambda seq_id: head)
        monkeypatch.setattr(admission.db_wfseq, "get_sequence_items", boom)
        with pytest.raises(wpa_svc.PreInstructionAttachmentError) as caught:
            admission._inject_hop_notes(
                MENTION, "some-doc",
                default_note=None, note_overrides=None,
                instruction_mode="auto_approved", locale="ko",
            )
        assert caught.value.code == "pre_instruction_sequence_lookup_failed"

    def test_legitimately_no_sequence_yet_returns_the_mention_unchanged(self, monkeypatch):
        # Contrast case: a workflow_decide run before any sequence has been saved returns None
        # WITHOUT raising — the ordinary "nothing to check yet" case must keep working exactly
        # as before this fix. Only an actual exception is fail-closed; a legitimate empty
        # result is not.
        monkeypatch.setattr(admission.db_wfseq, "get_sequence_for_member_doc", lambda doc_id: None)
        result = admission._inject_hop_notes(
            MENTION, "some-doc",
            default_note=None, note_overrides=None,
            instruction_mode="auto_approved", locale="ko",
        )
        assert result == MENTION


class TestPreInstructionMalformedJsonFailsClosed:
    """0554 T0014 §5 rework (review rej_01M334Z5Y72GK6BW finding 1): decode_pre_instruction_
    attachment returns None for BOTH an empty column and a corrupted one — that ambiguity used
    to make resolve_pre_instruction treat a damaged reference as "no attachment" (falling
    through to text-only, or to no pre-instruction at all if there was no text either) instead
    of stopping the hop. pre_instruction_attachment_json_malformed exists to tell those two
    cases apart so a stored-but-corrupted column always raises."""

    def test_decode_helper_distinguishes_empty_from_malformed(self):
        from modules.flow_gate.db import workflow_sequences as db_wfseq

        # Empty/absent: "no attachment", not malformed.
        assert db_wfseq.pre_instruction_attachment_json_malformed(None) is False
        assert db_wfseq.pre_instruction_attachment_json_malformed("") is False
        assert db_wfseq.pre_instruction_attachment_json_malformed("   ") is False
        # A well-formed reference is never malformed, whether given as JSON text or as an
        # already-decoded dict (some store backends hand back JSONB as a dict).
        assert db_wfseq.pre_instruction_attachment_json_malformed(_attachment_json()) is False
        assert db_wfseq.pre_instruction_attachment_json_malformed(dict(ATTACHMENT)) is False
        # Non-empty but corrupted: malformed.
        assert db_wfseq.pre_instruction_attachment_json_malformed("{not json") is True
        assert db_wfseq.pre_instruction_attachment_json_malformed("[1,2,3]") is True
        assert db_wfseq.pre_instruction_attachment_json_malformed('"just a string"') is True
        assert db_wfseq.pre_instruction_attachment_json_malformed("42") is True

    def test_resolve_pre_instruction_raises_a_stable_code_for_malformed_json(self):
        item = {
            "pre_instruction_text": None,
            "pre_instruction_attachment_json": "{this is not valid json",
            "source_doc_id": ATTACHMENT["doc_id"],
        }
        with pytest.raises(wpa_svc.PreInstructionAttachmentError) as caught:
            wpa_svc.resolve_pre_instruction(item)
        assert caught.value.code == "pre_instruction_attachment_decode_failed"
        assert caught.value.source_doc_id == ATTACHMENT["doc_id"]

    def test_malformed_json_fails_closed_even_when_text_is_also_present(self):
        # Before the fix: a malformed attachment column alongside a present text degraded to
        # "text-only" — the exact silent drop T0014 §5 forbids ("text가 있으면 text-only로").
        item = {
            "pre_instruction_text": PRE_TEXT,
            "pre_instruction_attachment_json": "not json at all",
            "source_doc_id": ATTACHMENT["doc_id"],
        }
        with pytest.raises(wpa_svc.PreInstructionAttachmentError) as caught:
            wpa_svc.resolve_pre_instruction(item)
        assert caught.value.code == "pre_instruction_attachment_decode_failed"

    def test_empty_attachment_column_with_text_still_returns_text_only(self):
        # Contrast case: an ACTUALLY empty column (never malformed) keeps the ordinary
        # text-only path working — the fix must not turn every row into a fail-closed one.
        item = {
            "pre_instruction_text": PRE_TEXT,
            "pre_instruction_attachment_json": None,
            "source_doc_id": ATTACHMENT["doc_id"],
        }
        assert wpa_svc.resolve_pre_instruction(item) == {"text": PRE_TEXT, "attachment": None}

    def test_malformed_attachment_stops_the_hop_before_the_worker_is_spawned(self, pre_env):
        pre_env["wfseq"].items[0]["pre_instruction_attachment_json"] = "{ this is corrupted"
        with pytest.raises(HTTPException) as caught:
            _start(pre_env, MENTION)
        assert caught.value.status_code == 409
        assert caught.value.detail["code"] == "pre_instruction_attachment_decode_failed"
        assert caught.value.detail["source_doc_id"] == ATTACHMENT["doc_id"]
        # T0014 §5: "AI 호출 시작 안 됨" — no subprocess ever ran, so no capture file exists.
        assert list(pre_env["tmp"].glob("captured_*.txt")) == []
        # The token this hop already minted must not be left behind unrevoked.
        assert pre_env["revoked"] == [
            ("tok_20260922_000001", "ai_invoke_pre_instruction_attachment_invalid")
        ]


class TestResolvePreInstruction:
    """Unit coverage for work_plan_attachment_service.resolve_pre_instruction."""

    def test_no_text_and_no_attachment_returns_none(self):
        assert wpa_svc.resolve_pre_instruction(
            {"pre_instruction_text": None, "pre_instruction_attachment_json": None}
        ) is None
        assert wpa_svc.resolve_pre_instruction(None) is None

    def test_text_only(self):
        got = wpa_svc.resolve_pre_instruction({
            "pre_instruction_text": "  지시문  ", "pre_instruction_attachment_json": None,
        })
        assert got == {"text": "지시문", "attachment": None}

    def test_valid_attachment_is_returned_verbatim(self, monkeypatch):
        monkeypatch.setattr(wpa_svc, "validate_reference", lambda doc_id, reference: None)
        item = {
            "pre_instruction_text": None,
            "pre_instruction_attachment_json": _attachment_json(),
            "source_doc_id": ATTACHMENT["doc_id"],
        }
        got = wpa_svc.resolve_pre_instruction(item)
        assert got == {"text": None, "attachment": ATTACHMENT}

    def test_invalid_attachment_raises_with_code_and_source_doc_id(self, monkeypatch):
        monkeypatch.setattr(
            wpa_svc, "validate_reference",
            lambda doc_id, reference: "pre_instruction_attachment_digest_mismatch",
        )
        item = {
            "pre_instruction_text": None,
            "pre_instruction_attachment_json": _attachment_json(),
            "source_doc_id": ATTACHMENT["doc_id"],
        }
        with pytest.raises(wpa_svc.PreInstructionAttachmentError) as caught:
            wpa_svc.resolve_pre_instruction(item)
        assert caught.value.code == "pre_instruction_attachment_digest_mismatch"
        assert caught.value.source_doc_id == ATTACHMENT["doc_id"]


class TestPrependPreInstructionSection:
    """Unit coverage for invoke_mention_service.prepend_pre_instruction_section."""

    def test_text_only(self):
        got = invoke_mention_service.prepend_pre_instruction_section(
            MENTION, text="지시문", attachment=None, locale="ko",
        )
        assert got == f"## WorkPlan 사전지시\n---\n지시문{invoke_mention_service.SECTION_SEPARATOR}{MENTION}"

    def test_attachment_only(self):
        got = invoke_mention_service.prepend_pre_instruction_section(
            MENTION, text=None, attachment=ATTACHMENT, locale="ko",
        )
        assert ATTACHMENT["original_filename"] in got
        assert ATTACHMENT["doc_id"] in got
        assert ATTACHMENT["filename"] in got
        assert got.endswith(MENTION)

    def test_neither_returns_mention_unchanged(self):
        assert invoke_mention_service.prepend_pre_instruction_section(
            MENTION, text=None, attachment=None, locale="ko",
        ) == MENTION
        assert invoke_mention_service.prepend_pre_instruction_section(
            MENTION, text="   ", attachment=None, locale="ko",
        ) == MENTION

    def test_never_merges_with_the_step_note_section(self):
        # T0014 §14: both sections survive, each under its own distinct header.
        with_note = invoke_mention_service.prepend_messages_section(MENTION, ["개별 멘트"], "ko")
        combined = invoke_mention_service.prepend_pre_instruction_section(
            with_note, text="지시문", attachment=None, locale="ko",
        )
        assert combined.count("## WorkPlan 사전지시") == 1
        assert combined.count("## 사용자 메세지") == 1
        assert "개별 멘트" in combined and "지시문" in combined


class TestReworkPreInstruction:
    """0554 T0014 §9/E3: rework re-applies the same slot's pre-instruction; the reviewer's
    own prompt (a completely separate assembly) never sees it."""

    def test_rework_mention_reapplies_the_slots_pre_instruction(self, monkeypatch):
        from modules.flow_gate.api import token_routes as _token_routes
        from modules.flow_gate.db import documents as db_documents
        from modules.flow_gate.db import workflow_sequences as db_wfseq

        item = {
            "source_doc_id": ATTACHMENT["doc_id"],
            "pre_instruction_text": "재작업 때도 이 지시를 다시 반영해라",
            "pre_instruction_attachment_json": None,
        }
        monkeypatch.setattr(db_wfseq, "get_item_by_result_doc_id", lambda doc_id: dict(item))
        monkeypatch.setattr(
            db_documents, "get_by_id",
            lambda doc_id: {"doc_id": doc_id, "rejection_history": []},
        )
        monkeypatch.setattr(_token_routes, "_build_mention_for_token", lambda **kw: MENTION)

        mention = invoke_mention_service.build_rework_mention(
            doc_id="flowgate.default.0554.9002-T", group_id=GROUP_ID,
            project_id="flowgate", scratch_dir="/scratch/x", raw_token="raw",
            api_base_url="http://x/api/v1", locale="ko",
        )
        assert mention.count("WorkPlan 사전지시") == 1
        assert "재작업 때도 이 지시를 다시 반영해라" in mention

    def test_no_pre_instruction_on_the_slot_leaves_rework_mention_untouched(self, monkeypatch):
        from modules.flow_gate.api import token_routes as _token_routes
        from modules.flow_gate.db import documents as db_documents
        from modules.flow_gate.db import workflow_sequences as db_wfseq

        monkeypatch.setattr(db_wfseq, "get_item_by_result_doc_id", lambda doc_id: None)
        monkeypatch.setattr(
            db_documents, "get_by_id",
            lambda doc_id: {"doc_id": doc_id, "rejection_history": []},
        )
        monkeypatch.setattr(_token_routes, "_build_mention_for_token", lambda **kw: MENTION)

        mention = invoke_mention_service.build_rework_mention(
            doc_id="flowgate.default.0554.9002-T", group_id=GROUP_ID,
            project_id="flowgate", scratch_dir="/scratch/x", raw_token="raw",
            api_base_url="http://x/api/v1", locale="ko",
        )
        assert mention == MENTION

    def test_issue_rework_request_revokes_the_token_when_attachment_is_invalid(self, monkeypatch):
        from modules.flow_gate.api import token_routes as _token_routes
        from modules.flow_gate.db import documents as db_documents
        from modules.flow_gate.db import workflow_sequences as db_wfseq
        from modules.flow_gate.services import token_service

        item = {
            "source_doc_id": ATTACHMENT["doc_id"],
            "pre_instruction_text": None,
            "pre_instruction_attachment_json": _attachment_json(),
        }
        monkeypatch.setattr(db_wfseq, "get_item_by_result_doc_id", lambda doc_id: dict(item))
        monkeypatch.setattr(db_documents, "get_by_id", lambda doc_id: {
            "doc_id": doc_id, "group_id": GROUP_ID, "project_id": "flowgate",
            "rejection_history": [],
        })
        monkeypatch.setattr(_token_routes, "_build_mention_for_token", lambda **kw: MENTION)
        monkeypatch.setattr(
            wpa_svc, "validate_reference",
            lambda doc_id, reference: "pre_instruction_attachment_digest_mismatch",
        )
        issued = {"token_id": "tok-rework-1", "raw_token": "raw", "scratch_dir": "/scratch/x"}
        monkeypatch.setattr(token_service, "issue", lambda **kw: issued)
        revoked = []
        monkeypatch.setattr(
            token_service, "revoke",
            lambda token_id, reason=None: revoked.append((token_id, reason)),
        )

        with pytest.raises(wpa_svc.PreInstructionAttachmentError):
            invoke_mention_service.issue_rework_request(
                doc_id="flowgate.default.0554.9003-TR", issued_to="usr_admin",
                api_base_url="http://x/api/v1", locale="ko",
            )
        assert revoked == [("tok-rework-1", "ai_invoke_pre_instruction_attachment_invalid")]

    def test_reviewer_prompt_assembly_is_a_separate_function_never_touched(self):
        # The reviewer's own prompt (build_review_mention / workflow_decision_service.request_review)
        # is a completely different assembly from build_rework_mention — asserting they remain
        # distinct functions is the structural guarantee behind "reviewer prompt에는 worker용
        # 사전지시를 넣지 않는다" (T0014 §9): there is no shared code path that could leak it.
        import inspect

        rework_src = inspect.getsource(invoke_mention_service.build_rework_mention)
        assert "resolve_pre_instruction" not in inspect.getsource(
            invoke_mention_service.build_reject_context
        )
        assert "_resolve_rework_pre_instruction" in rework_src


class TestRetryRebuildsPreInstruction:
    """0554 T0014 §10/E1/E2: a no-output retry and a provider-fallback retry both reissue
    through worker._prepare_retry_token, which calls the SAME admission._inject_hop_notes
    convergence point (worker.py:862) as the initial hop — so pre-instruction is not a
    separate thing retry code has to remember to carry, it is re-resolved fresh from the
    sequence row on every reissue, exactly like the initial hop (mirrors
    test_ai_invoke_no_output_retry_0359.py's TestRetryToken, which pins the same function for
    token reuse/reissue but never turns on `mode=continuous` and so never exercises this
    branch at all)."""

    def _run(self, tmp_path, **over):
        run = {
            "run_id": "aiv_retry_x", "doc_ref": ROOT_DOC, "token_id": "tok_old",
            "mention": "## prompt\n", "raw_token": "raw_old",
            "token_scratch_dir": str(tmp_path / "old"),
            "mode": "continuous", "action_scope": "new",
            "continuation_default_note": None, "continuation_note_overrides": None,
            "continuation_instruction_mode": "auto_approved",
            "continuation_auto_approve_item_seqs": None, "continuation_locale": "ko",
        }
        run.update(over)
        return run

    def _force_reissue(self, monkeypatch):
        monkeypatch.setattr(svc.db_tokens, "get_by_id", lambda tid: {
            "token_id": tid, "consumed_at": None, "revoked_at": None,
            "expires_at": "2000-01-01T00:00:00+00:00",
        })

    def test_reissued_retry_reads_the_auto_approved_source_authoring_context(
        self, pre_env, monkeypatch, tmp_path,
    ):
        pre_env["wfseq"].head_item_seq = 3
        pre_env["wfseq"].items = [
            {
                "item_seq": 3, "type": "T", "result_doc_id": None,
                "source_doc_id": ATTACHMENT["doc_id"],
                "pre_instruction_text": PRE_TEXT,
                "pre_instruction_attachment_json": None,
            },
            {
                "item_seq": 4, "type": "TR", "result_doc_id": None,
                "source_doc_id": ATTACHMENT["doc_id"],
                "pre_instruction_text": "paired-row stale sentinel",
                "pre_instruction_attachment_json": None,
            },
        ]
        self._force_reissue(monkeypatch)

        def _issue(ai_run_id=None):
            return {
                "raw_token": "raw_new", "token_id": "tok_new", "mention": MENTION,
                "scratch_dir": str(tmp_path / "new"),
            }

        run = self._run(tmp_path, issue_builder=_issue)
        prepared = svc._prepare_retry_token(run)
        assert prepared["reissued"] is True
        got = prepared["mention"]
        assert got.count("WorkPlan 사전지시") == 1
        assert PRE_TEXT in got
        assert "source-row stale sentinel" not in got

    def test_each_reissue_re_reads_the_row_instead_of_replaying_a_stale_copy(
        self, pre_env, monkeypatch, tmp_path,
    ):
        self._force_reissue(monkeypatch)
        calls = {"n": 0}

        def _issue(ai_run_id=None):
            calls["n"] += 1
            return {
                "raw_token": f"raw_{calls['n']}", "token_id": f"tok_{calls['n']}",
                "mention": MENTION, "scratch_dir": str(tmp_path / f"s{calls['n']}"),
            }

        run = self._run(tmp_path, issue_builder=_issue)
        first = svc._prepare_retry_token(run)
        assert PRE_TEXT in first["mention"]

        pre_env["wfseq"].items[0]["pre_instruction_text"] = "바뀐 사전지시"
        second = svc._prepare_retry_token(run)
        assert "바뀐 사전지시" in second["mention"]
        assert PRE_TEXT not in second["mention"]

    def test_invalid_attachment_mid_retry_propagates_instead_of_silently_dropping(
        self, pre_env, monkeypatch, tmp_path,
    ):
        # T0014 §5 fail-closed applies just as much to a retry reissue as to the first
        # attempt: worker.py:862's call to _inject_hop_notes is NOT wrapped in a try/except,
        # so PreInstructionAttachmentError propagates out of _prepare_retry_token itself
        # (the outer _worker try/except then ends the run as worker_error rather than
        # silently spawning the next attempt without the pre-instruction).
        pre_env["wfseq"].items[0]["pre_instruction_attachment_json"] = _attachment_json()
        monkeypatch.setattr(
            wpa_svc, "validate_reference",
            lambda doc_id, reference: "pre_instruction_attachment_digest_mismatch",
        )
        self._force_reissue(monkeypatch)

        def _issue(ai_run_id=None):
            return {
                "raw_token": "raw_new", "token_id": "tok_new", "mention": MENTION,
                "scratch_dir": str(tmp_path / "new"),
            }

        run = self._run(tmp_path, issue_builder=_issue)
        with pytest.raises(wpa_svc.PreInstructionAttachmentError):
            svc._prepare_retry_token(run)

    def test_orphaned_new_token_is_revoked_when_inject_hop_notes_fails_mid_retry(
        self, pre_env, monkeypatch, tmp_path,
    ):
        # 0554 T0014 §5 rework (review rej_01M334Z5Y72GK6BW finding 2, first half): the
        # reissue above already minted a BRAND NEW token via issue_builder before
        # _inject_hop_notes ever runs, and run["token_id"]/run["raw_token"] are not updated to
        # it until AFTER _inject_hop_notes succeeds (worker.py, below the injection call).
        # Before the fix the exception propagated (proven above) but the new token was never
        # recorded on the run AND never revoked — an orphan the finalizer could not see because
        # it only knows the run's OLD token_id. This asserts the cleanup half directly.
        pre_env["wfseq"].items[0]["pre_instruction_attachment_json"] = _attachment_json()
        monkeypatch.setattr(
            wpa_svc, "validate_reference",
            lambda doc_id, reference: "pre_instruction_attachment_digest_mismatch",
        )
        self._force_reissue(monkeypatch)

        def _issue(ai_run_id=None):
            return {
                "raw_token": "raw_new", "token_id": "tok_new", "mention": MENTION,
                "scratch_dir": str(tmp_path / "new"),
            }

        run = self._run(tmp_path, issue_builder=_issue)
        with pytest.raises(wpa_svc.PreInstructionAttachmentError):
            svc._prepare_retry_token(run)
        assert ("tok_new", "ai_invoke_pre_instruction_attachment_invalid") in pre_env["revoked"]
        # The run object itself was never updated to point at the orphan — confirms the token
        # id this test just asserted revoked really is the NEW one, not the run's old one.
        assert run["token_id"] == "tok_old"

    def test_rework_issue_builder_error_propagates_with_its_own_code_intact(
        self, pre_env, monkeypatch, tmp_path,
    ):
        # 0554 T0014 §5 rework (review finding 2, second half): a rework issue_builder
        # (invoke_mention_service.issue_rework_request) that raises PreInstructionAttachmentError
        # directly from _call_issue_builder used to be caught by the broad
        # `except Exception: ... return None` around that call, folding a specific,
        # already-revoked-its-own-token failure into a generic "reissue failed" log + None —
        # which the document-review-loop transition then reported as a bare
        # document_review_loop_transition_failed, losing the actual code entirely. It must now
        # propagate with its code intact, exactly like the mid-injection case above.
        self._force_reissue(monkeypatch)

        def _rework_issue_builder(ai_run_id=None):
            raise wpa_svc.PreInstructionAttachmentError(
                "pre_instruction_attachment_digest_mismatch", ATTACHMENT["doc_id"],
            )

        run = self._run(tmp_path, issue_builder=_rework_issue_builder)
        with pytest.raises(wpa_svc.PreInstructionAttachmentError) as caught:
            svc._prepare_retry_token(run)
        assert caught.value.code == "pre_instruction_attachment_digest_mismatch"
        assert caught.value.source_doc_id == ATTACHMENT["doc_id"]


class TestRunToEndTailExpansionAppliesOwnBaseline:
    """0554 T0014 §12/E7 rework (rej_01M33DPHG59E1H56 finding 3): the pre-fix version of
    this test pre-seeded item_seq=2 onto FakeWfseq and bumped head_item_seq BEFORE calling
    _start() — a basic item_seq lookup test with no hop ever in flight while the tail item
    appeared, and no real rebase call. This drives the REAL mid-run path instead:
    admission.start_run(continuation_to_end=True) begins while item 2 does not exist yet,
    a tail item is appended while hop 1 is still the group's live run (the
    _slow_capture_cmd window, exactly like TestPauseResumeThroughRealApis's pause/resume
    tests), and chain.rebase_active_to_end — the same eager refresh the real WP-approval
    hook (pipeline_service.py) calls after work_plan_sequence_service.expand_final_work_
    plan lands a tail item — is what actually moves this run's own continuation_target_seq
    onto the newly-appended row, not a hand-set FakeWfseq attribute."""

    def test_mid_run_tail_append_is_picked_up_by_a_real_rebase_call(self, pre_env):
        from modules.flow_gate.services.ai_invoke import chain as chain_module

        # item_seq=1 keeps the fixture's default shape (no pre-instruction of its own) —
        # this run-to-end chain's first hop must not read item 2's baseline for it.
        pre_env["wfseq"].items[0]["pre_instruction_text"] = None
        slow_cmd, outfile1 = _slow_capture_cmd(pre_env["tmp"], seconds=1.0)
        res, _ = _start(pre_env, MENTION, cmd=slow_cmd, outfile=outfile1, to_end=True)
        run = svc.get_run_record(res["run_id"])
        assert run["target_to_end"] is True
        assert run["continuation_target_seq"] is None   # unresolved until the hop boundary

        # Mid-hop, real WP-apply tail expansion: item 2 did not exist when this run's chain
        # was assembled at admission time above.
        pre_env["wfseq"].items.append({
            "item_seq": 2, "type": "D", "result_doc_id": None,
            "source_doc_id": ATTACHMENT["doc_id"], "pre_instruction_text": "새로 추가된 지시",
            "pre_instruction_attachment_json": None,
        })
        resolved = chain_module.rebase_active_to_end(GROUP_ID, ROOT_DOC)
        assert resolved == 2
        assert svc.get_run_record(res["run_id"])["continuation_target_seq"] == 2

        _wait_finished(res["run_id"])
        got1 = _read(outfile1).decode("utf-8")
        # Hop 1 itself already read item 1's (empty) baseline before the tail existed.
        assert "새로 추가된 지시" not in got1

        # Hop 1 completing is what really advances the effective head in production (its
        # result document lands and is folded/approved) — FakeWfseq's get_effective_head has
        # no completion logic of its own to derive this from, so this is the one place that
        # fact is asserted directly, mirroring every sibling FakeWfseq-based test in this file.
        pre_env["wfseq"].head_item_seq = 2

        # The next hop this run-to-end chain issues (mirroring what its own self-chain does
        # once it reaches the rebased target) reads item 2's OWN freshly-appended baseline —
        # not a default, and not item 1's.
        res2, outfile2 = _start(pre_env, MENTION, target_seq=2, to_end=True)
        _wait_finished(res2["run_id"])
        got2 = _read(outfile2).decode("utf-8")
        assert got2.count("WorkPlan 사전지시") == 1
        assert "새로 추가된 지시" in got2

    def test_explicit_target_run_is_not_rebased_past_its_own_target_by_a_tail_append(
        self, pre_env,
    ):
        from modules.flow_gate.services.ai_invoke import chain as chain_module

        slow_cmd, outfile = _slow_capture_cmd(pre_env["tmp"], seconds=1.0)
        res, _ = _start(pre_env, MENTION, cmd=slow_cmd, outfile=outfile, target_seq=1)
        run = svc.get_run_record(res["run_id"])
        assert run["target_to_end"] is False
        assert run["continuation_target_seq"] == 1

        pre_env["wfseq"].items.append({
            "item_seq": 2, "type": "D", "result_doc_id": None,
            "source_doc_id": ATTACHMENT["doc_id"], "pre_instruction_text": "새로 추가된 지시",
            "pre_instruction_attachment_json": None,
        })
        # T0014 §12: "explicit target run은 기존 target을 넘어 새 item을 실행하지 않는다" —
        # the eager rebase itself is a no-op for a run that never asked for run-to-end.
        assert chain_module.rebase_active_to_end(GROUP_ID, ROOT_DOC) is None
        assert svc.get_run_record(res["run_id"])["continuation_target_seq"] == 1

        _wait_finished(res["run_id"])
        got = _read(outfile).decode("utf-8")
        assert "새로 추가된 지시" not in got


class TestPauseResumeReReadsTheSameBaseline:
    """0554 T0014 §11/E4: pre-instruction has no runtime-override map of its own (unlike
    provider/note/review) — its only storage is the sequence row (D0007 §3.5), so a
    pause/resume round trip producing the same effective pre-instruction is not a separate
    code path to wire, it falls out of _inject_hop_notes reading the row fresh on every call
    (exactly like TestRunToEndTailExpansionAppliesOwnBaseline / TestRetryRebuildsPreInstruction
    above). This isolates JUST that resolution fact (two start_run calls against the same
    target_seq never diverge) without the pause/resume machinery itself — TestPauseResume
    ThroughRealApis below is the companion that drives the actual svc.pause_run/resume_chain
    functions and the real ai_invoke_paused_chains carrier (review rej_01M334Z5Y72GK6BW
    finding 3: the pre-fix version of this file only had the two-independent-calls check,
    which the review correctly read as not exercising pause/resume at all)."""

    def test_pre_pause_and_post_resume_hops_produce_the_same_pre_instruction(self, pre_env):
        before, outfile_before = _start(pre_env, MENTION, target_seq=1)
        _wait_finished(before["run_id"])
        before_text = _read(outfile_before).decode("utf-8")

        after, outfile_after = _start(pre_env, MENTION, target_seq=1)
        _wait_finished(after["run_id"])
        after_text = _read(outfile_after).decode("utf-8")

        assert before_text == after_text
        assert before_text.count("WorkPlan 사전지시") == 1
        assert PRE_TEXT in before_text


@pytest.fixture
def paused_env(pre_env, monkeypatch):
    """Module-level so both TestPauseResumeThroughRealApis and
    TestConnectedFlowFullEffectiveBundle can use the SAME real pause/resume harness without
    the latter inheriting (and re-running) the former's test methods."""
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused

    class FakePausedStore:
        """Same dict-backed contract as test_ai_invoke_pause_resume_0252.py's own
        FakePausedStore — a real CAS release_owned, not a bare group-keyed delete, so
        resume_chain's ownership-race protection is exercised for real."""

        def __init__(self):
            self.rows: dict[str, dict] = {}

        def upsert(self, *, group_id, **fields):
            self.rows[group_id] = {"group_id": group_id, **fields}

        def get_by_group(self, group_id):
            row = self.rows.get(group_id)
            return dict(row) if row else None

        def exists(self, group_id):
            return group_id in self.rows

        def delete_and_return(self, group_id):
            return self.rows.pop(group_id, None)

        def release_owned(self, group_id, *, paused_by, paused_at, stop_kind, stop_run_id):
            row = self.rows.get(group_id)
            if row is None:
                return None
            normalized = stop_kind or "user"
            if (
                row.get("paused_by") != paused_by
                or row.get("paused_at") != paused_at
                or (row.get("stop_kind") or "user") != normalized
                or row.get("stop_run_id") != stop_run_id
            ):
                return None
            return self.rows.pop(group_id)

        def delete_by_group(self, group_id):
            self.rows.pop(group_id, None)

        def delete_system_stop(self, group_id, stop_run_id):
            row = self.rows.get(group_id)
            if row and row.get("stop_kind") == "system" and row.get("stop_run_id") == stop_run_id:
                self.rows.pop(group_id, None)

        def list_by_user(self, user_id):
            return [dict(r) for r in self.rows.values() if r.get("paused_by") == user_id]

    paused = FakePausedStore()
    for name in ("upsert", "get_by_group", "exists", "delete_and_return", "release_owned",
                 "delete_by_group", "delete_system_stop", "list_by_user"):
        monkeypatch.setattr(db_paused, name, getattr(paused, name))
    pre_env["paused"] = paused
    return pre_env


class TestPauseResumeThroughRealApis:
    """0554 T0014 §11/E4 rework (review rej_01M334Z5Y72GK6BW finding 3): drives the SAME real
    functions test_ai_invoke_pause_resume_0252.py already pins — svc.pause_run,
    svc.mark_user_paused (the inbox boundary-hook simulation that class also uses),
    svc.resume_chain, and a dict-backed ai_invoke_paused_chains fake honouring the exact
    upsert/release_owned CAS contract db_paused_chains itself implements — instead of two
    independent start_run calls. resume_chain's own "new" path (chain.py, no pending review
    slot) relaunches through admission.start_run with issue_builder=_issue_resume, and that
    start_run call reaches the SAME _inject_hop_notes convergence point every other hop in
    this file does — so this proves T0014 §11 ("resume 후 현재 item_seq 기준으로 sequence
    row를 다시 읽어 worker context를 조립한다") through the real pause/resume code path, not
    just the fact that _inject_hop_notes is deterministic."""

    def test_full_bundle_survives_a_real_pause_resume_round_trip(self, paused_env, monkeypatch):
        from modules.flow_gate.services import workflow_decision_service as wds

        env = paused_env
        # The target item carries note + text + attachment together — E4's full effective
        # bundle, not text alone.
        env["wfseq"].items[0]["pre_instruction_attachment_json"] = _attachment_json()
        env["wfseq"].items[0]["note"] = "이 단계 개별 멘트"
        monkeypatch.setattr(wpa_svc, "validate_reference", lambda doc_id, reference: None)

        # Hop 1: a slow worker leaves a window for pause_run/mark_user_paused while it is
        # still the group's live run (mirrors test_ai_invoke_pause_resume_0252.py's
        # test_boundary_stop_classifies_user_paused_and_keeps_row).
        slow_cmd, outfile_before = _slow_capture_cmd(env["tmp"], seconds=1.0)
        before, _ = _start(env, MENTION, target_seq=1, cmd=slow_cmd, outfile=outfile_before)

        out = svc.pause_run(before["run_id"], "usr_admin")
        assert out["status"] == "pause_requested"
        assert svc.mark_user_paused(GROUP_ID, before["run_id"]) is True
        run = _wait_finished(before["run_id"])
        assert run["end_reason"] == "user_paused"
        # The real carrier really holds the row — not a stand-in this test invented.
        assert GROUP_ID in env["paused"].rows
        paused_row = env["paused"].rows[GROUP_ID]
        assert paused_row["doc_ref"] == ROOT_DOC
        assert paused_row["stop_kind"] == "user"

        before_text = _read(outfile_before).decode("utf-8")
        assert before_text.count("## WorkPlan 사전지시") == 1
        assert PRE_TEXT in before_text
        assert "이 단계 개별 멘트" in before_text
        assert ATTACHMENT["original_filename"] in before_text

        # resume_chain's plain "new" path issues through workflow_decision_service.advance_
        # workflow (numbering/head-in_progress/token bookkeeping this file's fakes do not
        # model) and hands the resulting mention to admission.start_run(issue_builder=...) —
        # the SAME start_run this file's _start() helper calls, which is what actually
        # injects the pre-instruction. Canning advance_workflow's own return value (like
        # 0252's own resume tests already do via _patch_advance) isolates that convergence
        # point without needing the whole numbering subsystem.
        cmd2, outfile_after = _capture_cmd(env["tmp"])
        monkeypatch.setattr(wds, "advance_workflow", lambda **kw: {
            "token": "tok_raw_resume", "token_id": "tok_resume_1",
            "expires_at": "2026-09-23T00:00:00+00:00",
            "scratch_dir": str(env["tmp"] / "resumework"),
            "mention": MENTION,
        })
        env["chain"]["providers"] = [_provider(cmd=cmd2)]

        after = svc.resume_chain(
            group_id=GROUP_ID, user_id="usr_admin",
            api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        )
        # The row was really CONSUMED through release_owned's CAS, not copied.
        assert GROUP_ID not in env["paused"].rows
        _wait_finished(after["run_id"])
        after_text = _read(outfile_after).decode("utf-8")

        assert after_text.count("## WorkPlan 사전지시") == 1
        # Byte-identical effective bundle across the real pause -> resume boundary: same
        # text, same note, same attachment advertisement — E4 in full, not text alone.
        assert after_text == before_text

    def test_resume_into_a_pending_rework_stage_reapplies_the_slots_pre_instruction(
        self, paused_env, monkeypatch,
    ):
        # 0554 T0014 §9/§11 rework (review finding 3, "rework 직전 resume"): a chain paused
        # while a document sat mid review-loop resumes not through the plain "new" path above
        # but through resolve_review_gate -> _spawn_rework_hop -> issue_rework_request ->
        # build_rework_mention -> _resolve_rework_pre_instruction — a structurally different
        # code path (mode="single", action_scope="edit") that never touches
        # admission._inject_hop_notes at all (T0014 §9's own separation of concerns). This
        # proves the slot's pre-instruction still reaches the real spawned worker through
        # THAT path when resume_chain has to re-derive the gate cold.
        from modules.flow_gate.services.ai_invoke import review as review_module
        from modules.flow_gate.db import documents as db_documents
        from modules.flow_gate.api import token_routes as _token_routes

        env = paused_env
        rework_doc = f"{GROUP_ID}.9002-T"
        env["wfseq"].sequence = {"id": 1}
        env["wfseq"].items = [
            {
                "item_seq": 3, "type": "T", "result_doc_id": rework_doc,
                "source_doc_id": ATTACHMENT["doc_id"],
                "pre_instruction_text": "재작업에도 다시 적용될 사전지시",
                "pre_instruction_attachment_json": None,
                "review_count": 1,
            },
            # A genuinely pending slot after the rework target — resume's own preflight
            # (_paused_row_resume_state) counts PENDING worker items only, so a paused row
            # whose whole target is already "realized" (item_seq 3 has a result_doc_id, just
            # not yet approved) reads as no_pending_worker_steps without this. This is the
            # real production invariant, not a fixture-only workaround.
            {
                "item_seq": 4, "type": "D", "result_doc_id": None,
                "source_doc_id": None, "pre_instruction_text": None,
                "pre_instruction_attachment_json": None,
            },
        ]

        def _get_by_id(doc_id):
            if doc_id == rework_doc:
                return {
                    "doc_id": rework_doc, "branch": "main", "group_id": GROUP_ID,
                    "project_id": "flowgate", "type_code": "T",
                    "doc_review_status": "pending_review", "revision_no": 0,
                    "rejection_history": [],
                }
            return {"doc_id": doc_id, "branch": "main"}

        monkeypatch.setattr(svc.db_docs, "get_by_id", _get_by_id)
        monkeypatch.setattr(db_documents, "get_by_id", _get_by_id)
        monkeypatch.setattr(
            review_module.db_reviews, "list_by_doc",
            lambda doc_id: (
                [{"verdict": "issues", "revision_no": 0}] if doc_id == rework_doc else []
            ),
        )
        monkeypatch.setattr(_token_routes, "_build_mention_for_token", lambda **kw: MENTION)

        # A chain paused at the target this document occupies — resume_chain must discover
        # the pending REWORK stage on its own via resolve_review_gate, not be told about it.
        env["paused"].upsert(
            group_id=GROUP_ID, doc_ref=ROOT_DOC, paused_by="usr_admin",
            paused_at="2026-09-22T00:00:00+09:00",
            continuation_target_seq=4, docs_target=1, docs_reached=0,
            chain_id="aiv_chain_rework", chain_docs_target=1, chain_docs_reached=0,
            stop_kind="user", stop_code=None, stop_run_id=None,
            continuation_base_provider_id=None, continuation_provider_pinned=False,
            continuation_provider_overrides=None,
            continuation_default_note=None, continuation_note_overrides=None,
            continuation_instruction_mode="auto_approved",
            continuation_auto_approve_item_seqs=None,
            continuation_step_timeout_sec=None, continuation_restart_max_attempts=None,
            continuation_review_count_overrides=None, continuation_reviewer_overrides=None,
        )

        cmd, outfile = _capture_cmd(env["tmp"])
        env["chain"]["providers"] = [_provider(cmd=cmd)]

        res = svc.resume_chain(
            group_id=GROUP_ID, user_id="usr_admin",
            api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        )
        # The row this call read was consumed synchronously, before the spawn (chain.py's
        # release_owned CAS runs ahead of _spawn_rework_hop) -- what may exist under this
        # group_id an instant later is a SYSTEM row the finished hop's own post-process
        # parked, an unrelated race this assertion must not fight with _wait_finished below.
        _wait_finished(res["run_id"])
        got = _read(outfile).decode("utf-8")
        assert got.count("## WorkPlan 사전지시") == 1
        assert "재작업에도 다시 적용될 사전지시" in got


class _DocWorld:
    """A minimal real doc store for exactly the ONE result document this scenario reviews —
    the same shape test_ai_invoke_review_gate_0414.py's own `World.update_doc` uses, so
    `pipeline_service.transition_document_review`'s real `db_docs.get_by_id`/`db_docs.update`
    calls (inside `_auto_reject`) land on a row this test can read back, instead of the
    review/reject transition being mocked away."""

    def __init__(self, doc_id, **fields):
        self.doc_id = doc_id
        self.doc = {"doc_id": doc_id, "branch": "main", **fields}

    def get_by_id(self, doc_id):
        if doc_id == self.doc_id:
            return dict(self.doc)
        return {"doc_id": doc_id, "branch": "main"}

    def update(self, doc_id, fields):
        if doc_id != self.doc_id:
            return None
        self.doc.update(fields)
        return dict(self.doc)


class TestConnectedFlowFullEffectiveBundle:
    """T0014 §15 rework (rej_01M33DPHG59E1H56 findings 1-2): the rev2 version of this test
    called work_plan_apply_service.project() (a pure computation) and hand-copied its return
    value into FakeWfseq, called the file's own _start() helper directly, and jumped straight
    from a hand-set "issues" db_reviews row to resume_chain's COLD rework dispatch — so
    apply()'s real durable write, and _spawn_review_hop/a real reviewer subprocess, never ran
    at all. This version ties together, in one scenario, the real functions that make up
    T0014's own end-to-end diagram (§ "목적"):

      1. WP step -> sequence row through the REAL work_plan_apply_service.apply() (not
         project() alone) — apply() re-derives the sequence, enforces the workflow_tag/
         wp_revision_no CAS, and is the SAME function the WP apply route calls; only the
         db_wfseq module functions it calls underneath are the fixture's fakes.
      2. dialog/start payload -> a REAL start_run hop, byte-exact captured prompt, paused
         mid-hop (worker 실행 전 pause/resume) and resumed through the REAL svc.pause_run/
         mark_user_paused/resume_chain — E4.
      3. the resumed hop's result document is real (backed by _DocWorld) and review_count/
         reviewer come from the row apply() actually wrote — a REAL review._pending_review_
         slot lookup, not a hand-built dict.
      4. review 전후 pause/resume: the FIRST review round is dispatched through the REAL
         review.run_review_gate -> review._spawn_review_hop, spawning REVIEWER_PROVIDER's own
         real subprocess (R4) — the exact call the rev2 version never made — paused mid-hop
         and resumed, which (because the slot's result document already exists) resumes
         through the SAME real gate dispatch, not a hand-restored paused-row shortcut.
      5. reject -> rework: inserting the round's "issues" verdict and calling run_review_gate
         AGAIN drives the REAL review._auto_reject (real pipeline_service.transition_
         document_review, real rejection_history) and then REAL review._spawn_rework_hop,
         with the reviewer's OWN provider still live in the chain but never invoked (R6).
      6. the finite review_count=2 baseline apply() wrote is confirmed to still demand a
         second review round after the rework lands (R2); the final pass then runs the REAL
         settlement, approves the canonical T, hands off to TR, and executes that worker.
    """

    def test_apply_start_pause_resume_review_reject_rework_pass_settle_and_handoff(
        self, paused_env, monkeypatch,
    ):
        from modules.flow_gate.services import work_plan_apply_service as apply_svc
        from modules.flow_gate.services.ai_invoke import review as review_module
        from modules.flow_gate.services import workflow_decision_service as wds
        from modules.flow_gate.db import documents as db_documents
        from modules.flow_gate.db import users as db_users
        from modules.flow_gate.workflow import pipeline_service
        from modules.flow_gate.workflow.routers import workflow as workflow_router
        from modules.flow_gate.api import token_routes as _token_routes

        env = paused_env
        WORKER_PROVIDER = "aip_worker_x"
        REVIEWER_PROVIDER = "aip_reviewer_x"
        WP_DOC_ID = f"{GROUP_ID}.9000-WP"
        RESULT_DOC_ID = f"{GROUP_ID}.9001-T"

        # ---- 1. WP T step -> source T authoring row through the REAL apply() (steps 1-10).
        # The canonical pre-instruction remains on T#1, the row auto_approved now executes;
        # the later TR row must not receive an authoring-context snapshot.
        plan_step = {
            "key": "T#1", "type": "T", "ordinal": 1, "locked": False,
            "provider_id": WORKER_PROVIDER, "note": "계획 단계 개별 메모",
            "review_count": 2, "reviewer_provider_id": REVIEWER_PROVIDER,
            "pre_instruction_text": PRE_TEXT, "pre_instruction_attachment": ATTACHMENT,
        }
        result_step = {
            "key": "TR#1", "type": "TR", "ordinal": 1, "locked": False,
            "provider_id": None, "note": "",
            "review_count": 0, "reviewer_provider_id": None,
            "pre_instruction_text": None, "pre_instruction_attachment": None,
        }
        provider_registry = [
            {"id": WORKER_PROVIDER, "name": "Worker CLI", "enabled": True},
            {"id": REVIEWER_PROVIDER, "name": "Reviewer CLI", "enabled": True},
        ]
        monkeypatch.setattr(wpa_svc, "validate_reference", lambda doc_id, reference: None)
        # pre_env's FakeWfseq starts with one default item (every sibling test in this file
        # relies on it) — this scenario is about a workflow apply() decides from scratch, so
        # start it from a genuinely empty sequence like a group with no workflow yet.
        env["wfseq"].sequence = None
        env["wfseq"].items = []

        from contextlib import nullcontext

        class _NullTransactionStore:
            def transaction(self):
                return nullcontext()

        monkeypatch.setattr(apply_svc, "get_store", lambda: _NullTransactionStore())
        before_tag = apply_svc.build_workflow_tag(None, [])
        result = apply_svc.apply(
            doc={
                "doc_id": WP_DOC_ID, "revision_no": 1, "doc_review_status": "approved",
                "target_id": ROOT_DOC,
            },
            owner_doc={"doc_id": ROOT_DOC, "type_code": "R"},
            plan={"steps": [plan_step, result_step], "defaults": {"note": ""}},
            plan_path=env["tmp"] / "wp_plan.json",
            providers=provider_registry,
            instruction_mode="auto_approved",
            change_workflow=True,
            workflow_tag=before_tag,
            wp_revision_no=1,
            applied_by="usr_admin",
        )
        assert result["ok"] is True
        assert result["workflow_changed"] is True
        assert result["fill"]["provider_overrides"]["1"] == WORKER_PROVIDER
        assert result["fill"]["review_count_overrides"]["1"] == 2
        assert result["fill"]["reviewer_overrides"]["1"] == REVIEWER_PROVIDER
        assert result["fill"]["pre_instruction_texts"]["1"] == PRE_TEXT
        assert result["fill"]["pre_instruction_attachments"]["1"] == ATTACHMENT
        assert not any(
            entry.get("reason") == "instruction_step_is_server_assembled_no_worker_target"
            for entry in result["fill"]["unfilled"]
        )

        # The row this asserts against was written by apply() itself — read back through the
        # SAME db_wfseq.get_sequence_items() every other real function in this test uses,
        # never hand-authored.
        source_row = next(
            i for i in env["wfseq"].get_sequence_items(1) if i["item_seq"] == 1
        )
        row = next(i for i in env["wfseq"].get_sequence_items(1) if i["item_seq"] == 2)
        assert source_row["type"] == "T"
        assert source_row["provider_id"] == WORKER_PROVIDER
        assert source_row["note"] == "계획 단계 개별 메모"
        assert source_row["review_count"] == 2
        assert source_row["reviewer_provider_id"] == REVIEWER_PROVIDER
        assert source_row["pre_instruction_text"] == PRE_TEXT
        assert source_row["source_doc_id"] == WP_DOC_ID
        assert row["type"] == "TR"
        assert row["pre_instruction_text"] is None

        # ---- 2. dialog/start payload -> a real start_run hop (steps 11-13), paused mid-hop
        # (worker 실행 전 pause/resume) and resumed through the real pause/resume APIs (E4).
        worker_cmd, worker_outfile = _slow_capture_cmd(env["tmp"], seconds=1.0)
        reviewer_round1_outfile = env["tmp"] / "reviewer_round1.txt"
        reviewer_reject_check_outfile = env["tmp"] / "reviewer_during_reject.txt"

        def _reviewer_cmd(outfile: Path) -> str:
            return (
                f'"{PY}" -c "import pathlib; '
                f'pathlib.Path(\'{outfile.as_posix()}\').write_text(\'called\')"'
            )

        env["chain"]["providers"] = [
            _provider(pid=WORKER_PROVIDER, cmd=worker_cmd),
            _provider(pid=REVIEWER_PROVIDER, cmd=_reviewer_cmd(reviewer_round1_outfile)),
        ]
        env["chain"]["registered_count"] = 2
        before, _ = _start(env, MENTION, target_seq=2, cmd=worker_cmd, outfile=worker_outfile)

        out = svc.pause_run(before["run_id"], "usr_admin")
        assert out["status"] == "pause_requested"
        assert svc.mark_user_paused(GROUP_ID, before["run_id"]) is True
        run = _wait_finished(before["run_id"])
        assert run["end_reason"] == "user_paused"
        assert GROUP_ID in env["paused"].rows
        before_text = _read(worker_outfile).decode("utf-8")
        assert before_text.count("## WorkPlan 사전지시") == 1
        assert PRE_TEXT in before_text
        assert "계획 단계 개별 메모" in before_text
        assert ATTACHMENT["original_filename"] in before_text

        cmd2, outfile_after = _capture_cmd(env["tmp"])
        monkeypatch.setattr(wds, "advance_workflow", lambda **kw: {
            "token": "tok_raw_resume", "token_id": "tok_resume_1",
            "expires_at": "2026-09-23T00:00:00+00:00",
            "scratch_dir": str(env["tmp"] / "resumework"),
            "mention": MENTION,
        })
        env["chain"]["providers"] = [
            _provider(pid=WORKER_PROVIDER, cmd=cmd2),
            _provider(pid=REVIEWER_PROVIDER, cmd=_reviewer_cmd(reviewer_round1_outfile)),
        ]
        env["chain"]["registered_count"] = 2

        after = svc.resume_chain(
            group_id=GROUP_ID, user_id="usr_admin",
            api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        )
        assert GROUP_ID not in env["paused"].rows
        _wait_finished(after["run_id"])
        after_text = _read(outfile_after).decode("utf-8")
        # E4 in full: same text, same note, same attachment advertisement, survived a real
        # pause -> resume boundary.
        assert after_text == before_text

        # ---- 3. The resumed hop produced a real result document — wire it into a real
        # (mutable) doc world so review._pending_review_slot, resolve_review_count and
        # resolve_reviewer all read the row apply() actually wrote (step 1), and the reject
        # transition below is the real pipeline_service.transition_document_review, not a
        # hand-set doc_review_status.
        env["wfseq"].items[0]["result_doc_id"] = RESULT_DOC_ID
        canonical_path = env["tmp"] / "canonical-t.md"
        canonical_path.write_text(
            "# 실제 작업지시\n\n작성 worker가 만든 canonical T 본문입니다.\n",
            encoding="utf-8",
        )
        docs = _DocWorld(
            RESULT_DOC_ID, group_id=GROUP_ID, project_id="flowgate", type_code="T",
            doc_review_status="pending_review", revision_no=0, rejection_history=None,
            file_path=str(canonical_path),
        )
        monkeypatch.setattr(
            pipeline_service.storage_paths, "resolve_storage_path",
            lambda *_args, **_kwargs: canonical_path,
        )
        monkeypatch.setattr(svc.db_docs, "get_by_id", docs.get_by_id)
        monkeypatch.setattr(db_documents, "get_by_id", docs.get_by_id)
        monkeypatch.setattr(svc.db_docs, "update", docs.update)
        monkeypatch.setattr(db_documents, "update", docs.update)
        monkeypatch.setattr(db_users, "get_by_id", lambda uid: {"user_id": uid, "is_admin": 1})
        monkeypatch.setattr(
            workflow_router, "_get_user_permissions",
            lambda actor: {"document.reject", "document.update", "document.approve"},
        )
        monkeypatch.setattr(pipeline_service, "log_state_changed", lambda **kw: None)
        review_rows: list = []
        monkeypatch.setattr(
            review_module.db_reviews, "list_by_doc",
            lambda doc_id: [dict(r) for r in review_rows] if doc_id == RESULT_DOC_ID else [],
        )

        def _gate_bundle(**overrides):
            base = {
                "doc_ref": ROOT_DOC, "target_seq": 2, "issued_to": "usr_admin",
                "api_base_url": "http://127.0.0.1:1/flowgate/api/v1", "locale": "ko",
                "instruction_mode": "auto_approved", "chain_id": None,
            }
            base.update(overrides)
            return base

        # ---- 4. real reviewer dispatch (finding 1's core gap): with no review rows yet, the
        # REAL gate resolves stage=review and run_review_gate spawns the REAL review._spawn_
        # review_hop — REVIEWER_PROVIDER's own subprocess actually runs, which the rev2
        # version of this test never made happen at all ("_spawn_review_hop 또는 reviewer
        # subprocess는 한 번도 실행되지 않습니다"). This hop is mode="single" — chain.py's own
        # pause_run rejects single-mode runs outright ("Single-mode runs cannot be paused. Use
        # cancel instead."), so review/rework hops are never a pause/resume target in
        # production either; T0014 §11's "review 전후 pause/resume" is the CONTINUOUS run
        # pausing before its own hop's completion would let the chain reach review (step 2
        # above) and resuming directly into a pending rework stage after a review already
        # landed (TestPauseResumeThroughRealApis::test_resume_into_a_pending_rework_stage_
        # reapplies_the_slots_pre_instruction, unchanged) — both real, both already covered.
        assert review_module.resolve_review_gate(_gate_bundle())["stage"] == review_module.REVIEW_HOP_KIND
        monkeypatch.setattr(_token_routes, "_build_mention_for_token", lambda **kw: MENTION)
        review_run = review_module.run_review_gate(GROUP_ID, _gate_bundle(), {"run_id": None})
        assert review_run is True
        active_review_run = svc._active_run_for_group(GROUP_ID)
        assert active_review_run is not None
        _wait_finished(active_review_run["run_id"])
        # The real reviewer subprocess actually ran — this is exactly the gap the rev2
        # version left open. Wait for its post-process handoff to park before injecting the
        # verdict; status="finished" is published before that terminal phase completes.
        assert reviewer_round1_outfile.exists()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            parked = env["paused"].get_by_group(GROUP_ID)
            if (
                parked is not None
                and parked.get("stop_kind") == "system"
                and svc.peek_auto_resume(GROUP_ID) is None
            ):
                break
            time.sleep(0.01)
        else:
            raise AssertionError("reviewer terminal handoff did not settle")

        # ---- 5. reject -> rework through the REAL run_review_gate (reject_first ->
        # review._auto_reject -> real pipeline_service.transition_document_review -> real
        # review._spawn_rework_hop), reviewer's own provider live but never invoked (R6).
        review_rows.append({"id": 1, "verdict": "issues", "revision_no": 0,
                            "comment": "please fix", "findings": "[]"})
        rework_cmd, rework_outfile = _capture_cmd(env["tmp"])
        env["chain"]["providers"] = [
            _provider(pid=WORKER_PROVIDER, cmd=rework_cmd),
            _provider(pid=REVIEWER_PROVIDER, cmd=_reviewer_cmd(reviewer_reject_check_outfile)),
        ]
        monkeypatch.setattr(_token_routes, "_build_mention_for_token", lambda **kw: MENTION)

        rework_started = review_module.run_review_gate(GROUP_ID, _gate_bundle(), {"run_id": None})
        assert rework_started is True
        # The real reject transition really ran: the document is now rejected, with a real
        # rejection_history item this test never wrote by hand.
        rejected_doc = docs.get_by_id(RESULT_DOC_ID)
        assert rejected_doc["doc_review_status"] == "rejected"
        # transition_document_review stores rejection_history as a JSON-encoded string
        # column, exactly like the real documents table — decode it the same way any real
        # reader (review._parse_rejection_history) would.
        rejection_history = json.loads(rejected_doc["rejection_history"] or "[]")
        assert len(rejection_history) == 1
        assert rejection_history[0]["reason"]

        rework_run = svc._active_run_for_group(GROUP_ID)
        assert rework_run is not None
        _wait_finished(rework_run["run_id"])
        rework_text = _read(rework_outfile).decode("utf-8")
        assert rework_text.count("## WorkPlan 사전지시") == 1
        assert PRE_TEXT in rework_text
        # T0014 §9: rework re-applies the slot's pre-instruction, not `note` — that field is
        # only ever injected on the "new"/continuous path (_inject_hop_notes), which the
        # rework hop structurally never reaches (see the companion rework test above).
        # R6: the reviewer's own provider was NEVER invoked for the rework worker spawn.
        assert not reviewer_reject_check_outfile.exists()

        # ---- 6. review_count=2's finite budget (R2): the rework landed (bump revision_no,
        # mirroring a real submit), but ONE review+rework pair does not exhaust a budget of
        # two — resolve_review_gate must still demand a second review round, not approve yet.
        docs.update(RESULT_DOC_ID, {"revision_no": 1, "doc_review_status": "revised"})
        mid_gate = review_module.resolve_review_gate(_gate_bundle())
        assert mid_gate["stage"] == review_module.REVIEW_HOP_KIND
        assert mid_gate["round_no"] == 2

        # ---- 7. final pass (최종 pass) — run the actual settlement rather than stopping
        # at resolve_review_gate(). The fake sequence view derives its next head from the
        # canonical T's real approval state, mirroring the production SQL effective-head join.
        review_rows.append({"id": 2, "verdict": "pass", "revision_no": 1,
                            "comment": None, "findings": "[]"})
        final_gate = review_module.resolve_review_gate(_gate_bundle())
        assert final_gate["stage"] == review_module.WORK_HOP_KIND
        assert final_gate.get("approve_first") is True

        def _approval_aware_head(_sequence_id):
            next_seq = 2 if docs.get_by_id(RESULT_DOC_ID)["doc_review_status"] == "approved" else 1
            return next(
                dict(item) for item in env["wfseq"].items if item["item_seq"] == next_seq
            )

        monkeypatch.setattr(svc.db_wfseq, "get_effective_head", _approval_aware_head)
        handoff_cmd, handoff_outfile = _capture_cmd(env["tmp"])
        env["chain"]["providers"] = [
            _provider(pid=WORKER_PROVIDER, cmd=handoff_cmd),
            _provider(pid=REVIEWER_PROVIDER, cmd=_reviewer_cmd(reviewer_reject_check_outfile)),
        ]
        env["chain"]["registered_count"] = 2

        handoff_started = review_module.run_review_gate(
            GROUP_ID, _gate_bundle(), {"run_id": None},
        )
        assert handoff_started is True
        assert docs.get_by_id(RESULT_DOC_ID)["doc_review_status"] == "approved"
        assert _approval_aware_head(1)["type"] == "TR"

        handoff_run = svc._active_run_for_group(GROUP_ID)
        assert handoff_run is not None
        _wait_finished(handoff_run["run_id"])
        handoff_text = _read(handoff_outfile).decode("utf-8")
        assert "WorkPlan 사전지시" not in handoff_text
        assert PRE_TEXT not in handoff_text
        assert ATTACHMENT["original_filename"] not in handoff_text
