"""flowgate.v02test.0029 T0009: one pass from the composer's upload to the CLI prompt.

The per-card tests each stub the neighbouring layer. This chain keeps the real routes,
the real SQLite links and the real worker prompt boundary, and only replaces what needs
a live service (document lifecycle, event fan-out, the chat's saved context window).
No external AI is called: the real Claude CLI cannot be launched from this environment,
so the last hop is checked as the prompt text and the files the CLI would be told to read.
"""
from pathlib import Path

import pytest

from test_message_images_0029 import env, PNG, USER_ROW, restore_lazy_agent_environment  # noqa: F401
from _fg_conversation_support import CHAT_DOC, GROUP, PROJECT, fg_store  # noqa: F401
from modules.flow_gate.db import ai_invoke_runs, group_ai_leases
from modules.flow_gate.services import conversation_query_service as query
from modules.flow_gate.services import invoke_mention_service as mentions
from modules.flow_gate.services import message_image_delivery as delivery
from modules.flow_gate.services.ai_invoke import finalize, worker

CLAUDE = dict(id="claude", exec_type="cli", kind="claude", exec_location="local")
CODEX = dict(CLAUDE, id="codex", kind="codex")
IMAGE_KEYS = {"image_id", "media_type", "size", "width", "height", "url"}


def _send_with_image(client, monkeypatch, *, lease):
    """Upload, send and list through the HTTP routes, optionally while an AI run holds the lease."""
    if lease:
        group_ai_leases.acquire(group_id=GROUP, project_id=PROJECT, run_id="chain-test-run",
                                chain_id=None, action_scope="chat", worker_identity="test-worker")
    uploaded = client.post(f"/api/v1/flows/{GROUP}/message-images", files={"file": ("clip.png", PNG, "image/png")})
    assert uploaded.status_code == 200
    image = uploaded.json()["image"]
    assert set(image) == IMAGE_KEYS and image["image_id"].startswith("img_")
    sent = client.post(f"/api/v1/documents/{CHAT_DOC}/conversation/turn",
                       json={"body": "look at this", "idempotency_key": "chain-key-0001",
                             "images": [image["image_id"]]})
    assert sent.status_code == 201
    assert sent.json()["turn"]["images"] == [image]
    monkeypatch.setattr(query.document_service, "get_document", lambda _: {
        "doc_id": CHAT_DOC, "project_id": PROJECT, "group_id": GROUP, "type_code": "CH"})
    monkeypatch.setattr(query, "_ensure_readable_rows", lambda _: "migrated")
    listed = client.get(f"/api/v1/documents/{CHAT_DOC}/conversation/turns")
    assert listed.status_code == 200
    assert [turn["images"] for turn in listed.json()["turns"]] == [[image]]
    return image, listed.json()["turns"][0]["images"][0]


def _mention(monkeypatch):
    monkeypatch.setattr(mentions.chat_doc_settings_service, "context_window",
                        lambda *a, **k: dict(after_seq=0, folded=0, checkpoint_seq=None))
    return mentions.build_conversation_mention(
        doc_id=CHAT_DOC, project="p", module="m", group_name="g", raw_token="test", token_id="tok",
        api_base_url="http://test", locale="ko", command_execution=True)


@pytest.mark.parametrize("lease", [False, True], ids=["idle", "ai_holds_lease"])
@pytest.mark.parametrize("provider", [CLAUDE, CODEX], ids=["claude", "codex"])
def test_paste_to_claude_cli_prompt(env, tmp_path, monkeypatch, lease, provider):
    store, client = env
    image, listed = _send_with_image(client, monkeypatch, lease=lease)
    # The address the timeline gets is the address the original is served from.
    original = client.get(listed["url"])
    assert original.status_code == 200 and original.content == PNG
    assert original.headers["content-type"] == "image/png"
    assert original.headers["x-content-type-options"] == "nosniff"
    assert original.headers["content-disposition"].startswith("inline;")
    # The in-app invoke path hands the already-resolved window to the run.
    mention = _mention(monkeypatch)
    assert mention.image_context == {"conversation_id": CHAT_DOC, "after_seq": 0, "locale": "ko"}
    scratch = tmp_path / "token"
    scratch.mkdir()
    run = dict(message_image_context=mention.image_context, token_scratch_dir=str(scratch), action_scope="chat")
    prompt = worker._provider_prompt(run, provider, str(mention))
    copied = scratch / "images" / "1-0.png"
    assert copied.read_bytes() == PNG and not copied.is_symlink()
    assert not copied.samefile(delivery.images.images_for_window(CHAT_DOC, 0)[0]["abs_path"])
    assert prompt.startswith(str(mention))
    assert f"- 메시지 #1: {copied}" in prompt
    assert ("Read 도구" if provider == CLAUDE else "Codex --image") in prompt
    assert (run["images_total"], run["images_delivered"], run["images_undelivered"]) == (1, 1, 0)
    # Flow-owned: the Flow attachment registry never sees a message image.
    assert store._fetch_all("SELECT * FROM attachments") == []


def test_unsupported_ai_leaves_a_durable_record_and_no_file(env, tmp_path, monkeypatch):
    _, client = env
    _send_with_image(client, monkeypatch, lease=False)
    mention = _mention(monkeypatch)
    scratch = tmp_path / "token"
    scratch.mkdir()
    run = dict(message_image_context=mention.image_context, token_scratch_dir=str(scratch), action_scope="chat",
               run_id="r", group_id=GROUP, project_id=PROJECT, doc_ref=CHAT_DOC, mode="single")
    prompt = worker._provider_prompt(run, dict(CODEX, exec_location="remote"), str(mention))
    assert "1장이 있으나 이 연결로는 전달되지 않음" in prompt
    assert not (scratch / "images").exists() and str(scratch) not in prompt
    saved = []
    monkeypatch.setattr(ai_invoke_runs, "upsert", lambda row: saved.append(row))
    monkeypatch.setattr(ai_invoke_runs, "maybe_purge", lambda: None)
    assert finalize._persist_run_record(run)
    entry = saved[0]["fallback_history"][0]
    assert entry["reason"] == "message_images" and entry["images_undelivered"] == 1
    assert entry["provider_id"] == "codex"


def test_flow_attachment_listing_does_not_show_message_images(env, monkeypatch):
    from modules.flow_gate.documents.attachments import flow
    _, client = env
    _send_with_image(client, monkeypatch, lease=False)
    assert flow.listing(GROUP, USER_ROW)["attachments"] == []
