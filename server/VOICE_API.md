# Voice backend (flowgate.v02test.0058)

All routes below use the normal signed-in user Bearer under `/flowgate/api/v1`
(the configured context prefix may differ). Management operations require `is_admin`.
Errors use `{ "ok": false, "error": { "code": "...", "message": "..." } }`.

| Method/path | Input | Response / purpose |
| --- | --- | --- |
| GET `/voice/status` | — | `enabled`, `state`, `default`, `error`, `can_manage` |
| GET `/voice/catalog` | — | `services[].models[]`; ordinary users see installed models only |
| POST `/voice/installs` | JSON `service`, `model` | 202, `install_id`; continues after browser closes |
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
Whisper models and SenseVoiceSmall are not. SenseVoiceSmall supports Chinese,
English, Japanese, Korean and Cantonese.

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
manager used by the API. Options: `--service`, `--model`, `--from-dir`, `--list`.
Defaults are `whisper.cpp` / `base`. The Windows main setup asks whether to install
voice after database initialization. The catalog currently supports Windows x64;
other platforms receive `available:false` with a reason.

Runtime files live at `FLOWGATE_VOICE_DIR`, or the system storage root's `voice/`
directory. Project storage overrides are not consulted. Runtime archives, models,
and the smoke-test sample have pinned versions and SHA-256 hashes. Downloads
support resume, cancellation, disk-space checks and durable progress records.
On server interruption, an old job is reported interrupted; starting another
install reuses verified downloads and resumes its partial files.

Offline mode never downloads and never changes the source directory. Supply the
catalog archives/model and `jfk.wav`. For whisper.cpp, exact-version expanded
executables/DLLs and ffmpeg are also accepted and checked against per-file hashes.
`Recording_1.m4a` may substitute for the offline smoke-test sample. A mismatched
binary version is rejected rather than executed. Catalog versions: whisper.cpp
1.8.2, sherpa-onnx 1.12.20, Vosk 0.3.45, ffmpeg 8.0.

Catalog contents (`services/voice_catalog.json`, the only source of the list):
whisper.cpp carries every ggml model of the pinned upstream revision (tiny to
large-v3 and large-v3-turbo, their q5/q8 quantized builds, and the English-only
`.en` builds); sherpa-onnx carries SenseVoiceSmall and the English and Korean
streaming Zipformers; Vosk carries the small Korean and US English models.

The first model installs its runtime. Every model must pass a real sample
transcription before publication; installation then activates the default.
The sample is `jfk.wav` (must contain "country") unless the model entry has
`self_test {sample, expect}`: `sample` is a file inside the model archive or a
pinned service artifact, and `expect` must appear in the transcript (whitespace
ignored). Models without English need this entry.
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
