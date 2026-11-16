"""Flow namespace regression: legacy provenance, collision choices, RBAC and rollback."""
import io
import hashlib
from zipfile import ZipFile
import pytest
from starlette.datastructures import UploadFile
from test_attachment_upload_requests_0017 import (
    env, fg_store, clean_runs, make_request, approve, ADMIN_USER, VIEWER_USER, NOROLE_USER,
    DOC, DOC2, GROUP, PROJECT, room_file, chat_requester, held_lease, _app,
)
from modules.flow_gate.documents.attachments import flow
from modules.flow_gate.documents.attachments.errors import AttachmentError
from modules.flow_gate.documents.attachments.requests import RequestError

def test_default_ai_request_uses_flow_namespace_and_overwrites_legacy_file(env):
    from modules.flow_gate.documents.attachments import requests as svc
    approve(make_request(b"legacy", name="same.txt"))
    request = svc.create_from_stream(chat_requester(), {}, "same.txt", io.BytesIO(b"replacement").read)
    assert request["target_node_key"] == "__flow__"
    assert flow.pending_requests(GROUP, ADMIN_USER)["requests"][0]["request_id"] == request["request_id"]
    public = svc.public(request)
    assert public["conflict"]["content_sha256"] == hashlib.sha256(b"legacy").hexdigest()
    svc.decide(request["request_id"], ADMIN_USER, {"decision": "approve", "expected_doc_id": request["target_doc_id"],
        "conflict": "overwrite", "expected_existing_sha256": public["conflict"]["content_sha256"]})
    assert room_file("same.txt").read_bytes() == b"replacement"
    assert len(flow.listing(GROUP, ADMIN_USER)["attachments"]) == 1
    assert flow.pending_requests(GROUP, ADMIN_USER)["requests"] == []

def test_flow_http_paths_work_under_ai_lease_and_enforce_permission(env, held_lease):
    from modules.flow_gate.api.v1.attachment_request_routes import router
    client = _app(router)
    base = f"/flowgate/api/v1/flows/{GROUP}"
    response = client.post(base + "/attachments", files={"file": ("sample.txt", b"data")})
    assert response.status_code == 201, response.text
    item = client.get(base + "/attachments").json()["attachments"][0]
    assert client.get(base + "/attachments/" + item["key"]).content == b"data"
    zipped = client.post(base + "/attachments-zip", json={"keys": [item["key"]]})
    assert zipped.status_code == 200, zipped.text
    assert ZipFile(io.BytesIO(zipped.content)).read("sample.txt") == b"data"
    viewer = _app(router, user=VIEWER_USER)
    assert viewer.post(base + "/attachments", files={"file": ("x.txt", b"x")}).status_code == 403
    response = client.delete(base + "/attachments/" + item["key"], params={"expected_sha256": item["content_sha256"]})
    assert response.status_code == 200, response.text

def test_worker_reads_only_bound_flow_metadata_and_selected_contents(env, monkeypatch):
    from modules.flow_gate.api.v1 import document_routes
    flow.upload(GROUP, part(b"shared"), ADMIN_USER)
    token = {"group_id": GROUP, "project": PROJECT, "doc_ref": DOC}
    monkeypatch.setattr(document_routes, "verify_bearer", lambda request: token)
    monkeypatch.setattr(document_routes, "_attachment_read_kind", lambda auth: ("read", None))
    client = _app(document_routes.router, middleware=False)
    response = client.get(f"/flowgate/api/v1/flow/{GROUP}/attachments")
    assert response.status_code == 200, response.text
    item = response.json()["attachments"][0]
    assert client.get("/flowgate" + item["read_url"]).json()["content"] == "shared"
    assert client.get("/flowgate/api/v1/flow/unrelated/attachments").status_code == 403
    from fastapi.responses import JSONResponse
    monkeypatch.setattr(document_routes, "_attachment_read_kind", lambda auth: ("none", JSONResponse(status_code=403, content={})))
    assert client.get(f"/flowgate/api/v1/flow/{GROUP}/attachments").status_code == 403

def part(data=b"new", name="sample.txt"):
    return UploadFile(filename=name, file=io.BytesIO(data))

def test_legacy_documents_appear_together_without_moving_files(env):
    approve(make_request(b"one", name="same.txt"))
    env["state"]["doc_id"] = DOC2
    approve(make_request(b"two", name="same.txt"), doc_id=DOC2)
    items = flow.listing(GROUP, ADMIN_USER)["attachments"]
    assert [r["display_name"] for r in items] == ["same.txt", "same (2).txt"]
    assert len({r["key"] for r in items}) == 2
    assert {r["doc_id"] for r in items} == {DOC, DOC2}
    assert room_file("same.txt").read_bytes() == b"one"
    for item, data in zip(items, (b"one", b"two")):
        path, _ = flow.download(GROUP, item["key"], ADMIN_USER)
        assert path.read_bytes() == data

def test_upload_collision_overwrite_keep_both_and_stale_delete(env):
    flow.upload(GROUP, part(b"one"), ADMIN_USER)
    with pytest.raises(AttachmentError) as collision:
        flow.upload(GROUP, part(b"two"), ADMIN_USER)
    assert collision.value.code == "ATTACHMENT_NAME_CONFLICT"
    item = flow.listing(GROUP, ADMIN_USER)["attachments"][0]
    flow.upload(GROUP, part(b"two"), ADMIN_USER, "overwrite", item["content_sha256"], item["key"])
    with pytest.raises(AttachmentError):
        flow.remove(GROUP, item["key"], ADMIN_USER, item["content_sha256"])
    flow.upload(GROUP, part(b"three"), ADMIN_USER, "keep_both")
    items = flow.listing(GROUP, ADMIN_USER)["attachments"]
    assert {i["display_name"] for i in items} == {"sample.txt", "sample (2).txt"}
    stream = flow.archive(GROUP, [i["key"] for i in items], ADMIN_USER)
    with ZipFile(stream) as zipped:
        assert zipped.read("sample.txt") == b"two"
        assert zipped.read("sample (2).txt") == b"three"
    stream.close()
    item = next(i for i in items if i["display_name"] == "sample.txt")
    flow.remove(GROUP, item["key"], ADMIN_USER, item["content_sha256"])
    assert len(flow.listing(GROUP, ADMIN_USER)["attachments"]) == 1

def test_listing_tags_each_file_with_its_source(env):
    """The window's source tag: an AI request a person approved, or a file uploaded in the window."""
    approve(make_request(b"ai", name="from-ai.txt"))
    flow.upload(GROUP, part(b"mine", "mine.txt"), ADMIN_USER)
    tags = {i["display_name"]: i["origin"] for i in flow.listing(GROUP, ADMIN_USER)["attachments"]}
    assert tags == {"from-ai.txt": "ai", "mine.txt": "upload"}
    assert flow.origin({"doc_id": "other", "filename": "x", "source_type": "CH"}, DOC, set()) == "chat"
    assert flow.origin({"doc_id": "other", "filename": "x", "source_type": "B"}, DOC, set()) == "bug"
    assert flow.origin({"doc_id": "other", "filename": "x", "source_type": "T"}, DOC, set()) == "card"

def test_permissions_and_cross_flow_key_are_enforced(env):
    flow.upload(GROUP, part(), ADMIN_USER)
    assert len(flow.listing(GROUP, VIEWER_USER)["attachments"]) == 1
    with pytest.raises(RequestError) as denied:
        flow.upload(GROUP, part(), VIEWER_USER)
    assert denied.value.status == 403
    with pytest.raises(RequestError):
        flow.listing(GROUP, NOROLE_USER)
    with pytest.raises(AttachmentError):
        flow.download(GROUP, "another-flow-key", ADMIN_USER)

def test_failed_overwrite_restores_bytes_and_metadata(env, monkeypatch):
    flow.upload(GROUP, part(b"old"), ADMIN_USER)
    item = flow.listing(GROUP, ADMIN_USER)["attachments"][0]
    def fail(**kwargs):
        raise RuntimeError("registry unavailable")
    monkeypatch.setattr(flow, "registry_replace", fail)
    with pytest.raises(RuntimeError):
        flow.upload(GROUP, part(b"new"), ADMIN_USER, "overwrite", item["content_sha256"], item["key"])
    path, _ = flow.download(GROUP, item["key"], ADMIN_USER)
    assert path.read_bytes() == b"old"
    assert flow.listing(GROUP, ADMIN_USER)["attachments"][0]["content_sha256"] == hashlib.sha256(b"old").hexdigest()
