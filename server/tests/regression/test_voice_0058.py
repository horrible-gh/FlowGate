"""Voice security, lifecycle and transport regressions without model downloads."""
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import threading
import zipfile

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from modules.flow_gate.api.v1 import voice_routes
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import voice_download as dl, voice_service as vs
from modules.flow_gate.services import voice_preferences as prefs, ui_settings_service as ui
from modules.flow_gate.services.voice_errors import VoiceError
from modules.flow_gate.services.voice_lock import installation_lock


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWGATE_VOICE_DIR", str(tmp_path / "voice"))
    state = {"enabled": False, "default": None}
    monkeypatch.setattr(vs, "configuration", lambda: copy.deepcopy(state))
    monkeypatch.setattr(vs, "save_configuration", lambda value: state.update(copy.deepcopy(value)))
    value = vs.Manager()
    value.test_config = state
    return value


def installed(manager, model="base"):
    path = manager.model_path("whisper.cpp", model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"model")
    (manager.service_dir("whisper.cpp") / (model + ".installed.json")).write_text("{}")
    return {"service": "whisper.cpp", "model": model}


def test_root_never_uses_project_override(monkeypatch, tmp_path):
    monkeypatch.delenv("FLOWGATE_VOICE_DIR", raising=False)
    monkeypatch.setattr(vs, "get_storage_root", lambda: tmp_path)
    assert vs.root() == tmp_path / "voice"


def test_initially_off_and_normal_users_see_installed_only(manager):
    assert manager.status()["state"] == "not_installed"
    assert manager.catalog(False)["services"] == []
    installed(manager)
    models = manager.catalog(False)["services"][0]["models"]
    assert [m["id"] for m in models] == ["base"]
    assert not manager.status()["enabled"]
    assert "elapsed_ms" not in models[0]


def test_unresponsive_process_is_not_on(manager):
    manager.test_config.update(enabled=True, default=installed(manager))
    assert manager.status()["enabled"] is False
    assert manager.status()["error"]["code"] == "voice_module_unavailable"


def test_delete_default_disables_and_last_model_removes_service(manager):
    selected = installed(manager)
    manager.test_config.update(default=selected, enabled=True)
    result = manager.remove(**selected)
    assert result == {"ok": True, "service_removed": True, "voice_disabled": True}
    assert manager.test_config == {"enabled": False, "default": None}
    assert not manager.service_dir("whisper.cpp").exists()


def test_delete_one_keeps_other_models(manager):
    selected = installed(manager)
    installed(manager, "tiny")
    result = manager.remove(**selected)
    assert result["service_removed"] is False
    assert manager.installed("whisper.cpp", "tiny")


def test_installing_model_cannot_be_deleted(manager):
    selected = installed(manager)
    manager.jobs["job"] = dict(done=False, **selected)
    with pytest.raises(VoiceError, match="Cancel installation"):
        manager.remove(**selected)
    assert manager.installed(**selected)


def test_user_choice_and_deleted_choice_fall_back_to_default(manager, monkeypatch):
    manager.test_config["default"] = installed(manager)
    installed(manager, "tiny")
    monkeypatch.setattr(prefs, "resolve", lambda uid: {**prefs.DEFAULTS, "voice_service": "whisper.cpp", "voice_model": "tiny"})
    assert manager.select(user_id="u")["model"] == "tiny"
    manager.remove("whisper.cpp", "tiny")
    assert manager.select(user_id="u")["model"] == "base"
    with pytest.raises(VoiceError):
        manager.select("whisper.cpp", "../evil")


def test_disabled_rejects_before_decoding(manager):
    with pytest.raises(VoiceError) as error:
        manager.transcribe(b"audio")
    assert error.value.code == "voice_disabled"


def test_admin_gate_covers_every_mutation(manager, monkeypatch):
    app = FastAPI()
    app.include_router(voice_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u", "is_admin": False}
    monkeypatch.setattr(voice_routes, "manager", manager)
    client = TestClient(app)
    for method, path, kwargs in [
        ("post", "/voice/installs", {"json": {"service": "whisper.cpp", "model": "base"}}),
        ("delete", "/voice/installs/vi_" + "a" * 32, {}),
        ("get", "/voice/installs/vi_" + "a" * 32, {}),
        ("delete", "/voice/services/whisper.cpp/models/base", {}),
        ("put", "/voice/default", {"json": {"service": "whisper.cpp", "model": "base"}}),
        ("put", "/voice/enabled", {"json": {"enabled": True}}),
        ("post", "/voice/restart", {}),
        ("post", "/voice/test", {"files": {"file": ("a.wav", b"data")}}),
    ]:
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "voice_admin_required"


def spec(data=b"verified-data"):
    return {"filename": "model.bin", "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "url": "https://example.invalid/model"}


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status, self.headers = status, headers or {}


def test_range_ignored_restarts_instead_of_appending(tmp_path, monkeypatch):
    data = b"verified-data"
    (tmp_path / "model.bin.part").write_bytes(b"ver")
    requests = []
    def open_(request, **kwargs):
        requests.append(request)
        return Response(data)
    monkeypatch.setattr(dl.urllib.request, "urlopen", open_)
    result = dl.fetch(spec(data), tmp_path, threading.Event(), lambda *args: None)
    assert requests[0].get_header("Range") == "bytes=3-"
    assert result.read_bytes() == data


def test_range_resume_verifies_complete_digest(tmp_path, monkeypatch):
    data = b"verified-data"
    (tmp_path / "model.bin.part").write_bytes(data[:3])
    monkeypatch.setattr(dl.urllib.request, "urlopen", lambda *a, **k: Response(data[3:], 206, {"Content-Range": f"bytes 3-12/{len(data)}"}))
    assert dl.fetch(spec(data), tmp_path, threading.Event(), lambda *args: None).read_bytes() == data


def test_hash_mismatch_does_not_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(dl.urllib.request, "urlopen", lambda *a, **k: Response(b"x" * 13))
    with pytest.raises(VoiceError) as error:
        dl.fetch(spec(), tmp_path, threading.Event(), lambda *args: None)
    assert error.value.code == "voice_sha256_mismatch"
    assert not (tmp_path / "model.bin").exists()
    assert not (tmp_path / "model.bin.part").exists()


def test_cancel_preserves_partial_for_resume(tmp_path):
    partial = tmp_path / "model.bin.part"
    partial.write_bytes(b"ver")
    event = threading.Event()
    event.set()
    with pytest.raises(VoiceError):
        dl.fetch(spec(), tmp_path, event, lambda *args: None)
    assert partial.read_bytes() == b"ver"


def test_offline_copy_never_changes_original_or_downloads(tmp_path, monkeypatch):
    source = tmp_path / "original"
    source.mkdir()
    (source / "model.bin").write_bytes(b"verified-data")
    monkeypatch.setattr(dl.urllib.request, "urlopen", lambda *a, **k: pytest.fail("offline network"))
    result = dl.fetch(spec(), tmp_path / "dest", threading.Event(), lambda *args: None, source)
    assert result.read_bytes() == (source / "model.bin").read_bytes()


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/outside"])
def test_archive_traversal_rejected(tmp_path, name):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(name, b"payload")
    with pytest.raises(VoiceError):
        dl.extract(archive, tmp_path / "out", threading.Event())


def test_os_lock_excludes_other_installers(tmp_path):
    with installation_lock(tmp_path):
        with pytest.raises(VoiceError):
            with installation_lock(tmp_path):
                pass


@pytest.mark.parametrize("patch", [{"voice_action": "other"}, {"voice_realtime": "true"}, {"voice_service": "../x"}, {"voice_model": None}])
def test_preference_validation_is_strict(patch):
    with pytest.raises(ui.UiSettingsError):
        ui.validate_patch(patch)


def test_valid_preferences():
    ui.validate_patch({"voice_action": "audio", "voice_service": "whisper.cpp", "voice_model": "base", "voice_realtime": False})


def test_persisted_interrupted_job_can_be_read(manager):
    job_id = "vi_" + "a" * 32
    vs.atomic_json(vs.root() / "jobs" / (job_id + ".json"), {"done": False, "step": "downloading"})
    result = manager.job(job_id)
    assert result["done"] is True
    assert result["error"]["code"] == "voice_install_interrupted"


def test_catalog_entries_are_pinned_unique_and_testable():
    services = vs.CATALOG["services"]
    assert [s["id"] for s in services] == ["whisper.cpp", "sherpa-onnx", "vosk"]
    for service in services:
        ids = [m["id"] for m in service["models"]]
        assert len(set(ids)) == len(ids)
        names = [a["filename"] for a in service["artifacts"]]
        assert {"jfk.wav"} <= set(names) and any(n.startswith("ffmpeg") for n in names)
        for item in [*service["artifacts"], *service["models"]]:
            assert item["url"].startswith("https://") and item["size_bytes"] > 0, item
            assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), item
        for model in service["models"]:
            assert prefs.valid("voice_model", model["id"]), model["id"]
            assert isinstance(model["realtime"], bool) and model["languages"]
            # A model that cannot read the English sample must name its own sample.
            if not {"en", "multilingual"} & set(model["languages"]):
                assert model["self_test"]["sample"] and model["self_test"]["expect"], model["id"]
    whisper = {m["id"]: m for m in vs.specification("whisper.cpp")["models"]}
    assert {"large-v3-turbo", "large-v3", "large-v3-turbo-q5_0", "medium", "small", "tiny.en"} <= set(whisper)
    assert whisper["tiny.en"]["languages"] == ["en"]
    realtime = {(s["id"], m["id"]) for s in services for m in s["models"] if m["realtime"]}
    assert ("sherpa-onnx", "zipformer-ko") in realtime and ("vosk", "small-ko") in realtime
    assert not any(service == "whisper.cpp" for service, _ in realtime)


def test_catalog_response_hides_download_and_self_test_details(manager):
    models = [m for s in manager.catalog(True)["services"] for m in s["models"]]
    assert len(models) == sum(len(s["models"]) for s in vs.CATALOG["services"])
    assert not any({"url", "sha256", "filename", "self_test"} & set(m) for m in models)
