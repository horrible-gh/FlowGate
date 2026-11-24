"""Voice installation lifecycle. Artifacts live outside the source tree."""
from __future__ import annotations

import copy
import filecmp
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile
import threading
import time
import uuid

from modules.flow_gate.db import system_settings
from modules.flow_gate.storage.paths import get_storage_root
from .voice_errors import VoiceError
from . import voice_download as download
from .voice_runtime import Runtime, convert

log = logging.getLogger(__name__)
CATALOG = json.loads(Path(__file__).with_name("voice_catalog.json").read_text(encoding="utf-8"))


def root() -> Path:
    override = os.environ.get("FLOWGATE_VOICE_DIR", "").strip()
    return Path(override).resolve() if override else get_storage_root().resolve() / "voice"


def specification(service, model=None):
    entry = next((s for s in CATALOG["services"] if s["id"] == service), None)
    if entry is None:
        raise VoiceError("voice_unknown_service", "Unknown service", 404)
    if model is None:
        return entry
    item = next((m for m in entry["models"] if m["id"] == model), None)
    if item is None:
        raise VoiceError("voice_unknown_model", "Unknown model", 404)
    return item


def available(entry):
    return platform.system() + "-" + platform.machine() in entry["platforms"]


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def configuration():
    raw = system_settings.get_value("voice.configuration")
    value = json.loads(raw) if raw else {"enabled": False, "default": None}
    selected = value.get("default")
    if selected and not specification(**selected).get("default_eligible", True):
        value.update(enabled=False, default=None)
    return value


def save_configuration(value):
    system_settings.set_value("voice.configuration", json.dumps(value), value_type="json")


class Manager:
    def __init__(self):
        self.lock = threading.RLock()
        self.inference = threading.RLock()
        self.runtime = Runtime()
        self.jobs = {}
        self.cancel_events = {}
        self.threads = {}
        self.trials = {}
        self.error = None
        self.stopping = False

    def service_dir(self, service):
        specification(service)
        return root() / "services" / service

    def model_path(self, service, model):
        spec = specification(service, model)
        return self.service_dir(service) / "models" / (model if spec.get("archive") else spec["filename"])

    def installed(self, service, model):
        return (self.service_dir(service) / (model + ".installed.json")).is_file() and self.model_path(service, model).exists()

    def busy(self, service=None, model=None):
        return any(not j["done"] and (service is None or j["service"] == service)
                   and (model is None or j["model"] == model) for j in self.jobs.values())

    def catalog(self, can_manage):
        config = configuration()
        services = []
        for entry in CATALOG["services"]:
            supported = available(entry)
            models = []
            for spec in entry["models"]:
                installed = self.installed(entry["id"], spec["id"])
                if not can_manage and not installed:
                    continue
                models.append({k: copy.deepcopy(v) for k, v in spec.items() if k not in ("url", "sha256", "filename", "self_test")} | {
                    "installed": installed, "available": supported,
                    "unavailable_reason": None if supported else "This release requires Windows x64",
                    "is_default": config["default"] == {"service": entry["id"], "model": spec["id"]}})
            if can_manage or models:
                services.append({"id": entry["id"], "name": entry["name"], "version": entry["version"],
                                 "components": [{"filename": a["filename"], "component_type": a["component_type"],
                                                 "license": a["license"], "distribution": a["distribution"],
                                                 "url": a["url"]} for a in entry["artifacts"]],
                                 "native_notices": entry.get("native_notices", []),
                                 "license": entry["license"], "installed": any(m["installed"] for m in models),
                                 "available": supported, "unavailable_reason": None if supported else "This release requires Windows x64",
                                 "models": models})
        return {"ok": True, "services": services}

    def status(self, can_manage=False):
        config = configuration()
        healthy = self.runtime.healthy()
        has_models = any(self.installed(s["id"], m["id"]) for s in CATALOG["services"] for m in s["models"])
        enabled = bool(config["enabled"] and healthy)
        state = "on" if enabled else "off" if has_models else "not_installed"
        with self.lock:
            if self.busy():
                state = "installing"
            elif self.error or (config["enabled"] and not healthy):
                state = "error"
        error = self.error or ({"code": "voice_module_unavailable", "message": "Voice module is not responding"} if config["enabled"] and not healthy else None)
        return {"ok": True, "enabled": enabled, "state": state, "default": config["default"], "error": error, "can_manage": can_manage}

    def _update_job(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)
            atomic_json(root() / "jobs" / (job_id + ".json"), self.jobs[job_id])

    def job(self, job_id):
        if not re.fullmatch(r"vi_[0-9a-f]{32}", job_id):
            raise VoiceError("voice_install_not_found", "Unknown install", 404)
        with self.lock:
            row = self.jobs.get(job_id)
            if row is None:
                path = root() / "jobs" / (job_id + ".json")
                if not path.is_file():
                    raise VoiceError("voice_install_not_found", "Unknown install", 404)
                row = json.loads(path.read_text(encoding="utf-8"))
                if not row["done"]:
                    row.update(done=True, step="interrupted", error={"code": "voice_install_interrupted", "message": "Retry to resume verified partial downloads"})
            return {"ok": True, **copy.deepcopy(row)}

    def install(self, service, model, from_dir=None, license_ack=False):
        from . import voice_streams
        if voice_streams.active(service):
            raise VoiceError("voice_busy", "Finish live transcription before installing", 409)
        entry = specification(service)
        spec = specification(service, model)
        if from_dir is not None:
            from .voice_distribution import validate_local_install
            validate_local_install(entry, spec)
        if spec.get("requires_license_ack") and not license_ack:
            raise VoiceError("voice_license_ack_required",
                             spec["license"] + " — " + spec["source"] + " — " + spec["license_url"], 422)
        if not available(entry):
            raise VoiceError("voice_platform_unsupported", "This release requires Windows x64", 422)
        with voice_streams._condition, self.lock:
            if voice_streams.active(service):
                raise VoiceError("voice_busy", "Live transcription is active", 409)
            if self.stopping:
                raise VoiceError("voice_stopping", "Server is stopping", 503)
            if self.busy():
                raise VoiceError("voice_install_in_progress", "Another installation is in progress", 409)
            if self.installed(service, model):
                raise VoiceError("voice_already_installed", "Model already installed", 409)
            job_id = "vi_" + uuid.uuid4().hex
            self.jobs[job_id] = dict(install_id=job_id, service=service, model=model, step="queued", received_bytes=0,
                                     total_bytes=0, done=False, error=None)
            self.cancel_events[job_id] = threading.Event()
            self._update_job(job_id)
            worker = threading.Thread(target=self._install_worker, args=(job_id, from_dir), name=job_id, daemon=True)
            self.threads[job_id] = worker
            worker.start()
            return {"ok": True, "install_id": job_id}

    def cancel(self, job_id):
        row = self.job(job_id)
        with self.lock:
            if not row["done"] and job_id in self.cancel_events:
                self.cancel_events[job_id].set()
                trial = self.trials.get(job_id)
                if trial is not None:
                    trial.stop()
                self._update_job(job_id, step="cancelling")
        return self.job(job_id)

    def _install_worker(self, job_id, from_dir):
        job = self.jobs[job_id]
        service, model = job["service"], job["model"]
        entry, spec = specification(service), specification(service, model)
        cancel = self.cancel_events[job_id]
        staging = root() / "staging" / job_id
        promoted = False
        try:
            # All installs, CLI included, claim the same disk lock. Never remove a
            # lock owned by somebody else; OS locks are released after a crash.
            from .voice_lock import installation_lock
            with installation_lock(root()):
                from .voice_distribution import cleanup_legacy_samples
                cleanup_legacy_samples(root())
                destination = self.service_dir(service)
                load_only = spec.get("self_test", {}).get("mode") == "load_only"
                artifacts = [a for a in entry["artifacts"]
                             if not (load_only and a.get("component_type") == "sample")
                             and not (from_dir and a.get("component_type") == "ffmpeg")] + [spec]
                offline_sample = None
                if from_dir and not load_only and not list(Path(from_dir).rglob("jfk.wav")):
                    samples = list(Path(from_dir).rglob("Recording_1.m4a"))
                    if not samples:
                        raise VoiceError("voice_offline_file_invalid", "Offline bundle needs jfk.wav or Recording_1.m4a", 422)
                    offline_sample = samples[0]
                    artifacts = [a for a in artifacts if a["filename"] != "jfk.wav"]
                total = sum(a["size_bytes"] for a in artifacts)
                root().mkdir(parents=True, exist_ok=True)
                if shutil.disk_usage(root()).free < total * 2 + 1024 * 1024 * 1024:
                    raise VoiceError("voice_disk_full", "Insufficient disk space for download and extraction", 507)
                self._update_job(job_id, total_bytes=total, step="downloading")
                files = []
                completed = 0
                for artifact in artifacts:
                    base = completed
                    last = [0.0]
                    def progress(done, size):
                        now = time.monotonic()
                        if done == size or now - last[0] >= 0.5:
                            self._update_job(job_id, received_bytes=base + done)
                            last[0] = now
                    files.append(download.fetch(artifact, root() / "downloads" / service, cancel, progress,
                                                Path(from_dir).resolve() if from_dir else None))
                    completed += artifact["size_bytes"]
                self._update_job(job_id, step="extracting", received_bytes=total)
                staging.mkdir(parents=True, exist_ok=True)
                for item in files[:-1]:
                    if item.is_dir():
                        shutil.copytree(item, staging, dirs_exist_ok=True)
                    elif item.suffix == ".zip" or item.name.endswith(".tar.bz2"):
                        download.extract(item, staging, cancel)
                    else:
                        shutil.copyfile(item, staging / item.name)
                (staging / "models").mkdir(exist_ok=True)
                staged_model = staging / "models" / (model if spec.get("archive") else spec["filename"])
                if spec.get("archive"):
                    download.extract(files[-1], staged_model, cancel,
                                     excluded_parts=tuple(path.strip("/") for path in spec.get("excluded_paths", ())))
                    if spec.get("excluded_paths"):
                        # The original upstream archive itself contains KSS audio.
                        files[-1].unlink(missing_ok=True)
                else:
                    shutil.copyfile(files[-1], staged_model)
                download.check_cancel(cancel)
                self._update_job(job_id, step="testing")
                # Test in isolation before making a failed model visible to users.
                trial = Runtime()
                trial.cancel_event = cancel
                with self.lock:
                    self.trials[job_id] = trial
                try:
                    trial.start(staging, staged_model)
                    if load_only:
                        passed = trial.healthy()
                    elif offline_sample:
                        from .voice_runtime import MAX_AUDIO_BYTES
                        with offline_sample.open("rb") as stream:
                            sample_data = stream.read(MAX_AUDIO_BYTES + 1)
                        with tempfile.TemporaryDirectory(prefix="flowgate-voice-test-") as temporary:
                            wav = convert(staging, sample_data, Path(temporary))
                            transcript = trial.transcribe(wav)
                        passed = bool(transcript.strip())
                    elif spec.get("self_test"):
                        # Models that cannot read the English sample name their own:
                        # a file inside the model archive or a pinned service artifact.
                        test = spec["self_test"]
                        sample = next((p for base in (staged_model, staging) if base.is_dir()
                                       for p in sorted(base.rglob(test["sample"]))), None)
                        if sample is None:
                            raise VoiceError("voice_self_test_failed", "Self-test sample is missing", 422)
                        with tempfile.TemporaryDirectory(prefix="flowgate-voice-test-") as temporary:
                            transcript = trial.transcribe(convert(staging, sample.read_bytes(), Path(temporary)))
                        # Spacing differs between engines; compare without whitespace.
                        passed = "".join(test["expect"].split()) in "".join(transcript.split())
                    else:
                        transcript = trial.transcribe(staging / "jfk.wav")
                        passed = "country" in transcript.lower()
                    if not passed:
                        raise VoiceError("voice_self_test_failed", "Sample transcription did not match", 422)
                finally:
                    trial.stop()
                    with self.lock:
                        self.trials.pop(job_id, None)
                download.check_cancel(cancel)
                with self.inference:
                    destination.mkdir(parents=True, exist_ok=True)
                    # Stop before replacing DLLs on Windows; existing model markers
                    # survive and all models continue to share one runtime.
                    self.runtime.stop()
                    for source in staging.rglob("*"):
                        if not source.is_file():
                            continue
                        target = destination / source.relative_to(staging)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        # Skip byte-identical loaded DLLs on Windows, but repair
                        # incomplete files left by a failed promotion before publishing.
                        if not target.exists() or not filecmp.cmp(source, target, shallow=False):
                            temporary_target = target.with_name(target.name + ".installing")
                            shutil.copyfile(source, temporary_target)
                            os.replace(temporary_target, target)
                    atomic_json(destination / (model + ".installed.json"), {"sha256": spec["sha256"], "version": entry["version"]})
                    promoted = True
                    config = configuration()
                    if config["default"] is None and spec.get("default_eligible", True):
                        config["default"] = {"service": service, "model": model}
                    selected = config["default"]
                    if selected:
                        self.runtime.start(self.service_dir(selected["service"]), self.model_path(**selected))
                        config["enabled"] = True
                    else:
                        config["enabled"] = False
                    save_configuration(config)
                    self.error = None
                self._update_job(job_id, step="complete", done=True)
        except Exception as exc:
            err = exc if isinstance(exc, VoiceError) else VoiceError("voice_install_failed", "Installation failed: " + type(exc).__name__, 500)
            if cancel.is_set():
                err = VoiceError("voice_install_cancelled", "Installation cancelled", 409)
            # A promoted, verified model remains installed when activation fails.
            # The error state is visible; it can be retried with /voice/restart.
            if promoted:
                self.error = {"code": err.code, "message": err.message}
            self._update_job(job_id, done=True, step="cancelled" if err.code == "voice_install_cancelled" else "error", error=err.response()["error"])
            if not isinstance(exc, VoiceError):
                log.exception("Voice installation failed")
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if spec.get("excluded_paths"):
                # Also discard partial/error downloads containing restricted samples.
                archive = root() / "downloads" / service / spec["filename"]
                archive.unlink(missing_ok=True)
                archive.with_name(archive.name + ".part").unlink(missing_ok=True)

    def select(self, service=None, model=None, user_id=None):
        if bool(service) != bool(model):
            raise VoiceError("voice_invalid_selection", "Specify service and model together", 422)
        if service and model:
            specification(service, model)
            if not self.installed(service, model):
                raise VoiceError("voice_not_installed", "Selected model is not installed", 409)
            return {"service": service, "model": model}
        if user_id:
            from .voice_preferences import resolve
            prefs = resolve(user_id)
            s, m = prefs["voice_service"], prefs["voice_model"]
            if s and m:
                try:
                    if self.installed(s, m):
                        return {"service": s, "model": m}
                except VoiceError:
                    pass
        selected = configuration()["default"]
        if not selected or not self.installed(**selected):
            raise VoiceError("voice_not_installed", "No default model installed", 409)
        return selected

    def transcribe(self, data, service=None, model=None, user_id=None, testing=False):
        started = time.monotonic()
        with self.inference:
            if not testing and not configuration()["enabled"]:
                raise VoiceError("voice_disabled", "Voice input is off", 409)
            selected = self.select(service, model, user_id)
            directory = self.service_dir(selected["service"])
            with tempfile.TemporaryDirectory(prefix="flowgate-voice-") as temporary:
                wav = convert(directory, data, Path(temporary))
                self.runtime.start(directory, self.model_path(**selected))
                text = self.runtime.transcribe(wav)
            self.error = None
            return {"ok": True, "text": text, "elapsed_ms": round((time.monotonic() - started) * 1000), **selected}

    def set_default(self, service, model):
        if not specification(service, model).get("default_eligible", True):
            raise VoiceError("voice_default_restricted", "Model license scope is pending; choose another default", 422)
        with self.inference:
            selected = self.select(service, model)
            config = configuration()
            if config["enabled"]:
                self.runtime.start(self.service_dir(service), self.model_path(service, model))
            config["default"] = selected
            save_configuration(config)
        return self.status(True)

    def enable(self, enabled):
        from . import voice_streams
        with voice_streams._condition, self.inference:
            if not enabled:
                voice_streams.stop_all()
            config = configuration()
            if enabled:
                selected = self.select()
                self.runtime.start(self.service_dir(selected["service"]), self.model_path(**selected))
            else:
                self.runtime.stop()
            config["enabled"] = enabled
            save_configuration(config)
            self.error = None
        return self.status(True)

    def restart(self):
        from . import voice_streams
        with voice_streams._condition, self.inference:
            self.runtime.stop()
            return self.enable(configuration()["enabled"])

    def remove(self, service, model):
        from . import voice_streams
        if voice_streams.active(service):
            raise VoiceError("voice_busy", "Finish live transcription before removing the service", 409)
        specification(service, model)
        from .voice_lock import installation_lock
        with voice_streams._condition, self.inference, self.lock, installation_lock(root()):
            if voice_streams.active(service):
                raise VoiceError("voice_busy", "Live transcription is active", 409)
            if self.busy(service, model):
                raise VoiceError("voice_install_in_progress", "Cancel installation before deleting", 409)
            if not self.installed(service, model):
                raise VoiceError("voice_not_installed", "Model is not installed", 404)
            self.runtime.stop()
            config = configuration()
            disabled = config["default"] == {"service": service, "model": model}
            if disabled:
                config.update(enabled=False, default=None)
                save_configuration(config)
            directory = self.service_dir(service)
            (directory / (model + ".installed.json")).unlink()
            model_path = self.model_path(service, model)
            if model_path.is_dir():
                shutil.rmtree(model_path)
            else:
                model_path.unlink(missing_ok=True)
            cached = root() / "downloads" / service / specification(service, model)["filename"]
            cached.unlink(missing_ok=True)
            remaining = any(self.installed(service, m["id"]) for m in specification(service)["models"])
            if not remaining:
                shutil.rmtree(directory)
                shutil.rmtree(root() / "downloads" / service, ignore_errors=True)
            if config["enabled"]:
                selected = self.select()
                self.runtime.start(self.service_dir(selected["service"]), self.model_path(**selected))
            return {"ok": True, "service_removed": not remaining, "voice_disabled": disabled}

    def startup(self):
        try:
            from .voice_distribution import cleanup_legacy_samples
            from .voice_lock import installation_lock
            with installation_lock(root()):
                cleanup_legacy_samples(root())
            if configuration()["enabled"]:
                self.enable(True)
        except Exception:
            self.error = {"code": "voice_module_unavailable", "message": "Voice module failed to start"}
            log.exception("Voice startup failed")

    def shutdown(self):
        with self.lock:
            self.stopping = True
            for event in self.cancel_events.values():
                event.set()
            for trial in self.trials.values():
                trial.stop()
        # Do not block the server's event loop on an inference in progress.
        self.runtime.stop()


manager = Manager()
