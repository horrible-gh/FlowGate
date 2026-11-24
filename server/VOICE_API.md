# Voice backend (flowgate.v02test.0058)

All routes below use the normal signed-in user Bearer under `/flowgate/api/v1`
(the configured context prefix may differ). Management operations require `is_admin`.
Errors use `{ "ok": false, "error": { "code": "...", "message": "..." } }`.

| Method/path | Input | Response / purpose |
| --- | --- | --- |
| GET `/voice/status` | — | `enabled`, `state`, `default`, `error`, `can_manage`, `retired` (administrators only) |
| GET `/voice/catalog` | — | `services[].models[]`; ordinary users see installed models only |
| POST `/voice/installs` | JSON `service`, `model` (other fields are rejected with 422) | 202, `install_id`; continues after browser closes |
| GET `/voice/installs/{id}` | — | `step`, `received_bytes`, `total_bytes`, `done`, `error` |
| DELETE `/voice/installs/{id}` | — | Request cancellation; poll until `done` |
| DELETE `/voice/services/{service}/models/{model}` | — | `service_removed`, `voice_disabled` |
| PUT `/voice/default` | JSON `service`, `model` | Current status |
| PUT `/voice/enabled` | JSON `enabled` boolean | Current status |
| POST `/voice/restart` | — | Restart if enabled; current status |
| POST `/voice/transcriptions` | multipart `file`, optional paired `service`/`model` | `text`, `elapsed_ms`, `service`, `model` |
| POST `/voice/test` | Same multipart, administrator only | Test an installed model even when switched off |
| POST `/flows/{flow_id}/message-audio` | Same multipart | `audio{audio_id,media_type,size,text,url}`, `elapsed_ms` |
| GET `/flows/{flow_id}/message-audio/{id}/content` | — | Private m4a playback, including byte ranges |
| DELETE `/flows/{flow_id}/message-audio/{id}` | — | Delete the owner's unsent upload |

An audio message is sent through the existing session conversation append route:
`POST /documents/{doc_id}/conversation/turn` with `body`, `idempotency_key`, and
`audio_id`. An empty body is allowed with audio. The server includes its stored
transcript in the body, links audio in the same transaction, and rejects reuse
in a different message. Turn responses contain `audio` metadata or null. AI
conversation context gets the transcript as ordinary text; it receives no audio file.
Deleting a turn also makes its playback unavailable. Unsent audio expires after 24h.

`GET/PATCH /me/ui-settings` adds `voice_action` (`text` or `audio`, default `text`),
`voice_service`, `voice_model` (empty strings mean use the default), and
`voice_realtime` (boolean, default false). Send service/model together. A deleted
personal model falls back to the system default. Realtime can only be selected for
a realtime-capable installed model. Responses retain the existing settings envelope.

## Realtime input

Realtime-capable models: `sherpa-onnx` / `zipformer-en-20m` (English) and
`zipformer-ko` (Korean), and every `vosk` model (`small-ko`, `small-en-us`).
Whisper models are not.

1. POST `/voice/streams` with JSON `{}` or paired `service`/`model`.
2. Receive `stream_id`, `sample_rate:16000`, `channels:1`, `format:"pcm_s16le"`.
3. POST multipart `file` to `/voice/streams/{id}/chunks?index=0`, then index 1, 2,
   etc. Each chunk is raw signed little-endian PCM16, mono, 16 kHz, at most 5 seconds.
   Send requests sequentially. Each response returns the current `text`, `final`,
   `next_chunk`, and actual `elapsed_ms`; replace the previous partial text.
4. Send the final chunk with `&final=true` (an empty file is valid), or DELETE
   `/voice/streams/{id}` to cancel. Limit: 120 seconds of samples.

Sessions are owned by the authenticated user. Only one live session is admitted
at a time; another waits for its turn, with a bounded timeout. Abandoned sessions
expire. Installation/removal of a service is refused while its live session is
active. Encoded browser webm/m4a is for the ordinary transcription route; live
input requires PCM from AudioWorklet. No external speech API is used.

## Installation and storage

`setup-voice.ps1` and `setup-voice.sh` call `server/setup_voice.py` and the same
manager used by the API. Options include `--service`, `--model`, `--from-dir`, `--list`, `--export-bundle`,
`--prepare-model`, `--prepared-output` and `--ffmpeg-from-dir`.
Defaults are `whisper.cpp` / `base`. The Windows main setup asks whether to install
voice after database initialization. The catalog currently supports Windows x64;
other platforms receive `available:false` with a reason.

Runtime files live at `FLOWGATE_VOICE_DIR`, or the system storage root's `voice/`
directory. Project storage overrides are not consulted. Runtime archives, models,
and the smoke-test sample have pinned versions and SHA-256 hashes. Downloads
support resume, cancellation, disk-space checks and durable progress records.
On server interruption, an old job is reported interrupted; starting another
install reuses verified downloads and resumes its partial files.

Local `--from-dir` installation retains catalog hash and size verification.
It accepts the catalog-pinned KSS-free Korean tar, not the restricted original.
Available catalog notices are supplied automatically from the repository and
preserved in the installation; callers need not copy them manually.

`--export-bundle DEST --from-dir SOURCE` exports only reviewed components and
exact notices. whisper.cpp 1.8.2 CPU runtime plus selected pinned Whisper weights
are enabled; the base combination has actual export/install evidence. sherpa and
Vosk redistribution remain pending native provenance. This is not a statement
that a component has no license or that commercial use is prohibited.

Exports exclude samples, the original KSS-containing Korean archive and FFmpeg.
Offline installation requires separately acquired, locally verified FFmpeg 8.0
and the Microsoft runtime system prerequisites. See the F86-9 instructions below
for the exact preparation, export and offline-install commands and limitations.
FFmpeg's source commit is `140fd653ae`; this installer does not provide an FFmpeg
redistribution path or claim GPL section 6 fulfillment for rehosting it.

Catalog contents (`services/voice_catalog.json`, the only source of the list):
whisper.cpp carries the six multilingual base models of the pinned upstream
revision (tiny, base, small, medium, large-v3-turbo, large-v3) and no quantized,
English-only `.en` or older large-v1/v2 builds; sherpa-onnx carries the English and Korean
streaming Zipformers; Vosk carries the small Korean and US English models.

The first eligible model installs and activates its runtime. Korean Zipformer and
Vosk Korean use `self_test {mode: "load_only"}`: the real runtime must load and
respond to health checks, with no sample transcription. Their weight license
labels remain Apache-2.0. The settings test accepts a user's microphone recording
or uploaded audio and does not require a built-in Korean sample. Other models
continue their pinned sample transcription checks.

Korean Zipformer extraction excludes `test_wavs/`, then discards the original
archive (also on error/cancellation). Startup and subsequent installation clean
known application-owned legacy KSS paths and archive caches. User recordings and
read-only import directories are not cleaned or modified.

SenseVoiceSmall (`sherpa-onnx` / `sensevoice-small-int8`) was removed in v0.2
(F86-7, operator decision U1). It is not in the catalog, and install, default,
selection, transcription and removal requests for it return `voice_model_retired`
(410). The former `license_ack` request field and `--ack-model-license` CLI option
were removed with it. If an older installation left its files on disk or stored it as
the default, voice input stays off for it and `GET /voice/status` lists it under
`retired` for administrators, who see it as "no longer supported". The server never
deletes those user files automatically. `SherpaOnnxOfflineSenseVoiceModelConfig`
remains in `voice_sherpa_abi.py` only because it is part of the sherpa-onnx C struct
layout. Model terms are never inferred from an engine's license label.

Deleting the default switches voice off; deleting a service's last model removes
that runtime and its download cache. Shell arguments are never accepted from users.
Whisper runs on a random loopback port; sherpa and Vosk each use an authenticated
loopback HTTP worker with the same contract (`voice_sherpa_worker.py`,
`voice_vosk_worker.py`) over the pinned native C API, with no additional Python
ML dependencies.

Apply migrations 207 (user voice settings) and 208 (audio and conversation link)
through the normal server migration mechanism before using these endpoints.

## Validation

Regression files: `test_voice_0058.py`, `test_voice_streams_0058.py`,
`test_message_audio_0058.py`, `test_voice_local_bundle_0058.py` under
`server/tests/regression/`. The local bundle smoke test copies the operator's
`C:/tmp/stt-test` assets to a temporary directory and never executes in the source
bundle. Override with `FLOWGATE_VOICE_TEST_DIR`.

Full isolated download/install tests are opt-in:
`python -m pytest -q tests/regression/voice_integration --voice-downloads`
from `server/`. They use temporary voice storage and in-memory system settings.

Upstream contracts: [whisper server](https://github.com/ggml-org/whisper.cpp/tree/v1.8.2/examples/server),
[sherpa C API](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.20/sherpa-onnx/c-api/c-api.h),
[Vosk C API](https://github.com/alphacep/vosk-api/blob/v0.3.45/src/vosk_api.h).

## F86-9: KSS-free offline models and reviewed export

Korean Zipformer local installation accepts only the catalog-pinned `.no-kss.tar`,
not the original `.tar.bz2`, an original renamed to the clean name, or hashes in
an imported manifest. Prepare it on a connected staging machine:

```sh
python server/setup_voice.py --service sherpa-onnx --model zipformer-ko --prepare-model /staging/sherpa-onnx-streaming-zipformer-korean-2024-06-16.tar.bz2 --prepared-output /staging/clean/sherpa-onnx-streaming-zipformer-korean-2024-06-16.no-kss.tar
```

The original stays unchanged. The transformer verifies original SHA256
`e346a5882a409650472be17326237e24df7bf409db6b4a8a52e1a61422bf2500`,
streams only the eight model/token files into deterministic USTAR, adds the exact
Apache-2.0 text, and verifies the result: 446904320 bytes, SHA256
`23bddf71915710fcccc350486cc2350502ff3504e18627857e35b2012005a93f`.
There is no upstream LICENSE/NOTICE in this model archive; the added LICENSE is
identified as our addition. No upstream NOTICE is invented. `test_wavs/` is never
extracted. Archive links, special files, traversal, duplicate/unexpected and missing
members are rejected. Individual source member hashes are fixed in the catalog.
The sanitized file has no external-download URL: offline mode cannot fetch a raw
original instead. This restores local administrator installation, not permission
to redistribute the sherpa engine/model bundle.

For a local Korean install, put the pinned sherpa engine archive and the clean
model tar in `/offline/korean`. Obtain Gyan FFmpeg 8.0 independently on a connected
machine, keep its exact catalog archive in `/independently-acquired/ffmpeg`, and
carry it to the offline machine. The following does not access upstream:

```sh
python server/setup_voice.py --service sherpa-onnx --model zipformer-ko --from-dir /offline/korean --ffmpeg-from-dir /independently-acquired/ffmpeg
```

FFmpeg is verified, copied and executed locally (`-version`) before installation
succeeds. It is not included in our export. Windows x64 must already have the
Microsoft VC++ x64 and OpenMP runtime required by these binaries; those Microsoft
DLLs are system prerequisites and are not copied by this exporter. A missing
prerequisite causes installation to fail rather than silently downloading it.
Offline installation checks model loading without downloading or packaging a
sample. Use your own recording for the subsequent transcription test.

The restored export combination is whisper.cpp 1.8.2 CPU runtime plus one pinned
Whisper model. The exporter accepts the exact upstream engine ZIP or its pinned
expanded files and emits only the six catalog-listed runtime files, the selected
weights, and exact component license/attribution files. It does not copy unrelated
upstream example programs, FFmpeg, jfk.wav, Korean audio or arbitrary cache files.

```sh
python server/setup_voice.py --service whisper.cpp --model base --from-dir /staging/verified-whisper-files --export-bundle /delivery/whisper-base
python server/setup_voice.py --service whisper.cpp --model base --from-dir /delivery/whisper-base --ffmpeg-from-dir /independently-acquired/ffmpeg
```

License texts are supplied automatically from `third_party/licenses/`, including
in Docker's existing copy of that directory. An existing but changed input notice
is an error, not a reason to substitute it silently. Export verifies every emitted
file after copying. Installation also preserves available catalog notices under
its `licenses/` directory. No online fallback occurs when `--from-dir` is set.

sherpa-onnx and Vosk redistribution remain pending. Source license snapshots and
hashes are recorded in `third_party/voice_source_audit.json`; they are not proof
that an unverified candidate version was linked into an original release binary.
The nested kaldi-decoder CMake selects kaldifst 1.7.16, while sherpa's separate
candidate helper names 1.7.17. Resolve the actual build graph (and OpenFst,
KissFFT/Eigen attribution) before enabling sherpa export. Vosk still needs exact
GCC/mingw-w64 and static Kaldi/OpenFst/BLAS provenance or a fixed replacement build.
The available Windows test host has no CMake, C/C++ compiler or Ninja. Unresolved
npm development-tool notices do not participate in these Python/native bundles.
SenseVoice and the Korean default sample remain removed.

Opt-in real tests use `FLOWGATE_VOICE_EVIDENCE_DIR` as scratch. Alternatively pass
an explicit `--basetemp <task-scratch>/native-tests/<run>`; artifacts persist in the
sibling `voice-artifacts` directory for bounded acquisition/preparation/install
checks. Acquisition tests need upstream access; actual installation tests deny
external downloads. Ordinary regression runs skip these opt-in checks.
