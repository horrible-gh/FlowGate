"""flowgate.v02.0136 T0004: a document review loop's rework submission binds to its own hop token.

Regression for B0001 / NR0003 rev 2. A review-loop run keeps ``run["action_scope"]="review"``
(the oracle, the judge and the retry gates read it), but every token the run is handed --
the first hop's (``admission.start_run``), a stage transition's and a same-stage reissue's
(``worker._prepare_retry_token``) -- must move ``run["register_context"]`` and
``run["current_token_id"]`` with it. Before the fix the CLI ``/inbox`` edit boundary
(``agent_adapter.check_boundary`` -> ``worker._bind_register_context``) compared a correct edit
token with a context lazily seeded from ``run["action_scope"]`` and returned
``403 context_binding_mismatch axis=action`` -- for the rework hop of a review-first loop AND
for the very first hop of a loop that starts with rework. That second-stage rejection was also
never written to ``run["register_errors"]`` / ``register_context_failures``.

Real code: SQLite (all migrations), token_service, the /inbox router, agent_adapter.check_boundary,
admission.start_run / _register_run, worker._prepare_retry_token / _bind_register_context /
_adopt_continuation_token / _inbox_register / _absorb_binding_failure,
invoke_mention_service.issue_rework_request, workflow_decision_service.request_review, the card
document revision store and finalize._persist_register_context_failures.
Stubbed in ``world``: the admission prelude (provider list, group lease, git sync), the worker
THREAD body (no AI process), permission checks and notifications -- the same stubs the NR0003
isolated harness (appendix A) and the repository's admission tests use. Those tests check the
FUNCTION boundaries (binding, reissue, adoption); their reviewer verdict is emulated by stamping the
review token consumed and their rework is an ``edit_reason=worker_self`` submission.

``gated`` (rejection rej_01M6Q2T0KPHYGK8M) turns the real pieces back on for the PATH tests: the
real group lease (db.group_ai_leases, its in-memory pytest mirror with the same CAS contract) and
the real ``mutation_policy.assert_group_mutation_allowed`` owner match that ``env`` disables, the
real loop row (``_insert_document_review_loop``) and the real checkpoint transaction
(``review._checkpoint_document_review_loop`` -> ``_auto_reject`` -> ``transition_document_review``).
There an ``issues`` verdict is registered through ``POST /inbox action=review`` (dry_run receipt,
then the real submission), the hop end rejects the document, the rework hop answers that very
rejection with ``edit_reason=rejected`` and the next review hop gets its own token.
"""
import itertools
import os
from types import SimpleNamespace

os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from test_card_document_edit_0127 import env, F, D, HEADER  # noqa: F401
from modules.flow_gate.api import inbox_routes as inbox
from modules.flow_gate.db import documents, groups, tokens as db_tokens
from modules.flow_gate.db import group_ai_leases as db_leases
from modules.flow_gate.services import token_service, graph_source_hooks, ai_invoke_service as svc
from modules.flow_gate.services import register_binding
from modules.flow_gate.services.ai_invoke import worker, admission
from modules.flow_gate.storage import paths as storage_paths
from flowgate.card_document_edit import integration, legacy_edit, guards

# The real functions, captured before any fixture replaces them (``env`` stubs the mutation gate,
# ``world`` the lease and the loop row); ``gated`` puts them back.
_REAL_LEASE = {name: getattr(db_leases, name) for name in ("get_active", "acquire", "activate", "release")}
_REAL_GATE = guards.mutation_policy.assert_group_mutation_allowed
_REAL_INSERT_LOOP = svc._insert_document_review_loop

OTHER_GROUP = "flowgate.v02.0128"
OTHER_DOC = F + ".0003-NR"                 # another document of the SAME project/group
API = "http://localhost/flowgate/api/v1"
RUN_PREFIX = "aiv_20261106_136"
_RUN_SEQ = itertools.count(1)

LOOP = {
    "review_count": 2, "reviewer_provider_id": "prov-rev", "review_criteria": "document_type_default",
    "rework_provider_id": "prov-rw", "rework_timeout_sec": 1800, "rework_message": "fix every finding",
    "failure_restart_max_attempts": 0, "total_timeout_sec": 3600, "review_baseline_id": 0,
    "baseline_revision_no": 0, "starts_with_rework": False, "round_no": 1, "current_stage": "review",
    "attempts_used": 0,
}
NR_BODY = ("---\nproject: flowgate\nmodule: v02\ngroup: 0127\ntype: NR\ntarget_id: %s\n---\n\n"
           "# NR first submission\n\nbody\n" % D)


@pytest.fixture
def world(env, monkeypatch):
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "test")
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_test", "agent-test-pepper-0136")
    integration.register()
    monkeypatch.setattr(legacy_edit, "has_permission", lambda *a: True)
    monkeypatch.setattr(inbox, "has_permission", lambda *a, **k: True)
    monkeypatch.setattr(graph_source_hooks, "_fence_handler", None)
    from modules.flow_gate.services import node_title_hooks, provider_capability_service
    monkeypatch.setattr(node_title_hooks, "after_registration", lambda *a: None)
    app = FastAPI()
    app.include_router(inbox.router, prefix="/flowgate")
    client = TestClient(app)

    # Admission prelude -- same shape as the NR0003 harness / test_ai_invoke_review_admission_0544.
    monkeypatch.setattr(admission.ai_settings_service, "resolve_effective",
        lambda _p: {"providers": [{"id": "prov-rev", "name": "REV"}, {"id": "prov-rw", "name": "RW"}],
                    "source": "project", "registered_count": 2})
    monkeypatch.setattr(admission.ai_settings_service, "selectable_provider",
        lambda _p, pid, *_a, **_k: {"id": pid, "name": pid})
    from modules.flow_gate.db import ai_providers
    monkeypatch.setattr(ai_providers, "get_runnable_row", lambda pid, who=None: {"id": pid, "enabled": True})
    monkeypatch.setattr(provider_capability_service, "capability_finding", lambda *_a, **_kw: None)
    monkeypatch.setattr(admission.db_git, "get_config", lambda _p: None)
    monkeypatch.setattr(admission.git_service, "ensure_initial_group_source_sync",
                        lambda *_a, **_kw: {"reason": "already_synced"})
    released, started = [], []
    monkeypatch.setattr(admission.db_group_ai_leases, "get_active", lambda _g: None)
    monkeypatch.setattr(admission.db_group_ai_leases, "acquire", lambda **kw: {"run_id": kw["run_id"]})
    monkeypatch.setattr(admission.db_group_ai_leases, "release",
                        lambda *a, **k: released.append((a, k)))
    monkeypatch.setattr(admission.db_group_ai_leases, "activate", lambda g, r, *_a, **_k: {"run_id": r})
    monkeypatch.setattr(svc, "_worker", lambda run, chain, mention: started.append(run["run_id"]))
    monkeypatch.setattr(svc, "_insert_document_review_loop", lambda run: None)
    monkeypatch.setattr(svc, "request_run_scratch_sweep", lambda *_a, **_k: None)
    monkeypatch.setattr(svc, "_next_run_id", lambda: "%s%03d" % (RUN_PREFIX, next(_RUN_SEQ)))
    yield SimpleNamespace(client=client, monkeypatch=monkeypatch, env=env, released=released, started=started)
    with svc._runs_lock:
        for run_id in [k for k in svc._runs if str(k).startswith(RUN_PREFIX)]:
            svc._runs.pop(run_id, None)


@pytest.fixture
def gated(world):
    """``world`` with the real group lease, the real mutation gate, the real loop row and an issuer
    who may reject (``_auto_reject`` resolves the run issuer's own permissions)."""
    mp = world.monkeypatch
    for name in ("get_active", "acquire", "activate"):
        mp.setattr(db_leases, name, _REAL_LEASE[name])

    def release(*a, **k):
        world.released.append((a, k))
        return _REAL_LEASE["release"](*a, **k)
    mp.setattr(db_leases, "release", release)
    mp.setattr(guards.mutation_policy, "assert_group_mutation_allowed", _REAL_GATE)
    mp.setattr(svc, "_insert_document_review_loop", _REAL_INSERT_LOOP)
    mp.setattr(svc, "_broadcast", lambda *a, **k: None)
    for pid, name in (("prov-rev", "REV"), ("prov-rw", "RW")):     # the loop row's provider FKs
        world.env.db._execute(
            "INSERT INTO ai_providers (provider_id,name,exec_type,kind,enabled,created_at,updated_at) "
            "VALUES (?,?,'api','openai',1,'t','t')", [pid, name])
    world.env.db._execute("UPDATE users SET is_admin=1 WHERE user_id='user'")
    # A submitted document waiting for its review, the state an automatic rejection starts from.
    world.env.db._execute("UPDATE documents SET doc_review_status='pending_review' WHERE doc_id=?", [D])
    return world


# ── helpers ────────────────────────────────────────────────────────────────────────────

def post(world, raw, **over):
    body = {"action": "edit", "project": "flowgate", "module": "v02", "group_name": F, "doc_id": D,
            "expected_revision": documents.get_by_id(D)["revision_no"], "content": HEADER + "reworked\n",
            "edit_reason": "worker_self"}
    body.update(over)
    body = {k: v for k, v in body.items() if v is not None}
    r = world.client.post(API.replace("http://localhost", "") + "/inbox",
                          headers={"Authorization": "Bearer " + raw}, json=body)
    js = r.json()
    return r.status_code, js, ((js.get("error") or {}).get("details") or {})


def post_new(world, raw, **over):
    body = {"action": "new", "project": "flowgate", "module": "v02", "group_name": F, "prev_doc_id": D,
            "doc_type": "NR", "title": "first NR", "content": NR_BODY}
    body.update(over)
    body = {k: v for k, v in body.items() if v is not None}
    r = world.client.post("/flowgate/api/v1/inbox", headers={"Authorization": "Bearer " + raw}, json=body)
    js = r.json()
    return r.status_code, js, ((js.get("error") or {}).get("details") or {})


def token_row(token_id):
    return db_tokens.get_by_id(token_id) or {}


def spend(world, token_id):
    """Emulate the reviewer's verdict submission spending its review token."""
    world.env.db._execute("UPDATE tokens SET consumed_at=? WHERE token_id=?",
                          ["2026-11-06T00:00:00+00:00", token_id])


def make_builder(foreign_group=None, foreign_doc=None):
    """The two mints legacy_adapter.start_review_loop._issue performs (graph hooks omitted).

    ``foreign_group`` makes the NEXT mint carry another group -- a token the run must refuse
    to adopt (project/group are immutable for the life of a run). ``foreign_doc`` makes it name
    another document of the SAME project/group -- refused too: a review loop is pinned to its
    own document (T0004 §2-4/§5-4)."""
    from modules.flow_gate.services import invoke_mention_service, workflow_decision_service

    def _issue(ai_run_id=None):
        if _issue.foreign_doc:
            scope = "edit" if getattr(_issue, "loop_stage", None) == svc.REWORK_HOP_KIND else "review"
            t = token_service.issue("flowgate", F, scope, _issue.foreign_doc, "user", ai_run_id=ai_run_id)
            _issue.minted.append(t["token_id"])
            return {"raw_token": t["raw_token"], "token_id": t["token_id"],
                    "scratch_dir": t["scratch_dir"], "mention": "m"}
        if _issue.foreign_group:
            t = token_service.issue("flowgate", _issue.foreign_group, "edit", D, "user", ai_run_id=ai_run_id)
            _issue.minted.append(t["token_id"])
            return {"raw_token": t["raw_token"], "token_id": t["token_id"],
                    "scratch_dir": t["scratch_dir"], "mention": "m"}
        if getattr(_issue, "loop_stage", None) == svc.REWORK_HOP_KIND:
            issued = invoke_mention_service.issue_rework_request(
                doc_id=D, issued_to="user", api_base_url=API, locale="ko", ai_run_id=ai_run_id)
            _issue.minted.append(issued["token_id"])
            return {**issued, "mention": issued.get("mention") or "m"}
        issued = workflow_decision_service.request_review(
            doc_id=D, issued_to="user", api_base_url=API, ref_doc_ids=None, locale="ko", ai_run_id=ai_run_id)
        _issue.minted.append(issued["token_id"])
        return {"raw_token": issued["token"], "token_id": issued["token_id"],
                "scratch_dir": issued["scratch_dir"], "mention": issued.get("mention") or "m"}
    _issue.foreign_group = foreign_group
    _issue.foreign_doc = foreign_doc
    _issue.minted = []
    return _issue


def start_loop(world, *, rejected, builder=None):
    """Real admission.start_run exactly as legacy_adapter.start_review_loop calls it."""
    if rejected:
        world.env.db._execute("UPDATE documents SET doc_review_status='rejected' WHERE doc_id=?", [D])
    builder = builder or make_builder()
    out = svc.start_run(
        project_id="flowgate", module="v02", group_id=F, doc_ref=D, action_scope="review", mode="single",
        continuation_target_seq=None, continuation_review_mode=False, continuation_instruction_mode=None,
        continuation_locale=None, issued_to="user", api_base_url=API,
        mention_builder=lambda _raw, _scratch: None, issue_builder=builder,
        document_review_loop=dict(LOOP),
    )
    return svc.get_run_record(out["run_id"]), builder


def transition(run, builder, stage):
    """worker.py's stage-switch block: loop_stage, then the shared _prepare_retry_token."""
    builder.loop_stage = stage
    prepared = worker._prepare_retry_token(run)
    if prepared is not None:
        run["hop_kind"] = stage
        run["attempt_no"] = int(run.get("attempt_no") or 0) + 1
    return prepared


def assert_bound_to_current_token(run, action):
    assert run["register_context"]["action"] == action
    assert run["register_context"]["project"] == "flowgate"
    assert run["register_context"]["group"] == F
    assert run["current_token_id"] == run["token_id"]
    # Readers that fall back with `current_token_id or token_id` see the same id.
    assert (run.get("current_token_id") or run.get("token_id")) == run["token_id"]
    # The oracle / judge / retry gates keep the loop's start scope.
    assert run["action_scope"] == "review"


def submit_and_check(world, run):
    """dry_run 200 (nothing spent), then the real submission 200: revision +1, token spent."""
    before = documents.get_by_id(D)
    reviews_before = world.env.db._fetch_one("SELECT COUNT(*) AS n FROM document_reviews")["n"]
    st, js, details = post(world, run["raw_token"], dry_run=True)
    assert st == 200, (st, js)
    assert documents.get_by_id(D)["revision_no"] == before["revision_no"]
    assert not token_row(run["token_id"]).get("consumed_at")
    st, js, details = post(world, run["raw_token"])
    assert st == 200, (st, js)
    after = documents.get_by_id(D)
    assert after["revision_no"] == before["revision_no"] + 1
    assert token_row(run["token_id"]).get("consumed_at")
    assert world.env.db._fetch_one("SELECT COUNT(*) AS n FROM document_reviews")["n"] == reviews_before
    assert run.get("register_errors") == []


# ── 1. review first -> issues -> rework edit ────────────────────────────────────────────

def test_review_first_loop_rework_hop_submits_with_its_edit_token(world):
    run, builder = start_loop(world, rejected=False)
    review_token_id = run["token_id"]
    assert token_row(review_token_id)["action_scope"] == "review"
    assert_bound_to_current_token(run, "review")          # first hop seeded at admission

    spend(world, review_token_id)
    prepared = transition(run, builder, "rework")
    assert prepared and prepared["reissued"] is True
    assert token_row(run["token_id"])["action_scope"] == "edit"
    assert run["token_id"] != review_token_id
    assert_bound_to_current_token(run, "edit")            # was: register_context.action=review -> 403

    submit_and_check(world, run)


# ── 2. loop that starts with rework: the very first hop ─────────────────────────────────

def test_loop_starting_with_rework_first_hop_submits_without_any_transition(world):
    run, _builder = start_loop(world, rejected=True)
    assert run["document_review_loop"]["starts_with_rework"] is True
    assert token_row(run["token_id"])["action_scope"] == "edit"
    assert_bound_to_current_token(run, "edit")            # was: None, lazily seeded to review -> 403
    submit_and_check(world, run)


# ── 3. rework -> review transition, same-stage reissue, stale tokens ────────────────────

def test_rework_then_review_transition_and_same_stage_reissue_follow_the_current_token(world):
    run, builder = start_loop(world, rejected=True)
    first_edit_raw, first_edit_id = run["raw_token"], run["token_id"]
    submit_and_check(world, run)                          # consumes the first edit token

    prepared = transition(run, builder, "review")
    assert prepared and prepared["reissued"] is True
    assert_bound_to_current_token(run, "review")
    context, rec = worker._bind_register_context(run, run["raw_token"])
    assert context["action"] == "review" and rec["token_id"] == run["token_id"]
    # The previous hop's (spent) edit token is refused -- authentication fault, not a binding pass.
    with pytest.raises(worker._RegisterBindingRejected) as exc:
        worker._bind_register_context(run, first_edit_raw)
    assert exc.value.response["http_status"] == 401
    st, _js, _d = post(world, first_edit_raw, dry_run=True)
    assert st == 401

    # Same-stage reissue (retry): the stale review token is revoked, a new one is minted.
    old_review_raw, old_review_id = run["raw_token"], run["token_id"]
    token_service.revoke(old_review_id, reason="t0136_retry")
    prepared = worker._prepare_retry_token(run)
    assert prepared and prepared["reissued"] is True and prepared["token_id_before"] == old_review_id
    assert run["token_id"] != old_review_id
    assert_bound_to_current_token(run, "review")
    with pytest.raises(worker._RegisterBindingRejected):
        worker._bind_register_context(run, old_review_raw)

    # A valid, unconsumed review token of the same run that is NOT the current one.
    sibling = token_service.issue("flowgate", F, "review", D, "user", ai_run_id=run["run_id"])
    with pytest.raises(worker._RegisterBindingRejected) as exc:
        worker._bind_register_context(run, sibling["raw_token"])
    assert exc.value.response["error"]["details"]["reason"] == "token_not_current"

    # Back to rework: the edit token of the new hop submits again.
    spend(world, run["token_id"])
    prepared = transition(run, builder, "rework")
    assert prepared and prepared["reissued"] is True
    assert_bound_to_current_token(run, "edit")
    submit_and_check(world, run)
    assert first_edit_id != run["token_id"]


def test_reuse_branch_keeps_the_token_and_the_context_untouched(world):
    run, _builder = start_loop(world, rejected=True)
    context, token_id = dict(run["register_context"]), run["token_id"]
    prepared = worker._prepare_retry_token(run)           # token neither spent nor revoked
    assert prepared["reissued"] is False and prepared["token_id"] == token_id
    assert run["register_context"] == context and run["current_token_id"] == token_id


def test_same_stage_rework_reissue_submits_with_the_new_token(world):
    run, builder = start_loop(world, rejected=True)
    old_raw, old_id = run["raw_token"], run["token_id"]
    token_service.revoke(old_id, reason="t0136_retry")
    builder.loop_stage = "rework"
    prepared = worker._prepare_retry_token(run)
    assert prepared["reissued"] is True and run["token_id"] != old_id
    assert_bound_to_current_token(run, "edit")
    st, _js, _d = post(world, old_raw, dry_run=True)
    assert st == 401                                       # revoked
    submit_and_check(world, run)


# ── 4. initialization / transition failure ─────────────────────────────────────────────

def test_first_hop_token_outside_the_run_group_fails_admission_and_cleans_up(world):
    groups.create({"group_id": OTHER_GROUP, "project_id": "flowgate", "module": "v02",
                   "title": OTHER_GROUP, "status": "OPEN"})
    builder = make_builder(foreign_group=OTHER_GROUP)
    with pytest.raises(HTTPException) as exc:
        start_loop(world, rejected=True, builder=builder)
    assert exc.value.status_code == 500
    assert "document_review_loop_context_init_failed" in str(exc.value.detail)
    [token_id] = builder.minted
    assert token_row(token_id).get("revoked_at")          # the unused token is discarded
    assert world.released and world.released[-1][1].get("reason") == \
        "admission_rollback_review_loop_context_init_failed"
    with svc._runs_lock:
        assert not [k for k in svc._runs if str(k).startswith(RUN_PREFIX)]
    assert world.started == []                             # no worker thread for a half-seeded run


def test_transition_token_outside_the_run_group_is_an_explicit_failure(world):
    groups.create({"group_id": OTHER_GROUP, "project_id": "flowgate", "module": "v02",
                   "title": OTHER_GROUP, "status": "OPEN"})
    run, builder = start_loop(world, rejected=False)
    review_context, review_id = dict(run["register_context"]), run["current_token_id"]
    spend(world, run["token_id"])
    builder.foreign_group = OTHER_GROUP
    assert transition(run, builder, "rework") is None      # worker: document_review_loop_transition_failed
    assert token_row(builder.minted[-1]).get("revoked_at")
    # The run context did not move to the refused token.
    assert run["register_context"] == review_context and run["current_token_id"] == review_id
    st, _js, _d = post(world, run["raw_token"], dry_run=True)
    assert st == 401                                       # the refused token is dead


# ── 5. both rejection stages are recorded once, with their boundary ─────────────────────

def test_binding_rejections_are_recorded_once_each_and_name_their_boundary(world):
    from modules.flow_gate.db import ai_invoke_runs as db_runs, register_context_failures as db_failures
    run_id = "%s%03d" % (RUN_PREFIX, next(_RUN_SEQ))
    issued = token_service.issue("flowgate", F, "edit", D, "user", ai_run_id=run_id)
    # A run whose server-owned context is review while it holds an edit token: the 2nd stage refuses.
    run = {"run_id": run_id, "project_id": "flowgate", "group_id": F, "module": "v02",
           "action_scope": "review", "doc_ref": D, "token_id": issued["token_id"], "register_errors": [],
           "status": "running", "raw_token": issued["raw_token"],
           "handoff_gate": SimpleNamespace(abort=lambda: True)}
    admission._register_run(run, [{"id": "p"}])

    st, js, details = post(world, issued["raw_token"], dry_run=True)
    assert st == 403 and details["axis"] == "action" and details["reason"] == "context_binding_mismatch"
    assert details["boundary"] == "register_dispatch"      # token <-> run context (2nd stage)
    assert len(run["register_errors"]) == 1
    stage2 = run["register_errors"][0]
    assert stage2["correlation_id"] == details["correlation_id"] and stage2["boundary"] == "register_dispatch"
    # The API provider loop reading the same response does not stack a second row.
    assert worker._absorb_binding_failure(run, js, 4) is True
    assert len(run["register_errors"]) == 1 and stage2["turn"] == 4

    st, js, details = post(world, issued["raw_token"], action="new", dry_run=True)
    assert st == 403 and details["axis"] == "action"
    assert details["boundary"] == "inbox"                  # request action <-> token (1st stage)
    assert len(run["register_errors"]) == 2
    # Nothing secret in the 403.
    text = str(js)
    assert issued["raw_token"] not in text and issued["token_id"] not in text

    db_runs.upsert({"run_id": run_id, "group_id": F, "project_id": "flowgate", "doc_ref": D,
                    "mode": "single", "started_at": "2026-11-06T00:00:00+09:00",
                    "finished_at": "2026-11-06T00:01:00+09:00",
                    "created_at": "2026-11-06T00:00:00+09:00", "updated_at": "2026-11-06T00:01:00+09:00"})
    svc._persist_register_context_failures(run, "2026-11-06T00:02:00+09:00")
    svc._persist_register_context_failures(run, "2026-11-06T00:03:00+09:00")
    rows = db_failures.list_by_run(run_id)
    assert sorted(r["boundary"] for r in rows) == ["inbox", "register_dispatch"]
    assert {r["correlation_id"] for r in rows} == {e["correlation_id"] for e in run["register_errors"]}


# ── 6. ordinary refusals survive the fix ───────────────────────────────────────────────

def test_adopted_rework_token_still_refuses_wrong_action_doc_group_project_and_old_runs(world):
    groups.create({"group_id": OTHER_GROUP, "project_id": "flowgate", "module": "v02",
                   "title": OTHER_GROUP, "status": "OPEN"})
    run, builder = start_loop(world, rejected=False)
    review_raw = run["raw_token"]
    spend(world, run["token_id"])
    transition(run, builder, "rework")
    raw = run["raw_token"]
    for over, axis in ((dict(action="new"), "action"), (dict(action="review", verdict="issues"), "action"),
                       (dict(doc_id=F + ".0009-N"), None), (dict(group_name=OTHER_GROUP), None),
                       (dict(project="other"), None)):
        st, js, details = post(world, raw, dry_run=True, **over)
        assert st == 403, (over, st, js)
        if axis:
            assert details.get("axis") == axis
    assert post(world, review_raw, dry_run=True)[0] == 401          # spent review token
    sibling = token_service.issue("flowgate", F, "edit", D, "user", ai_run_id=run["run_id"])
    st, _js, details = post(world, sibling["raw_token"], dry_run=True)
    assert st == 403 and details.get("reason") == "token_not_current"
    assert not token_row(run["token_id"]).get("consumed_at")         # nothing above spent it
    assert documents.get_by_id(D)["revision_no"] == 0

    # Re-run: the first run is gone, its unspent token stays refused; the new run's own works.
    old_raw = run["raw_token"]
    with svc._runs_lock:
        svc._runs.pop(run["run_id"], None)
    st, _js, details = post(world, old_raw, dry_run=True)
    assert st == 403 and details.get("reason") == "token_not_current"
    run2, _b2 = start_loop(world, rejected=True)
    assert_bound_to_current_token(run2, "edit")
    submit_and_check(world, run2)


# ── 7. the first NR submission (action=new) is unchanged ───────────────────────────────

def start_author_run(world):
    def _issue(ai_run_id=None):
        t = token_service.issue("flowgate", F, "new", D, "user", ai_run_id=ai_run_id)
        return {"raw_token": t["raw_token"], "token_id": t["token_id"], "scratch_dir": t.get("scratch_dir"),
                "mention": "m"}
    out = svc.start_run(
        project_id="flowgate", module="v02", group_id=F, doc_ref=D, action_scope="new", mode="single",
        continuation_target_seq=None, continuation_review_mode=False, continuation_instruction_mode=None,
        continuation_locale=None, issued_to="user", api_base_url=API,
        mention_builder=lambda _r, _s: None, issue_builder=_issue)
    return svc.get_run_record(out["run_id"])


def docs_in_group(world):
    rows = world.env.db._fetch_all("SELECT doc_id, type_code, status, revision_no, doc_review_status "
                                   "FROM documents WHERE group_id=? ORDER BY doc_id", [F])
    return [dict(r) for r in rows]


def test_first_nr_submission_with_a_new_token_is_unchanged(world):
    run = start_author_run(world)
    # Not a review loop: nothing is seeded at admission.
    assert run.get("register_context") is None and run.get("current_token_id") is None
    before = docs_in_group(world)
    st, js, _d = post_new(world, run["raw_token"], dry_run=True)
    assert st == 200, (st, js)
    assert docs_in_group(world) == before and not token_row(run["token_id"]).get("consumed_at")
    st, js, _d = post_new(world, run["raw_token"])
    assert st == 201, (st, js)
    created = [d for d in docs_in_group(world) if d not in before]
    assert len(created) == 1 and created[0]["type_code"] == "NR" and created[0]["status"] == "open"
    assert created[0]["revision_no"] == 0
    assert token_row(run["token_id"]).get("consumed_at")
    live = svc.get_run_record(run["run_id"])
    assert live.get("register_context") is None and live.get("current_token_id") is None
    assert live.get("register_errors") == []


def test_new_token_still_refuses_wrong_action_doc_and_group(world):
    run = start_author_run(world)
    raw = run["raw_token"]
    before = docs_in_group(world)
    results = [
        post_new(world, raw, action="edit", dry_run=True, doc_id=D, expected_revision=0, edit_reason="worker_self"),
        post_new(world, raw, action="review", dry_run=True, doc_id=D, verdict="issues", review_summary="x"),
        post_new(world, raw, dry_run=True, prev_doc_id=F + ".0009-N"),
        post_new(world, raw, dry_run=True, group_name="flowgate.v02.0128"),
    ]
    assert [(st, d.get("axis")) for st, _js, d in results] == [
        (403, "action"), (403, "action"), (403, "doc"), (403, "group")]
    assert docs_in_group(world) == before
    assert not token_row(run["token_id"]).get("consumed_at")
    assert len(svc.get_run_record(run["run_id"])["register_errors"]) == 4


# ── 8. the API provider path (register_dispatch) after a transition ────────────────────

def test_api_provider_register_dispatch_follows_the_rework_token(world):
    run, builder = start_loop(world, rejected=False)
    worker._run_register_context(run)                     # _api_execute's hop-start seed: a no-op now
    assert_bound_to_current_token(run, "review")
    spend(world, run["token_id"])
    transition(run, builder, "rework")
    worker._run_register_context(run)                     # the rework hop re-enters _api_execute
    assert_bound_to_current_token(run, "edit")
    context, _rec = worker._bind_register_context(run, run["raw_token"])
    assert context["action"] == "edit"

    def via_test_client(_run, raw, body):
        r = world.client.post("/flowgate/api/v1/inbox", headers={"Authorization": "Bearer " + raw}, json=body)
        return r.status_code, r.json()
    world.monkeypatch.setattr(worker, "_post_inbox_json", via_test_client)
    run["transport_api_base"] = API
    status, resp = worker._inbox_register(run, run["raw_token"], {
        "content": HEADER + "api rework\n", "expected_revision": 0, "edit_reason": "worker_self"})
    assert 200 <= status < 300, (status, resp)
    assert documents.get_by_id(D)["revision_no"] == 1
    assert token_row(run["token_id"]).get("consumed_at")
    assert run["register_errors"] == []


# ══ rejection rej_01M6Q2T0KPHYGK8M: real lease, real mutation gate, real rejection path ═══════

def lease_of(run):
    lease = db_leases.get(F) or {}
    assert lease.get("run_id") == run["run_id"], lease
    return lease


def assert_lease_follows_token(run):
    """The group lease names the run's CURRENT token and that token's own scope -- what
    mutation_policy's owner match compares a worker token against."""
    lease = lease_of(run)
    assert lease["token_id"] == run["token_id"] == run["current_token_id"]
    assert lease["action_scope"] == token_row(run["token_id"])["action_scope"]
    assert lease["action_scope"] == run["register_context"]["action"]
    assert run["action_scope"] == "review"
    return lease


def make_other_doc(world):
    path = world.env.root / "storage" / "documents" / "other.md"
    path.write_bytes(b"---\nproject: flowgate\nmodule: v02\ngroup: 0127\ntype: NR\n---\nother\n")
    documents.create({"doc_id": OTHER_DOC, "project_id": "flowgate", "module": "v02", "group_id": F,
                      "type_code": "NR", "seq": 3, "title": "Other", "status": "open", "owner_id": "user",
                      "file_path": storage_paths.to_storage_relative(path, "flowgate")})


def end_run(run):
    """The run is over (its worker finished): it leaves memory and gives its lease back."""
    with svc._runs_lock:
        svc._runs.pop(run["run_id"], None)
    _REAL_LEASE["release"](F, run["run_id"], reason="t0136_run_ended")


# ── P1-1: a loop that starts with rework -- the group lease takes the first token's scope ──

def test_rework_first_hop_lease_takes_the_edit_scope_and_passes_the_real_mutation_gate(gated):
    run, _builder = start_loop(gated, rejected=True)
    assert run["document_review_loop"]["starts_with_rework"] is True
    assert token_row(run["token_id"])["action_scope"] == "edit"
    assert_bound_to_current_token(run, "edit")
    lease = assert_lease_follows_token(run)
    assert lease["state"] == "active" and lease["action_scope"] == "edit"   # was: review (activate's run scope)

    # The gate is really on in this fixture: the lease scope the fix replaced is refused by the
    # real owner match exactly as reported (403 GROUP_AI_RUN_OWNER_MISMATCH), and nothing moves.
    db_leases.update_token(F, run["run_id"], run["token_id"], action_scope="review")
    st, js, _d = post(gated, run["raw_token"], dry_run=True)
    assert st == 403 and "GROUP_AI_RUN_OWNER_MISMATCH" in str(js), (st, js)
    assert not token_row(run["token_id"]).get("consumed_at")
    db_leases.update_token(F, run["run_id"], run["token_id"], action_scope="edit")

    submit_and_check(gated, run)                       # dry_run 200 -> real 200, revision +1, token spent
    assert_lease_follows_token(run)


def test_review_first_loop_lease_follows_each_hop_under_the_real_mutation_gate(gated):
    run, builder = start_loop(gated, rejected=False)
    assert assert_lease_follows_token(run)["action_scope"] == "review"
    spend(gated, run["token_id"])
    assert transition(run, builder, "rework")["reissued"] is True
    assert assert_lease_follows_token(run)["action_scope"] == "edit"
    submit_and_check(gated, run)
    assert transition(run, builder, "review")["reissued"] is True
    assert assert_lease_follows_token(run)["action_scope"] == "review"


def test_first_hop_lease_sync_failure_fails_closed_and_cleans_up(gated):
    def broken(*_a, **_k):
        raise RuntimeError("injected lease write failure")
    gated.monkeypatch.setattr(db_leases, "update_token", broken)
    builder = make_builder()
    with pytest.raises(HTTPException) as exc:
        start_loop(gated, rejected=True, builder=builder)
    assert exc.value.status_code == 500
    assert "document_review_loop_context_init_failed" in str(exc.value.detail)
    [token_id] = builder.minted
    assert token_row(token_id).get("revoked_at")                     # the unused edit token is discarded
    assert gated.released[-1][1].get("reason") == "admission_rollback_review_loop_context_init_failed"
    assert db_leases.get(F) is None                                  # the lease is really gone
    with svc._runs_lock:
        assert not [k for k in svc._runs if str(k).startswith(RUN_PREFIX)]
    assert gated.started == []


def test_graph_execution_first_hop_writes_no_group_lease(world):
    """A Graph execution has an execution lease, not a group lease: binding its first token
    never creates or re-points one, and its failure path never releases one."""
    calls = []
    world.monkeypatch.setattr(db_leases, "update_token", lambda *a, **k: calls.append(("update", a, k)))
    run_id = "%s%03d" % (RUN_PREFIX, next(_RUN_SEQ))
    issued = token_service.issue("flowgate", F, "edit", D, "user", ai_run_id=run_id)
    run = {"run_id": run_id, "project_id": "flowgate", "group_id": F, "doc_ref": D, "action_scope": "review",
           "graph_exec_id": "gx-0136", "token_id": issued["token_id"], "raw_token": issued["raw_token"],
           "handoff_gate": SimpleNamespace(abort=lambda: True)}
    admission._bind_review_loop_first_hop(run, holds_group_lease=False)
    assert run["register_context"]["action"] == "edit" and run["current_token_id"] == issued["token_id"]
    assert calls == [] and _REAL_LEASE["get_active"](F) is None and db_leases.get(F) is None

    make_other_doc(world)
    other = token_service.issue("flowgate", F, "edit", OTHER_DOC, "user", ai_run_id=run_id)
    refused = dict(run, token_id=other["token_id"], raw_token=other["raw_token"], register_context=None,
                   current_token_id=None)
    with pytest.raises(HTTPException) as exc:
        admission._bind_review_loop_first_hop(refused, holds_group_lease=False)
    assert exc.value.status_code == 500
    assert world.released == [] and calls == []                      # no group lease touched
    assert token_row(other["token_id"]).get("revoked_at")
    assert refused["register_context"] is None and refused["current_token_id"] is None


# ── P2-1: the loop's document is pinned -- first hop and every reissue ──────────────────

def test_first_hop_token_for_another_document_of_the_group_is_refused(gated):
    make_other_doc(gated)
    builder = make_builder(foreign_doc=OTHER_DOC)
    with pytest.raises(HTTPException) as exc:
        start_loop(gated, rejected=True, builder=builder)
    assert exc.value.status_code == 500
    assert "document_review_loop_context_init_failed" in str(exc.value.detail)
    [token_id] = builder.minted
    assert token_row(token_id)["doc_ref"] == OTHER_DOC and token_row(token_id).get("revoked_at")
    assert gated.released[-1][1].get("reason") == "admission_rollback_review_loop_context_init_failed"
    assert db_leases.get(F) is None
    with svc._runs_lock:
        assert not [k for k in svc._runs if str(k).startswith(RUN_PREFIX)]
    assert gated.started == []


def test_reissued_token_for_another_document_is_refused_and_the_context_stays(gated):
    make_other_doc(gated)
    run, builder = start_loop(gated, rejected=False)
    review_raw = run["raw_token"]
    kept = (dict(run["register_context"]), run["current_token_id"], dict(lease_of(run)))
    spend(gated, run["token_id"])

    builder.foreign_doc = OTHER_DOC                                  # stage switch review -> rework
    assert transition(run, builder, "rework") is None                # worker: ..._transition_failed
    refused_id = builder.minted[-1]
    assert token_row(refused_id)["doc_ref"] == OTHER_DOC and token_row(refused_id).get("revoked_at")
    lease = lease_of(run)
    assert (run["register_context"], run["current_token_id"]) == kept[:2]
    assert run["register_context"]["doc"] == D
    assert (lease["token_id"], lease["action_scope"]) == (kept[2]["token_id"], kept[2]["action_scope"])
    assert post(gated, run["raw_token"], dry_run=True)[0] == 401     # the refused token is dead
    assert post(gated, review_raw, dry_run=True)[0] == 401           # the spent one too

    builder.loop_stage = "review"                                    # same-stage reissue
    assert worker._prepare_retry_token(run) is None
    assert token_row(builder.minted[-1]).get("revoked_at")
    assert (run["register_context"], run["current_token_id"]) == kept[:2]
    assert lease_of(run)["token_id"] == kept[2]["token_id"]

    builder.foreign_doc = None                                        # the loop's own document again
    assert transition(run, builder, "rework")["reissued"] is True
    assert_bound_to_current_token(run, "edit")
    assert run["register_context"]["doc"] == D
    assert_lease_follows_token(run)


def test_ordinary_continuation_still_moves_to_the_tokens_document(world):
    """The doc pin is the review loop's only; a plain continuation keeps its existing contract."""
    make_other_doc(world)
    run_id = "%s%03d" % (RUN_PREFIX, next(_RUN_SEQ))
    nxt = token_service.issue("flowgate", F, "new", OTHER_DOC, "user", ai_run_id=run_id)
    run = {"run_id": run_id, "project_id": "flowgate", "group_id": F, "doc_ref": D, "action_scope": "new",
           "register_context": register_binding.canonical_context("new", "flowgate", F, D)}
    assert worker._adopt_continuation_token(run, nxt["raw_token"]) is True
    assert run["register_context"]["doc"] == OTHER_DOC and run["current_token_id"] == nxt["token_id"]
    # The same token offered with the loop's pin is refused and moves nothing.
    pinned = {"run_id": run_id, "project_id": "flowgate", "group_id": F, "doc_ref": D}
    assert worker._adopt_continuation_token(pinned, nxt["raw_token"], pinned_doc=D) is False
    assert "register_context" not in pinned and "current_token_id" not in pinned


# ── P1-2: every hop's rejections reach the run diagnosis and the DB exactly once ───────────

def test_rejections_of_every_hop_survive_resets_and_transitions_and_persist_once(world):
    from modules.flow_gate.db import ai_invoke_runs as db_runs, register_context_failures as db_failures
    run, builder = start_loop(world, rejected=False)                 # hop 1: review
    run_id = run["run_id"]

    # hop 1, attempt 1: a 1st-stage (request action vs token) and a 2nd-stage (token vs run) 403.
    st, _js, d1 = post(world, run["raw_token"], dry_run=True)        # edit with the review token
    assert st == 403 and d1["boundary"] == "inbox" and d1["axis"] == "action"
    sibling = token_service.issue("flowgate", F, "edit", D, "user", ai_run_id=run_id)
    st, _js, d2 = post(world, sibling["raw_token"], dry_run=True)    # edit token vs review context
    assert st == 403 and d2["boundary"] == "register_dispatch" and d2["axis"] == "action"
    assert [e["correlation_id"] for e in run["register_errors"]] == [d1["correlation_id"], d2["correlation_id"]]

    worker._reset_attempt_state(run)                                 # same-stage retry
    assert run["register_errors"] == []                              # per-attempt list: still emptied
    assert len(run[register_binding.CARRIED_FAILURES_KEY]) == 2

    # hop 1, attempt 2: another 1st-stage 403, then the stage switch the worker performs.
    st, _js, d3 = post(world, run["raw_token"], dry_run=True)        # edit with the review token, again
    assert st == 403 and d3["boundary"] == "inbox" and d3["correlation_id"] != d1["correlation_id"]
    spend(world, run["token_id"])
    assert transition(run, builder, "rework")["reissued"] is True
    worker._reset_attempt_state(run)                                 # worker.py: after the transition

    # hop 2 (rework): one more 403 -- and a stale reference of a carried record in the live list.
    st, _js, d4 = post(world, run["raw_token"], action="new", dry_run=True)
    assert st == 403 and d4["boundary"] == "inbox"
    run["register_errors"].append(run[register_binding.CARRIED_FAILURES_KEY][0])
    expected = [d1["correlation_id"], d2["correlation_id"], d3["correlation_id"], d4["correlation_id"]]
    assert [e["correlation_id"] for e in register_binding.run_failures(run)] == expected
    assert register_binding.carry_failures(run) == 1 and register_binding.carry_failures(run) == 0

    db_runs.upsert({"run_id": run_id, "group_id": F, "project_id": "flowgate", "doc_ref": D,
                    "mode": "single", "started_at": "2026-11-06T00:00:00+09:00",
                    "finished_at": "2026-11-06T00:01:00+09:00",
                    "created_at": "2026-11-06T00:00:00+09:00", "updated_at": "2026-11-06T00:01:00+09:00"})
    svc._persist_register_context_failures(run, "2026-11-06T00:02:00+09:00")
    svc._persist_register_context_failures(run, "2026-11-06T00:03:00+09:00")   # a re-settlement
    rows = db_failures.list_by_run(run_id)
    assert sorted(r["correlation_id"] for r in rows) == sorted(expected)          # each once
    assert sorted(r["boundary"] for r in rows) == ["inbox", "inbox", "inbox", "register_dispatch"]
    assert db_failures.count_for_run(run_id) == 4
    text = str(rows)
    assert run["raw_token"] not in text and sibling["raw_token"] not in text


# ── P2-2: the real rejection path -- issues verdict, rejected rework, next review hop ──────

def post_review(world, raw, verdict, findings):
    """POST /inbox action=review: dry_run (its receipt), then the real registration."""
    body = {"action": "review", "project": "flowgate", "module": "v02", "group_name": F, "doc_id": D,
            "verdict": verdict, "findings": findings, "comment": "AI review of %s" % D}
    r = world.client.post("/flowgate/api/v1/inbox", headers={"Authorization": "Bearer " + raw},
                          json={**body, "dry_run": True})
    assert r.status_code == 200, (r.status_code, r.json())
    receipt = r.json().get("receipt")
    assert receipt, r.json()
    r = world.client.post("/flowgate/api/v1/inbox", headers={"Authorization": "Bearer " + raw},
                          json={**body, "receipt": receipt})
    assert r.status_code in (200, 201), (r.status_code, r.json())
    return r.json()


def reviews_of_doc(world):
    rows = world.env.db._fetch_all("SELECT id, revision_no, verdict, findings, review_run_id "
                                   "FROM document_reviews WHERE doc_id=? ORDER BY id", [D])
    return [dict(r) for r in rows]


def history_of_doc():
    import json
    return json.loads(documents.get_by_id(D).get("rejection_history") or "[]")


FINDINGS = [{"locus": "§1", "note": "the goal section is missing its acceptance criteria"}]


def review_hop_rejects(world, run):
    """Hop 1: the reviewer registers `issues`; the hop end's real checkpoint rejects the document."""
    assert run["register_context"]["action"] == "review"
    post_review(world, run["raw_token"], "issues", FINDINGS)
    assert token_row(run["token_id"]).get("consumed_at")
    [review] = reviews_of_doc(world)
    assert review["verdict"] == "issues" and review["review_run_id"] == run["run_id"]
    run["outcome"] = "complete"
    loop = svc._checkpoint_document_review_loop(run)
    assert loop["current_stage"] == "rework", loop
    doc = documents.get_by_id(D)
    assert doc["doc_review_status"] == "rejected"
    [entry] = history_of_doc()
    assert entry.get("rejection_id") and str(entry.get("review_id")) == str(review["id"])
    assert not entry.get("ai_response")
    return review, entry


def rework_answers_the_rejection(world, run, review, entry):
    """The rework hop's edit token answers THAT rejection: dry_run 200, real 200, revision +1,
    rejected -> revised, the rejection and the review row both preserved."""
    before = documents.get_by_id(D)
    over = dict(edit_reason="rejected", rejection_id=entry["rejection_id"],
                rejection_response="added the acceptance criteria to §1")
    st, js, _d = post(world, run["raw_token"], dry_run=True, **over)
    assert st == 200, (st, js)
    assert documents.get_by_id(D)["revision_no"] == before["revision_no"]
    assert documents.get_by_id(D)["doc_review_status"] == "rejected"
    assert not token_row(run["token_id"]).get("consumed_at")
    st, js, _d = post(world, run["raw_token"], **over)
    assert st == 200, (st, js)
    after = documents.get_by_id(D)
    assert after["revision_no"] == before["revision_no"] + 1
    assert after["doc_review_status"] == "revised"
    assert token_row(run["token_id"]).get("consumed_at")
    [answered] = history_of_doc()
    assert answered["rejection_id"] == entry["rejection_id"] and answered["reason"] == entry["reason"]
    assert str(answered.get("review_id")) == str(review["id"])
    assert answered.get("ai_response") == "added the acceptance criteria to §1" and answered.get("responded_at")
    assert reviews_of_doc(world) == [review]                         # the issues verdict is kept as it was
    assert run["register_errors"] == []


def next_review_hop(world, run, builder):
    """The rework hop's end reserves the next review; its token is bound and registers a verdict."""
    run["outcome"] = "complete"
    loop = svc._checkpoint_document_review_loop(run)
    assert loop["current_stage"] == "review", loop
    assert transition(run, builder, "review")["reissued"] is True
    assert_bound_to_current_token(run, "review")
    assert_lease_follows_token(run)
    post_review(world, run["raw_token"], "pass", [])
    assert token_row(run["token_id"]).get("consumed_at")
    assert [r["verdict"] for r in reviews_of_doc(world)] == ["issues", "pass"]
    run["outcome"] = "complete"
    assert svc._checkpoint_document_review_loop(run)["current_stage"] == "stopped"


def test_review_first_issues_rejection_is_answered_by_the_rework_hop_on_the_real_path(gated):
    run, builder = start_loop(gated, rejected=False)
    assert run["document_review_loop"]["starts_with_rework"] is False
    assert_lease_follows_token(run)
    review, entry = review_hop_rejects(gated, run)

    assert transition(run, builder, "rework")["reissued"] is True    # worker.py stage switch
    assert token_row(run["token_id"])["action_scope"] == "edit"
    assert_bound_to_current_token(run, "edit")
    assert assert_lease_follows_token(run)["action_scope"] == "edit"
    rework_answers_the_rejection(gated, run, review, entry)
    next_review_hop(gated, run, builder)


def test_rework_first_loop_answers_the_stored_rejection_with_its_first_token(gated):
    # An earlier loop reviewed the document and its `issues` verdict rejected it for real.
    first, _b1 = start_loop(gated, rejected=False)
    review, entry = review_hop_rejects(gated, first)
    end_run(first)

    # A new loop on the rejected document starts with rework: its FIRST token is edit.
    run, builder = start_loop(gated, rejected=False)
    assert run["document_review_loop"]["starts_with_rework"] is True
    assert token_row(run["token_id"])["action_scope"] == "edit"
    assert_bound_to_current_token(run, "edit")                       # no transition happened
    assert assert_lease_follows_token(run)["action_scope"] == "edit"
    rework_answers_the_rejection(gated, run, review, entry)
    next_review_hop(gated, run, builder)
