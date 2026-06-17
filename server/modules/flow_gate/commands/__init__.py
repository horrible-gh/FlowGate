"""Command substitution engine."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from modules.flow_gate.db.env_vars import get_all_as_map

_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _resolve_dynamic(name: str) -> str | None:
    """Handler for the __ prefix. Current implementation: only supports __now__."""
    if name == "__now__":
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    return None


def resolve_template(template: str) -> dict:
    """
    Replace ${var_name} patterns in the template.

    Returns: {resolved: str, unresolved: list[str]}
    Priority: user > system. '__' prefix is handled by a dynamic handler.
    """
    var_map = get_all_as_map()
    unresolved: list[str] = []

    def replacer(match: re.Match) -> str:
        name = match.group(1)
        # Dynamic handler (__ prefix)
        if name.startswith("__"):
            val = _resolve_dynamic(name)
            if val is not None:
                return val
            unresolved.append(name)
            return match.group(0)
        # Regular variables
        if name in var_map:
            return var_map[name]
        unresolved.append(name)
        return match.group(0)

    resolved = _VAR_PATTERN.sub(replacer, template)
    return {"resolved": resolved, "unresolved": list(dict.fromkeys(unresolved))}
