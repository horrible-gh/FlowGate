"""flowgate.default.0441 T0004 item 3 + item 4 — the server refuses manual TSR authoring.

B0001 / NR0003 §2: while the workflow head is a still-empty TSR slot, the action bar on a
non-TS tab offered [Create Empty Doc], which called ``/documents/next-empty`` with
``type_code=TSR``. The only type check there was ``head_type == requested_type`` — and in
this state both are TSR — so the request succeeded: a number was reserved, a Markdown file
was written, a document row was created and the workflow slot the test run was going to
fill was taken by a hand-written document. The inbox had the same shape of hole
(``expected_head_type == submitted_type`` passes), and ``advance_workflow`` fell through to
an ordinary ``action_scope='new'`` token whose only meaning is "go write that document".

A TSR is assembled by ``test_run_service.assemble_tsr`` out of a finished run. That is the
whole policy, and it now has one name — ``documents.constants.SERVER_ASSEMBLED_DOC_TYPES``
— read by every creation gate. These tests pin each gate separately, because the point of
the fix is that they no longer depend on one another.

Every refusal case here also asserts the ABSENCE of the side effects: a rejected request
must leave no document number, no file, no row and no slot change behind. Each is paired
with a positive control so a MainPanel-style "refuses everything" regression cannot pass.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

try:  # pragma: no cover — depends on whether this checkout has a local server/.env
    import config  # noqa: F401 — importing runs config.Settings()
except Exception:  # noqa: BLE001
    # config.Settings() reads server/.env, and the repo ships only .env.sample, so a plain
    # checkout cannot import anything that touches config — the same collection error
    # tests/test_continuous_routes_0086.py hits here today. Two of the four gates below live
    # in route modules that do import it. Fill in exactly .env.sample's values, and only for
    # keys that are genuinely absent: an environment that already answered keeps its answer
    # (this branch never even runs there, because the import above succeeds).
    os.environ.setdefault("ALLOWED_ORIGIN", "")
    os.environ.setdefault("CONTEXT", "/flowgate")
    os.environ.setdefault("DB_TYPE", "sqlite3")

from inbox_client import post_inbox  # noqa: E402 — needs the sys.path insert above

GROUP = "flowgate.default.0441"
PREV_DOC = "flowgate.default.0441.0004-TS"


# ── The policy itself ────────────────────────────────────────────────────────────────


def test_one_name_is_read_by_every_holder_of_the_policy():
    """The three places that used to spell "TSR" out separately now read one set.

    NR0003 §2 traced the incident to the fact that ``SERVER_ASSEMBLED_REPORT_TYPES`` existed
    and was consulted when BUILDING a sequence row, while the creation gates each carried
    their own unrelated type list. Identity (``is``), not equality: a copy that happens to
    hold the same value today is exactly what drifted last time.
    """
    from modules.flow_gate.documents import constants
    from modules.flow_gate.services import work_plan_sequence_service, workflow_decision_service

    assert constants.SERVER_ASSEMBLED_DOC_TYPES == frozenset({"TSR"})
    assert workflow_decision_service.SERVER_ASSEMBLED_REPORT_TYPES is constants.SERVER_ASSEMBLED_DOC_TYPES
    assert work_plan_sequence_service.SERVER_ASSEMBLED_REPORT_TYPES is constants.SERVER_ASSEMBLED_DOC_TYPES
    assert constants.WORK_PLAN_LOCKED_TYPES is constants.SERVER_ASSEMBLED_DOC_TYPES

    assert constants.is_server_assembled_type("TSR") is True
    assert constants.is_server_assembled_type("  tsr ") is True
    assert constants.is_server_assembled_type("TS") is False
    assert constants.is_server_assembled_type("TR") is False
    assert constants.is_server_assembled_type(None) is False


# ── Gate 1: POST /documents/next-empty ───────────────────────────────────────────────


class _Store:
    @contextmanager
    def transaction(self):
        yield


def _next_empty_spies(monkeypatch):
    """Spy on every side effect the route can have, without letting any of them run."""
    from modules.flow_gate.documents.routers import documents as routes
    from modules.flow_gate.db import connection as db_connection
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.workflow import pipeline_service

    spies = {
        "get_document": MagicMock(return_value={
            "doc_id": PREV_DOC, "project_id": "flowgate", "group_id": GROUP,
        }),
        "reserve": MagicMock(return_value="0005-TSR"),
        "create": MagicMock(return_value={
            "doc_id": f"{GROUP}.0005-TSR", "project_id": "flowgate",
            "group_id": GROUP, "type_code": "TSR", "doc_review_status": None,
        }),
        "register": MagicMock(),
        "transition": MagicMock(),
    }
    monkeypatch.setattr(routes.document_service, "get_document", spies["get_document"])
    monkeypatch.setattr(routes.numbering_service, "reserve_document", spies["reserve"])
    monkeypatch.setattr(routes.document_service, "create_document", spies["create"])
    monkeypatch.setattr(pipeline_service, "register_workflow_result", spies["register"])
    monkeypatch.setattr(pipeline_service, "transition_document_review", spies["transition"])
    monkeypatch.setattr(db_connection, "get_store", lambda: _Store())
    monkeypatch.setattr(routes, "_reject_if_group_ai_running", lambda _doc: None)
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda _id: {"id": 7})
    monkeypatch.setattr(
        db_wfseq, "get_effective_head",
        lambda _sid: {"id": 70, "type": "TSR", "result_doc_id": None,
                      "result_doc_review_status": None},
    )
    return routes, spies


def test_next_empty_refuses_a_tsr_even_when_the_head_is_tsr(monkeypatch):
    from fastapi import HTTPException

    routes, spies = _next_empty_spies(monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        routes.create_next_empty_document(
            routes.NextEmptyDocumentCreate(
                project_id="flowgate", group_id=GROUP, prev_doc_id=PREV_DOC,
                type_code="TSR", title="hand-written test report",
            ),
            current_user={"user_id": "usr_test"},
        )

    assert excinfo.value.status_code == 422
    assert "TSR" in str(excinfo.value.detail)
    # The refusal lands before ANY of it: no number, no document, no slot registration.
    spies["reserve"].assert_not_called()
    spies["create"].assert_not_called()
    spies["register"].assert_not_called()
    spies["transition"].assert_not_called()
    # It lands before the head is even consulted, so a matching head cannot excuse it —
    # which is exactly how the request used to get through.
    spies["get_document"].assert_not_called()


def test_next_empty_refuses_the_lowercase_spelling_too(monkeypatch):
    from fastapi import HTTPException

    routes, spies = _next_empty_spies(monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        routes.create_next_empty_document(
            routes.NextEmptyDocumentCreate(
                project_id="flowgate", group_id=GROUP, prev_doc_id=PREV_DOC,
                type_code="tsr", title="hand-written test report",
            ),
            current_user={"user_id": "usr_test"},
        )

    assert excinfo.value.status_code == 422
    spies["reserve"].assert_not_called()


def test_next_empty_control_an_ordinary_type_still_reaches_numbering(monkeypatch):
    """Positive control: the guard is about the TYPE, not about next-empty as a whole."""
    from fastapi import HTTPException

    routes, spies = _next_empty_spies(monkeypatch)
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    monkeypatch.setattr(
        db_wfseq, "get_effective_head",
        lambda _sid: {"id": 70, "type": "DS", "result_doc_id": None,
                      "result_doc_review_status": None},
    )
    # Stop the run right after the reservation — this control is about reaching it at all.
    monkeypatch.setattr(
        routes.numbering_service, "reserve_document",
        MagicMock(side_effect=ValueError("stop after reservation")),
    )

    with pytest.raises(HTTPException) as excinfo:
        routes.create_next_empty_document(
            routes.NextEmptyDocumentCreate(
                project_id="flowgate", group_id=GROUP, prev_doc_id=PREV_DOC,
                type_code="DS", title="ordinary empty document",
            ),
            current_user={"user_id": "usr_test"},
        )

    assert excinfo.value.status_code == 400  # numbering ValueError, i.e. we got past the gate
    spies["get_document"].assert_called_once()


# ── Gate 2: POST /inbox with action=new ──────────────────────────────────────────────


def _inbox_body(doc_type: str, *, dry_run: bool = False) -> dict:
    return {
        "action": "new",
        "project": "flowgate",
        "module": "default",
        "group_name": GROUP,
        "prev_doc_id": PREV_DOC,
        "doc_type": doc_type,
        "title": "server assembled guard",
        "content": "body",
        "dry_run": dry_run,
    }


def _patch_inbox(monkeypatch, head_type: str):
    from modules.flow_gate.api import inbox_routes

    token = {
        "token_id": "tok-0441",
        "project": "flowgate",
        "issued_to": "worker-0441",
        # 0492 T0018: `group_id` is a real tokens column (migration 075a) and inbox
        # Step 3 now compares it as the `group` axis.
        "group_id": GROUP,
        "action_scope": "new",
        "doc_ref": PREV_DOC,
        "dry_run_count": 0,
    }
    monkeypatch.setattr(inbox_routes, "_normalize_group_name", lambda _p, _m, g: g)
    monkeypatch.setattr(inbox_routes, "_normalize_doc_id", lambda _g, d: d)
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: token)
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes, "_is_valid_doc_type", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.template_provision, "is_design_type", lambda _t: False)
    monkeypatch.setattr(inbox_routes, "_disposed_group_fail", lambda *_a, **_k: None)
    monkeypatch.setattr(inbox_routes, "_resolve_group", lambda *_a, **_k: {"group_id": GROUP})
    monkeypatch.setattr(
        inbox_routes.db_docs, "get_by_id",
        lambda doc_id: {"doc_id": doc_id, "doc_review_status": "approved"},
    )
    monkeypatch.setattr(inbox_routes, "_find_body_twin", lambda *_a, **_k: None)
    monkeypatch.setattr(inbox_routes.tr_scope_service, "evaluate", lambda **_k: None)
    monkeypatch.setattr(
        inbox_routes.db_wfseq, "get_pending_head_by_group",
        MagicMock(return_value={"id": 70, "type": head_type}),
    )
    spies = {
        "reserve": MagicMock(),
        "create": MagicMock(),
        "consume": MagicMock(),
        "increment": MagicMock(),
    }
    monkeypatch.setattr(inbox_routes.numbering_service, "reserve_document", spies["reserve"])
    monkeypatch.setattr(inbox_routes.db_docs, "create", spies["create"])
    monkeypatch.setattr(inbox_routes.token_service, "consume", spies["consume"])
    monkeypatch.setattr(inbox_routes.token_service, "increment_dry_run", spies["increment"])
    return spies


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("doc_type", ["TSR", "tsr"])
def test_inbox_new_tsr_is_refused_with_no_side_effects(monkeypatch, doc_type, dry_run):
    """head=TSR and submitted=TSR agreed, so the Step 5.8 head guard let this through."""
    spies = _patch_inbox(monkeypatch, "TSR")

    response = post_inbox(_inbox_body(doc_type, dry_run=dry_run))
    payload = response.json()

    assert response.status_code == 409, response.text
    assert payload["ok"] is False
    assert "TSR" in payload["error_message"]
    spies["reserve"].assert_not_called()
    spies["create"].assert_not_called()
    spies["consume"].assert_not_called()
    # A dry run is a preview of the same verdict, so it must not spend a dry-run either.
    spies["increment"].assert_not_called()


@pytest.mark.parametrize("locale_header", ["ko", "en", "ja"])
def test_inbox_new_tsr_refusal_speaks_the_workers_locale(monkeypatch, locale_header):
    spies = _patch_inbox(monkeypatch, "TSR")

    response = post_inbox(_inbox_body("TSR"), headers={"x-locale": locale_header})

    assert response.status_code == 409
    message = response.json()["error_message"]
    assert message.strip(), "a refusal with no sentence is the silent-return this T forbids"
    assert "TSR" in message
    spies["reserve"].assert_not_called()


def test_inbox_control_a_matching_non_assembled_head_still_passes_dry_run(monkeypatch):
    """Positive control: the same submission shape with head=T is accepted as before."""
    spies = _patch_inbox(monkeypatch, "T")

    response = post_inbox(_inbox_body("T", dry_run=True))
    payload = response.json()

    assert response.status_code == 200, response.text
    assert payload["ok"] is True
    assert "workflow_head" in payload["would_register"]["checks_passed"]
    spies["increment"].assert_called_once_with("tok-0441")
    spies["reserve"].assert_not_called()


# ── Gate 3: advance_workflow's ordinary 'new' hand-off ───────────────────────────────


def _patch_advance(monkeypatch, *, head_type="TSR", predecessor=None):
    from modules.flow_gate.services import workflow_decision_service as wds

    root = {
        "doc_id": f"{GROUP}.0001-R", "project_id": "flowgate",
        "group_id": GROUP, "module": "default", "seq": 1, "type_code": "R",
    }
    docs = {root["doc_id"]: root}
    if predecessor is not None:
        docs[predecessor["doc_id"]] = predecessor

    monkeypatch.setattr(wds.db_documents, "get_by_id", lambda _id: docs.get(_id))
    monkeypatch.setattr(wds.db_wfseq, "get_sequence_for_member_doc", lambda _id: {"id": 7})
    monkeypatch.setattr(wds, "_auto_complete_instruction_heads", lambda **_k: 0)
    monkeypatch.setattr(
        wds.db_wfseq, "get_effective_head",
        lambda _sid: {"id": 70, "type": head_type, "label": head_type, "item_seq": 6,
                      "result_doc_id": None, "result_doc_review_status": None},
    )
    monkeypatch.setattr(
        wds.db_wfseq, "get_predecessor_result_doc_id",
        lambda _sid, _hid: (predecessor or {}).get("doc_id"),
    )
    from modules.flow_gate.db import tokens as db_tokens
    monkeypatch.setattr(db_tokens, "get_unconsumed_by_doc_ref", lambda _ref: None)

    issue = MagicMock()
    monkeypatch.setattr(wds.token_service, "issue", issue)
    return wds, root, issue


@pytest.mark.parametrize("continuous", [False, True])
def test_advance_refuses_a_server_assembled_head_instead_of_minting_a_new_token(
    monkeypatch, continuous,
):
    """No approved TS behind the head → there is nothing to hand off and nothing to write.

    Before 0441 this fell through to ``token_service.issue(action_scope='new')``, which is
    the managed [Copy mention] / [Invoke AI] path NR0003 §2 named as the second route to a
    hand-written TSR.
    """
    wds, root, issue = _patch_advance(monkeypatch, head_type="TSR", predecessor=None)

    with pytest.raises(ValueError) as excinfo:
        wds.advance_workflow(
            doc_id=root["doc_id"], issued_to="user-1",
            api_base_url="http://x/api/v1", locale="ko", continuous=continuous,
            continuation_target_seq=9 if continuous else None,
        )

    assert str(excinfo.value).startswith("server_assembled_head:TSR:")
    issue.assert_not_called()


def test_advance_control_a_continuous_chain_with_an_approved_ts_still_gets_a_test_run_token(
    monkeypatch,
):
    """Positive control — group 0150's hand-off must survive the new guard untouched."""
    from modules.flow_gate.services import test_run_service

    ts_doc = {
        "doc_id": PREV_DOC, "project_id": "flowgate", "group_id": GROUP,
        "type_code": "TS", "doc_review_status": "approved",
    }
    wds, root, issue = _patch_advance(monkeypatch, head_type="TSR", predecessor=ts_doc)
    monkeypatch.setattr(
        test_run_service, "issue_test_run_request",
        lambda **kwargs: {
            "doc_ref": kwargs["doc_id"], "action_scope": "test_run", "group_id": GROUP,
            "token": "RAW", "token_id": "tok-7", "expires_at": "2026-08-24T00:00:00+09:00",
            "scratch_dir": "scratch", "mention": "MENTION",
        },
    )

    adv = wds.advance_workflow(
        doc_id=root["doc_id"], issued_to="user-1", api_base_url="http://x/api/v1",
        locale="ko", continuous=True, continuation_target_seq=9,
    )

    assert adv["action_scope"] == "test_run"
    assert adv["doc_ref"] == PREV_DOC
    issue.assert_not_called()  # the test_run token comes from test_run_service, not here


def test_advance_control_an_ordinary_head_still_mints_a_new_token(monkeypatch):
    wds, root, issue = _patch_advance(monkeypatch, head_type="DS")
    issue.return_value = {"raw_token": "RAW", "scratch_dir": "scratch",
                          "token_id": "tok-1", "expires_at": "2026-08-24T00:00:00+09:00"}

    try:
        wds.advance_workflow(
            doc_id=root["doc_id"], issued_to="user-1",
            api_base_url="http://x/api/v1", locale="ko", continuous=False,
        )
    except Exception:  # noqa: BLE001 — later steps need more of the DB than this test wires
        pass

    issue.assert_called_once()
    assert issue.call_args.kwargs["action_scope"] == "new"


def test_advance_route_maps_the_refusal_to_409_with_a_stable_code(monkeypatch):
    """The screen has to be able to say WHY, so the code and message survive the boundary."""
    from modules.flow_gate.api.v1 import workflow_decision_routes as wdr

    class _FakeRequest:
        def __init__(self):
            self.headers = {"x-locale": "ko"}
            self.base_url = "http://localhost/"

    monkeypatch.setattr(wdr, "verify_bearer", lambda _r: {"issued_to": "pm-1"})
    monkeypatch.setattr(
        wdr._db_documents, "get_by_id",
        lambda _id: {"group_id": GROUP, "project_id": "flowgate"},
    )
    monkeypatch.setattr(wdr._process_service, "is_group_disposed", lambda _g: False)

    def _raise(**_kwargs):
        raise ValueError(f"server_assembled_head:TSR:{GROUP}.0001-R")

    monkeypatch.setattr(wdr, "advance_workflow", _raise)

    response = wdr.post_workflow_advance_rpc(
        wdr.AdvanceBodyRequest(doc_id=f"{GROUP}.0001-R"), _FakeRequest(),
    )

    assert response.status_code == 409
    import json as _json
    payload = _json.loads(bytes(response.body).decode("utf-8"))
    assert payload["error"] == "server_assembled_head"
    assert payload["head_type"] == "TSR"
    assert "test run" in payload["detail"]


# ── Gate 4: POST /token/issue with the ordinary 'new' scope ──────────────────────────


def _issue_body(**over):
    from modules.flow_gate.api.token_routes import TokenIssueRequest

    kwargs = dict(project="flowgate", module="default", group="0441",
                  doc_ref=None, action_scope="new")
    kwargs.update(over)
    return TokenIssueRequest(**kwargs)


def _patch_token_routes(monkeypatch, head_type):
    from modules.flow_gate.api import token_routes

    monkeypatch.setattr(token_routes.db_projects, "get_by_id", lambda _p: {"project_id": "flowgate"})
    monkeypatch.setattr(token_routes, "_resolve_group", lambda *_a, **_k: GROUP)
    monkeypatch.setattr(token_routes, "has_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(
        token_routes.db_wfseq, "get_pending_head_by_group",
        lambda _g, _p: None if head_type is None else {"id": 70, "type": head_type},
    )
    issue = MagicMock(side_effect=RuntimeError("token_service.issue must not be reached"))
    monkeypatch.setattr(token_routes.token_service, "issue", issue)
    return token_routes, issue


def test_token_issue_refuses_a_new_scope_while_the_head_is_server_assembled(monkeypatch):
    from fastapi import HTTPException

    token_routes, issue = _patch_token_routes(monkeypatch, "TSR")

    class _FakeRequest:
        headers = {"x-locale": "ko"}

    with pytest.raises(HTTPException) as excinfo:
        token_routes._issue_token(_issue_body(), _FakeRequest(), {"user_id": "pm-1"})

    assert excinfo.value.status_code == 409
    assert "TSR" in str(excinfo.value.detail)
    issue.assert_not_called()


def test_token_issue_control_an_ordinary_head_is_untouched(monkeypatch):
    token_routes, issue = _patch_token_routes(monkeypatch, "DS")

    class _FakeRequest:
        headers = {"x-locale": "ko"}

    # The control proves the guard let the request THROUGH: issue is the next call, and the
    # sentinel it raises is not the 409 above.
    with pytest.raises(RuntimeError, match="must not be reached"):
        token_routes._issue_token(_issue_body(), _FakeRequest(), {"user_id": "pm-1"})

    issue.assert_called_once()


def test_token_issue_refuses_a_new_scope_even_with_a_continuation_target_seq(monkeypatch):
    """0441 TR0005 rev10 — closing the direct-issue bypass NR0003-style review found.

    Setting `continuation_target_seq` flips `is_continuous=true`, which used to exempt the
    request from this guard entirely on the theory that a continuous chain reaching a TSR
    head is always handed a test_run-scoped token by advance_workflow instead. But
    advance_workflow mints that token via a direct in-process call, never through this HTTP
    route — so a direct caller could set action_scope='new' and any continuation_target_seq
    to walk straight past the old guard to `token_service.issue(action_scope='new')`, minting
    exactly the hand-written-TSR token this gate exists to deny. This must still 409.
    """
    from fastapi import HTTPException

    token_routes, issue = _patch_token_routes(monkeypatch, "TSR")

    class _FakeRequest:
        headers = {"x-locale": "ko"}

    body = _issue_body(continuation_target_seq=9)

    with pytest.raises(HTTPException) as excinfo:
        token_routes._issue_token(body, _FakeRequest(), {"user_id": "pm-1"})

    assert excinfo.value.status_code == 409
    assert "TSR" in str(excinfo.value.detail)
    issue.assert_not_called()


def test_token_issue_control_continuation_scope_on_an_ordinary_head_is_untouched(monkeypatch):
    """Positive control — continuation requests for a non-server-assembled head are unaffected."""
    token_routes, issue = _patch_token_routes(monkeypatch, "DS")

    class _FakeRequest:
        headers = {"x-locale": "ko"}

    body = _issue_body(continuation_target_seq=9)

    with pytest.raises(RuntimeError, match="must not be reached"):
        token_routes._issue_token(body, _FakeRequest(), {"user_id": "pm-1"})

    issue.assert_called_once()
