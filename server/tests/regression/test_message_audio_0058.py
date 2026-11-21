"""Audio ownership, atomic linking, replay, playback and transcript delivery."""
import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from _fg_conversation_support import fg_store, seed, GROUP, PROJECT, CHAT_DOC, USER, OTHER_USER
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.api.v1 import message_audio_routes
from modules.flow_gate.services import message_audio_service as audio, conversation_turn_service as turns
from modules.flow_gate.services import voice_preferences, ui_settings_service
from modules.flow_gate.services.voice_errors import VoiceError


@pytest.fixture(autouse=True)
def restore_lazy_environment():
    previous = os.environ.get("FLOWGATE_AGENT_PEPPER_ID")
    yield
    if previous is None:
        os.environ.pop("FLOWGATE_AGENT_PEPPER_ID", None)
    else:
        os.environ["FLOWGATE_AGENT_PEPPER_ID"] = previous


@pytest.fixture
def env(fg_store, tmp_path, monkeypatch):
    seed(fg_store)
    from config import settings
    monkeypatch.setattr(settings, "CONTEXT", "")
    monkeypatch.setattr(audio.paths, "get_storage_root", lambda project_id=None: tmp_path)
    doc = {"doc_id": CHAT_DOC, "group_id": GROUP, "project_id": PROJECT, "type_code": "CH"}
    monkeypatch.setattr(turns, "_validate_document_for_append", lambda doc_id: doc)
    from modules.flow_gate.services import conversation_events, chat_activity_anchor_service
    monkeypatch.setattr(conversation_events, "broadcast_turn_appended", lambda *a: None)
    monkeypatch.setattr(chat_activity_anchor_service, "on_turn_appended", lambda *a: None)
    monkeypatch.setattr(audio.manager, "transcribe", lambda *a: {"text": "시험 음성입니다.", "service": "whisper.cpp", "model": "base", "elapsed_ms": 123})
    def encode(service, data, directory, **kwargs):
        target = directory / "audio.m4a"
        target.write_bytes(b"test-encoded-m4a")
        return target
    monkeypatch.setattr(audio, "convert", encode)
    app = FastAPI()
    app.include_router(message_audio_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": USER, "is_admin": True}
    return fg_store, TestClient(app)


def upload(user=USER):
    return audio.upload(GROUP, SimpleNamespace(file=io.BytesIO(b"input")), {"user_id": user, "is_admin": True})["audio"]


def append(audio_id, user=USER, key="voice-message-key-0001"):
    return turns.append_turn(doc_id=CHAT_DOC, actor={"kind": "session", "user_id": user}, body_raw="",
                             idempotency_key=key, audio_id=audio_id)


def test_audio_upload_playback_delete(env):
    store, client = env
    item = upload()
    result = client.get(item["url"])
    assert result.status_code == 200
    assert result.content == b"test-encoded-m4a"
    assert result.headers["content-type"] == "audio/mp4"
    assert item["text"] == "시험 음성입니다."
    assert audio.remove(GROUP, item["audio_id"], {"user_id": USER, "is_admin": True})["ok"]
    assert client.get(item["url"]).status_code == 404


def test_other_user_cannot_read_delete_or_link_temp_audio(env):
    item = upload()
    other = {"user_id": OTHER_USER, "is_admin": True}
    with pytest.raises(VoiceError):
        audio.content(GROUP, item["audio_id"], other)
    with pytest.raises(VoiceError):
        audio.remove(GROUP, item["audio_id"], other)
    with pytest.raises(turns.ConversationTurnError):
        append(item["audio_id"], OTHER_USER)


def test_link_includes_transcript_and_replay_is_idempotent(env):
    store, client = env
    item = upload()
    result = append(item["audio_id"])
    assert result["turn"]["body"] == "시험 음성입니다."
    assert result["turn"]["audio"]["audio_id"] == item["audio_id"]
    assert append(item["audio_id"])["replayed"] is True
    assert len(store._fetch_all("SELECT * FROM conversation_turns")) == 1
    second = upload()
    with pytest.raises(turns.ConversationTurnError):
        append(second["audio_id"])
    assert store._fetch_one("SELECT state FROM message_audio WHERE audio_id = ?", [second["audio_id"]])["state"] == "temp"
    with pytest.raises(VoiceError):
        audio.remove(GROUP, item["audio_id"], {"user_id": USER, "is_admin": True})


def test_link_failure_rolls_back_turn(env):
    store, _ = env
    item = upload()
    append(item["audio_id"])
    with pytest.raises(turns.ConversationTurnError):
        append(item["audio_id"], key="another-message-key-0001")
    assert len(store._fetch_all("SELECT * FROM conversation_turns")) == 1


def test_deleted_message_audio_cannot_be_played(env):
    store, client = env
    item = upload()
    result = append(item["audio_id"])
    store._execute("UPDATE conversation_turns SET deleted_at = ? WHERE doc_id = ? AND seq = ?", ["deleted", CHAT_DOC, result["turn"]["seq"]])
    assert client.get(item["url"]).status_code == 404


def test_preference_round_trip_is_user_scoped(env):
    ui_settings_service.save_ui_settings(USER, {"voice_action": "audio"})
    assert voice_preferences.resolve(USER)["voice_action"] == "audio"
    assert voice_preferences.resolve(OTHER_USER)["voice_action"] == "text"
    assert env[0]._fetch_one("SELECT * FROM user_ui_settings WHERE user_id = ?", [USER]) is None
