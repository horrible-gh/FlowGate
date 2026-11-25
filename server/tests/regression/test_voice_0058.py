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
            # Korean models prove installation/loading without redistributing KSS audio.
            if not {"en", "multilingual"} & set(model["languages"]):
                assert model["self_test"] == {"mode": "load_only"}, model["id"]
    whisper = {m["id"]: m for m in vs.specification("whisper.cpp")["models"]}
    # Only the six multilingual base models: no quantized, English-only or older large builds.
    assert list(whisper) == ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]
    assert all("multilingual" in m["languages"] for m in whisper.values())
    realtime = {(s["id"], m["id"]) for s in services for m in s["models"] if m["realtime"]}
    assert ("sherpa-onnx", "zipformer-ko") in realtime and ("vosk", "small-ko") in realtime
    assert not any(service == "whisper.cpp" for service, _ in realtime)


def test_catalog_response_hides_download_and_self_test_details(manager):
    models = [m for s in manager.catalog(True)["services"] for m in s["models"]]
    assert len(models) == sum(len(s["models"]) for s in vs.CATALOG["services"])
    assert not any({"url", "sha256", "filename", "self_test"} & set(m) for m in models)


@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_korean_archive_excludes_samples_preserves_weights_and_notices(tmp_path, kind):
    import tarfile
    members = {"model/test_wavs/3.wav": b"restricted", "model/weights.onnx": b"weights", "model/LICENSE": b"license"}
    archive = tmp_path / ("model.tar.bz2" if kind == "tar" else "model.zip")
    if kind == "tar":
        with tarfile.open(archive, "w:bz2") as stream:
            for name, data in members.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                stream.addfile(member, io.BytesIO(data))
    else:
        with zipfile.ZipFile(archive, "w") as stream:
            for name, data in members.items():
                stream.writestr(name, data)
    output = tmp_path / "out"
    dl.extract(archive, output, threading.Event(), excluded_parts=("test_wavs",))
    assert not list(output.rglob("*.wav"))
    assert (output / "model/weights.onnx").read_bytes() == b"weights"
    assert (output / "model/LICENSE").read_bytes() == b"license"


def test_legacy_cleanup_is_scoped_and_idempotent(tmp_path):
    from modules.flow_gate.services.voice_distribution import cleanup_legacy_samples, KOREAN_ARCHIVE
    removed = ["services/vosk/korean-sample.wav", "downloads/vosk/korean-sample.wav.part",
               "services/sherpa-onnx/models/zipformer-ko/model/test_wavs/3.wav",
               "downloads/sherpa-onnx/" + KOREAN_ARCHIVE,
               "downloads/sherpa-onnx/" + KOREAN_ARCHIVE + ".part",
               "staging/old/models/zipformer-ko/model/test_wavs/3.wav"]
    kept = ["recordings/korean-sample.wav", "services/sherpa-onnx/models/zipformer-ko/model/weights.onnx",
            "services/sherpa-onnx/models/zipformer-en-20m/model/test_wavs/0.wav"]
    for name in removed + kept:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"data")
    cleanup_legacy_samples(tmp_path)
    cleanup_legacy_samples(tmp_path)
    assert all(not (tmp_path / p).exists() for p in removed)
    assert all((tmp_path / p).read_bytes() == b"data" for p in kept)


RETIRED = ("sherpa-onnx", "sensevoice-small-int8")


def test_sensevoice_is_removed_from_catalog_and_every_request_is_rejected(manager, tmp_path):
    # F86-7 U1: removed from the product, not "show a notice and install".
    assert "sense" not in json.dumps(vs.CATALOG).lower()
    assert all(m.get("kind") != "sense_voice" and not m.get("requires_license_ack")
               for s in vs.CATALOG["services"] for m in s["models"])
    assert all(m["id"] != RETIRED[1] for s in manager.catalog(True)["services"] for m in s["models"])
    for action in [
        lambda: vs.specification(*RETIRED),
        lambda: manager.install(*RETIRED),
        lambda: manager.install(*RETIRED, from_dir=tmp_path),
        lambda: manager.set_default(*RETIRED),
        lambda: manager.select(*RETIRED),
        lambda: manager.transcribe(b"data", *RETIRED, testing=True),
        lambda: manager.remove(*RETIRED),
    ]:
        with pytest.raises(VoiceError) as exc:
            action()
        assert (exc.value.code, exc.value.status_code) == ("voice_model_retired", 410)
    assert manager.jobs == {}


def test_install_api_rejects_sensevoice_and_the_removed_license_ack_field(manager, monkeypatch):
    app = FastAPI()
    app.include_router(voice_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u", "is_admin": True}
    monkeypatch.setattr(voice_routes, "manager", manager)
    client = TestClient(app)
    response = client.post("/voice/installs", json={"service": RETIRED[0], "model": RETIRED[1]})
    assert response.status_code == 410 and response.json()["error"]["code"] == "voice_model_retired"
    response = client.post("/voice/installs", json={"service": RETIRED[0], "model": RETIRED[1], "license_ack": True})
    assert response.status_code == 422
    response = client.put("/voice/default", json={"service": RETIRED[0], "model": RETIRED[1]})
    assert response.status_code == 410
    assert manager.jobs == {}


def test_stored_sensevoice_default_and_files_are_reported_retired_not_activated_or_deleted(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWGATE_VOICE_DIR", str(tmp_path / "voice"))
    stored = {"enabled": True, "default": {"service": RETIRED[0], "model": RETIRED[1]}}
    saved = []
    monkeypatch.setattr(vs.system_settings, "get_value", lambda key: json.dumps(stored))
    monkeypatch.setattr(vs.system_settings, "set_value", lambda key, value, value_type=None: saved.append(json.loads(value)))
    config = vs.configuration()
    assert (config["enabled"], config["default"]) == (False, None)
    marker = tmp_path / "voice/services/sherpa-onnx/sensevoice-small-int8.installed.json"
    weights = tmp_path / "voice/services/sherpa-onnx/models/sensevoice-small-int8/model.int8.onnx"
    for path, data in [(marker, b"{}"), (weights, b"user data")]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manager = vs.Manager()
    manager.startup()
    status = manager.status(True)
    assert (status["enabled"], status["default"]) == (False, None)
    assert status["retired"] == [{"service": RETIRED[0], "model": RETIRED[1], "name": "SenseVoiceSmall int8",
                                  "status": "retired", "installed": True, "was_default": True}]
    assert manager.status(False)["retired"] == []
    vs.save_configuration(config)
    assert saved == [{"enabled": False, "default": None}]
    # Operational data on the user's disk is never removed automatically.
    assert weights.read_bytes() == b"user data" and marker.is_file()


def test_removing_last_catalog_model_keeps_retired_sensevoice_files(manager):
    # Deleting the last Zipformer removes the engine and its cache, but never the SenseVoice
    # weights, installed marker or cached archive the user did not ask to delete.
    service = "sherpa-onnx"
    voice = vs.root()
    base, cache = voice / "services" / service, voice / "downloads" / service
    archive = vs.RETIRED_MODELS[RETIRED]["filename"]
    kept = {
        base / "sensevoice-small-int8.installed.json": b"{}",
        base / "models/sensevoice-small-int8/model.int8.onnx": b"user weights",
        base / "models/sensevoice-small-int8/tokens.txt": b"user tokens",
        cache / archive: b"cached archive",
        cache / (archive + ".part"): b"partial archive",
    }
    removed = [base / "bin/sherpa-onnx-c-api.dll", base / "models/zipformer-en-20m/encoder.onnx",
               base / "zipformer-en-20m.installed.json", cache / "engine.zip",
               cache / vs.specification(service, "zipformer-en-20m")["filename"]]
    for path, data in [*kept.items(), *[(p, b"x") for p in removed]]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    assert manager.installed(service, "zipformer-en-20m")
    result = manager.remove(service, "zipformer-en-20m")
    assert result == {"ok": True, "service_removed": True, "voice_disabled": False}
    assert {p: p.read_bytes() for p in kept if p.is_file()} == kept
    assert not any(p.exists() for p in removed)
    assert not (base / "bin").exists() and not (base / "models/zipformer-en-20m").exists()
    assert [r["model"] for r in manager.retired_models(manager.test_config)] == [RETIRED[1]]


def test_removing_last_model_without_retired_files_still_removes_service_dirs(manager):
    service = "sherpa-onnx"
    base, cache = vs.root() / "services" / service, vs.root() / "downloads" / service
    for path in [base / "bin/engine.dll", base / "models/zipformer-en-20m/encoder.onnx",
                 base / "zipformer-en-20m.installed.json", cache / "engine.zip"]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    assert manager.remove(service, "zipformer-en-20m")["service_removed"] is True
    assert not base.exists() and not cache.exists()


def test_engine_notices_are_pinned_files_and_status_matches_review():
    root = Path(__file__).resolve().parents[3]
    sources = {item["path"]: item for item in json.loads((root / "third_party/license_sources.json").read_text(encoding="utf-8"))["files"]}
    for service in vs.CATALOG["services"]:
        engine = next(a for a in service["artifacts"] if a["component_type"] == "engine")
        assert engine["notices"], service["id"]
        for notice in engine["notices"]:
            path = root / "third_party/licenses" / notice["filename"]
            data = path.read_bytes()
            assert (len(data), hashlib.sha256(data).hexdigest()) == (notice["size_bytes"], notice["sha256"]), notice
            assert sources["third_party/licenses/" + notice["filename"]]["source_url"] == notice["source_url"]
        # Native notices are still incomplete, so the offline export gate stays closed.
        if service["id"] == "whisper.cpp":
            assert engine["redistribution_allowed"] is True
            assert engine["redistribution_layout"] == "expanded"
        else:
            assert engine["redistribution_allowed"] is False and service["redistribution_status"] == "pending-native-notices"


def test_korean_catalog_has_no_kss_and_weight_license_unchanged():
    assert "korean-sample.wav" not in json.dumps(vs.CATALOG)
    for service, model in [("sherpa-onnx", "zipformer-ko"), ("vosk", "small-ko")]:
        spec = vs.specification(service, model)
        assert spec["license"] == "Apache-2.0"
        assert spec["self_test"] == {"mode": "load_only"}


@pytest.mark.parametrize("service,model", [("sherpa-onnx", "zipformer-ko"), ("vosk", "small-ko")])
def test_worker_load_only_and_restricted_first_install(manager, monkeypatch, service, model):
    import tarfile
    spec = vs.specification(service, model)
    fetched = []
    class Runtime:
        def start(self, directory, model):
            assert model.exists()
        def stop(self):
            pass
        def healthy(self):
            return True
        def transcribe(self, sample):
            raise AssertionError("Korean model requested a sample")
    monkeypatch.setattr(vs, "Runtime", Runtime)
    manager.runtime = Runtime()
    monkeypatch.setattr(vs.shutil, "disk_usage", lambda path: type("Disk", (), {"free": 100 * 1024**3})())
    def fetch(item, directory, cancel, progress, from_dir=None):
        directory.mkdir(parents=True, exist_ok=True)
        fetched.append(item["filename"])
        if item is spec:
            target = directory / item["filename"]
            if item["filename"].endswith(".tar.bz2"):
                with tarfile.open(target, "w:bz2") as archive:
                    for name in ["model/weights.onnx", "model/test_wavs/3.wav"]:
                        member = tarfile.TarInfo(name)
                        member.size = 4
                        archive.addfile(member, io.BytesIO(b"data"))
            else:
                with zipfile.ZipFile(target, "w") as archive:
                    archive.writestr("model/weights", b"data")
        else:
            target = directory / (item["filename"] + ".fixture")
            target.write_bytes(b"data")
        return target
    monkeypatch.setattr(dl, "fetch", fetch)
    job = "vi_" + "b" * 32
    manager.jobs[job] = dict(service=service, model=model, done=False, error=None)
    manager.cancel_events[job] = threading.Event()
    manager._install_worker(job, None)
    assert manager.jobs[job]["error"] is None, manager.jobs[job]
    assert manager.installed(service, model)
    assert "jfk.wav" not in fetched
    assert manager.test_config["enabled"]
    if model == "zipformer-ko":
        assert not list(manager.model_path(service, model).rglob("test_wavs"))
        assert not (vs.root() / "downloads" / service / spec["filename"]).exists()


def test_bundle_gate_rejects_unreviewed_components_without_creating_output(tmp_path):
    from modules.flow_gate.services.voice_distribution import export_bundle
    for entry in vs.CATALOG["services"]:
        if entry["id"] == "whisper.cpp":
            continue  # Real approved export is covered by test_voice_distribution_real_0086.
        for model in entry["models"]:
            with pytest.raises(VoiceError) as exc:
                export_bundle(entry, model, tmp_path / "source", tmp_path / "output")
            assert exc.value.code == "voice_redistribution_pending"
            assert not (tmp_path / "output").exists()


def test_bundle_copies_only_verified_components_and_exact_notices(tmp_path):
    from modules.flow_gate.services.voice_distribution import export_bundle
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    (source / "licenses").mkdir()
    def file_spec(filename, data, directory=source):
        (directory / filename).write_bytes(data)
        return {"filename": filename, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    license = file_spec("engine-LICENSE", b"fixture license", source / "licenses")
    notice = file_spec("model-NOTICE", b"fixture notice", source / "licenses")
    engine = file_spec("engine.bin", b"engine") | {"redistribution_allowed": True, "notices": [license], "component_type": "engine"}
    model = file_spec("model.bin", b"weights") | {"redistribution_allowed": True, "notices": [notice]}
    file_spec("ffmpeg.exe", b"not distributed")
    file_spec("korean-sample.wav", b"not distributed")
    file_spec("uncatalogued-model.bin", b"not distributed")
    entry = {"artifacts": [engine, {"component_type": "ffmpeg"}, {"component_type": "sample"}]}
    export_bundle(entry, model, source, output)
    assert {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()} == {"engine.bin", "model.bin", "licenses/engine-LICENSE", "licenses/model-NOTICE"}
    (source / "licenses/engine-LICENSE").write_bytes(b"tampered")
    with pytest.raises(VoiceError) as exc:
        export_bundle(entry, model, source, tmp_path / "bad")
    assert exc.value.code == "voice_offline_notice_invalid"
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("service,model", [("whisper.cpp", "base"), ("vosk", "small-ko")])
def test_local_install_does_not_require_redistribution_notices(manager, tmp_path, monkeypatch, service, model):
    # Local files without approved notices still reach the install job; the worker verifies them.
    monkeypatch.setattr(vs, "available", lambda entry: True)
    captured = []
    class Worker:
        def __init__(self, target, args, **kwargs):
            captured.append(args)
        def start(self):
            pass
    monkeypatch.setattr(vs.threading, "Thread", Worker)
    result = manager.install(service, model, from_dir=tmp_path)
    assert captured == [(result["install_id"], tmp_path)]


def test_excluded_paths_blocks_renamed_model_even_when_notices_approved(tmp_path):
    # Renaming the model or approving notices never lets a restricted original in or out.
    from modules.flow_gate.services.voice_distribution import validate_local_install, export_bundle
    model = {"id": "renamed-model", "filename": "model.zip", "excluded_paths": ["test_wavs/"],
             "redistribution_allowed": True, "notices": [{"filename": "LICENSE"}]}
    entry = {"artifacts": []}
    for action in [lambda: validate_local_install(entry, model),
                   lambda: export_bundle(entry, model, tmp_path, tmp_path / "out")]:
        with pytest.raises(VoiceError) as exc:
            action()
        assert exc.value.code == "voice_redistribution_pending"
    assert not (tmp_path / "out").exists()


def test_catalog_license_links_and_notice_keys_for_admin_and_user(manager):
    installed(manager)
    for can_manage in (True, False):
        services = manager.catalog(can_manage)["services"]
        assert services
        for service in services:
            entry = vs.specification(service["id"])
            assert service["notice_keys"] == entry["notice_keys"]
            assert len(service["notice_keys"]) == len(service["native_notices"])
            assert service["native_notices"] == entry["native_notices"]
            assert service["license"] == entry["license"]
            for component, artifact in zip(service["components"], entry["artifacts"], strict=True):
                assert "license_url" in component
                assert component["license_url"] == artifact["license_url"]
                assert component["url"] == artifact["url"]
                assert component["license"] == artifact["license"]
    engine, ffmpeg, sample = manager.catalog(True)["services"][0]["components"]
    assert engine["license_url"].endswith("/whisper.cpp/v1.8.2/LICENSE")
    assert ffmpeg["license_url"] is None
    assert sample["license_url"] is None


def test_catalog_missing_optional_license_metadata_has_explicit_defaults(manager, monkeypatch):
    catalog = copy.deepcopy(vs.CATALOG)
    for entry in catalog["services"]:
        entry.pop("notice_keys")
        for artifact in entry["artifacts"]:
            artifact.pop("license_url")
    monkeypatch.setattr(vs, "CATALOG", catalog)
    for service in manager.catalog(True)["services"]:
        assert service["notice_keys"] == []
        assert all("license_url" in c and c["license_url"] is None for c in service["components"])
