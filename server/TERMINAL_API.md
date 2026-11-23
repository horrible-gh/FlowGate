# Terminal system API

Base: `/flowgate/api/v1/terminal`. Login required; all `/system` endpoints require
`is_admin`. Errors use `TerminalError.body()`: `{ok:false,error:{code,message,details}}`.

| Method/path | Response |
| --- | --- |
| GET `/status?flow_id=` | Existing session fields; `enabled` reads `terminal.enabled`; disabled admin: `reason="disabled"`; hide toolbar button. |
| GET `/system` | `platform` (windows/linux/darwin), `bundle_required`, `installed`, `enabled`, `open_sessions`, `bundle`, `install`. |
| POST `/system/installs` | 202 install object; 409 `terminal_install_running` for web/CLI lock conflict; 409 `terminal_bundle_not_required` on native PTY OS. |
| GET `/system/installs/{install_id}` | Install object. |
| DELETE `/system/installs/{install_id}` | 202; request cancellation, poll until terminal status. |
| DELETE `/system/bundle` | `{ok:true}`; 409 `terminal_enabled` while enabled. |
| PUT `/system/enabled` | Strict boolean body `{enabled:true|false}`; `{enabled,closed_sessions}`. 409 `terminal_not_installed` if missing. |
| POST `/system/test` | `{ok:true,shell}` or `{ok:false,error_code,message}`; isolated shell spawn and cleanup. |

An install object has `install_id,status,stage,progress,bytes_done,bytes_total,error_code,message`.
Statuses: `queued|running|done|failed|cancelled`; stages: `download|verify|extract|test|apply`;
progress 0..1. Network failure: `terminal_download_failed`. Hash failure deletes the
bad download and returns `terminal_sha256_mismatch`. Cancellation leaves resumable
partial downloads. Jobs persist under storage `terminal/jobs/`; closing the browser
has no effect. Retrying after a server interruption resumes the partial download.

Windows bundles pin pywinpty 3.1.2 CPython 3.10–3.15 x64/ARM64, including cp314t/cp315t.
Unsupported ABI/architecture returns `terminal_platform_unsupported`.
Catalog: `services/terminal_catalog.json` (PyPI release metadata, exact URLs, sizes,
SHA-256). Data lives under the same storage root as voice, in `terminal/`.
No pip, site-packages writes, or activation on install. Linux/macOS are already
installed and return `bundle_required=false,bundle=null`.

Use `setup-terminal.ps1 --from-dir DIRECTORY` or
`bash setup-terminal.sh --from-dir DIRECTORY` for offline installation. The directory
must contain the exact catalog wheel for the server interpreter; source files are
read-only and there is no network fallback. `--list` prints the catalog.
`setup.ps1` offers optional component installation; enable separately in System Settings.

The sole activation source is system setting `terminal.enabled` (bool, default false),
read on each request. Retired dotenv entries are ignored. Disabling terminates every
session including pending tickets; connected WebSockets close with 4403 and
`terminal_disabled`. The UI must close its window, refresh status and hide the button.
Existing per-flow/per-user limits, idle timeout, cwd selection and audit rules remain.

On Windows, live imports use a process-local copy under `terminal/runtime/<pid>`
so mapped DLLs do not block deleting the installed bundle. Those in-use copies are
inert after deletion and are reclaimed on a later import/removal once their process
has exited. Re-enabling still requires a valid installed bundle.
