"""AI settings service (flowgate.default.0164 D0002 / P0003 / L0004).

Owns the ordered AI-provider list ("routing chain": array order = fallback order) in two
scopes — global and per-project tri-state (inherit / disabled / custom) — plus the
effective-settings resolution the follow-up execution module (0116 line) will consume.
Actual invocation (fallback firing, invoke) is a later group; this module only registers,
stores and interprets settings.

Secret handling (L0004 §2.3): api_key is write-only. Requests may carry it
(absent/null = keep, "" = delete, value = replace); serialized responses only ever carry
api_key_set + api_key_hint. The raw value is reachable solely via get_provider_secret(),
which must never be called from an HTTP serialization path.

This layer works in PLAINTEXT throughout: at-rest encryption (0371 NR0007 §3) is the db
layer's job, which encrypts on write and decrypts on read, so the keep/replace/delete
merge and the last-4 hint keep operating on the real value.
"""
from __future__ import annotations

import re
import secrets
from typing import Optional

from modules.flow_gate.db import ai_provider_doctype_map as _doctype_db
from modules.flow_gate.db import ai_providers as _ai_db
from modules.flow_gate.db import projects as _projects_db
from modules.flow_gate.services import test_command_service as _tcs

# ── Parameters (L0004 §1) ────────────────────────────────────────────────────
NAME_MAX = 100
# Raised 500 -> 4000 (0241 CH0004): real commands (a `claude` one-liner carrying sandbox
# settings) run past 500. The DB column is TEXT, so this cap is policy only — it exists to
# keep the value renderable in the UI/logs, not because storage or the shell needs it.
CLI_COMMAND_MAX = 4000
API_BASE_URL_MAX = 500
API_MODEL_MAX = 200
API_KEY_MAX = 1000
PROVIDERS_MAX = 20
# Per-document-type provider assignment (flowgate.default.0317 D0004 implementation). A project's
# workflow has a handful of worker doc types (NR/TR/TSR/TS, ...); cap generously so the map
# stays renderable without constraining any real sequence.
DOCTYPE_ASSIGN_MAX = 50
DOCTYPE_CODE_MAX = 40
HINT_LEN = 4
ID_PREFIX = "aip_"
ID_SUFFIX_LEN = 6
ID_RETRY_MAX = 5
EXEC_TYPES = ("cli", "api")
KINDS_CLI = ("claude", "copilot", "codex", "custom")
KINDS_API = ("claude", "openai", "custom")
MODES = ("inherit", "disabled", "custom")
MODE_DEFAULT = "inherit"

_ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

_DEFAULT_BASE_URLS = {
    "claude": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
}

# Per-kind × host-OS example CLI commands (flowgate.default.0281 T0005, NR0003 §4 F2 / R2).
# Before this, the only guidance the product offered was the static claude-shaped
# placeholder `claude -p` (client i18n `placeholder_cli_command`), which stayed put even
# after picking Codex/Copilot — so operators pasted the CLIs' Linux-written docs verbatim
# and they broke under cmd.exe. The catalog now carries a starting point per kind, keyed by
# host OS ("nt"/"posix"), so the UI can surface the example that matches THIS host.
#
# These are seeds, not verified truth: the codex/copilot strings are the POSIX-documented
# forms from R0001, reused for "nt" until an operator confirms a Windows form via the
# provider connection test (test-provider endpoint). "custom" gets no example by design —
# it is the escape hatch for arbitrary commands.
#
# 0295 NR0003 §5-1: the claude row was the old i18n placeholder promoted verbatim, so it
# carried no model, and the codex row was missing `--skip-git-repo-check`, which
# `codex exec` requires whenever cwd is not a git repo; see CODEX_SKIP_GIT_FLAG below.
#
# 0371 NR0007 §5: that same fix also parked `--dangerously-skip-permissions` (claude) and
# `--ask-for-approval never` (codex) in here, and this dict is what the installer seeds and
# what the settings screen offers — so every provider created the easy way ran with
# permission confirmation switched OFF, a decision nobody consciously made because it was
# spelled as a word inside a free-text command box. These examples now carry the SAFE form.
# The permissive form still exists and is one checkbox (editor) or one flag (seed) away —
# see _PERMISSION_SKIP_RULES / set_permission_skip below — it just has to be asked for.
_CLI_COMMAND_EXAMPLES: dict[str, dict[str, str]] = {
    "claude": {
        "posix": "claude --model claude-opus-4-8 -p -",
        "nt": "claude --model claude-opus-4-8 -p -",
    },
    "codex": {
        "posix": (
            "codex --ask-for-approval on-request --sandbox workspace-write exec "
            "--skip-git-repo-check "
            "-c sandbox_workspace_write.network_access=true --json --model gpt-5.6-sol -"
        ),
        "nt": (
            "codex --ask-for-approval on-request --sandbox workspace-write exec "
            "--skip-git-repo-check "
            "-c sandbox_workspace_write.network_access=true --json --model gpt-5.6-sol -"
        ),
    },
    "copilot": {
        "posix": "copilot --model claude-sonnet-5 --output-format=json",
        "nt": "copilot --model claude-sonnet-5 --output-format=json",
    },
    "custom": {
        "posix": "",
        "nt": "",
    },
}


# ── Permission confirmation (0371 NR0007 §5) ───────────────────────────────────
#
# Neither CLI is configured through a FlowGate setting here: "ask before you act" is a flag
# inside the free-text cli_command. "Default off" therefore has to mean two concrete things
# — every string this module hands out carries the safe form, and switching the skip back
# on is a deliberate act (the editor checkbox, the seed's --skip-permissions) instead of a
# side effect of accepting a suggestion.
#
#   skip     the canonical permissive token this module writes.
#   safe     what takes its place when the skip is switched off. Empty means "just drop it";
#            codex keeps asking only if some policy is named, so its safe form spells out
#            the CLI's own default rather than leaving the flag out.
#   markers  every spelling that means "permission checks are off" — detection only, so a
#            hand-written or legacy command is still reported for what it is.
#
# Rows already in ai_providers.cli_command are deliberately NOT rewritten: that string is
# operator configuration, and editing it behind their back would be its own surprise (and
# would break the one host that needs the skip). Existing installs keep running exactly as
# before; what changes is where NEW providers start.
_PERMISSION_SKIP_RULES: dict[str, dict] = {
    "claude": {
        "skip": "--dangerously-skip-permissions",
        "safe": "",
        "markers": ("--dangerously-skip-permissions",),
    },
    "codex": {
        "skip": "--ask-for-approval never",
        "safe": "--ask-for-approval on-request",
        "markers": (
            "--ask-for-approval never",
            "--ask-for-approval=never",
            "--dangerously-bypass-approvals-and-sandbox",
            "--yolo",
        ),
    },
}


def _token_re(token: str) -> "re.Pattern":
    """Match *token* as whole shell words, tolerating any run of whitespace inside it.

    The boundaries are shell boundaries, not \\b: `--yolo` must not match inside
    `--yolo-mode`, and `--ask-for-approval never` must not match `--ask-for-approval
    never-mind`.
    """
    body = r"\s+".join(re.escape(word) for word in token.split())
    return re.compile(r"(?<![^\s])" + body + r"(?![^\s])")


def _drop_token(cmd: str, token: str) -> str:
    """Remove *token* together with the whitespace that separated it from what came before."""
    return re.sub(r"\s*" + _token_re(token).pattern, "", cmd).strip()


def _insert_after_program(cmd: str, token: str) -> str:
    """Put *token* directly after the executable name.

    Both CLIs take these as global options that precede the subcommand (`codex ... exec`),
    and a working command usually ends in a bare `-` meaning "prompt on stdin" — appending
    there would hand the flag to the wrong parsing stage.
    """
    parts = cmd.split(None, 1)
    if len(parts) == 1:
        return f"{parts[0]} {token}"
    return f"{parts[0]} {token} {parts[1]}"


def permission_skip_rule(kind: Optional[str]) -> Optional[dict]:
    """The rule for *kind*, or None for a kind with no known flag (copilot, custom).

    None means "this screen has nothing to offer here" — not "this CLI always asks".
    """
    return _PERMISSION_SKIP_RULES.get(kind or "")


def has_permission_skip(kind: Optional[str], cli_command: Optional[str]) -> bool:
    """True when *cli_command* tells this CLI not to ask before it reads, writes or runs."""
    rule = permission_skip_rule(kind)
    cmd = (cli_command or "").strip()
    if rule is None or not cmd:
        return False
    return any(_token_re(marker).search(cmd) for marker in rule["markers"])


def set_permission_skip(
    kind: Optional[str], cli_command: Optional[str], enabled: bool,
) -> str:
    """Return *cli_command* with permission confirmation off (*enabled*) or back on.

    Rewrites in place wherever it can — an `--ask-for-approval never` becomes
    `--ask-for-approval on-request` right where it stands — so toggling twice hands the
    original string back. A kind with no known flag, or an empty command, is returned as is:
    inventing a flag for a CLI we have not verified would be worse than leaving it alone.
    """
    cmd = (cli_command or "").strip()
    rule = permission_skip_rule(kind)
    if rule is None or not cmd:
        return cmd

    if enabled:
        if has_permission_skip(kind, cmd):
            return cmd
        safe = rule["safe"]
        if safe and _token_re(safe).search(cmd):
            return _token_re(safe).sub(lambda _m: rule["skip"], cmd, count=1)
        return _insert_after_program(cmd, rule["skip"])

    if not has_permission_skip(kind, cmd):
        return cmd
    for marker in rule["markers"]:
        cmd = _drop_token(cmd, marker)
    # Dropping codex's flag would leave the policy unnamed, which is not the same promise as
    # "it asks" — so the safe policy is spelled out, but only on a command that really did
    # carry a skip. Switching an already-safe command "off" must be a no-op.
    safe = rule["safe"]
    if safe and safe.split()[0] not in cmd:
        cmd = _insert_after_program(cmd, safe)
    return cmd.strip()


def _unattended_cli_examples() -> dict[str, dict[str, str]]:
    """The examples above with permission confirmation switched off.

    Derived, never a second copy: a fix to a command string reaches both forms, and the
    editor's checkbox and this catalog can never disagree about what "off" looks like.
    """
    return {
        kind: {
            host_os: set_permission_skip(kind, command, True)
            for host_os, command in per_os.items()
        }
        for kind, per_os in _CLI_COMMAND_EXAMPLES.items()
        if kind in _PERMISSION_SKIP_RULES
    }


# Fixing the example above only helps providers registered AFTER the fix — rows already in
# `ai_providers.cli_command` keep the broken string, which is exactly the provider that
# produced 0295 B0001. So the flag is also injected at spawn time, by both the invoke path
# and the connection probe, through normalize_cli_command() (0295 NR0003 §5-2 / §5-3).
CODEX_SKIP_GIT_FLAG = "--skip-git-repo-check"


def normalize_cli_command(kind: Optional[str], cli_command: str) -> str:
    """Return *cli_command* with the per-kind flags a run cannot work without.

    Today that is codex only. `codex exec` refuses to start unless cwd is inside a git
    repository, and reports it as::

        Not inside a trusted directory and --skip-git-repo-check was not specified.

    on stderr with exit 1, in well under a second — which the invoke path then classifies as
    `fast_fail` ("exit immediately") and burns as a provider failure (0295 NR0003 §2). FlowGate has
    three ways to end up outside a repo: the scratch-dir fallback when the source mirror is
    missing (ai_invoke_service._cli_execute), a project whose mirror exists but is not a git
    checkout, and the probe's tempfile.mkdtemp() cwd — the last of which made the 0281
    connection test report `command_failed` for every codex provider, however correct.

    The flag only suppresses a precondition check, so appending it is harmless when cwd IS a
    repo. It is appended at the end rather than spliced after `exec`: verified equivalent on
    codex (0295 NR0003 §4-4), and appending needs no parsing of an operator-authored string.
    Callers that already carry the flag are left untouched.
    """
    cmd = (cli_command or "").strip()
    if not cmd or (kind or "") != "codex":
        return cmd
    if CODEX_SKIP_GIT_FLAG in cmd:
        return cmd
    return f"{cmd} {CODEX_SKIP_GIT_FLAG}"


class AiSettingsValidationError(Exception):
    """Carries the P0003 422 error array (code: validation_failed)."""

    def __init__(self, errors: list[dict]):
        super().__init__("validation_failed")
        self.errors = errors


def get_catalog() -> dict:
    """Enum lists the editor renders from, plus (0281 T0005) host-OS context and per-kind
    example CLI commands so the UI can offer an OS-appropriate starting point instead of the
    static `claude -p` placeholder. `host_os`/`host_shell` describe the machine that will
    actually run cli_command via shell=True — the same values the connection test injects as
    FLOWGATE_OS / FLOWGATE_SHELL.

    `cli_permission_skip` (0371 NR0007 §5) is how the editor renders the permission-skip
    opt-in without owning a second copy of the flags: `default_enabled` is the answer to
    "what does a new provider start as" (false), `rules` drives detect/apply on the command
    string, and `examples` is `cli_examples` with the skip already applied.
    """
    return {
        "exec_types": list(EXEC_TYPES),
        "kinds": {"cli": list(KINDS_CLI), "api": list(KINDS_API)},
        "host_os": _tcs.current_os(),
        "host_shell": _tcs.current_shell(),
        "cli_examples": {k: dict(v) for k, v in _CLI_COMMAND_EXAMPLES.items()},
        "cli_permission_skip": {
            "default_enabled": False,
            "rules": {
                kind: {
                    "skip": rule["skip"],
                    "safe": rule["safe"],
                    "markers": list(rule["markers"]),
                }
                for kind, rule in _PERMISSION_SKIP_RULES.items()
            },
            "examples": _unattended_cli_examples(),
        },
    }


# ── Validation (L0004 §2.1) ──────────────────────────────────────────────────

def validate_settings(
    providers: Optional[list[dict]],
    default_provider_id: Optional[str],
    default_provider_index: Optional[int],
    mode: Optional[str],
) -> list[dict]:
    """Collect ALL errors (no early stop on the first one)."""
    errors: list[dict] = []

    if mode is not None and mode not in MODES:
        errors.append({"field": "mode", "reason": "unknown_mode"})

    # inherit/disabled do not validate providers (null allowed).
    if mode in ("inherit", "disabled"):
        return errors

    if providers is None:
        errors.append({"field": "providers", "reason": "required"})
        return errors

    if len(providers) > PROVIDERS_MAX:
        errors.append({"field": "providers", "reason": "too_many"})

    names_seen: set[str] = set()
    for index, p in enumerate(providers):
        name = (p.get("name") or "").strip()
        if name == "":
            errors.append({"index": index, "field": "name", "reason": "required"})
        elif len(name) > NAME_MAX:
            errors.append({"index": index, "field": "name", "reason": "too_long"})
        elif name.lower() in names_seen:
            errors.append({"index": index, "field": "name", "reason": "duplicate_name"})
        else:
            names_seen.add(name.lower())

        exec_type = p.get("exec_type")
        if exec_type not in EXEC_TYPES:
            errors.append({"index": index, "field": "exec_type", "reason": "unknown_exec_type"})
        elif exec_type == "cli":
            if p.get("kind") not in KINDS_CLI:
                errors.append({"index": index, "field": "kind", "reason": "unknown_kind"})
            cli_command = p.get("cli_command") or ""
            if cli_command.strip() == "":
                errors.append({"index": index, "field": "cli_command", "reason": "required_for_cli"})
            elif len(cli_command) > CLI_COMMAND_MAX:
                errors.append({"index": index, "field": "cli_command", "reason": "too_long"})
        else:  # exec_type == "api"
            if p.get("kind") not in KINDS_API:
                errors.append({"index": index, "field": "kind", "reason": "unknown_kind"})
            api_model = p.get("api_model") or ""
            if api_model.strip() == "":
                errors.append({"index": index, "field": "api_model", "reason": "required_for_api"})
            elif len(api_model) > API_MODEL_MAX:
                errors.append({"index": index, "field": "api_model", "reason": "too_long"})
            api_base_url = p.get("api_base_url")
            if api_base_url is not None and len(api_base_url) > API_BASE_URL_MAX:
                errors.append({"index": index, "field": "api_base_url", "reason": "too_long"})
            api_key = p.get("api_key")
            if api_key is not None and len(api_key) > API_KEY_MAX:
                errors.append({"index": index, "field": "api_key", "reason": "too_long"})
            # Blank api_base_url falls back to the per-kind default URL — not an error.

    # Default selection: id wins over index; both null = auto-pick at save (L §4.1).
    if default_provider_id is not None:
        payload_ids = [p.get("id") for p in providers if p.get("id") is not None]
        if default_provider_id not in payload_ids:
            errors.append({"field": "default_provider_id", "reason": "bad_default"})
    elif default_provider_index is not None:
        if default_provider_index < 0 or default_provider_index >= len(providers):
            errors.append({"field": "default_provider_index", "reason": "bad_default"})

    return errors


# ── Internal helpers ─────────────────────────────────────────────────────────

def _issue_id(taken_ids: set[str]) -> str:
    for _ in range(ID_RETRY_MAX):
        candidate = ID_PREFIX + "".join(
            secrets.choice(_ID_ALPHABET) for _ in range(ID_SUFFIX_LEN)
        )
        if candidate not in taken_ids:
            return candidate
    raise RuntimeError("id_collision_exhausted")  # 36^6 space — practically unreachable


def _resolve_base_url(p: dict) -> Optional[str]:
    if p.get("exec_type") == "cli":
        return None
    base_url = (p.get("api_base_url") or "").strip()
    if base_url:
        return p["api_base_url"]
    return _DEFAULT_BASE_URLS.get(p.get("kind"))


def _merge_api_key(old_row: Optional[dict], incoming: Optional[str]):
    """absent/null = keep, "" = delete, value = replace (L0004 §2.3).

    "Keep" on a row whose stored key no longer decrypts (0371) has no plaintext to
    carry over, so it returns the db layer's keep-stored sentinel instead of None —
    a request that never mentioned api_key must not delete one.
    """
    if incoming is None:
        if old_row is None:
            return None
        if old_row.get("api_key_unreadable"):
            return _ai_db.KEEP_STORED_SECRET
        return old_row.get("api_key")
    if incoming == "":
        return None
    return incoming


def _to_view(row: dict) -> dict:
    api_key = row.get("api_key")
    # 0371: rows arrive decrypted, so the hint is still the real last 4 characters.
    # `unreadable` is the one case where a key IS stored but its plaintext is gone
    # (master key changed/lost) — reporting api_key_set=False there would read as
    # "no key configured" and hide the loss behind an ordinary-looking screen.
    unreadable = bool(row.get("api_key_unreadable"))
    view = {
        "id": row["provider_id"],
        "name": row["name"],
        "exec_type": row["exec_type"],
        "kind": row["kind"],
        "enabled": bool(row["enabled"]),
        "cli_command": row.get("cli_command"),
        "api_base_url": row.get("api_base_url"),
        "api_model": row.get("api_model"),
        "api_key_set": api_key is not None or unreadable,
        "api_key_hint": api_key[-HINT_LEN:] if api_key else None,
    }
    if unreadable:
        view["api_key_unreadable"] = True
    return view


def _resolve_default(
    default_provider_id: Optional[str],
    default_provider_index: Optional[int],
    saved_rows: list[dict],
    id_map: dict[str, str],
) -> Optional[str]:
    """Settle the default at save time (L0004 §4.1). id_map translates a payload id
    that was re-issued (unknown/foreign id treated as new row, L §5) to its saved id."""
    saved_ids = [r["provider_id"] for r in saved_rows]
    if default_provider_id is not None:
        mapped = id_map.get(default_provider_id, default_provider_id)
        if mapped in saved_ids:
            return mapped
    elif default_provider_index is not None:
        return saved_rows[default_provider_index]["provider_id"]
    for r in saved_rows:
        if r["enabled"]:
            return r["provider_id"]
    if saved_rows:
        return saved_rows[0]["provider_id"]
    return None


def _pick_default(default_id: Optional[str], chain: list[dict]) -> Optional[str]:
    """Settle the default at read time (L0004 §4.2): a stale/disabled selection falls
    back to the chain head without rewriting the stored value."""
    if not chain:
        return None
    if default_id is not None and any(p["id"] == default_id for p in chain):
        return default_id
    return chain[0]["id"]


def _save_scope(
    project_id: Optional[str],
    providers: list[dict],
    default_provider_id: Optional[str],
    default_provider_index: Optional[int],
    mode: Optional[str],
    updated_by: Optional[str],
) -> tuple[list[dict], Optional[str]]:
    """Full-replace + key-preserving merge (L0004 §2.2). Returns (saved rows, default id)."""
    existing = {r["provider_id"]: r for r in _ai_db.list_scope(project_id)}
    taken = set(existing)
    rows: list[dict] = []
    id_map: dict[str, str] = {}
    for index, p in enumerate(providers):
        old = existing.get(p.get("id")) if p.get("id") else None
        if old is not None:
            provider_id = old["provider_id"]
        else:
            provider_id = _issue_id(taken)
            if p.get("id"):
                id_map[p["id"]] = provider_id
        taken.add(provider_id)
        exec_type = p["exec_type"]
        rows.append({
            "provider_id": provider_id,
            "name": (p.get("name") or "").strip(),
            "exec_type": exec_type,
            "kind": p.get("kind"),
            "enabled": 1 if p.get("enabled", True) else 0,
            "cli_command": p.get("cli_command") if exec_type == "cli" else None,
            "api_model": p.get("api_model") if exec_type == "api" else None,
            "api_base_url": _resolve_base_url(p),
            "api_key": _merge_api_key(old, p.get("api_key")),
            "sort_order": index,
        })

    default_id = _resolve_default(default_provider_id, default_provider_index, rows, id_map)
    _ai_db.replace_scope(
        project_id, rows, set(existing), default_id, mode=mode, updated_by=updated_by,
    )
    return rows, default_id


def _scope_updated_at(project_id: Optional[str]) -> Optional[str]:
    rows = _ai_db.list_scope(project_id)
    stamps = [r["updated_at"] for r in rows if r.get("updated_at")]
    if project_id is not None:
        state = _ai_db.get_project_ai_state(project_id)
        if state and state.get("ai_mode") and state.get("updated_at"):
            stamps.append(state["updated_at"])
    return max(stamps) if stamps else None


def _require_project(project_id: str) -> None:
    if _projects_db.get_by_id(project_id) is None:
        raise LookupError(f"Project not found: {project_id}")


# ── Global scope (P0003 system endpoints) ────────────────────────────────────

def get_system_settings(include_catalog: bool = True) -> dict:
    rows = _ai_db.list_scope(None)
    result = {
        "ok": True,
        "providers": [_to_view(r) for r in rows],
        "default_provider_id": _ai_db.get_system_default_provider_id(),
        "updated_at": _scope_updated_at(None),
    }
    if include_catalog:
        result["catalog"] = get_catalog()
    return result


def save_system_settings(
    providers: Optional[list[dict]],
    default_provider_id: Optional[str],
    default_provider_index: Optional[int],
    updated_by: Optional[str] = None,
) -> dict:
    errors = validate_settings(providers, default_provider_id, default_provider_index, None)
    if errors:
        raise AiSettingsValidationError(errors)
    _save_scope(None, providers or [], default_provider_id, default_provider_index,
                mode=None, updated_by=updated_by)
    return get_system_settings(include_catalog=False)


# ── Project scope (tri-state, P0003 project endpoints) ───────────────────────

def _project_mode(state: Optional[dict]) -> str:
    """Row/column absence and unknown stored values read as inherit (L0004 §4.3)."""
    if state is None:
        return MODE_DEFAULT
    mode = state.get("ai_mode")
    if mode == "disabled" or mode == "custom":
        return mode
    return MODE_DEFAULT


def get_project_settings(project_id: str, include_catalog: bool = True) -> dict:
    _require_project(project_id)
    state = _ai_db.get_project_ai_state(project_id)
    mode = _project_mode(state)
    rows = _ai_db.list_scope(project_id)
    # In custom mode the (possibly empty) list is the state; otherwise expose the
    # preserved list when present so the UI can restore it on a mode switch (L0004 §3).
    if mode == "custom":
        providers = [_to_view(r) for r in rows]
    else:
        providers = [_to_view(r) for r in rows] if rows else None
    result = {
        "ok": True,
        "mode": mode,
        "providers": providers,
        "default_provider_id": state.get("ai_default_provider_id") if state else None,
        "updated_at": _scope_updated_at(project_id) if (rows or (state and state.get("ai_mode"))) else None,
        "effective": _effective_view(project_id),
    }
    if include_catalog:
        result["catalog"] = get_catalog()
    return result


def save_project_settings(
    project_id: str,
    mode: str,
    providers: Optional[list[dict]],
    default_provider_id: Optional[str],
    default_provider_index: Optional[int],
    updated_by: Optional[str] = None,
) -> dict:
    _require_project(project_id)
    errors = validate_settings(providers, default_provider_id, default_provider_index, mode)
    if errors:
        raise AiSettingsValidationError(errors)

    if mode == "custom":
        _save_scope(project_id, providers or [], default_provider_id,
                    default_provider_index, mode="custom", updated_by=updated_by)
    else:
        # Mode-only transition: never touch the provider rows (list preservation,
        # L0004 §3) and keep the stored project default for a later custom return.
        state = _ai_db.get_project_ai_state(project_id)
        _ai_db.upsert_project_ai_state(
            project_id, mode, state.get("ai_default_provider_id") if state else None,
        )
    return get_project_settings(project_id, include_catalog=False)


# ── Effective resolution (L0004 §2.4) ────────────────────────────────────────

def _effective_view(project_id: str) -> dict:
    state = _ai_db.get_project_ai_state(project_id)
    mode = _project_mode(state)

    if mode == "disabled":
        return {"source": "disabled", "providers": [], "default_provider_id": None,
                "registered_count": 0}

    if mode == "custom":
        rows = _ai_db.list_scope(project_id)
        source = "project"
        default_id = state.get("ai_default_provider_id") if state else None
    else:  # inherit
        rows = _ai_db.list_scope(None)
        source = "system"
        default_id = _ai_db.get_system_default_provider_id()

    chain = [_to_view(r) for r in rows if r["enabled"]]
    return {
        "source": source,
        "providers": chain,
        "default_provider_id": _pick_default(default_id, chain),
        # 0292 T0003: rows BEFORE the enabled filter. An empty chain has two very
        # different causes — "nothing was ever registered" (a fresh install that
        # skipped the provider seed) and "everything registered is switched off" —
        # and the caller cannot tell them apart from `providers` alone. Callers that
        # only need the chain can keep ignoring this key.
        "registered_count": len(rows),
    }


def resolve_effective(project_id: str) -> dict:
    """Effective chain for the follow-up execution module. providers order = fallback
    order; disabled rows are excluded; api_key never appears (use get_provider_secret)."""
    _require_project(project_id)
    return {"ok": True, **_effective_view(project_id)}


def get_provider_secret(project_id: Optional[str], provider_id: str) -> Optional[str]:
    """Raw api_key for internal execution use. NEVER route this into an HTTP response,
    and never log the value (length-only logging allowed; L0004 §2.3).

    Raises api_key_crypto.ApiKeyCryptoError when the stored value is encrypted but the
    master key cannot read it — fail-closed, never "no key configured" (0371 NR0007 §3).
    """
    return _ai_db.get_secret(project_id, provider_id)


# ── Per-document-type provider assignment (flowgate.default.0317 D0004) ─────────
#
# The continuous chain's hop-provider resolver: a project-scoped "document type -> provider"
# map the chain consults at each step boundary. An empty map reproduces today's
# single-provider behavior (every doc type resolves to the effective default), so the
# feature is additive and fully backward-compatible (D0004 §1, backward compatibility).

def _norm_doctype(code: Optional[str]) -> str:
    return (code or "").strip().upper()


def validate_doctype_assignments(
    project_id: str, assignments: Optional[list[dict]],
) -> list[dict]:
    """Collect ALL assignment errors (no early stop), mirroring validate_settings.

    A valid assignment names a non-empty doc_type (unique in the payload) and a provider_id
    that is in this project's EFFECTIVE enabled chain — the same set the run engine can
    actually launch, so the UI cannot pin a provider a hop could never use.
    """
    errors: list[dict] = []
    if not assignments:
        return errors
    if len(assignments) > DOCTYPE_ASSIGN_MAX:
        errors.append({"field": "assignments", "reason": "too_many"})

    enabled_ids = {p["id"] for p in (_effective_view(project_id).get("providers") or [])}
    seen: set[str] = set()
    for index, a in enumerate(assignments):
        doc_type = _norm_doctype(a.get("doc_type"))
        if doc_type == "":
            errors.append({"index": index, "field": "doc_type", "reason": "required"})
        elif len(doc_type) > DOCTYPE_CODE_MAX:
            errors.append({"index": index, "field": "doc_type", "reason": "too_long"})
        elif doc_type in seen:
            errors.append({"index": index, "field": "doc_type", "reason": "duplicate_doc_type"})
        else:
            seen.add(doc_type)

        provider_id = (a.get("provider_id") or "").strip()
        if provider_id == "":
            errors.append({"index": index, "field": "provider_id", "reason": "required"})
        elif provider_id not in enabled_ids:
            # Not in the effective enabled chain (disabled, deleted, or foreign scope).
            errors.append({"index": index, "field": "provider_id", "reason": "unknown_provider"})
    return errors


def _doctype_view(project_id: str) -> dict:
    """Serialized assignment map + the effective provider list the UI renders options from."""
    effective = _effective_view(project_id)
    return {
        "ok": True,
        "project": project_id,
        "assignments": [
            {"doc_type": r["doc_type"], "provider_id": r["provider_id"]}
            for r in _doctype_db.list_for_project(project_id)
        ],
        "providers": [
            {"id": p["id"], "name": p["name"], "exec_type": p["exec_type"], "kind": p["kind"]}
            for p in (effective.get("providers") or [])
        ],
        "default_provider_id": effective.get("default_provider_id"),
    }


def get_doctype_providers(project_id: str) -> dict:
    """Current assignment map for a project (404 via LookupError if the project is unknown)."""
    _require_project(project_id)
    return _doctype_view(project_id)


def save_doctype_providers(
    project_id: str,
    assignments: Optional[list[dict]],
    updated_by: Optional[str] = None,
) -> dict:
    """Full-replace the project's assignment map. Raises AiSettingsValidationError (422)
    on any invalid row; an empty/None list clears the map."""
    _require_project(project_id)
    errors = validate_doctype_assignments(project_id, assignments)
    if errors:
        raise AiSettingsValidationError(errors)
    rows = [
        {"doc_type": _norm_doctype(a.get("doc_type")), "provider_id": (a.get("provider_id") or "").strip()}
        for a in (assignments or [])
    ]
    _doctype_db.replace_for_project(project_id, rows)
    return _doctype_view(project_id)


def resolve_doctype_provider(project_id: str, doc_type: str) -> Optional[str]:
    """The hop provider decider (D0004 §2): the provider_id assigned to *doc_type*, or None
    when there is no rule OR the assigned provider is not in the effective enabled chain.

    None means "use the default/fallback" — so an unmapped type, a disabled assignment, or
    any lookup hiccup all degrade to today's behavior. Never raises: a resolution failure in
    the continuous hot path must not stall an otherwise-healthy chain (D0004 §3, fallback)."""
    normalized = _norm_doctype(doc_type)
    if not normalized:
        return None
    try:
        provider_id = _doctype_db.get_provider_for_type(project_id, normalized)
        if not provider_id:
            return None
        chain = _effective_view(project_id).get("providers") or []
        if any(p.get("id") == provider_id for p in chain):
            return provider_id
    except Exception:  # noqa: BLE001 — degrade to default, never break the hop
        return None
    return None
