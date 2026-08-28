# External CLI launcher handoff contract

FlowGate's in-repository Codex and Claude process-creation boundary is
`modules.flow_gate.services.ai_invoke_service._cli_execute`. It resolves and validates a
single launch decision immediately before `subprocess.Popen`.

An out-of-repository session host must supply exactly one agent cwd input:

- `group_worktree_abs`: the ledger-matching worktree for the requested project/group; or
- `token_scratch_abs`: the current token/run's manifest-proven direct child of FlowGate's
  managed scratch root.

The value must be an existing, normalized absolute directory. Relative paths, the managed
root itself, another run, a base checkout for a Git-integrated group, and symlink/junction/
reparse targets are rejected. There is no fallback to the host process cwd, TEMP, HOME,
source tree, or a Git/CLI installation directory.

The host must record one `flowgate.external-cli-launch.v1` /
`external_cli_launch_decision` JSON event per decision. Required fields are `decision`,
fixed `reason` code for blocks, validated `run_id`, stable `provider_kind`, `cwd_source`,
`spawn_cwd`, `agent_cwd`, `cwd_transition`, `shell_kind`, and `is_unc`. Local/POSIX launches
use identical spawn and agent cwd with transition `none`. A Windows UNC worktree launches
cmd.exe in the local run scratch and uses a launcher-owned `pushd <agent_cwd> && <command>`
transition; a failed pushd must prevent the provider command.

Events and response details must never contain a raw token, Authorization header, prompt or
document body, command/argv, stdin/stdout/stderr, full environment, provider credential,
display name, or HOME value. JSON encoding must keep control characters inside one event.
Logging failure must not change execution.

No separate external session orchestrator, Git Bash launcher, adapter, configuration, name,
or owner was found in this repository as of T0011. The unapplied party is therefore
`unidentified external orchestrator`. Its owner is responsible for applying this input and
audit schema before creating its process. FlowGate owns the in-repository `_cli_execute`
boundary only; it neither claims enforcement on an unidentified host nor searches, moves,
or deletes historical files in TEMP, HOME, Git installations, or other external paths.