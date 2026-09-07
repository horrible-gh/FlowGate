"""Dedicated failure-origin review issuance and evidence prompt (0503 T0009)."""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.db import test_runs as db_test_runs
from modules.flow_gate.services import ai_invoke_service, token_service

CLASSIFICATIONS = ("product_defect", "test_defect", "hold")

# NR0003 §10-12, §14, §28: what a TS worker is told to do with each classification, once
# it has been picked (not what the classifier is asked to judge — see build_failure_origin_mention).
_REWORK_CONSTRAINT = {
    "product_defect": (
        "PRODUCT_DEFECT: modify the product source guided by the evidence below until it "
        "satisfies the original requirement. Do not weaken this TS's assert/expect to match "
        "the observed actual value — the case stays as originally specified."
    ),
    "test_defect": (
        "TEST_DEFECT: product source modification is prohibited for this rework. Fix the TS "
        "definition itself (assert/fixture/expectation) so it matches the original requirement, "
        "and record the reason for the change in the revised TS."
    ),
}


def _build_evidence(*, doc: dict, run: dict, items: list[dict]) -> dict:
    failed = next((item for item in items if item.get("result") in {"fail", "timeout"}), {})
    return {
        "run_id": run.get("run_id"),
        "TS doc/revision": f"{doc.get('doc_id')} / {run.get('revision_no')}",
        "case_no": failed.get("case_no"),
        "case_title": failed.get("case_title"),
        "assert_mode": failed.get("assert_mode"),
        "expected": failed.get("expect"),
        "actual": failed.get("actual"),
        "exit_code": failed.get("exit_code"),
        "output_tail": failed.get("output_tail"),
        "requirement": doc.get("triggered_by"),
        "related_T_TR": doc.get("prev_doc_id"),
    }


def build_failure_origin_mention(*, doc: dict, run: dict, items: list[dict],
                                 api_base_url: str, raw_token: str) -> str:
    evidence = _build_evidence(doc=doc, run=run, items=items)
    lines = "\n".join(f"- {key}: {value}" for key, value in evidence.items())
    return (
        "## Failure-origin review\n---\n"
        "Classify which side violates the original requirement; do not merely choose the "
        "fastest way to make tests green. Use exactly one canonical classification: "
        "product_defect, test_defect, or hold.\n\n"
        f"{lines}\n\n"
        "## Submit\n---\n"
        f"POST {api_base_url}/inbox\nAuthorization: Bearer {raw_token}\n\n"
        '{"action":"failure_origin_review","project":"'
        + str(doc.get("project_id") or "") + '","doc_id":"' + str(doc.get("doc_id") or "")
        + '","run_id":"' + str(run.get("run_id") or "")
        + '","classification":"product_defect","findings":[],"comment":""}\n'
    )


def build_rework_instruction(*, classification: str, doc: dict, run: dict, items: list[dict]) -> str:
    """The classification-specific handoff attached to a reopened TS (NR0003 §10-12).

    ``classification`` must be ``product_defect`` or ``test_defect`` — ``hold`` never reopens
    (T0009 §9), so it has no rework instruction to build.
    """
    constraint = _REWORK_CONSTRAINT[classification]
    evidence = _build_evidence(doc=doc, run=run, items=items)
    lines = "\n".join(f"- {key}: {value}" for key, value in evidence.items())
    return f"{constraint}\n\n{lines}"


def issue_failure_origin_review(*, doc: dict, run: dict, issued_to: str,
                                api_base_url: str, ai_run_id: Optional[str] = None) -> dict:
    if run.get("doc_id") != doc.get("doc_id") or run.get("status") != "failed":
        raise ValueError("failure_origin_target_mismatch")
    issue = token_service.issue(
        project=doc.get("project_id") or "",
        group_id=doc.get("group_id"),
        action_scope="failure_origin_review",
        doc_ref=doc.get("doc_id"),
        issued_to=issued_to,
        ai_run_id=ai_run_id,
        failure_origin_target_run_id=run["run_id"],
        failure_origin_before_marker=run.get("failure_origin_reviewed_at"),
    )
    return {
        "raw_token": issue["raw_token"], "token_id": issue["token_id"],
        "scratch_dir": issue["scratch_dir"], "target_run_id": run["run_id"],
        "before_marker": run.get("failure_origin_reviewed_at"),
        "mention": build_failure_origin_mention(
            doc=doc, run=run, items=db_test_runs.list_cases(run["run_id"]),
            api_base_url=api_base_url, raw_token=issue["raw_token"],
        ),
    }


def dispatch_failure_origin_review(*, doc: dict, run: dict, issued_to: str,
                                   api_base_url: str) -> dict:
    """Launch the dedicated classifier through the common provider/runtime machinery."""
    def _issue(ai_run_id: Optional[str] = None) -> dict:
        return issue_failure_origin_review(
            doc=doc, run=run, issued_to=issued_to, api_base_url=api_base_url,
            ai_run_id=ai_run_id,
        )

    return ai_invoke_service.start_run(
        project_id=doc.get("project_id") or "", module=doc.get("module"),
        group_id=doc.get("group_id") or "", doc_ref=doc.get("doc_id") or "",
        action_scope="failure_origin_review", mode="single",
        continuation_target_seq=None, continuation_review_mode=False,
        continuation_instruction_mode=None, continuation_locale=None,
        issued_to=issued_to, api_base_url=api_base_url,
        mention_builder=lambda _raw, _scratch: None, issue_builder=_issue,
    )
