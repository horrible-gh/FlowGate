"""0506 T0004 §11-16 — cross-surface sentinel regression.

TR0005 rev0 was rejected because the sentinel regression only covered the standard new
mention, the review mention, and the submit-help paths. T0004 §12 lists every worker-facing
surface that must be sentinel-free; this file adds the surfaces the first revision skipped:

  * build_mention(action_scope="edit") — the edit/rework hand-off (§13, the edit-specific
    guidance assertion T0004 gives as the worked example).
  * qa_service.build_ment_text — the QA ment_copy prompt.
  * q_answer_invoke_service.build_answer_mention — issue_answer_token feeds this to BOTH
    [copy mention] and [ask the AI to answer] (dispatch_answer_run calls issue_answer_token
    internally), so one sentinel assertion here covers the Q-answer copy AND invoke surfaces.
  * token_routes._build_conflict_mention — the git-conflict worker mention.

The inverse assertion (FLOWGATE_SCRATCH must still carry the real path into the CLI
subprocess env) already lives in test_worker_scratch_manifest_0475.py::
test_cli_env_is_fully_run_owned and is untouched by this file.
"""
from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("FLOWGATE_GIT_ENCRYPT_KEY", base64.b64encode(b"K" * 32).decode())
os.environ.setdefault("FLOWGATE_STORAGE_DIR", tempfile.mkdtemp(prefix="fg-scratch-sentinel-0506-"))

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api import token_routes  # noqa: E402
from modules.flow_gate.services import mention_service  # noqa: E402
from modules.flow_gate.services import q_answer_invoke_service  # noqa: E402
from modules.flow_gate.services import qa_service  # noqa: E402

SENTINEL = r"C:\FLOWGATE_SECRET_SCRATCH\TOKEN_123"


def test_edit_mention_is_sentinel_free_and_keeps_generic_doc_path_guidance():
    """T0004 §13: action_scope='edit' is the repeatedly-missed surface — the rejected/rework
    hand-off. The sentinel must not leak, but the doc_path XOR guidance itself must stay."""
    mention = mention_service.build_mention(
        project="test",
        module="none",
        group="0002",
        parent_type="D",
        parent_doc_number="D0004",
        parent_title="FlowGate API verification design",
        parent_doc_id="D0004",
        parent_canonical_doc_id="test.none.0002.0004-D",
        parent_revision_no=0,
        head_type="",
        head_status="",
        scratch_dir=SENTINEL,
        raw_token="raw-token",
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1",
        action_scope="edit",
    )
    assert mention is not None
    assert SENTINEL not in mention
    assert "inside this token's scratch directory" in mention
    assert "{SCRATCH}" in mention


def test_qa_ment_copy_is_sentinel_free():
    """qa_service.build_ment_text is the QA [멘트 복사] (ment_copy) prompt body."""
    text = qa_service.build_ment_text(
        q_doc_id="flowgate.default.0506.0001-Q",
        a_doc_id="flowgate.default.0506.0002-A",
        scratch_dir=SENTINEL,
        prev_doc_id="flowgate.default.0506.0000-R",
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1",
        raw_token="raw-token",
    )
    assert SENTINEL not in text


def test_q_answer_mention_is_sentinel_free_for_both_copy_and_invoke():
    """q_answer_invoke_service.build_answer_mention is the single builder issue_answer_token
    hands to BOTH [copy mention] and dispatch_answer_run's [ask the AI to answer] path (see
    the module docstring: "Both mint the same token and render the same mention, so the two
    paths cannot drift") — one sentinel assertion here proves both surfaces are path-free."""
    mention = q_answer_invoke_service.build_answer_mention(
        doc={
            "doc_id": "flowgate.default.0506.0001-R",
            "group_id": "",
            "project_id": "flowgate",
            "title": "Document lookup",
        },
        item={"id": 7, "seq": 1, "title": "Question", "body": "What changed?", "options": []},
        raw_token="raw-token",
        scratch_dir=SENTINEL,
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1",
    )
    assert SENTINEL not in mention


def test_conflict_mention_is_sentinel_free(monkeypatch):
    """token_routes._build_conflict_mention is the git-conflict worker mention."""
    def fake_list_conflicts(group_id, merge_id):
        return {
            "ok": True,
            "merge_id": merge_id,
            "branch": "group/branch",
            "base_branch": "main",
            "files": [],
            "kind": "merge",
            "tr_conflict": None,
        }

    monkeypatch.setattr(token_routes.git_service, "list_conflicts", fake_list_conflicts)

    mention = token_routes._build_conflict_mention(
        group_id="flowgate.default.0506",
        project_id="flowgate",
        merge_id=1,
        scratch_dir=SENTINEL,
        raw_token="raw-token",
        api_base_url="http://127.0.0.1:8088/flowgate/api/v1",
    )
    assert mention is not None
    assert SENTINEL not in mention
