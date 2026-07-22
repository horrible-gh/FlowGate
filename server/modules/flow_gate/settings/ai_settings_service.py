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
"""
from __future__ import annotations

import secrets
from typing import Optional

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
    "openai": "https://api.openai.com",
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
# carried neither a model nor `--dangerously-skip-permissions` — without the latter the CLI
# stops on a permission prompt nobody can answer (the invoke contract forbids args and only
# feeds stdin), which reads as "the AI is slow" until the run times out. The codex row was
# missing `--skip-git-repo-check`, which `codex exec` requires whenever cwd is not a git
# repo; see CODEX_SKIP_GIT_FLAG below.
_CLI_COMMAND_EXAMPLES: dict[str, dict[str, str]] = {
    "claude": {
        "posix": "claude --model claude-opus-4-8 --dangerously-skip-permissions -p -",
        "nt": "claude --model claude-opus-4-8 --dangerously-skip-permissions -p -",
    },
    "codex": {
        "posix": (
            "codex --ask-for-approval never --sandbox workspace-write exec "
            "--skip-git-repo-check "
            "-c sandbox_workspace_write.network_access=true --json --model gpt-5.6-sol -"
        ),
        "nt": (
            "codex --ask-for-approval never --sandbox workspace-write exec "
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
    `fast_fail` ("즉시 종료") and burns as a provider failure (0295 NR0003 §2). FlowGate has
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
    """
    return {
        "exec_types": list(EXEC_TYPES),
        "kinds": {"cli": list(KINDS_CLI), "api": list(KINDS_API)},
        "host_os": _tcs.current_os(),
        "host_shell": _tcs.current_shell(),
        "cli_examples": {k: dict(v) for k, v in _CLI_COMMAND_EXAMPLES.items()},
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


def _merge_api_key(old_value: Optional[str], incoming: Optional[str]) -> Optional[str]:
    """absent/null = keep, "" = delete, value = replace (L0004 §2.3)."""
    if incoming is None:
        return old_value
    if incoming == "":
        return None
    return incoming


def _to_view(row: dict) -> dict:
    api_key = row.get("api_key")
    return {
        "id": row["provider_id"],
        "name": row["name"],
        "exec_type": row["exec_type"],
        "kind": row["kind"],
        "enabled": bool(row["enabled"]),
        "cli_command": row.get("cli_command"),
        "api_base_url": row.get("api_base_url"),
        "api_model": row.get("api_model"),
        "api_key_set": api_key is not None,
        "api_key_hint": api_key[-HINT_LEN:] if api_key else None,
    }


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
            "api_key": _merge_api_key(old.get("api_key") if old else None, p.get("api_key")),
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
        return {"source": "disabled", "providers": [], "default_provider_id": None}

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
    }


def resolve_effective(project_id: str) -> dict:
    """Effective chain for the follow-up execution module. providers order = fallback
    order; disabled rows are excluded; api_key never appears (use get_provider_secret)."""
    _require_project(project_id)
    return {"ok": True, **_effective_view(project_id)}


def get_provider_secret(project_id: Optional[str], provider_id: str) -> Optional[str]:
    """Raw api_key for internal execution use. NEVER route this into an HTTP response,
    and never log the value (length-only logging allowed; L0004 §2.3)."""
    return _ai_db.get_secret(project_id, provider_id)
