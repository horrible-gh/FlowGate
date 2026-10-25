"""flowgate.v02.0074 T#4 — a design card's 「설계 지시」 (DS) in the existing card run (NR0003 §4.4 (a)(b)(c), D0006 §8.1).

The work plan deploy writes a step's pre-instruction into a design card's DS line and adds no engine: the card run's
own instruction stage (``_instruction_job``) decides by the DS body. The 0056 cases of an N / T card, for D / P / L /
DB, on the fixtures of ``test_fg_instruction_stage_0056``:

- (a) a DS with a body: no instruction AI job; the DS is approved and the result stage starts with [DS] as its
  reference, writing the card's own design type;
- (b) instruction omitted: no instruction job; the result stage starts with the card's own type and no DS reference;
  the DS line and its document stay (same doc id, same bytes, not approved) — omission never deletes or remakes it;
- (c) the run's issuer may not approve: the DS is not approved, the result stage still starts with it.
"""
from __future__ import annotations

import pytest

from test_fg_instruction_stage_0056 import (  # noqa: F401 — fixtures used by name
    _card, _line, _one_job, card_approval_port, db_path, decided, env,
)
from test_fg_card_settings import _start
from test_fg_card_settings_review import _admin_and_reviewer, _review_status, _write_written

DESIGN_TYPES = ("D", "P", "L", "DB")


def _bytes(doc_id):
    from modules.flow_gate.db import documents as db_documents
    from flowgate.work_plan_deploy import legacy_adapter as legacy

    return legacy.file_of(db_documents.get_by_id(doc_id)).read_bytes()


@pytest.mark.parametrize("doc_type", DESIGN_TYPES)
def test_a_written_design_instruction_skips_the_instruction_ai_and_is_approved(env, decided, doc_type):
    _admin_and_reviewer(env)
    key = _card(env, doc_type)
    ds = _line(env, key, "instruction")
    _write_written(ds, "# 설계 지시\n\n승인 되었습니다\n")
    _start(env)
    job = _one_job(env)
    assert (job["stage"], job["ref_doc_ids"], job["result_doc_type"]) == ("result", [ds], doc_type)
    assert _review_status(ds) == "approved"


@pytest.mark.parametrize("written", [False, True])
@pytest.mark.parametrize("doc_type", DESIGN_TYPES)
def test_an_omitted_design_instruction_keeps_its_ds_and_runs_the_design_result(env, decided, doc_type, written):
    from modules.flow_gate.db import documents as db_documents

    _admin_and_reviewer(env)
    key = _card(env, doc_type, (("instruction", "omit"),))
    ds = _line(env, key, "instruction")
    if written:
        _write_written(ds, "# 설계 지시\n\n사람이 쓴 지시\n")
    before = _bytes(ds)
    _start(env)
    job = _one_job(env)
    assert (job["stage"], job["result_doc_type"], job["ref_doc_ids"]) == ("result", doc_type, None)
    assert _line(env, key, "instruction") == ds                         # the DS line and its document stay
    row = db_documents.get_by_id(ds)
    assert (row["type_code"], row["status"], row["doc_review_status"]) == ("DS", "draft", None)
    assert _bytes(ds) == before


@pytest.mark.parametrize("doc_type", DESIGN_TYPES)
def test_a_design_instruction_the_issuer_cannot_approve_is_left_to_the_card_approval(
        env, decided, monkeypatch, doc_type):
    from modules.flow_gate.workflow.routers import workflow

    _admin_and_reviewer(env)
    monkeypatch.setattr(workflow, "_get_user_permissions", lambda u: set())
    key = _card(env, doc_type)
    ds = _line(env, key, "instruction")
    _write_written(ds, "# 설계 지시\n\n승인 되었습니다\n")
    _start(env)
    job = _one_job(env)
    assert (job["stage"], job["ref_doc_ids"], job["result_doc_type"]) == ("result", [ds], doc_type)
    assert _review_status(ds) is None
