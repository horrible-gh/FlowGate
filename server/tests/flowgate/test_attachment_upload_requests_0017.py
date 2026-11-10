"""AI attachment upload requests (flowgate.chat.0017 CH0002, final design approved at seq 11).

What this suite pins, in the design's own priority order:

* asynchrony / lifecycle separation — an AI run is answered at once, a request survives the
  end of its run (finalize / cancel / startup command recovery never touch it) and can be
  approved afterwards without the run's token;
* the decision — keep both (default) and overwrite (backup copy, atomic in-place replace, CAS
  on the hash the person saw, restore on failure), the target re-check inside the document
  guard (409 ``attachment_target_changed``, nothing follows a changed target), concurrent
  deciders (exactly one wins), the group AI-run lease never refusing a decision;
* permissions — any ``perm_document_update`` holder decides, readers preview, others get 403;
  the human attachment routes now check project permissions too;
* settings — system defaults, project overrides that inherit when unset, hard caps;
* retention / recovery, the chat timeline block, the run-result count and the tool exposure.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _fg_conversation_support import (  # noqa: E402
    ADMIN, CHAT_DOC, GROUP, MODULE, OTHER_USER, PROJECT, RUN_ID, USER,
    add_turn, clean_commands_state, clean_runs, fg_store, make_run, seed, unregister_run,  # noqa: F401
)

from modules.flow_gate.documents.attachments import request_settings  # noqa: E402
from modules.flow_gate.documents.attachments import requests as svc  # noqa: E402
from modules.flow_gate.documents.attachments.registry import registry_list  # noqa: E402

DOC = f"{GROUP}.0002-R"
DOC2 = f"{GROUP}.0003-R"
NODE = "n_report"
NOROLE = "usr_norole"
ADMIN_USER = {"user_id": ADMIN, "is_admin": True}
WORKER_USER = {"user_id": USER, "is_admin": False}       # role_worker: read + update
VIEWER_USER = {"user_id": OTHER_USER, "is_admin": False}  # role_viewer: read only
NOROLE_USER = {"user_id": NOROLE, "is_admin": False}


@pytest.fixture()
def env(fg_store, tmp_path, monkeypatch, clean_runs):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db.connection import now_iso
    from modules.flow_gate.rbac import permission_service

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fg_harness_write_gate

    fg_harness_write_gate.install(monkeypatch)
    seed(fg_store)
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(storage))
    for doc_id, seq in ((DOC, 2), (DOC2, 3)):
        db_documents.create({"doc_id": doc_id, "project_id": PROJECT, "module": MODULE, "group_id": GROUP,
                             "type_code": "R", "seq": seq, "title": f"card doc {seq}", "status": "open",
                             "owner_id": USER})
    now = now_iso()
    fg_store._execute(
        "INSERT OR IGNORE INTO users (user_id, username, email, password, is_active, is_admin, "
        "first_login_required, created_at, updated_at) VALUES (?, ?, ?, 'x', 1, 0, 0, ?, ?)",
        [NOROLE, NOROLE, f"{NOROLE}@example.test", now, now])
    for user_id, role in ((USER, "role_worker"), (OTHER_USER, "role_viewer")):
        fg_store._execute("INSERT OR REPLACE INTO user_project_roles (user_id, project_id, role_id, granted_at) "
                          "VALUES (?, ?, ?, ?)", [user_id, PROJECT, role, now])
    permission_service.invalidate_all()

    state = {"doc_id": DOC, "exists": True, "title": "Report card", "ai_active": False, "resolves": 0}

    def resolver(flow_id, node_key):
        state["resolves"] += 1
        return svc.TargetView(flow_id, node_key, state["exists"], state["doc_id"], state["title"])

    monkeypatch.setattr(svc, "card_resolver", resolver)
    monkeypatch.setattr(svc, "_ai_active", lambda group_id: state["ai_active"])
    yield {"store": fg_store, "storage": storage, "state": state, "tmp": tmp_path}
    permission_service.invalidate_all()


def chat_requester(run_id: str = RUN_ID) -> svc.Requester:
    return svc.Requester(origin="chat", project_id=PROJECT, flow_id=GROUP, token_id=f"tok_{run_id}",
                         ai_run_id=run_id, issued_to=USER, provider_name="fg-provider",
                         source_doc_id=CHAT_DOC, run_start_seq=0)


def card_requester(run_id: str = "aiv_card_1") -> svc.Requester:
    return svc.Requester(origin="card", project_id=PROJECT, flow_id=GROUP, token_id=f"tok_{run_id}",
                         ai_run_id=run_id, issued_to=USER, provider_name="fg-provider", source_doc_id=DOC,
                         graph_run_id="grun_1", node_key=NODE)


def make_request(data: bytes = b"hello report\n", name: str = "report.md", requester=None, **meta) -> dict:
    requester = requester or chat_requester()
    if requester.origin == "chat":
        meta.setdefault("target", {"node_key": NODE})
    return svc.create_from_stream(requester, meta, name, io.BytesIO(data).read)


def approve(row: dict, user=ADMIN_USER, doc_id: str = DOC, **extra) -> dict:
    return svc.decide(row["request_id"], user, {"decision": "approve", "expected_doc_id": doc_id, **extra})


def room_file(name: str) -> Path:
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.documents.attachments.locator import resolve_attach_dir

    return resolve_attach_dir(db_documents.get_by_id(DOC)) / name


# ── intake: answered at once, staged, nothing registered ─────────────────────

def test_intake_stages_the_file_and_registers_nothing(env):
    row = make_request(note="weekly report")
    assert row["status"] == "pending"
    assert row["target_doc_id"] == DOC and row["target_title"] == "Report card"
    assert row["content_sha256"] == hashlib.sha256(b"hello report\n").hexdigest()
    staged = svc.staged_file(row)
    assert staged.read_bytes() == b"hello report\n"
    assert "_attachment_requests" in staged.parts
    assert registry_list(DOC) == []                      # nothing attached before a decision
    result = svc.ai_result(row)
    assert result["status"] == "pending" and "Do not wait" in result["message"]


def test_card_worker_cannot_choose_another_target(env):
    with pytest.raises(svc.RequestError) as refused:
        make_request(requester=card_requester(), target={"node_key": "n_other"})
    assert refused.value.code == "attachment_request_target_fixed"
    row = make_request(requester=card_requester())
    assert row["origin"] == "card" and row["target_node_key"] == NODE and row["graph_run_id"] == "grun_1"


def test_chat_needs_a_card_of_its_own_flow_with_a_document(env):
    with pytest.raises(svc.RequestError) as missing:
        svc.create_from_stream(chat_requester(), {}, "a.txt", io.BytesIO(b"x").read)
    assert missing.value.code == "attachment_request_target_required"
    with pytest.raises(svc.RequestError) as other_flow:
        make_request(target={"flow_id": "fgp.v02.9999", "node_key": NODE})
    assert other_flow.value.code == "attachment_target_outside_flow"
    env["state"]["doc_id"] = None
    with pytest.raises(svc.RequestError) as no_doc:
        make_request()
    assert no_doc.value.code == "attachment_target_document_missing"


def test_reserved_prefix_and_traversal_names_are_handled(env):
    with pytest.raises(svc.RequestError) as reserved:
        make_request(name="__wp_pre_instruction__x.txt")
    assert reserved.value.code == "attachment_request_filename_reserved"
    row = make_request(name="..\\..\\evil.txt")
    assert row["filename"] == "evil.txt"


# ── lifecycle separation: the run ends, the request stays and can be approved ─

def test_run_end_hooks_never_touch_requests_and_approval_needs_no_token(env, clean_commands_state):
    from flowgate.conversation import commands
    from modules.flow_gate.services import chat_command_service

    run = make_run()
    row = make_request(requester=chat_requester(run["run_id"]))
    commands.cancel_for_run(run["run_id"])             # what finalize / cancel run
    commands.recover()                                  # what startup runs
    chat_command_service.recover()
    unregister_run(run["run_id"])                       # the run is gone from the engine
    assert svc.get_row(row["request_id"])["status"] == "pending"
    approved = approve(row)
    assert approved["status"] == "approved" and approved["final_filename"] == "report.md"
    assert [r["filename"] for r in registry_list(DOC)] == ["report.md"]
    assert room_file("report.md").read_bytes() == b"hello report\n"
    assert not svc.staged_file(row).exists()            # the staged copy is gone after approval


# ── keep both (default) ──────────────────────────────────────────────────────

def test_keep_both_never_touches_the_existing_attachment(env):
    first = approve(make_request(b"v1"))
    second = approve(make_request(b"v2"))
    assert first["final_filename"] == "report.md"
    assert second["final_filename"] != "report.md" and second["final_filename"].startswith("report_")
    assert room_file("report.md").read_bytes() == b"v1"
    assert room_file(second["final_filename"]).read_bytes() == b"v2"
    rows = {r["filename"]: r for r in registry_list(DOC)}
    assert rows["report.md"]["content_sha256"] == hashlib.sha256(b"v1").hexdigest()
    assert rows[second["final_filename"]]["uploaded_by"] == ADMIN


def test_pending_request_reports_a_name_conflict(env):
    approve(make_request(b"v1"))
    pending = svc.public(svc.get_row(make_request(b"v2")["request_id"]))
    assert pending["conflict"]["filename"] == "report.md"
    assert pending["conflict"]["content_sha256"] == hashlib.sha256(b"v1").hexdigest()


# ── overwrite: backup copy, atomic replace, CAS on the hash, refusals ─────────

def test_overwrite_replaces_in_place_and_keeps_a_backup(env):
    approve(make_request(b"version one"))
    old_sha = hashlib.sha256(b"version one").hexdigest()
    done = approve(make_request(b"version two"), conflict="overwrite", expected_existing_sha256=old_sha)
    assert done["decision"] == "overwrite" and done["final_filename"] == "report.md"
    assert room_file("report.md").read_bytes() == b"version two"
    rows = registry_list(DOC)
    assert len(rows) == 1 and rows[0]["content_sha256"] == hashlib.sha256(b"version two").hexdigest()
    backup = svc.staging_dir(PROJECT, done["request_id"]) / svc.BACKUP_NAME
    assert backup.read_bytes() == b"version one"
    assert done["backup_path"] and done["backup_expires_at"]


def test_overwrite_with_a_stale_hash_changes_nothing(env):
    approve(make_request(b"version one"))
    row = make_request(b"version two")
    with pytest.raises(svc.RequestError) as stale:
        approve(row, conflict="overwrite", expected_existing_sha256="0" * 64)
    assert stale.value.code == "attachment_existing_changed"
    assert room_file("report.md").read_bytes() == b"version one"
    assert svc.get_row(row["request_id"])["status"] == "pending"


def test_overwrite_is_refused_while_an_ai_works_but_keep_both_is_not(env):
    approve(make_request(b"version one"))
    row = make_request(b"version two")
    env["state"]["ai_active"] = True
    with pytest.raises(svc.RequestError) as busy:
        approve(row, conflict="overwrite", expected_existing_sha256=hashlib.sha256(b"version one").hexdigest())
    assert busy.value.code == "attachment_overwrite_blocked_ai_running"
    assert approve(row)["status"] == "approved"          # keep both still works during the run


def test_overwrite_failure_after_replace_restores_the_previous_bytes(env, monkeypatch):
    from modules.flow_gate.documents.attachments import registry

    approve(make_request(b"version one"))
    row = make_request(b"version two")

    def broken(**_kwargs):
        raise RuntimeError("registry down")

    monkeypatch.setattr(registry, "registry_replace", broken)
    with pytest.raises(svc.RequestError) as failed:
        approve(row, conflict="overwrite", expected_existing_sha256=hashlib.sha256(b"version one").hexdigest())
    assert failed.value.code == "attachment_request_approve_failed"
    assert room_file("report.md").read_bytes() == b"version one"
    assert registry_list(DOC)[0]["content_sha256"] == hashlib.sha256(b"version one").hexdigest()
    reverted = svc.get_row(row["request_id"])
    assert reverted["status"] == "pending" and reverted["decided_by"] is None
    assert svc.staged_file(reverted).exists()            # the person can try again


# ── target re-check: nothing follows a changed target ────────────────────────

def test_changed_target_is_refused_and_can_then_be_approved_explicitly(env):
    row = make_request()
    env["state"]["doc_id"] = DOC2                        # supersede / R<->B moved the card's document
    with pytest.raises(svc.RequestError) as changed:
        approve(row, doc_id=DOC)
    assert changed.value.status == 409 and changed.value.code == "attachment_target_changed"
    assert changed.value.details["current_doc_id"] == DOC2
    assert registry_list(DOC) == [] and svc.get_row(row["request_id"])["status"] == "pending"
    approved = approve(row, doc_id=DOC2)                 # the person confirmed the new target
    assert approved["final_doc_id"] == DOC2 and [r["filename"] for r in registry_list(DOC2)] == ["report.md"]


def test_target_change_inside_the_guard_is_caught(env):
    row = make_request()
    original = svc.card_resolver
    calls = {"n": 0}

    def flips(flow_id, node_key):
        calls["n"] += 1
        view = original(flow_id, node_key)
        if calls["n"] >= 2:                              # the in-guard re-check sees the change
            return svc.TargetView(flow_id, node_key, True, DOC2, "moved")
        return view

    svc.card_resolver = flips
    try:
        with pytest.raises(svc.RequestError) as changed:
            approve(row)
    finally:
        svc.card_resolver = original
    assert changed.value.code == "attachment_target_changed"
    assert registry_list(DOC) == []
    assert not [p for p in room_file("x").parent.glob("*") if p.name.startswith("report")]


# ── concurrent deciders: exactly one wins ────────────────────────────────────

def test_two_people_approving_at_once_register_one_attachment(env):
    row = make_request()
    results, errors = [], []
    barrier = threading.Barrier(2)

    def decide():
        barrier.wait()
        try:
            results.append(approve(row))
        except svc.RequestError as exc:
            errors.append(exc.code)

    threads = [threading.Thread(target=decide) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(results) == 1 and errors == ["attachment_request_already_decided"]
    assert len(registry_list(DOC)) == 1


def test_approve_and_reject_race_leaves_one_outcome(env):
    row = make_request()
    svc.decide(row["request_id"], ADMIN_USER, {"decision": "reject"})
    with pytest.raises(svc.RequestError) as late:
        approve(row)
    assert late.value.code == "attachment_request_already_decided"
    assert registry_list(DOC) == []


# ── permissions ──────────────────────────────────────────────────────────────

def test_any_update_permission_holder_decides_readers_only_preview(env):
    row = make_request()
    with pytest.raises(svc.RequestError) as viewer:
        approve(row, user=VIEWER_USER)
    assert viewer.value.status == 403
    assert svc.preview(row["request_id"], VIEWER_USER)["kind"] == "text"
    with pytest.raises(svc.RequestError) as stranger:
        svc.preview(row["request_id"], NOROLE_USER)
    assert stranger.value.status == 403
    # not the run's starter, not an admin: a role_worker decides
    other = svc.Requester(**{**chat_requester().__dict__, "issued_to": ADMIN})
    second = make_request(requester=other)
    assert approve(second, user=WORKER_USER)["decided_by"] == USER


def test_reject_removes_the_staged_file_and_attaches_nothing(env):
    row = make_request()
    rejected = svc.decide(row["request_id"], WORKER_USER, {"decision": "reject"})
    assert rejected["status"] == "rejected" and rejected["decided_by"] == USER
    assert not svc.staged_file(row).exists() and registry_list(DOC) == []


# ── preview and download before the decision ─────────────────────────────────

def test_preview_kinds_and_safe_download_headers(env):
    text = make_request(b"line one\nline two\n", name="notes.txt")
    assert svc.preview(text["request_id"], ADMIN_USER)["content"] == "line one\nline two\n"
    binary = make_request(b"\x00\x01\x02binary", name="blob.bin")
    assert svc.preview(binary["request_id"], ADMIN_USER)["kind"] == "binary"
    png = make_request(b"\x89PNG\r\n\x1a\n" + b"0" * 32, name="shot.png")
    assert svc.preview(png["request_id"], ADMIN_USER)["kind"] == "image"
    _path, meta = svc.content(png["request_id"], ADMIN_USER, inline=True)
    assert meta["media_type"] == "image/png" and meta["headers"]["Content-Disposition"] == "inline"
    html = make_request(b"<script>alert(1)</script>", name="page.html")
    _path, meta = svc.content(html["request_id"], ADMIN_USER, inline=True)
    assert meta["media_type"] == "application/octet-stream"
    assert meta["headers"]["Content-Disposition"].startswith("attachment")
    assert meta["headers"]["X-Content-Type-Options"] == "nosniff"


# ── settings: system defaults, project overrides inherit when unset, hard caps ─

def test_settings_inherit_and_clamp(env):
    assert request_settings.effective(PROJECT)["max_pending_per_target"] == 10
    request_settings.save_system({"max_pending_per_target": 3, "request_retention_days": 14}, ADMIN)
    assert request_settings.effective(PROJECT)["max_pending_per_target"] == 3
    request_settings.save_project(PROJECT, {"max_pending_per_target": 1}, ADMIN)
    view = request_settings.project_view(PROJECT)
    assert view["effective"]["max_pending_per_target"] == 1 and view["sources"]["max_pending_per_target"] == "project"
    assert view["effective"]["request_retention_days"] == 14 and view["sources"]["request_retention_days"] == "system"
    request_settings.save_project(PROJECT, {"max_pending_per_target": None}, ADMIN)   # back to inheritance
    assert request_settings.effective(PROJECT)["max_pending_per_target"] == 3
    with pytest.raises(request_settings.SettingsError):
        request_settings.save_project(PROJECT, {"max_pending_per_target": 51}, ADMIN)  # above the hard cap
    with pytest.raises(request_settings.SettingsError):
        request_settings.save_system({"max_file_bytes": 21 * 1024 * 1024}, ADMIN)    # above the human constant
    with pytest.raises(request_settings.SettingsError):
        request_settings.save_system({"no_such_key": 1}, ADMIN)


def test_corrupt_stored_settings_fall_back(env):
    from modules.flow_gate.db import system_settings as db_settings

    db_settings.set_value(request_settings.SYSTEM_KEY, "{not json", "string")
    assert request_settings.effective(PROJECT) == request_settings.defaults()


def test_limits_are_enforced_at_intake(env):
    request_settings.save_project(PROJECT, {"max_pending_per_target": 1, "max_file_bytes": 8,
                                            "blocked_extensions": [".EXE"]}, ADMIN)
    make_request(b"small")
    with pytest.raises(svc.RequestError) as pending:
        make_request(b"small")
    assert pending.value.code == "attachment_request_pending_limit"
    request_settings.save_project(PROJECT, {"max_file_bytes": 8, "blocked_extensions": ["exe"]}, ADMIN)
    with pytest.raises(svc.RequestError) as big:
        make_request(b"far too large for eight bytes", name="other.md", target={"node_key": "n_other"})
    assert big.value.code == "attachment_request_too_large"
    assert not any((svc.staging_root(PROJECT)).glob("aur_*/blob")) or all(
        svc.db.get(p.parent.name) for p in svc.staging_root(PROJECT).glob("aur_*/blob"))
    with pytest.raises(svc.RequestError) as blocked:
        make_request(b"x", name="tool.exe", target={"node_key": "n_other"})
    assert blocked.value.code == "attachment_request_extension_blocked"


def test_run_request_limit(env):
    request_settings.save_system({"max_requests_per_run": 1}, ADMIN)
    make_request(b"a", name="a.txt")
    with pytest.raises(svc.RequestError) as limited:
        make_request(b"b", name="b.txt", target={"node_key": "n_other"})
    assert limited.value.status == 429 and limited.value.code == "attachment_request_run_limit"


# ── retention and crash recovery ─────────────────────────────────────────────

def test_expiry_closes_the_request_and_removes_its_file(env):
    row = make_request()
    env["store"]._execute("UPDATE attachment_upload_requests SET expires_at = ? WHERE request_id = ?",
                          ["2000-01-01T00:00:00+09:00", row["request_id"]])
    assert svc.expire_due() == 1
    assert svc.get_row(row["request_id"])["status"] == "expired"
    assert not svc.staged_file(row).exists()
    with pytest.raises(svc.RequestError) as closed:
        approve(row)
    assert closed.value.code == "attachment_request_already_decided"


def test_expired_backup_is_removed(env):
    approve(make_request(b"version one"))
    done = approve(make_request(b"version two"), conflict="overwrite",
                   expected_existing_sha256=hashlib.sha256(b"version one").hexdigest())
    env["store"]._execute("UPDATE attachment_upload_requests SET backup_expires_at = ? WHERE request_id = ?",
                          ["2000-01-01T00:00:00+09:00", done["request_id"]])
    svc.expire_due()
    assert svc.get_row(done["request_id"])["backup_path"] is None
    assert not (svc.staging_dir(PROJECT, done["request_id"]) / svc.BACKUP_NAME).exists()


def test_recovery_restores_an_interrupted_overwrite(env):
    approve(make_request(b"version one"))
    row = make_request(b"version two")
    backup = svc.staging_dir(PROJECT, row["request_id"]) / svc.BACKUP_NAME
    backup.write_bytes(b"version one")
    room_file("report.md").write_bytes(b"version two")      # the crash happened after the replace
    svc.db.transition(row["request_id"], ("pending",), "approving", decision="overwrite",
                      final_doc_id=DOC, decided_by=ADMIN)
    assert svc.recover() == 1
    assert room_file("report.md").read_bytes() == b"version one"
    recovered = svc.get_row(row["request_id"])
    assert recovered["status"] == "pending" and recovered["error_code"] == "attachment_request_interrupted"
    assert not backup.exists()


def test_orphan_staging_folders_are_swept(env):
    orphan = svc.staging_root(PROJECT) / ("aur_" + "a" * 24)
    orphan.mkdir(parents=True)
    (orphan / "blob").write_bytes(b"left over")
    live = make_request()
    assert svc.sweep_orphans(PROJECT, now_ts=10**12) == 1
    assert not orphan.exists() and svc.staged_file(live).exists()


# ── chat timeline block and the card run result ──────────────────────────────

def test_chat_block_sits_after_the_runs_reply(env):
    from flowgate.conversation import activity

    row = make_request()
    seq = add_turn(CHAT_DOC, "ai", RUN_ID, body="here is the report")
    groups = activity.list_for_doc(CHAT_DOC)["attachments"]
    assert len(groups) == 1
    block = groups[0]
    assert block["run_id"] == RUN_ID and block["pending"] == 1
    assert (block["anchor_seq"], block["anchor_position"], block["anchor_state"]) == (seq, "after", "reply")
    assert block["requests"][0]["request_id"] == row["request_id"]


def test_run_result_counts_pending_requests(env):
    from flowgate.graph_run import command_result

    make_request(requester=card_requester())
    assert command_result._attachment_requests("grun_1", GROUP) == {"pending": 1, "flow_pending": 1}


# ── HTTP: decision while the group lease is held, worker intake, human routes ─

def _app(*routers, user=ADMIN_USER, middleware=True):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.services import mutation_policy as policy

    app = FastAPI()
    if middleware:
        app.add_middleware(policy.GroupMutationPolicyMiddleware)
    for router in routers:
        app.include_router(router, prefix="/flowgate" if router.prefix.startswith("/api") else "/flowgate/api/v1")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture()
def held_lease(monkeypatch):
    from modules.flow_gate.services import mutation_policy as policy

    lease = {"group_id": GROUP, "run_id": "aiv_other_run", "token_id": "tok_other", "action_scope": "chat"}
    monkeypatch.setattr(policy.db_leases, "get_active", lambda group_id: dict(lease) if group_id == GROUP else None)
    monkeypatch.setattr(policy.db_leases, "heartbeat", lambda group_id, run_id: None)
    return lease


def test_decision_is_not_locked_by_the_group_ai_lease(env, held_lease):
    from modules.flow_gate.api.v1 import attachment_request_routes

    client = _app(attachment_request_routes.router)
    row = make_request()
    res = client.post(f"/flowgate/api/v1/attachment-requests/{row['request_id']}/decision",
                      json={"decision": "approve", "expected_doc_id": DOC})
    assert res.status_code == 200, res.text
    assert res.json()["request"]["status"] == "approved"


def test_http_target_changed_and_forbidden_shapes(env):
    from modules.flow_gate.api.v1 import attachment_request_routes

    row = make_request()
    env["state"]["doc_id"] = DOC2
    res = _app(attachment_request_routes.router, middleware=False).post(
        f"/flowgate/api/v1/attachment-requests/{row['request_id']}/decision",
        json={"decision": "approve", "expected_doc_id": DOC})
    assert res.status_code == 409 and res.json()["error"]["code"] == "attachment_target_changed"
    res = _app(attachment_request_routes.router, user=VIEWER_USER, middleware=False).post(
        f"/flowgate/api/v1/attachment-requests/{row['request_id']}/decision", json={"decision": "reject"})
    assert res.status_code == 403


def test_worker_intake_over_http_answers_at_once_even_under_another_runs_lease(env, held_lease, monkeypatch):
    from modules.flow_gate.api.v1 import document_routes

    monkeypatch.setattr(svc, "requester_from_raw_token", lambda raw: chat_requester())
    client = _app(document_routes.router)
    res = client.post("/flowgate/api/v1/attachment-requests", headers={"Authorization": "Bearer t"},
                      files={"file": ("report.md", b"over http", "text/markdown")},
                      data={"meta": json.dumps({"target": {"node_key": NODE}, "note": "n"})})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "pending" and body["target"]["doc_id"] == DOC
    bad = client.post("/flowgate/api/v1/attachment-requests", headers={"Authorization": "Bearer t"},
                      files={"file": ("r.md", b"x", "text/plain")}, data={"meta": "{not json"})
    assert bad.status_code == 422


def test_human_attachment_routes_check_project_permission(env):
    from modules.flow_gate.documents.routers import documents

    stranger = _app(documents.router, user=NOROLE_USER, middleware=False)
    assert stranger.get(f"/flowgate/api/v1/documents/{DOC}/attachments").status_code == 403
    viewer = _app(documents.router, user=VIEWER_USER, middleware=False)
    assert viewer.get(f"/flowgate/api/v1/documents/{DOC}/attachments").status_code == 200
    res = viewer.post(f"/flowgate/api/v1/documents/{DOC}/attachments", files={"file": ("a.txt", b"x")})
    assert res.status_code == 403
    admin = _app(documents.router, middleware=False)
    assert admin.get(f"/flowgate/api/v1/documents/{DOC}/attachments").status_code == 200


def test_settings_routes_are_for_administrators(env):
    from modules.flow_gate.api.v1 import attachment_request_routes

    admin = _app(attachment_request_routes.router, middleware=False)
    res = admin.put(f"/flowgate/api/v1/projects/{PROJECT}/attachment-request-settings",
                    json={"overrides": {"max_pending_per_target": 2}})
    assert res.status_code == 200, res.text
    assert res.json()["effective"]["max_pending_per_target"] == 2
    res = admin.put("/flowgate/api/v1/settings/attachment-requests", json={"values": {"max_pending_per_target": 99}})
    assert res.status_code == 422
    worker = _app(attachment_request_routes.router, user=WORKER_USER, middleware=False)
    assert worker.get("/flowgate/api/v1/settings/attachment-requests").status_code == 403
    limits = worker.get(f"/flowgate/api/v1/attachment-requests/limits?project_id={PROJECT}").json()
    assert limits["values"]["max_pending_per_target"] == 2 and limits["sources"]["max_pending_per_target"] == "project"


# ── tool exposure and route policy ───────────────────────────────────────────

def test_api_tool_reads_only_inside_the_worktree(env, monkeypatch, tmp_path):
    from modules.flow_gate.documents.attachments import request_tools

    root = tmp_path / "wt"
    (root / "out").mkdir(parents=True)
    (root / "out" / "result.csv").write_bytes(b"a,b\n1,2\n")
    monkeypatch.setattr(svc, "requester_from_run", lambda run: card_requester())
    monkeypatch.setattr(svc, "_source_root", lambda requester: root.resolve())
    status, body = request_tools.call({"run_id": "aiv_card_1"}, {"source_path": "out/result.csv", "note": "data"})
    assert status == 201 and body["status"] == "pending" and body["filename"] == "result.csv"
    for bad in ("../escape.txt", "/etc/passwd", "C:/x.txt"):
        status, body = request_tools.call({"run_id": "aiv_card_1"}, {"source_path": bad})
        assert status in (403, 422), bad
    status, body = request_tools.call({"run_id": "aiv_card_1"}, {"source_path": "out/missing.txt"})
    assert status == 404


def test_chat_descriptor_is_offered_with_a_cli_mention():
    from flowgate.conversation import integration

    tools = {tool["name"]: tool for tool in integration.provide_chat_tools(None)}
    tool = tools["request_attachment_upload"]
    assert tool["schema"]["required"] == ["source_path", "target"]
    mention = tool["cli_mention"]("http://x/flowgate/api/v1", "TOKEN")
    assert "POST http://x/flowgate/api/v1/attachment-requests" in mention and "do not wait" in mention


def test_card_worker_catalog_and_route_policy():
    from modules.flow_gate.services import mutation_policy as policy
    from modules.flow_gate.services import tool_registry

    assert "attachment_upload_request" in tool_registry.attachment_names("read")
    assert "attachment_upload_request" in tool_registry.attachment_names("read_write")
    assert "attachment_upload_request" not in tool_registry.attachment_names("none")
    assert policy.is_group_lease_exempt_path("/flowgate/api/v1/attachment-requests/aur_x/decision")
    assert policy.is_group_lease_exempt_path("/flowgate/api/v1/attachment-requests")
    assert not policy.is_group_lease_exempt_path("/flowgate/api/v1/attachment-requests/aur_x/decision", "DELETE")
    assert not policy.is_group_lease_exempt_path("/flowgate/api/v1/documents/x/attachments")
    assert policy.classify_mutation_route("/flowgate/api/v1/attachment-requests/{request_id}/decision", {"POST"}) == (
        "group", "attachment_request_decision_lease_exempt")
    assert policy.classify_mutation_route("/flowgate/api/v1/attachment-requests", {"POST"}) == (
        "system", "attachment_request_worker")


# ── the real card resolver (the Canvas projection), standalone and inside the document guard ─

def test_real_card_resolver_runs_inside_the_document_guard(fg_store, tmp_path, monkeypatch):
    """The suite above stubs the resolver; this runs the real projection on a seeded Flow, both on
    its own and inside the document guard the approval commit holds (it must join, not nest)."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.services.flow_guard import run_in_document_guard

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fg_harness_write_gate

    fg_harness_write_gate.install(monkeypatch)
    seed(fg_store)
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    db_documents.create({"doc_id": DOC, "project_id": PROJECT, "module": MODULE, "group_id": GROUP,
                         "type_code": "R", "seq": 2, "title": "card doc", "status": "open", "owner_id": USER})
    outside = svc._project_card(GROUP, "no-such-node")
    assert outside.exists is False and outside.doc_id is None
    inside = run_in_document_guard(DOC, lambda: svc._project_card(GROUP, "no-such-node"))
    assert inside.exists is False
    with pytest.raises(svc.RequestError) as missing:
        svc.resolve_card("fgp.v02.9999", NODE)
    assert missing.value.code in ("attachment_target_flow_not_found", "attachment_target_unavailable")


# ── bulk decision: approve all / reject all / the selected ones ──────────────

def bulk(user=ADMIN_USER, decision="approve", rows=(), doc_id: str = DOC) -> dict:
    items = [{"request_id": r["request_id"], **({"expected_doc_id": doc_id} if decision == "approve" else {})}
             for r in rows]
    return svc.decide_many(user, {"decision": decision, "items": items})


def outcomes(result: dict) -> dict:
    return {r["request_id"]: (r["outcome"], r.get("error", {}).get("code")) for r in result["results"]}


def test_bulk_approve_keeps_both_and_skips_what_needs_a_look(env):
    approve(make_request(b"v1", name="report.md"))
    same = make_request(b"v2", name="REPORT.md")          # same name on the target (case-folded)
    plain = [make_request(f"f{i}".encode(), name=f"f{i}.md") for i in range(3)]
    result = bulk(rows=[same, *plain])
    got = outcomes(result)
    assert got[same["request_id"]] == ("skipped", "attachment_name_conflict")
    assert all(got[r["request_id"]] == ("done", None) for r in plain)
    assert result["counts"] == {"done": 3, "skipped": 1, "failed": 0}
    assert svc.get_row(same["request_id"])["status"] == "pending"     # left for its own row
    names = sorted(r["filename"] for r in registry_list(DOC))
    assert names == ["f0.md", "f1.md", "f2.md", "report.md"]
    assert room_file("report.md").read_bytes() == b"v1"               # the existing file is untouched
    done = [r["request"] for r in result["results"] if r["outcome"] == "done"]
    assert all(r["status"] == "approved" and r["decision"] == "keep_both" for r in done)


def test_bulk_approve_skips_a_changed_target_and_registers_nothing_there(env):
    first, second = make_request(name="a.md"), make_request(name="b.md")
    env["state"]["doc_id"] = DOC2
    result = bulk(rows=[first, second], doc_id=DOC)
    assert set(outcomes(result).values()) == {("skipped", "attachment_target_changed")}
    assert registry_list(DOC) == [] and registry_list(DOC2) == []
    assert {svc.get_row(r["request_id"])["status"] for r in (first, second)} == {"pending"}


def test_bulk_reject_closes_each_and_skips_the_already_decided(env):
    rows = [make_request(name=f"r{i}.md") for i in range(3)]
    approve(rows[0])
    result = bulk(user=WORKER_USER, decision="reject", rows=rows)
    got = outcomes(result)
    assert got[rows[0]["request_id"]] == ("skipped", "attachment_request_already_decided")
    assert got[rows[1]["request_id"]] == ("done", None) and got[rows[2]["request_id"]] == ("done", None)
    for row in rows[1:]:
        assert svc.get_row(row["request_id"])["status"] == "rejected" and not svc.staged_file(row).exists()
    assert [r["filename"] for r in registry_list(DOC)] == ["r0.md"]


def test_bulk_decision_checks_permission_per_request(env):
    rows = [make_request(name=f"p{i}.md") for i in range(2)]
    result = bulk(user=VIEWER_USER, rows=rows)
    assert result["counts"] == {"done": 0, "skipped": 0, "failed": 2}
    assert {r["status"] for r in result["results"]} == {403}
    assert registry_list(DOC) == [] and {svc.get_row(r["request_id"])["status"] for r in rows} == {"pending"}


def test_bulk_input_is_validated_before_anything_is_decided(env):
    row = make_request()
    for body, code in (
        ({"decision": "approve", "items": [{"request_id": row["request_id"]}]}, "attachment_request_invalid"),
        ({"decision": "maybe", "items": [{"request_id": row["request_id"]}]}, "attachment_request_invalid_decision"),
        ({"decision": "reject", "items": []}, "attachment_request_invalid"),
        ({"decision": "reject", "items": [{"request_id": row["request_id"], "conflict": "overwrite"}]},
         "attachment_request_invalid"),
        ({"decision": "reject", "items": [{"request_id": f"aur_{i:024x}"} for i in range(svc.BULK_MAX_ITEMS + 1)]},
         "attachment_request_too_many"),
    ):
        with pytest.raises(svc.RequestError) as refused:
            svc.decide_many(ADMIN_USER, body)
        assert refused.value.status == 422 and refused.value.code == code
    assert svc.get_row(row["request_id"])["status"] == "pending"
    # the same request twice is decided once; an unknown id is a failure of its own
    twice = svc.decide_many(ADMIN_USER, {"decision": "reject", "items": [
        {"request_id": row["request_id"]}, {"request_id": row["request_id"]}, {"request_id": "aur_nope"}]})
    assert [r["outcome"] for r in twice["results"]] == ["done", "failed"]


def test_bulk_decision_over_http_is_not_locked_by_the_group_ai_lease(env, held_lease):
    from modules.flow_gate.api.v1 import attachment_request_routes
    from modules.flow_gate.services import mutation_policy as policy

    rows = [make_request(name=f"h{i}.md") for i in range(2)]
    res = _app(attachment_request_routes.router).post("/flowgate/api/v1/attachment-requests/decisions", json={
        "decision": "approve", "items": [{"request_id": r["request_id"], "expected_doc_id": DOC} for r in rows]})
    assert res.status_code == 200, res.text
    assert res.json()["counts"] == {"done": 2, "skipped": 0, "failed": 0}
    bad = _app(attachment_request_routes.router, middleware=False).post(
        "/flowgate/api/v1/attachment-requests/decisions", json={"decision": "approve", "items": []})
    assert bad.status_code == 422
    assert policy.is_group_lease_exempt_path("/flowgate/api/v1/attachment-requests/decisions")
    assert not policy.is_group_lease_exempt_path("/flowgate/api/v1/attachment-requests/decisions", "PUT")
    assert policy.classify_mutation_route("/flowgate/api/v1/attachment-requests/decisions", {"POST"}) == (
        "group", "attachment_request_bulk_decision_lease_exempt")
