"""Provider capability SSOT for code-modification safety gates."""
from __future__ import annotations

CAPABILITY_KEYS = ("source_read", "source_write", "shell", "test", "flowgate_mutation")
CODE_MODIFICATION_TYPES = frozenset(("T", "TR"))
REQUIRED_CODE_MODIFICATION_CAPABILITIES = ("source_write", "test")


def fail_closed_capabilities() -> dict[str, bool]:
    return {key: False for key in CAPABILITY_KEYS}


def provider_capabilities(provider: object) -> dict[str, bool]:
    """Return the complete capability contract; unknown or disabled is fail-closed."""
    if not isinstance(provider, dict) or not provider.get("id") or provider.get("enabled") is False:
        return fail_closed_capabilities()
    exec_type = str(provider.get("exec_type") or "").lower()
    if exec_type == "cli":
        return {key: True for key in CAPABILITY_KEYS}
    if exec_type == "api":
        try:
            from modules.flow_gate.services import api_server_tools
            mediated_ready = api_server_tools.ready()
        except Exception:
            mediated_ready = False
        return {"source_read": mediated_ready, "source_write": mediated_ready, "shell": False,
                "test": mediated_ready, "flowgate_mutation": True}
    return fail_closed_capabilities()


def missing_capabilities(step_type: object, provider: object) -> list[str]:
    """Stable missing list for the only code-modification gate (T/TR)."""
    if str(step_type or "").upper() not in CODE_MODIFICATION_TYPES:
        return []
    capabilities = provider_capabilities(provider)
    return [key for key in REQUIRED_CODE_MODIFICATION_CAPABILITIES if not capabilities[key]]


def capability_finding(step_key: object, step_type: object, provider: object) -> dict | None:
    """Return the public, server-authoritative finding, or ``None`` when allowed."""
    missing = missing_capabilities(step_type, provider)
    if not missing:
        return None
    row = provider if isinstance(provider, dict) else {}
    return {
        "step_key": str(step_key or ""),
        "step_type": str(step_type or "").upper(),
        "provider_id": row.get("id"),
        "provider_name": row.get("name") or row.get("id"),
        "missing_capabilities": missing,
    }
