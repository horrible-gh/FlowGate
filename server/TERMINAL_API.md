# Terminal API

Base: `/flowgate/api/v1/terminal`. Login required. Errors use `TerminalError.body()`:
`{ok:false,error:{code,message,details}}`.

The terminal is always available to administrators. There is no on/off setting, no
install screen and no bundle: a leftover `terminal.enabled` value in the DB and a
leftover `terminal/` folder in system storage are simply not read (nothing deletes them).

| Method/path | Response |
| --- | --- |
| GET `/status?flow_id=` | `{enabled:true, allowed, reason, max_sessions, active_sessions, flow_session_id}`. `enabled` is always `true` (kept for older screens). `reason` is `null` or `"not_admin"`; the screen shows the toolbar button when `allowed`. |
| POST `/sessions` | Admin only. Body `{flow_id, cols, rows}` → 201 `{session_id, cwd, ticket, ticket_expires_in}`. 403 `terminal_not_admin`; 409 `terminal_flow_session_exists` / `terminal_session_limit`; 404 `terminal_flow_not_found`; 409 `terminal_cwd_unavailable`. |
| WS `/ws?ticket=` | One-time 30-second ticket. Unknown/expired ticket closes with 1008. Frames: `output`, `busy`, `card_running`, `exit`. |
| DELETE `/sessions/{session_id}` | 204; ends the session and its whole process tree. |

`exit` frame: `{type:"exit", reason: closed|idle_timeout|shell_exit|error, code}`. When the
shell could not start, the frame also carries `message` (the start-failure reason) and the
screen shows it.

Kept rules: administrators only, one terminal per Flow per person, up to 3 per person,
30-minute idle timeout, start folder decided by the server (group worktree, else the
project branch checkout), start/end audit record without keystrokes or output.

## Dependencies

Shell components are ordinary Python requirements in `server/requirements.txt`:

- `pywinpty==3.1.2; sys_platform == "win32"` (ConPTY; CPython 3.10–3.15 wheels, x64/ARM64)
- `psutil>=5.9; sys_platform == "win32"` (process-tree cleanup; falls back to `taskkill /T`)

They are installed by the normal `pip install -r server/requirements.txt` that
`setup.ps1`, `setup.sh` and the `Dockerfile` already run. Linux/macOS use the stdlib
`pty` module and need nothing extra.

On an already-installed Windows server, rerun `pip install -r server/requirements.txt`
(or rerun setup) after updating, then restart the server. If `pywinpty` is missing the
shell does not crash the server: the WebSocket ends with `exit` reason `error` and a
`message` telling the operator to run `pip install -r server/requirements.txt`.
