"""Message image API, transaction and retention self-checks with real SQLite."""
import base64
import io
import os
import struct
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from _fg_conversation_support import fg_store, seed, GROUP, PROJECT, CHAT_DOC, USER
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.api.v1 import message_image_routes
from modules.flow_gate.documents.routers import conversation_turns
from modules.flow_gate.services import message_image_service as images
from modules.flow_gate.services import conversation_turn_service as turns
from modules.flow_gate.services import mutation_policy as policy
from modules.flow_gate.db import conversation_turns as turn_db, group_ai_leases
from modules.flow_gate.db.connection import now_iso

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jS1cAAAAASUVORK5CYII=")
USER_ROW = {"user_id": USER, "username": "User", "is_admin": True}


@pytest.fixture(autouse=True)
def restore_lazy_agent_environment():
    # Lazy shared-service initialization can create this variable. Preserve the
    # pre-test value, including absence, instead of leaking it into later suites.
    name = "FLOWGATE_AGENT_PEPPER_ID"
    original = os.environ.get(name)
    yield
    if original is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = original


@pytest.fixture
def env(fg_store, tmp_path, monkeypatch):
    seed(fg_store)
    from config import settings
    monkeypatch.setattr(settings, "CONTEXT", "")
    monkeypatch.setattr(images.storage_paths, "get_storage_root", lambda project_id=None: tmp_path)
    # Keep document lifecycle and event emission outside this service-focused fixture.
    doc = {"doc_id": CHAT_DOC, "group_id": GROUP, "project_id": PROJECT, "type_code": "CH"}
    monkeypatch.setattr(turns, "_validate_document_for_append", lambda doc_id: doc)
    from modules.flow_gate.services import conversation_events, chat_activity_anchor_service
    monkeypatch.setattr(conversation_events, "broadcast_turn_appended", lambda *a: None)
    monkeypatch.setattr(chat_activity_anchor_service, "on_turn_appended", lambda *a: None)
    app = FastAPI()
    app.add_middleware(policy.GroupMutationPolicyMiddleware)
    app.include_router(message_image_routes.router, prefix="/api/v1")
    app.include_router(conversation_turns.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: USER_ROW
    return fg_store, TestClient(app)


def upload(data=PNG, user=USER_ROW, flow=GROUP):
    return images.upload(flow, SimpleNamespace(file=io.BytesIO(data), filename="fake.png"), user)["image"]


def append(ids=None, key="message-key-0001"):
    return turns.append_turn(doc_id=CHAT_DOC, actor={"kind": "session", "user_id": USER, "user_name": "User"},
                             body_raw="hello", idempotency_key=key, images=ids)


def test_upload_read_delete_and_permission(env):
    store, client = env
    root = f"/api/v1/flows/{GROUP}/message-images"
    response = client.post(root, files={"file": ("fake.jpg", PNG, "text/plain")})
    assert response.status_code == 200
    image = response.json()["image"]
    assert (image["media_type"], image["width"], image["height"]) == ("image/png", 1, 1)
    response = client.get(image["url"])
    assert response.content == PNG
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["content-type"] == "image/png"
    assert store._fetch_all("SELECT * FROM attachments") == []
    row = store._fetch_one("SELECT * FROM message_images")
    assert row["state"] == "temp" and row["file_path"].startswith("work/_message_images/")
    assert client.delete(root + "/" + image["image_id"]).status_code == 200
    assert client.get(image["url"]).status_code == 404
    with pytest.raises(images.MessageImageError) as error:
        upload(user={"user_id": USER})
    assert error.value.status == 403


@pytest.mark.parametrize("data,status,code", [
    (b"plain text", 415, "message_image_unsupported_type"),
    (PNG + b"x" * images.MAX_IMAGE_BYTES, 413, "message_image_too_large"),
    (PNG[:16] + struct.pack(">II", 1569, 1) + PNG[24:], 422, "message_image_too_large_dimension"),
    (PNG[:12], 422, "message_image_invalid_dimensions"),
    (PNG[:16] + bytes(8) + PNG[24:], 422, "message_image_invalid_dimensions"),
], ids=["disguised", "over_bytes", "over_dimensions", "truncated", "zero_size"])
def test_upload_rejections(env, data, status, code):
    with pytest.raises(images.MessageImageError) as error:
        upload(data)
    assert (error.value.status, error.value.code) == (status, code)
    assert env[0]._fetch_all("SELECT * FROM message_images") == []


def test_link_replay_conflict_body_and_window(env):
    first, second = upload(), upload()
    ids = [first["image_id"], second["image_id"]]
    result = append(ids)
    assert result["turn"]["images"] == [first, second]
    assert result["turn"]["body"] == "hello"
    assert append(ids)["replayed"] is True
    for changed in (list(reversed(ids)), [], [ids[0]]):
        with pytest.raises(turns.ConversationTurnError) as error:
            append(changed)
        assert error.value.status_code == 409
    assert turn_db.count_turns(CHAT_DOC) == 1
    assert len(env[0]._fetch_all("SELECT * FROM message_image_links")) == 2
    window = images.images_for_window(CHAT_DOC, 0)
    assert [i["image_id"] for i in window] == ids
    assert all(Path(i["abs_path"]).read_bytes() == PNG for i in window)
    assert images.images_for_window(CHAT_DOC, 1) == []
    assert images.images_for_window(CHAT_DOC, 0, 1)[0]["image_id"] == ids[-1]
    with pytest.raises(images.MessageImageError):
        images.remove(GROUP, ids[0], USER_ROW)
    import hashlib
    assert turn_db.list_turns(CHAT_DOC)[0]["body_hash"] == hashlib.sha256(b"hello").hexdigest()


@pytest.mark.parametrize("invalid", ["missing", "other_owner", "other_flow", "linked", "duplicate", "six"])
def test_bad_link_rolls_back_turn_and_prior_links(env, invalid):
    store, _ = env
    good, bad = upload(), upload()
    bad_id = bad["image_id"]
    if invalid == "missing":
        bad_id = "img_" + "0" * 32
    elif invalid == "other_owner":
        store._execute("UPDATE message_images SET uploaded_by = 'other' WHERE image_id = ?", [bad_id])
    elif invalid == "other_flow":
        store._execute("UPDATE message_images SET flow_id = 'other' WHERE image_id = ?", [bad_id])
    elif invalid == "linked":
        store._execute("UPDATE message_images SET state = 'linked' WHERE image_id = ?", [bad_id])
    ids = [good["image_id"], bad_id]
    if invalid == "duplicate":
        ids = [good["image_id"]] * 2
    if invalid == "six":
        ids = [upload()["image_id"] for _ in range(6)]
    with pytest.raises(turns.ConversationTurnError) as error:
        append(ids)
    assert (error.value.status_code, error.value.message) == (422, "message_image_invalid_link")
    assert turn_db.count_turns(CHAT_DOC) == 0
    assert store._fetch_all("SELECT * FROM message_image_links") == []
    assert store._fetch_one("SELECT state FROM message_images WHERE image_id = ?", [good["image_id"]])["state"] == "temp"


def test_foreign_temp_cannot_read_or_delete(env):
    image = upload()
    other = {"user_id": "other", "is_admin": True}
    for operation in (images.remove, images.content):
        with pytest.raises(images.MessageImageError) as error:
            operation(GROUP, image["image_id"], other)
        assert error.value.status == 403
    assert env[0]._fetch_one("SELECT * FROM message_images") is not None


def test_retention_keeps_linked_and_fresh(env):
    store, _ = env
    expired, linked, fresh = upload(), upload(), upload()
    append([linked["image_id"]])
    old = (datetime.fromisoformat(now_iso()) - timedelta(hours=25)).isoformat(timespec="seconds")
    store._execute("UPDATE message_images SET created_at = ? WHERE image_id <> ?", [old, fresh["image_id"]])
    path = images.content(GROUP, expired["image_id"], USER_ROW)[1]
    assert images.sweep_expired() == 1
    assert not path.exists()
    assert {r["image_id"] for r in store._fetch_all("SELECT * FROM message_images")} == {linked["image_id"], fresh["image_id"]}
    assert images.sweep_expired() == 0


def test_upload_and_turn_while_ai_holds_lease(env):
    lease = group_ai_leases.acquire(group_id=GROUP, project_id=PROJECT, run_id="image-test-run",
                                    chain_id=None, action_scope="chat", worker_identity="test-worker")
    assert lease["run_id"] == "image-test-run"
    client = env[1]
    response = client.post(f"/api/v1/flows/{GROUP}/message-images",
                           files={"file": ("image.png", PNG, "image/png")})
    assert response.status_code == 200
    image = response.json()["image"]
    response = client.post(f"/api/v1/documents/{CHAT_DOC}/conversation/turn",
                           json={"body": "hello", "idempotency_key": "lease-test-key", "images": [image["image_id"]]})
    assert response.status_code == 201
    assert response.json()["turn"]["images"] == [image]


@pytest.mark.parametrize("kind,exec_type,location,expected", [
    ("claude", "cli", "local", True), ("claude", "cli", "remote", False),
    ("claude", "api", "local", False), ("codex", "cli", "local", False),
    ("copilot", "cli", "local", False),
])
def test_provider_capability(kind, exec_type, location, expected):
    assert images.provider_accepts_images(dict(kind=kind, exec_type=exec_type, exec_location=location)) is expected


@pytest.mark.parametrize("media,data", [
    ("image/gif", b"GIF89a" + struct.pack("<HH", 20, 30) + bytes(3)),
    ("image/jpeg", b"\xff\xd8\xff\xc0" + struct.pack(">HBHHB", 8, 8, 30, 20, 0)),
    ("image/webp", b"RIFF" + struct.pack("<I", 22) + b"WEBPVP8X" + struct.pack("<I", 10) + bytes(4) + (19).to_bytes(3, "little") + (29).to_bytes(3, "little")),
    ("image/webp", b"RIFF" + struct.pack("<I", 18) + b"WEBPVP8L" + struct.pack("<I", 5) + b"\x2f" + (19 | (29 << 14)).to_bytes(4, "little") + bytes(1)),
    ("image/webp", b"RIFF" + struct.pack("<I", 22) + b"WEBPVP8 " + struct.pack("<I", 10) + bytes(3) + b"\x9d\x01\x2a" + struct.pack("<HH", 20, 30)),
])
def test_raster_dimension_headers(media, data):
    assert images.dimensions(data, media) == (20, 30)


def test_storage_jail_rejects_tampered_path(env):
    image = upload()
    env[0]._execute("UPDATE message_images SET file_path = '../outside' WHERE image_id = ?", [image["image_id"]])
    with pytest.raises(images.MessageImageError) as error:
        images.content(GROUP, image["image_id"], USER_ROW)
    assert error.value.code == "message_image_storage_outside_root"


def test_provider_endpoint_emits_capability_without_secrets(env, monkeypatch):
    from modules.flow_gate.api.v1 import ai_invoke_routes as routes
    monkeypatch.setattr(routes, "_require_user", lambda request: {"issued_to": USER, "is_admin": True})
    monkeypatch.setattr(routes.ai_invoke_service, "list_runtime_providers", lambda *args: {
        "ok": True, "providers": [{"id": "local"}, {"id": "remote"}, {"id": "api"}]})
    store = env[0]
    for provider_id, exec_type, location in (("local", "cli", "local"),
                                               ("remote", "cli", "remote"), ("api", "api", "local")):
        store._execute(
            "INSERT INTO ai_providers (provider_id, project_id, name, exec_type, kind, exec_location, "
            "api_key, created_at, updated_at) VALUES (?, ?, ?, ?, 'claude', ?, ?, ?, ?)",
            [provider_id, PROJECT, provider_id, exec_type, location, "secret-must-not-appear", now_iso(), now_iso()])
    app = FastAPI()
    app.include_router(routes.router)
    response = TestClient(app).get("/api/v1/ai-invoke/providers", params={"project": PROJECT})
    assert response.status_code == 200
    assert response.json()["providers"] == [
        {"id": "local", "accepts_images": True}, {"id": "remote", "accepts_images": False},
        {"id": "api", "accepts_images": False}]


def test_turn_get_and_plain_text_images(env, monkeypatch):
    from modules.flow_gate.services import conversation_query_service as query
    image = upload()
    append([image["image_id"]])
    append(key="plain-text-key")
    doc = {"doc_id": CHAT_DOC, "project_id": PROJECT, "group_id": GROUP, "type_code": "CH"}
    monkeypatch.setattr(query.document_service, "get_document", lambda doc_id: doc)
    monkeypatch.setattr(query, "_ensure_readable_rows", lambda doc_id: "migrated")
    response = env[1].get(f"/api/v1/documents/{CHAT_DOC}/conversation/turns")
    assert response.status_code == 200
    assert [turn["images"] for turn in response.json()["turns"]] == [[image], []]


def test_disposed_upload_and_configured_url(env, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "CONTEXT", "/flowgate")
    image = upload()
    assert image["url"].startswith("/flowgate/api/v1/")
    monkeypatch.setattr(images.process_service, "is_group_disposed", lambda flow: True)
    with pytest.raises(images.MessageImageError) as error:
        upload()
    assert error.value.status == 409


def test_window_selects_latest_ten_in_message_order(env):
    ids = []
    for number in range(3):
        batch = [upload()["image_id"] for _ in range(5)]
        ids.extend(batch)
        append(batch, key=f"window-key-{number}")
    assert [row["image_id"] for row in images.images_for_window(CHAT_DOC, 0)] == ids[-10:]
    assert [row["image_id"] for row in images.images_for_window(CHAT_DOC, 2)] == ids[-5:]


@pytest.mark.parametrize("path,method,expected", [
    ("/flowgate/api/v1/flows/f/message-images", "POST", True),
    ("/api/v1/flows/f/message-images/img_1", "DELETE", True),
    ("/api/v1/flows/f/message-images/img_1/content", "POST", False),
    ("/api/v1/documents/chat/conversation/turn", "POST", True),
    ("/api/v1/documents/chat/conversation/turn", "DELETE", False),
    ("/api/v1/conversation/chat/turn", "POST", False),
])
def test_lease_exemptions_are_narrow(path, method, expected):
    assert policy.is_group_lease_exempt_path(path, method) is expected


@pytest.mark.parametrize("role,can_write", [("role_viewer", False), ("role_worker", True)])
def test_real_project_rbac_read_and_upload(env, role, can_write):
    from modules.flow_gate.rbac import permission_service
    store, client = env
    user = {"user_id": USER, "username": "User", "is_admin": False}
    store._execute("INSERT INTO user_project_roles (user_id, project_id, role_id, granted_at) VALUES (?, ?, ?, ?)",
                   [USER, PROJECT, role, now_iso()])
    permission_service.invalidate_cache(USER)
    client.app.dependency_overrides[get_current_user] = lambda: user
    try:
        assert images.attachments.has_permission(user, "perm_document_read", PROJECT)
        assert images.attachments.has_permission(user, "perm_document_update", PROJECT) is can_write
        image = upload()
        assert client.get(image["url"]).status_code == 200
        response = client.post(f"/api/v1/flows/{GROUP}/message-images",
                               files={"file": ("image.png", PNG, "image/png")})
        assert response.status_code == (200 if can_write else 403)
        assert len(store._fetch_all("SELECT * FROM message_images")) == (2 if can_write else 1)
        if not can_write:
            assert response.json()["error"]["code"] == "message_image_forbidden"
    finally:
        permission_service.invalidate_cache(USER)


def test_list_images_one_query_per_page(env, monkeypatch):
    from modules.flow_gate.services import conversation_query_service as query
    store, client = env
    first, second = upload(), upload()
    append([first["image_id"], second["image_id"]])
    for i in range(8):
        append(key=f"plain-page-{i}")
    monkeypatch.setattr(query.document_service, "get_document", lambda _: {
        "doc_id": CHAT_DOC, "project_id": PROJECT, "group_id": GROUP, "type_code": "CH"})
    monkeypatch.setattr(query, "_ensure_readable_rows", lambda _: "migrated")
    original = store._fetch_all
    calls = []
    def fetch(sql, args=None):
        if "JOIN message_images" in sql:
            calls.append((sql, args))
        return original(sql, args)
    monkeypatch.setattr(store, "_fetch_all", fetch)
    for params in ({"after_seq": 0}, {"before_seq": 10}, {"after_seq": 1}):
        calls.clear()
        response = client.get(f"/api/v1/documents/{CHAT_DOC}/conversation/turns", params=params)
        assert response.status_code == 200
        page = response.json()["turns"]
        assert len(calls) == 1
        assert all(t["images"] == ([first, second] if t["seq"] == 1 else []) for t in page)
    calls.clear()
    assert client.get(f"/api/v1/documents/{CHAT_DOC}/conversation/turns", params={"after_seq": 99}).json()["turns"] == []
    assert calls == []


def test_sweep_bad_path_does_not_block_other_expired_rows(env, caplog):
    store, _ = env
    bad, good = upload(), upload()
    good_path = images.content(GROUP, good["image_id"], USER_ROW)[1]
    store._execute("UPDATE message_images SET created_at = '2000-01-01T00:00:00'")
    store._execute("UPDATE message_images SET file_path = '../outside' WHERE image_id = ?", [bad["image_id"]])
    assert images.sweep_expired() == 1
    assert not good_path.exists()
    assert store._fetch_one("SELECT * FROM message_images WHERE image_id = ?", [bad["image_id"]])
    assert "removed=1 failed=1" in caplog.text


def test_sweep_commit_failure_preserves_file_and_continues(env, monkeypatch, caplog):
    from contextlib import contextmanager
    store, _ = env
    first, second = upload(), upload()
    paths = {image["image_id"]: images.content(GROUP, image["image_id"], USER_ROW)[1]
             for image in (first, second)}
    store._execute("UPDATE message_images SET created_at = '2000-01-01T00:00:00'")
    original = store._db.begin_transaction
    attempts = []
    @contextmanager
    def fail_first_commit():
        with original() as txn:
            yield txn
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("injected commit failure")
    monkeypatch.setattr(store._db, "begin_transaction", fail_first_commit)
    assert images.sweep_expired() == 1
    rows = store._fetch_all("SELECT * FROM message_images")
    assert len(rows) == 1 and paths[rows[0]["image_id"]].exists()
    assert sum(path.exists() for path in paths.values()) == 1
    assert "removed=1 failed=1" in caplog.text


def test_sweep_unlink_failure_is_logged_and_continues(env, monkeypatch, caplog):
    store, _ = env
    first, second = upload(), upload()
    blocked = images.content(GROUP, first["image_id"], USER_ROW)[1]
    other = images.content(GROUP, second["image_id"], USER_ROW)[1]
    store._execute("UPDATE message_images SET created_at = '2000-01-01T00:00:00'")
    unlink = Path.unlink
    def fail_one(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("injected unlink failure")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_one)
    assert images.sweep_expired() == 1
    assert blocked.exists() and not other.exists()
    assert store._fetch_all("SELECT * FROM message_images") == []
    assert "removed=1 failed=1" in caplog.text


def test_linking_images_does_not_change_markdown_or_turn_hash(env, monkeypatch):
    from modules.flow_gate.services import conversation_markdown_service as markdown
    store, _ = env
    append()
    turn_db.ensure_migration_row(CHAT_DOC)
    store._execute("UPDATE conversation_docs SET migration_state = 'migrated', intro = '' WHERE doc_id = ?", [CHAT_DOC])
    doc = {"doc_id": CHAT_DOC, "group_id": GROUP, "project_id": PROJECT, "type_code": "CH"}
    monkeypatch.setattr(markdown.document_service, "get_document", lambda _: doc)
    before = markdown.render_markdown(CHAT_DOC)
    before_turn = turn_db.list_turns(CHAT_DOC)[0]
    image = upload()
    with store.transaction():
        images.link(doc, 1, USER, [image["image_id"]])
    after = markdown.render_markdown(CHAT_DOC)
    after_turn = turn_db.list_turns(CHAT_DOC)[0]
    assert (before["content"], before["fingerprint"]) == (after["content"], after["fingerprint"])
    assert (before_turn["body"], before_turn["body_hash"]) == (after_turn["body"], after_turn["body_hash"])
    assert image["image_id"] not in after["content"]


def test_plain_text_turn_while_ai_holds_lease(env):
    group_ai_leases.acquire(group_id=GROUP, project_id=PROJECT, run_id="plain-test-run",
                           chain_id=None, action_scope="chat", worker_identity="test-worker")
    response = env[1].post(f"/api/v1/documents/{CHAT_DOC}/conversation/turn",
                           json={"body": "plain hello", "idempotency_key": "plain-lease-key"})
    assert response.status_code == 201
    assert response.json()["turn"]["images"] == []
