"""flowgate.default.0549 T0008 — TS test specification / TSR test report / gate / resume.

The contract under test (0549 D0006, approved NR0005):

* TS → TSR stays a two-document set; no TSC type.
* A TS that declares ``test_contract_version: 2`` is a human-approved structured test
  specification. It is never executed by the server runner; a TS without the marker is a
  legacy executable TS and keeps its parser/runner unchanged.
* Automated (JUnit XML / explicit payload), manual and external results share one result
  model keyed by explicit TS Case ID; missing cases, results outside the TS and duplicate
  mappings are told apart; evidence and source identity are kept.
* The TSR is written for every submission (PASS or not). Its overall verdict is computed by
  the server from the REQUIRED cases; "TSR exists" is not "gate passed"; an overall claimed
  by the submitter or written into a body never opens the gate.
* FAIL on an unmanned chain goes to the existing failure-origin review; BLOCKED/NOT_RUN
  hold. PASS resumes the chain through the ordinary durable paused row + resume_chain when
  the target lies beyond the TSR, and ends it when the TSR is the target.

The result store runs against a real SQLite database built from every migration (121
included), so the new columns and SQL are exercised, not mocked.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from modules.flow_gate.services import test_spec_service as spec

GROUP = "flowgate.default.0549"
TS_ID = f"{GROUP}.0010-TS"
TSR_ID = f"{GROUP}.0011-TSR"


def _spec_doc(cases_md: str, *, extra_frontmatter: str = "") -> str:
    return (
        "---\n"
        "project: flowgate\n"
        "type: TS\n"
        f"{extra_frontmatter}"
        "test_contract_version: 2\n"
        "---\n"
        "# 권한 시험사양\n\n"
        "범위: 문서 수정 권한.\n\n"
        "## 시험 사양\n\n"
        f"{cases_md}"
    )


CASE_TC1 = (
    "### TC-001: 권한 없는 사용자의 수정 거부\n"
    "- category: negative\n"
    "- requirement: R0001 §4.2 / AC-03\n"
    "- execution_mode: automated\n"
    "- required: true\n"
    "- precondition: 일반 사용자로 로그인\n"
    "- input: PATCH /documents/x\n"
    "- procedure: 1. 수정 요청을 보낸다\n"
    "  2. 응답을 확인한다\n"
    "- expected: HTTP 403, error.code=forbidden\n"
    "- check_points: 권한 검사 / 응답 코드\n\n"
)
CASE_TC2 = (
    "### TC-002: 화면에 잠금 배지가 보인다\n"
    "- 분류: normal\n"
    "- 대상: AC-1\n"
    "- 실행 방식: manual\n"
    "- 필수: true\n"
    "- 절차: 문서를 연다\n"
    "- 기대 결과: 잠금 배지 표시\n"
    "- 확인 관점: UI\n\n"
)
CASE_TC3 = (
    "### TC-003: 외부 장비 로그 (선택)\n"
    "- category: boundary\n"
    "- requirement: AC-9\n"
    "- execution_mode: external\n"
    "- required: false\n"
    "- procedure: 장비 로그 확인\n"
    "- expected: 오류 없음\n"
    "- check_points: 로그\n\n"
)
SPEC = _spec_doc(CASE_TC1 + CASE_TC2 + CASE_TC3)

LEGACY_TS = (
    "## 테스트 케이스\n\n"
    "### TC-1: smoke\n"
    "- cmd: python --version\n"
    "- 기대: exits 0\n"
)


# ── 1. Contract marker and specification grammar ─────────────────────────────


def test_contract_marker_is_explicit():
    assert spec.detect_contract_version(LEGACY_TS) == spec.CONTRACT_LEGACY
    assert spec.detect_contract_version("---\ntitle: x\n---\n## 테스트 케이스\n") == 1
    assert spec.detect_contract_version(SPEC) == spec.CONTRACT_SPEC
    assert spec.detect_contract_version("---\ntest_contract_version: two\n---\n") is None
    # A body that merely looks like a specification is still legacy without the marker —
    # nothing is inferred from the body or the creation time.
    assert spec.detect_contract_version(SPEC.replace("test_contract_version: 2\n", "")) == 1


def test_spec_parses_every_structured_field_with_aliases_and_multiline_procedure():
    parsed = spec.parse_spec(SPEC)
    assert parsed["errors"] == []
    assert parsed["title"] == "권한 시험사양"
    by_id = {c["case_id"]: c for c in parsed["cases"]}
    assert list(by_id) == ["TC-001", "TC-002", "TC-003"]
    tc1 = by_id["TC-001"]
    assert tc1["category"] == "negative"
    assert tc1["requirement"] == "R0001 §4.2 / AC-03"
    assert tc1["execution_mode"] == "automated"
    assert tc1["required"] is True
    assert tc1["precondition"] == "일반 사용자로 로그인"
    assert tc1["input"] == "PATCH /documents/x"
    assert tc1["procedure"] == "1. 수정 요청을 보낸다\n2. 응답을 확인한다"
    assert tc1["expected"] == "HTTP 403, error.code=forbidden"
    assert tc1["check_points"] == "권한 검사 / 응답 코드"
    tc2 = by_id["TC-002"]  # Korean field aliases
    assert (tc2["category"], tc2["execution_mode"], tc2["required"]) == ("normal", "manual", True)
    assert by_id["TC-003"]["required"] is False


@pytest.mark.parametrize(
    "mutation, code",
    [
        (lambda s: s.replace("### TC-002", "### TC-1"), "duplicate_case_id"),  # TC-1 == TC-001
        (lambda s: s.replace("- expected: HTTP 403, error.code=forbidden\n", ""), "missing_field"),
        (lambda s: s.replace("- category: negative", "- category: happy"), "invalid_category"),
        (lambda s: s.replace("- execution_mode: automated", "- execution_mode: robot"), "invalid_execution_mode"),
        (lambda s: s.replace("- required: true\n- precondition", "- required: maybe\n- precondition"), "invalid_required"),
        (lambda s: s.replace("- check_points: 권한 검사 / 응답 코드", "- cmd: curl http://x"), "legacy_field_in_spec"),
        (lambda s: s.replace("### TC-001: 권한", "### 권한"), "invalid_case_heading"),
        (lambda s: s.replace("## 시험 사양", "## 사양"), "missing_spec_section"),
        (lambda s: s.replace("- required: true", "- required: false").replace("- 필수: true", "- 필수: false"), "no_required_case"),
        (lambda s: s.replace("- check_points: 로그", "- color: red"), "unknown_field"),
    ],
)
def test_spec_validation_reports_each_boundary(mutation, code):
    parsed = spec.parse_spec(mutation(SPEC))
    assert code in {err["code"] for err in parsed["errors"]}, parsed["errors"]


def test_structured_editor_payload_round_trips_through_the_canonical_markdown():
    parsed = spec.parse_spec(SPEC)
    body = spec.render_spec_body(parsed["cases"], title="권한 시험사양", intro="범위: 문서 수정 권한.")
    doc = spec.render_spec_document(
        parsed["cases"], title="권한 시험사양", frontmatter_block="---\nproject: flowgate\n---",
        intro="범위: 문서 수정 권한.",
    )
    assert doc.startswith("---\nproject: flowgate\ntest_contract_version: 2\n---\n")
    assert doc.endswith(body)
    again = spec.parse_spec(doc)
    assert again["errors"] == [] and again["cases"] == parsed["cases"]
    assert spec.spec_intro(doc) == "범위: 문서 수정 권한."
    cases, errors = spec.validate_cases_payload([{**parsed["cases"][0], "case_id": "tc-9"}])
    assert errors == [] and cases[0]["case_id"] == "TC-9"
    _cases, errors = spec.validate_cases_payload([{"case_id": "TC-1", "title": "x"}])
    assert {e["field"] for e in errors} >= {"category", "requirement", "procedure", "expected"}


# ── 2. Result model: payload, JUnit XML, mapping, overall ───────────────────────


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.test_perm" name="test_forbidden_update" time="0.12">
    <properties><property name="flowgate.case_id" value="TC-001"/></properties>
  </testcase>
  <testcase classname="tests.test_badge" name="test_TC_2_badge" time="0.3">
    <failure message="badge missing">AssertionError: no badge</failure>
    <system-out>rendered html</system-out>
  </testcase>
  <testcase classname="tests.test_misc" name="test_unrelated"><skipped/></testcase>
</testsuite></testsuites>"""


def test_junit_adapter_links_case_ids_and_keeps_evidence_and_source_identity():
    results = spec.parse_junit_xml(
        JUNIT, submitted_by="usr_ai", now="2026-09-27T13:00:00+09:00",
        default_source_identity={"git_revision": "abc123", "runner": "pytest", "junk": "x"},
    )
    by_name = {r["source_name"]: r for r in results}
    tc1 = by_name["tests.test_perm::test_forbidden_update"]
    assert (tc1["case_id"], tc1["status"]) == ("TC-001", "PASS")
    tc2 = by_name["tests.test_badge::test_TC_2_badge"]
    assert (tc2["case_id"], tc2["status"]) == ("TC-2", "FAIL")
    assert "badge missing" in tc2["actual"]
    assert {e["kind"] for e in tc2["evidence"]} == {"structured", "log"}
    assert tc2["source_identity"] == {"git_revision": "abc123", "runner": "pytest"}
    other = by_name["tests.test_misc::test_unrelated"]
    assert (other["case_id"], other["status"]) == (None, "NOT_RUN")


@pytest.mark.parametrize("xml", ["", "<not-closed>", '<!DOCTYPE x [<!ENTITY a "b">]><testsuite/>', "<testsuite/>"])
def test_junit_adapter_refuses_bad_reports(xml):
    with pytest.raises(spec.SpecResultError):
        spec.parse_junit_xml(xml, submitted_by="u", now="t")


def test_result_payload_is_validated_as_a_whole():
    with pytest.raises(spec.SpecResultError) as exc:
        spec.normalize_results(
            [{"case_id": "TC-1", "status": "GREEN"},
             {"case_id": "TC-2", "status": "PASS", "evidence": [{"kind": "video", "value": "x"}]}],
            submitted_by="u", now="t",
        )
    codes = {e["code"] for e in exc.value.errors}
    assert codes == {"invalid_status", "invalid_evidence"}


def test_mapping_distinguishes_missing_unmapped_and_duplicate():
    cases = spec.parse_spec(SPEC)["cases"]
    results = spec.normalize_results(
        [
            {"case_id": "TC-001", "status": "PASS", "source_name": "a"},
            {"case_id": "TC-001", "status": "PASS", "source_name": "b"},   # duplicate mapping
            {"case_id": "TC-777", "status": "FAIL", "source_name": "c"},   # not in the TS
            {"status": "PASS", "source_name": "d"},                        # no case id at all
        ],
        submitted_by="u", now="t",
    )
    mapped = spec.map_results(cases, results)
    rows = {r["case_id"]: r for r in mapped["cases"]}
    assert rows["TC-001"]["status"] == "BLOCKED" and rows["TC-001"]["mapping_conflict"] is True
    assert rows["TC-002"]["status"] == "NOT_RUN" and rows["TC-002"]["result_origin"] == "missing"
    assert [c["case_id"] for c in mapped["conflicts"]] == ["TC-001"]
    assert {(u["case_id"], u["reason"]) for u in mapped["unmapped"]} == {
        ("TC-777", "unknown_case_id"), (None, "missing_case_id"),
    }
    # a duplicate that contains a FAIL is a FAIL, never a softer verdict
    failing = spec.normalize_results(
        [{"case_id": "TC-001", "status": "PASS"}, {"case_id": "TC-001", "status": "FAIL"}],
        submitted_by="u", now="t",
    )
    assert spec.map_results(cases, failing)["cases"][0]["status"] == "FAIL"


@pytest.mark.parametrize(
    "statuses, overall",
    [
        ({"TC-001": "PASS", "TC-002": "PASS", "TC-003": "FAIL"}, "PASS"),      # optional FAIL ≠ gate
        ({"TC-001": "PASS", "TC-002": "FAIL", "TC-003": "PASS"}, "FAIL"),
        ({"TC-001": "BLOCKED", "TC-002": "FAIL"}, "FAIL"),                      # FAIL outranks
        ({"TC-001": "BLOCKED", "TC-002": "PASS"}, "BLOCKED"),
        ({"TC-001": "PASS"}, "NOT_RUN"),                                        # required NOT_RUN
        ({}, "NOT_RUN"),
    ],
)
def test_overall_is_computed_from_required_cases(statuses, overall):
    cases = spec.parse_spec(SPEC)["cases"]
    results = spec.normalize_results(
        [{"case_id": cid, "status": st} for cid, st in statuses.items()], submitted_by="u", now="t"
    )
    summary = spec.compute_overall(spec.map_results(cases, results)["cases"])
    assert summary["overall"] == overall
    assert summary["gate_passed"] is (overall == "PASS")
    assert spec.compute_overall([])["overall"] == "NOT_RUN"


# ── 3. Service against a real migrated SQLite result store ─────────────────────


class _Store:
    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = OFF")

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


class _Env:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.contents: dict[str, str] = {}
        self.transitions: list[dict] = []
        self.registered: list[dict] = []
        self.chain_token: dict | None = None
        self.paused_row: dict | None = None
        self.relabels: list[tuple] = []
        self.deleted_rows: list[tuple] = []
        self.resumes: list[dict] = []
        self.ended: list[dict] = []
        self.dispatched: list[str] = []
        self.notified: list[str] = []
        self.next_incomplete: int | None = None
        self.tsr_item: dict | None = None


@pytest.fixture
def env(monkeypatch, tmp_path, migrated_sqlite_db):
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused
    from modules.flow_gate.db import test_runs as db_test_runs
    from modules.flow_gate.db import tokens as db_tokens
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.services import ai_invoke_service, failure_origin_review_service
    from modules.flow_gate.services import test_run_service as svc
    from modules.flow_gate.services.ai_invoke import chain as ai_chain
    from modules.flow_gate.workflow import event_logger, pipeline_service

    store = _Store(migrated_sqlite_db("spec_0549.db"))
    monkeypatch.setattr(db_test_runs, "get_store", lambda: store)
    e = _Env()
    e.docs[TS_ID] = {
        "doc_id": TS_ID, "project_id": "flowgate", "branch": "main", "module": "default",
        "group_id": GROUP, "type_code": "TS", "title": "권한 시험사양", "owner_id": "usr_admin",
        "doc_review_status": "approved", "revision_no": 2, "seq": 10, "file_path": "ts.md",
    }
    e.contents[TS_ID] = SPEC

    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: e.docs.get(doc_id))
    monkeypatch.setattr(svc.process_service, "is_group_disposed", lambda _g: False)
    monkeypatch.setattr(svc, "_read_doc_content", lambda doc: e.contents.get(doc["doc_id"], ""))

    def document_path(*, project_id, group_code, doc_code, filename, module, branch):
        return tmp_path / f"{doc_code}_{filename}"

    monkeypatch.setattr(svc.storage_paths, "document_path", document_path)
    monkeypatch.setattr(svc.storage_paths, "to_storage_relative", lambda path, project_id=None: str(path))
    monkeypatch.setattr(svc.numbering_service, "reserve_document", lambda *a, **k: "0011-TSR")
    monkeypatch.setattr(svc.id_formatter, "parse_doc_code", lambda code: ("TSR", 11))

    def create(data):
        e.docs[data["doc_id"]] = {**data, "id": 911, "doc_review_status": "draft"}
        return e.docs[data["doc_id"]]

    def update(doc_id, updates):
        e.docs.setdefault(doc_id, {"doc_id": doc_id}).update(updates)
        return e.docs[doc_id]

    monkeypatch.setattr(svc.db_docs, "create", create)
    monkeypatch.setattr(svc.db_docs, "update", update)
    monkeypatch.setattr(
        svc.db_docs, "get_documents_by_target_id",
        lambda target, types=None: [d for d in e.docs.values()
                                    if d.get("target_id") == target and d.get("type_code") in (types or ("TSR",))],
    )

    def transition(**kwargs):
        e.transitions.append(kwargs)
        if kwargs["action"] == "approve":
            # The real approval guard, against the real result store.
            pipeline_service._require_test_gate_for_approval(e.docs[kwargs["doc_id"]])
            e.docs[kwargs["doc_id"]]["doc_review_status"] = "approved"
        elif kwargs["action"] == "submit":
            e.docs[kwargs["doc_id"]]["doc_review_status"] = "pending_review"
        return {}

    monkeypatch.setattr(pipeline_service, "transition_document_review", transition)
    monkeypatch.setattr(pipeline_service, "register_workflow_result",
                        lambda **kw: e.registered.append(kw))
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda _d: None)
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda _s: [])
    monkeypatch.setattr(
        db_wfseq, "get_pending_head_by_group",
        lambda *_a: e.tsr_item if e.tsr_item and not e.tsr_item.get("result_doc_id") else None,
    )
    monkeypatch.setattr(db_wfseq, "get_item_by_result_doc_id", lambda _d: None)
    monkeypatch.setattr(svc, "_tsr_slot_item", lambda doc, _w: e.tsr_item)

    monkeypatch.setattr(db_tokens, "get_latest_consumed_by_scope_doc_ref",
                        lambda _s, _r: e.chain_token)
    import modules.flow_gate.workflow.routers.workflow as workflow_router
    monkeypatch.setattr(workflow_router, "_get_user_permissions", lambda _u: {"document.approve"})
    from modules.flow_gate.db import users as db_users
    monkeypatch.setattr(db_users, "get_by_id", lambda uid: {"user_id": uid, "is_admin": 1})

    monkeypatch.setattr(db_paused, "get_by_group", lambda _g: e.paused_row)
    monkeypatch.setattr(db_paused, "mark_stop_code",
                        lambda g, code, stop_run_id=None: e.relabels.append((g, code, stop_run_id)))
    monkeypatch.setattr(db_paused, "delete_system_stop",
                        lambda g, run_id: e.deleted_rows.append((g, run_id)))
    monkeypatch.setattr(ai_chain, "_next_incomplete_item_seq", lambda _d: e.next_incomplete)

    def resume_chain(**kwargs):
        e.resumes.append(kwargs)
        return {"run_id": "aiv_resumed", "chain_id": "chain-1"}

    monkeypatch.setattr(ai_invoke_service, "resume_chain", resume_chain)
    monkeypatch.setattr(event_logger, "log_continuous_work_ended", lambda **kw: e.ended.append(kw))
    monkeypatch.setattr(event_logger, "log_continuous_work_failed",
                        lambda **kw: e.notified.append(kw.get("run_id")))
    monkeypatch.setattr(failure_origin_review_service, "dispatch_failure_origin_review",
                        lambda **kw: e.dispatched.append(kw["run"]["run_id"]))
    monkeypatch.setattr(svc, "_broadcast", lambda *a, **k: None)
    e.svc = svc
    e.store = store
    e.db_test_runs = db_test_runs
    return e


def _submit(env, results=None, **kw):
    recorded = env.svc.record_spec_results(
        doc_id=TS_ID, runner_id=kw.pop("runner_id", "usr_admin"),
        triggered_via=kw.pop("triggered_via", "ui"), results=results, **kw,
    )
    outcome = env.svc.finalize_spec_results(recorded["doc"], recorded["run"])
    return recorded, outcome


def test_spec_run_is_stored_terminal_with_per_case_verdicts(env):
    recorded, outcome = _submit(
        env,
        [{"case_id": "TC-001", "status": "PASS", "actual": "HTTP 403",
          "evidence": [{"kind": "log", "value": "403 forbidden"}]},
         {"case_id": "TC-002", "status": "FAIL", "actual": "no badge", "defect_ref": "BUG-7"}],
        source_identity={"git_revision": "abc123", "runner": "pytest"},
    )
    run = env.db_test_runs.get_run(recorded["run"]["run_id"])
    assert run["contract_version"] == 2 and run["overall"] == "FAIL"
    assert run["status"] == "failed" and run["picked_at"] is not None  # never picked by the runner
    assert env.db_test_runs.pick_next_running() is None
    cases = {c["case_no"]: c for c in env.db_test_runs.list_cases(run["run_id"])}
    assert cases["TC-001"]["case_status"] == "PASS" and cases["TC-001"]["result"] == "pass"
    assert cases["TC-002"]["case_status"] == "FAIL" and cases["TC-002"]["result"] == "fail"
    assert cases["TC-003"]["case_status"] == "NOT_RUN" and cases["TC-003"]["result"] is None
    shaped = env.svc.shape_run(run, include_cases=True)
    tc1 = next(c for c in shaped["cases"] if c["case_no"] == "TC-001")
    assert tc1["evidence"] == [{"kind": "log", "value": "403 forbidden"}]
    assert tc1["source_identity"] == {"git_revision": "abc123", "runner": "pytest"}
    assert shaped["summary"]["required_counts"] == {"total": 2, "pass": 1, "fail": 1, "blocked": 0, "not_run": 0}
    # The TSR exists although the gate is not passed.
    assert outcome["tsr_doc_id"] == TSR_ID and outcome["gate_passed"] is False
    assert run["tsr_doc_id"] is None or env.db_test_runs.get_run(run["run_id"])["tsr_doc_id"] == TSR_ID


def test_submitted_overall_pass_never_opens_the_gate(env):
    recorded = env.svc.record_spec_results(
        doc_id=TS_ID, runner_id="usr_ai", triggered_via="token",
        results=[{"case_id": "TC-001", "status": "PASS"}, {"case_id": "TC-002", "status": "FAIL"}],
        submitted_overall="PASS",
    )
    run = env.db_test_runs.get_run(recorded["run"]["run_id"])
    assert run["overall"] == "FAIL"
    assert json.loads(run["result_meta"])["ignored_submitted_overall"] == "PASS"


def test_manual_results_carry_forward_onto_automated_ones(env):
    _submit(env, [{"case_id": "TC-001", "status": "PASS", "actual": "403"}])
    recorded, _ = _submit(env, [{"case_id": "TC-002", "status": "PASS", "actual": "badge shown"}])
    cases = {c["case_no"]: c for c in env.db_test_runs.list_cases(recorded["run"]["run_id"])}
    assert cases["TC-001"]["case_status"] == "PASS"
    meta = json.loads(cases["TC-001"]["case_meta"])
    assert meta["result_origin"] == "carried" and meta["carried_from_run_id"]
    assert recorded["run"]["overall"] == "PASS"
    # replace=True judges on this submission alone
    replaced, _ = _submit(env, [{"case_id": "TC-002", "status": "PASS"}], replace=True)
    assert replaced["run"]["overall"] == "NOT_RUN"
    # a new TS revision never inherits the previous revision's verdicts
    env.docs[TS_ID]["revision_no"] = 3
    fresh, _ = _submit(env, [{"case_id": "TC-002", "status": "PASS"}])
    assert fresh["run"]["overall"] == "NOT_RUN"


def test_junit_and_payload_share_one_submission(env):
    recorded, _ = _submit(env, [{"case_id": "TC-002", "status": "PASS", "actual": "badge"}],
                          junit_xml=JUNIT.replace("TC_2", "TC_9"))
    run = env.db_test_runs.get_run(recorded["run"]["run_id"])
    meta = json.loads(run["result_meta"])
    assert meta["result_sources"] == ["results", "junit_xml"]
    assert {u["source_name"] for u in meta["unmapped"]} == {
        "tests.test_badge::test_TC_9_badge", "tests.test_misc::test_unrelated",
    }
    assert run["overall"] == "PASS"


@pytest.mark.parametrize(
    "mutate, status, code",
    [
        (lambda e: e.contents.__setitem__(TS_ID, LEGACY_TS), 422, "legacy_ts_requires_execution"),
        (lambda e: e.contents.__setitem__(TS_ID, SPEC.replace("- expected: HTTP 403, error.code=forbidden\n", "")), 422, "invalid_spec"),
        (lambda e: e.docs[TS_ID].__setitem__("doc_review_status", "draft"), 409, "doc_not_approved"),
        (lambda e: e.docs[TS_ID].__setitem__("type_code", "T"), 422, "no_test_cases"),
    ],
)
def test_result_intake_admission_boundaries(env, mutate, status, code):
    mutate(env)
    with pytest.raises(HTTPException) as exc:
        env.svc.record_spec_results(doc_id=TS_ID, runner_id="u", triggered_via="ui",
                                    results=[{"case_id": "TC-001", "status": "PASS"}])
    assert exc.value.status_code == status and exc.value.detail["error"] == code


def test_an_approved_test_report_is_not_rewritten_by_new_results(env):
    _submit(env, [{"case_id": "TC-001", "status": "PASS"}, {"case_id": "TC-002", "status": "PASS"}])
    env.docs[TSR_ID]["doc_review_status"] = "approved"
    view = env.svc.describe_test_document(env.docs[TS_ID])
    assert (view["tsr_doc_id"], view["tsr_review_status"]) == (TSR_ID, "approved")
    runs_before = len(env.db_test_runs.list_by_doc(TS_ID))
    with pytest.raises(HTTPException) as exc:
        env.svc.record_spec_results(doc_id=TS_ID, runner_id="u", triggered_via="ui",
                                    results=[{"case_id": "TC-002", "status": "FAIL"}])
    assert exc.value.status_code == 409 and exc.value.detail["error"] == "tsr_already_approved"
    assert len(env.db_test_runs.list_by_doc(TS_ID)) == runs_before


def test_invalid_results_and_empty_submission_are_refused(env):
    with pytest.raises(HTTPException) as exc:
        env.svc.record_spec_results(doc_id=TS_ID, runner_id="u", triggered_via="ui",
                                    results=[{"case_id": "TC-001", "status": "OK?"}])
    assert exc.value.detail["error"] == "invalid_results"
    with pytest.raises(HTTPException) as exc:
        env.svc.record_spec_results(doc_id=TS_ID, runner_id="u", triggered_via="ui", results=[])
    assert exc.value.detail["error"] == "spec_results_required"
    assert env.db_test_runs.list_by_doc(TS_ID) == []


def test_executable_runner_refuses_a_specification_ts(env, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        env.svc.validate_and_create_run(doc_id=TS_ID, runner_id="u", triggered_via="ui")
    assert exc.value.status_code == 422 and exc.value.detail["error"] == "spec_ts_not_executable"
    assert env.db_test_runs.list_by_doc(TS_ID) == []


def test_legacy_executable_ts_keeps_its_runner_path(env, tmp_path, monkeypatch):
    env.contents[TS_ID] = LEGACY_TS
    monkeypatch.setattr(env.svc.storage_paths, "resolve_project_src_root", lambda *a, **k: tmp_path)
    monkeypatch.setattr(env.svc, "_emit_started", lambda *a, **k: None)
    result = env.svc.validate_and_create_run(doc_id=TS_ID, runner_id="u", triggered_via="ui")
    run = env.db_test_runs.get_run(result["run_id"])
    assert run["status"] == "running" and run["contract_version"] is None
    assert [c["cmd"] for c in env.db_test_runs.list_cases(run["run_id"])] == ["python --version"]
    # and the specification intake refuses the same document
    with pytest.raises(HTTPException) as exc:
        env.svc.record_spec_results(doc_id=TS_ID, runner_id="u", triggered_via="ui",
                                    results=[{"case_id": "TC-1", "status": "PASS"}])
    assert exc.value.detail["error"] == "legacy_ts_requires_execution"


def test_tsr_report_lists_every_case_even_when_failing(env, tmp_path):
    _submit(env, [{"case_id": "TC-001", "status": "PASS", "actual": "HTTP 403",
                   "evidence": [{"kind": "url", "value": "https://ci/1"}]},
                  {"case_id": "TC-002", "status": "FAIL", "actual": "no badge", "defect_ref": "BUG-7"}],
            source_identity={"git_revision": "abc123"})
    body = (tmp_path / "0011-TSR_document.md").read_text(encoding="utf-8")
    assert "**FAIL**" in body and "시험 gate 미통과" in body
    for needle in ("TC-001", "TC-002", "TC-003", "HTTP 403", "no badge", "BUG-7",
                   "https://ci/1", "git_revision=abc123", "NOT_RUN"):
        assert needle in body
    assert env.docs[TSR_ID]["target_id"] == TS_ID


# ── 4. Gate: TSR approval and the chain ─────────────────────────────────────────


def test_approval_guard_blocks_a_non_pass_tsr_and_allows_pass(env):
    from modules.flow_gate.workflow import pipeline_service
    from modules.flow_gate.workflow.pipeline_service import TransitionError

    _submit(env, [{"case_id": "TC-001", "status": "PASS"}, {"case_id": "TC-002", "status": "BLOCKED"}])
    tsr = env.docs[TSR_ID]
    with pytest.raises(TransitionError) as exc:
        pipeline_service._require_test_gate_for_approval(tsr, "en")
    assert "BLOCKED" in str(exc.value)
    # Someone writes "Overall: PASS" into the report body: the gate reads the record, not text.
    tsr["meta"] = json.dumps({"overall": "PASS"})
    with pytest.raises(TransitionError):
        pipeline_service._require_test_gate_for_approval(tsr, "ko")

    _submit(env, [{"case_id": "TC-002", "status": "PASS"}])
    pipeline_service._require_test_gate_for_approval(tsr, "ko")  # no raise

    # A legacy TSR (latest run is an executable run) keeps its approval semantics.
    env.store._execute("DELETE FROM test_runs")
    env.store._execute(
        "INSERT INTO test_runs (run_id, doc_id, revision_no, status, triggered_via, runner_id, created_at) "
        "VALUES ('trun_legacy', ?, 2, 'passed', 'ui', 'u', '2026-09-27T00:00:00+09:00')", [TS_ID])
    assert env.svc.tsr_gate_state(tsr) == {"applies": False, "passed": True}


def test_ts_approval_requires_a_valid_specification(env):
    from modules.flow_gate.workflow import pipeline_service
    from modules.flow_gate.workflow.pipeline_service import TransitionError

    ts = env.docs[TS_ID]
    pipeline_service._require_test_gate_for_approval(ts)  # valid spec
    env.contents[TS_ID] = SPEC.replace("### TC-002", "### TC-001")
    with pytest.raises(TransitionError):
        pipeline_service._require_test_gate_for_approval(ts)
    env.contents[TS_ID] = LEGACY_TS  # legacy TS: no new rule
    pipeline_service._require_test_gate_for_approval(ts)


def _chain(env, *, target_seq, tsr_seq=11, next_incomplete=None, row=True, row_target="same"):
    env.chain_token = {"issued_to": "usr_admin", "continuation_target_seq": target_seq,
                       "continuation_locale": "ko"}
    env.tsr_item = {"id": 5, "item_seq": tsr_seq, "type": "TSR", "result_doc_id": None}
    env.next_incomplete = next_incomplete
    env.paused_row = (
        {"group_id": GROUP, "stop_kind": "system", "stop_code": "test_run_pending",
         "stop_run_id": "aiv_hop", "paused_by": "usr_admin",
         "continuation_target_seq": target_seq if row_target == "same" else row_target}
        if row else None
    )


def _bind_tsr_slot(env):
    env.tsr_item["result_doc_id"] = TSR_ID


def test_chain_pass_with_target_beyond_tsr_resumes_the_next_head(env):
    _chain(env, target_seq=14, next_incomplete=12)
    recorded, outcome = _submit(
        env, [{"case_id": "TC-001", "status": "PASS"}, {"case_id": "TC-002", "status": "PASS"}],
        triggered_via="token", chain_context={"api_base_url": "http://h:8089/flowgate/api/v1", "locale": "ko"},
    )
    # _submit's TSR registration binds the slot in production; emulate the slot read-back.
    _bind_tsr_slot(env)
    again = env.svc.continue_chain_after_test_gate(env.docs[TS_ID], recorded["run"])
    assert env.docs[TSR_ID]["doc_review_status"] == "approved"   # chain auto-approve, gate PASS
    assert again["action"] == "resumed"
    call = env.resumes[-1]
    assert call["group_id"] == GROUP and call["user_id"] == "usr_admin"
    assert call["api_base_url"] == "http://h:8089/flowgate/api/v1"
    assert env.ended == []


def test_chain_pass_with_tsr_target_ends_the_chain_and_clears_the_row(env):
    _chain(env, target_seq=11, next_incomplete=12)
    _submit(env, [{"case_id": "TC-001", "status": "PASS"}, {"case_id": "TC-002", "status": "PASS"}],
            triggered_via="token")
    _bind_tsr_slot(env)
    out = env.svc.continue_chain_after_test_gate(env.docs[TS_ID])
    assert out["action"] == "ended"
    assert env.deleted_rows == [(GROUP, "aiv_hop")]
    assert env.ended and env.ended[-1]["doc_id"] == TSR_ID
    assert env.resumes == []


def test_chain_pass_before_the_hop_finalizes_defers_to_the_finalize_hook(env):
    _chain(env, target_seq=14, next_incomplete=12, row=False)
    _submit(env, [{"case_id": "TC-001", "status": "PASS"}, {"case_id": "TC-002", "status": "PASS"}],
            triggered_via="token")
    _bind_tsr_slot(env)
    assert env.svc.continue_chain_after_test_gate(env.docs[TS_ID])["action"] == "deferred"
    assert env.resumes == []
    # the hop finalizes → the durable row is parked → the finalize hook resumes
    _chain(env, target_seq=14, next_incomplete=12)
    _bind_tsr_slot(env)
    out = env.svc.resume_test_gate_chain_for_hop(TS_ID, api_base_url="http://w/api/v1", locale="ko")
    assert out["action"] == "resumed" and env.resumes[-1]["api_base_url"] == "http://w/api/v1"


def test_run_to_end_chain_resumes_while_steps_remain(env):
    _chain(env, target_seq=14, next_incomplete=12, row_target=None)
    _submit(env, [{"case_id": "TC-001", "status": "PASS"}, {"case_id": "TC-002", "status": "PASS"}],
            triggered_via="token")
    _bind_tsr_slot(env)
    assert env.svc.continue_chain_after_test_gate(env.docs[TS_ID])["action"] == "resumed"
    env.next_incomplete = None
    assert env.svc.continue_chain_after_test_gate(env.docs[TS_ID])["action"] == "ended"


@pytest.mark.parametrize("second, overall", [("FAIL", "FAIL"), ("BLOCKED", "BLOCKED"), (None, "NOT_RUN")])
def test_chain_never_resumes_past_a_non_pass_gate(env, second, overall):
    _chain(env, target_seq=14, next_incomplete=11)
    results = [{"case_id": "TC-001", "status": "PASS"}]
    if second:
        results.append({"case_id": "TC-002", "status": second})
    recorded, outcome = _submit(env, results, triggered_via="token")
    assert outcome["overall"] == overall and outcome["gate_passed"] is False
    assert env.docs[TSR_ID]["doc_review_status"] != "approved"
    assert env.resumes == []
    if overall == "FAIL":
        # failure-origin review (product_defect / test_defect / hold) is dispatched
        assert env.dispatched == [recorded["run"]["run_id"]]
        assert env.db_test_runs.get_run(recorded["run"]["run_id"])["error"] == "failure_origin_pending"
    else:
        assert env.dispatched == []
        assert env.relabels == [(GROUP, "test_gate_blocked", "aiv_hop")]
        assert env.notified == [recorded["run"]["run_id"]]


def test_manual_fail_is_left_to_the_human(env):
    recorded, outcome = _submit(env, [{"case_id": "TC-001", "status": "FAIL"}])
    assert outcome["failure_origin"] == {"routed": "human", "run_id": recorded["run"]["run_id"]}
    assert env.dispatched == []
    assert all(t["action"] != "approve" for t in env.transitions)
    assert env.docs[TSR_ID]["doc_review_status"] != "approved"


def test_failure_origin_evidence_and_rework_wording_for_a_spec_run(env):
    from modules.flow_gate.services import failure_origin_review_service as fo

    _chain(env, target_seq=14)
    recorded, _ = _submit(env, [{"case_id": "TC-001", "status": "PASS"},
                                {"case_id": "TC-002", "status": "FAIL", "actual": "no badge",
                                 "evidence": [{"kind": "screenshot", "value": "shot.png"}]}],
                          triggered_via="token")
    run = env.db_test_runs.get_run(recorded["run"]["run_id"])
    items = env.db_test_runs.list_cases(run["run_id"])
    text = fo.build_rework_instruction(classification="test_defect", doc=env.docs[TS_ID], run=run, items=items)
    assert "test automation, the Case ID mapping or the evidence" in text
    assert "Do not weaken the TS expected result" in text
    assert "TC-002" in text and "no badge" in text and "shot.png" in text
    product = fo.build_rework_instruction(classification="product_defect", doc=env.docs[TS_ID], run=run, items=items)
    assert "must not be weakened" in product


def test_legacy_async_pass_resumes_through_the_same_helper(env):
    """The executable path: finish_run first, then the gate outcome drives the chain."""
    _chain(env, target_seq=14, next_incomplete=12)
    env.store._execute(
        "INSERT INTO test_runs (run_id, doc_id, revision_no, status, triggered_via, runner_id, "
        "created_at, tsr_doc_id, result_meta) VALUES ('trun_leg', ?, 2, 'passed', 'token', 'u', "
        "'2026-09-27T00:00:00+09:00', ?, ?)",
        [TS_ID, TSR_ID, json.dumps({"chain": {"api_base_url": "http://legacy/api/v1"}})],
    )
    env.docs[TSR_ID] = {"doc_id": TSR_ID, "id": 911, "type_code": "TSR", "target_id": TS_ID,
                        "doc_review_status": "approved"}
    _bind_tsr_slot(env)
    out = env.svc.continue_chain_after_test_gate(env.docs[TS_ID], env.db_test_runs.get_run("trun_leg"))
    assert out["action"] == "resumed" and env.resumes[-1]["api_base_url"] == "http://legacy/api/v1"
    # a failed legacy rerun afterwards relabels the parked row and never resumes
    env.store._execute(
        "INSERT INTO test_runs (run_id, doc_id, revision_no, status, triggered_via, runner_id, created_at) "
        "VALUES ('trun_leg2', ?, 2, 'failed', 'token', 'u', '2026-09-27T01:00:00+09:00')", [TS_ID])
    before = len(env.resumes)
    assert env.svc.continue_chain_after_test_gate(env.docs[TS_ID])["action"] == "gate_blocked"
    assert len(env.resumes) == before and env.relabels[-1][1] == "test_gate_blocked"


def test_manned_run_is_not_a_chain(env):
    env.chain_token = {"issued_to": "u", "continuation_target_seq": None}
    _submit(env, [{"case_id": "TC-001", "status": "PASS"}, {"case_id": "TC-002", "status": "PASS"}])
    assert env.docs[TSR_ID]["doc_review_status"] != "approved"   # a human keeps the TSR gate
    assert env.svc.continue_chain_after_test_gate(env.docs[TS_ID])["action"] == "not_chain"


# ── 5. Structured view model, mentions, stop codes, inbox ───────────────────────


def test_describe_test_document_for_ts_and_tsr(env):
    view = env.svc.describe_test_document(env.docs[TS_ID])
    assert view["contract_version"] == 2 and [c["case_id"] for c in view["cases"]] == ["TC-001", "TC-002", "TC-003"]
    assert view["latest_result"] is None and view["errors"] == []
    _submit(env, [{"case_id": "TC-001", "status": "PASS"}])
    tsr_view = env.svc.describe_test_document(env.docs[TSR_ID])
    assert tsr_view["contract_version"] == 2
    assert tsr_view["report"]["overall"] == "NOT_RUN" and tsr_view["gate"]["passed"] is False
    env.contents[TS_ID] = LEGACY_TS
    assert env.svc.describe_test_document(env.docs[TS_ID])["contract_version"] == 1


def test_test_run_mention_follows_the_contract(env):
    doc = env.docs[TS_ID]
    spec_mention = env.svc._build_test_run_mention(
        doc=doc, api_base_url="http://h/api/v1", raw_token="RAW", continuous=True, locale="en",
        specification=True,
    )
    assert '"action": "test_run"' in spec_mention and '"results"' in spec_mention
    assert "junit_xml" in spec_mention and "Never report PASS for a case you did not verify" in spec_mention
    legacy_mention = env.svc._build_test_run_mention(
        doc=doc, api_base_url="http://h/api/v1", raw_token="RAW", continuous=True, locale="en",
    )
    assert '"results"' not in legacy_mention and "executes the TS server-side" in legacy_mention


def test_ts_authoring_guide_defaults_to_the_specification_in_every_locale():
    from modules.flow_gate.services import mention_service

    for locale in ("ko", "en", "ja"):
        text = mention_service._ts_authoring_section(locale)
        assert "test_contract_version: 2" in text
        assert "- expected:" in text and "- required:" in text
        # the legacy executable grammar is still there, after the specification
        assert text.index("test_contract_version: 2") < text.index("cmd")


def test_test_gate_stop_codes_are_resumable_and_keep_the_chain(monkeypatch):
    from modules.flow_gate.services.ai_invoke import finalize, runtime

    assert {"test_run_pending", "test_gate_blocked"} <= runtime.RESUMABLE_STOP_CODES
    assert "test_run_pending" not in runtime.NOTIFY_STOP_CODES
    assert "test_gate_blocked" not in runtime.NOTIFY_STOP_CODES
    assert "resumes on its own" in finalize._stop_reason_text("test_run_pending", {})
    assert "test_run_pending" in finalize._CHAIN_KEEPING_STOPS

    calls = []
    from modules.flow_gate.services import test_run_service

    monkeypatch.setattr(test_run_service, "resume_test_gate_chain_for_hop",
                        lambda doc_id, **kw: calls.append((doc_id, kw)) or {"action": "resumed"})
    finalize._auto_resume_test_gate_chain({"stop_code": "question_pending", "test_gate_doc_id": TS_ID})
    finalize._auto_resume_test_gate_chain({"stop_code": "test_run_pending"})
    assert calls == []
    finalize._auto_resume_test_gate_chain({
        "stop_code": "test_run_pending", "test_gate_doc_id": TS_ID,
        "api_base_url": "http://x/api/v1", "continuation_locale": "ja",
    })
    assert calls == [(TS_ID, {"api_base_url": "http://x/api/v1", "locale": "ja"})]


def test_the_handing_hop_parks_the_ordinary_durable_row_with_its_chain(monkeypatch):
    """The inbox tag → finalize → ai_invoke_paused_chains path the resume later consumes."""
    from modules.flow_gate.db import ai_invoke_paused_chains as db_paused
    from modules.flow_gate.services.ai_invoke import finalize

    run = {
        "run_id": "aiv_hop", "group_id": GROUP, "doc_ref": f"{GROUP}.0001-R", "mode": "continuous",
        "issued_to": "usr_admin", "end_reason": "exited", "inbox_stop_code": "test_run_pending",
        "continuation_target_seq": 14, "docs_target": 1, "docs_reached": 1,
        "chain_id": "chain-7", "chain_docs_target": 9, "chain_docs_reached": 4,
        "outcome": "complete", "finished_at": "2026-09-27T14:00:00+09:00",
    }
    code = finalize._resolve_stop_code(run, False)
    assert code == "test_run_pending" and finalize.is_resumable(code)
    run.update(stop_code=code, resumable=True)
    upserts = []
    monkeypatch.setattr(db_paused, "get_by_group", lambda _g: None)
    monkeypatch.setattr(db_paused, "upsert", lambda **kw: upserts.append(kw))
    monkeypatch.setattr(db_paused, "delete_by_group", lambda _g: upserts.append("deleted"))
    finalize._apply_stop_row(run, False)
    assert len(upserts) == 1 and upserts[0] != "deleted"
    row = upserts[0]
    assert (row["stop_kind"], row["stop_code"], row["stop_run_id"]) == ("system", "test_run_pending", "aiv_hop")
    assert (row["continuation_target_seq"], row["paused_by"]) == (14, "usr_admin")
    assert (row["chain_id"], row["chain_docs_target"], row["chain_docs_reached"]) == ("chain-7", 9, 4)


def test_mark_chain_stop_carries_the_test_gate_document(monkeypatch):
    from modules.flow_gate.services.ai_invoke import finalize

    live = {"run_id": "aiv_1"}

    class _Svc:
        def _active_run_for_group(self, _g):
            return live

    monkeypatch.setattr(finalize, "_svc", lambda: _Svc())
    assert finalize.mark_chain_stop(GROUP, "test_run_pending", None, extra={"test_gate_doc_id": TS_ID})
    assert live["inbox_stop_code"] == "test_run_pending" and live["test_gate_doc_id"] == TS_ID


def test_inbox_spec_submission_is_validated_before_numbering():
    from modules.flow_gate.api import inbox_routes

    assert inbox_routes._spec_ts_submission_failure(LEGACY_TS) is None
    assert inbox_routes._spec_ts_submission_failure(SPEC) is None
    resp = inbox_routes._spec_ts_submission_failure(SPEC.replace("- category: negative", "- category: x"))
    assert resp is not None and resp.status_code == 400
    body = json.loads(resp.body)
    assert body["error"]["code"] == "invalid_spec"
    assert "category must be one of" in body["error_message"]


def _token(target_seq):
    return {
        "token_id": "tok-spec", "project": "flowgate", "issued_to": "usr_admin", "group_id": GROUP,
        "action_scope": "test_run", "doc_ref": TS_ID, "dry_run_count": 0,
        "continuation_target_seq": target_seq, "continuation_review_mode": 0,
        "continuation_locale": "ko",
    }


@pytest.mark.parametrize(
    "target, results, stop",
    [
        (14, [{"case_id": "TC-001", "status": "PASS"}, {"case_id": "TC-002", "status": "PASS"}], "test_run_pending"),
        (14, [{"case_id": "TC-001", "status": "FAIL"}], "test_gate_blocked"),
    ],
)
def test_inbox_test_run_records_spec_results_and_tags_the_chain(env, monkeypatch, target, results, stop):
    from unittest.mock import MagicMock

    from inbox_client import post_inbox
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import ai_invoke_service

    _chain(env, target_seq=target, next_incomplete=12, row=False)
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: _token(target))
    monkeypatch.setattr(env.svc, "token_can_run_tests", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda doc_id: env.docs.get(doc_id))
    consume = MagicMock()
    monkeypatch.setattr(inbox_routes.token_service, "consume", consume)
    marks = []
    monkeypatch.setattr(ai_invoke_service, "mark_chain_stop",
                        lambda group, code, detail=None, **kw: marks.append((group, code, kw)) or True)

    resp = post_inbox({"action": "test_run", "project": "flowgate", "doc_id": TS_ID,
                       "results": results, "overall": "PASS"})
    data = resp.json()
    assert resp.status_code == 201, data
    assert data["contract_version"] == 2 and data["continuation_stop_code"] == stop
    assert data["overall"] == ("PASS" if stop == "test_run_pending" else "FAIL")
    consume.assert_called_once()
    assert marks[-1][0] == GROUP and marks[-1][1] == stop
    assert marks[-1][2]["extra"] == {"test_gate_doc_id": TS_ID}


def test_inbox_test_run_invalid_results_do_not_consume_the_token(env, monkeypatch):
    from unittest.mock import MagicMock

    from inbox_client import post_inbox
    from modules.flow_gate.api import inbox_routes

    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: _token(14))
    monkeypatch.setattr(env.svc, "token_can_run_tests", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda doc_id: env.docs.get(doc_id))
    consume = MagicMock()
    monkeypatch.setattr(inbox_routes.token_service, "consume", consume)
    resp = post_inbox({"action": "test_run", "project": "flowgate", "doc_id": TS_ID,
                       "results": [{"case_id": "TC-001", "status": "GREEN"}]})
    assert resp.status_code == 422 and resp.json()["error"] == "invalid_results"
    consume.assert_not_called()
    assert env.db_test_runs.list_by_doc(TS_ID) == []


def test_ui_results_route_records_and_reports(env, monkeypatch):
    from starlette.requests import Request

    from modules.flow_gate.api.v1 import test_run_routes

    monkeypatch.setattr(test_run_routes, "verify_bearer",
                        lambda _r: {"_is_user_jwt": True, "issued_to": "usr_admin", "is_admin": 1})
    monkeypatch.setattr(test_run_routes.db_docs, "get_by_id", lambda doc_id: env.docs.get(doc_id))
    request = Request({"type": "http", "headers": [(b"x-locale", b"en")], "method": "POST", "path": "/"})
    body = test_run_routes.TestResultsBody(
        doc_id=TS_ID, results=[{"case_id": "TC-001", "status": "PASS"}, {"case_id": "TC-002", "status": "PASS"}],
        overall="FAIL",
    )
    resp = test_run_routes.post_test_results(body, request)
    data = json.loads(resp.body)
    assert resp.status_code == 201
    assert data["overall"] == "PASS" and data["gate_passed"] is True and data["tsr_doc_id"] == TSR_ID
    assert [c["case_status"] for c in data["cases"]] == ["PASS", "PASS", "NOT_RUN"]


def test_structured_editor_route_saves_through_the_content_path(env, monkeypatch):
    from modules.flow_gate.documents.routers import documents as documents_router

    saved = {}
    monkeypatch.setattr(documents_router.document_service, "get_document", lambda doc_id: env.docs.get(doc_id))
    monkeypatch.setattr(documents_router, "update_document_content",
                        lambda doc_id, body, user, request=None: saved.update(doc_id=doc_id, content=body.content)
                        or {"data": {"doc_id": doc_id}, "content": body.content})
    env.contents[TS_ID] = "---\nproject: flowgate\ntype: TS\n---\n"
    cases = spec.parse_spec(SPEC)["cases"]
    body = documents_router.TestSpecSaveBody(cases=cases, title="권한 시험사양", locale="ko")
    documents_router.save_test_spec(TS_ID, body, current_user={"user_id": "usr_admin"})
    assert saved["doc_id"] == TS_ID
    assert saved["content"].startswith("---\nproject: flowgate\ntype: TS\ntest_contract_version: 2\n---\n")
    assert spec.parse_spec(saved["content"])["cases"] == cases

    bad = documents_router.TestSpecSaveBody(cases=[{**cases[0], "category": "happy"}], title="t")
    with pytest.raises(HTTPException) as exc:
        documents_router.save_test_spec(TS_ID, bad, current_user={"user_id": "usr_admin"})
    assert exc.value.status_code == 422 and exc.value.detail["error"] == "invalid_spec"

    env.contents[TS_ID] = LEGACY_TS  # an executable TS is never re-interpreted
    with pytest.raises(HTTPException) as exc:
        documents_router.save_test_spec(TS_ID, body, current_user={"user_id": "usr_admin"})
    assert exc.value.status_code == 409


def test_work_plan_keeps_ts_tsr_two_document_set_without_tsc():
    from modules.flow_gate.documents import constants

    assert constants.WORK_PLAN_PAIR_MAP.get("TS") == "TSR"
    flat = json.dumps({k: sorted(v) if isinstance(v, (set, frozenset)) else v
                       for k, v in vars(constants).items() if k.isupper()}, default=str)
    assert "TSC" not in flat
