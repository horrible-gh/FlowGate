"""Authoritative TR2 gate admission, shared by read and approval."""
from __future__ import annotations

import hashlib
import json
import re

from modules.flow_gate.db import project_test_commands as registry
from modules.flow_gate.documents import tr2_service as tr2
from modules.flow_gate.services import test_command_service as commands

MAX_CANDIDATES = 3
_SHELL_COMPLEX = re.compile(r"&&|\|\||[|><;`&]|\$\(")


def classify_gate_commands(project_id: str, raw_commands: list[str]) -> dict:
    """Preserve gate order and distinguish absent rows from suppressed tombstones."""
    items = []
    issues = []
    candidates = set()
    host = commands.current_os()
    for index, raw in enumerate(raw_commands):
        command = commands.normalize_command(raw)
        row = registry.find_by_command(project_id, command)
        # The registry identity is case-sensitive even on installations with a
        # case-insensitive database collation.
        if row is not None and commands.normalize_command(row["command"]) != command:
            row = None
        state = ("candidate" if row is None else
                 "suppressed" if row.get("status") == "suppressed" else "registered")
        item = {"index": index, "command": command, "state": state,
                "registry_row_id": row["id"] if row else None,
                "origin": row.get("origin") if row else None,
                "verified_os": row.get("verified_os") if row else None,
                "shell_complex": bool(_SHELL_COMPLEX.search(command))}
        items.append(item)
        loc = f"edit_spec.gate.commands[{index}]"
        if state == "suppressed":
            issues.append(("tr2_validation_command_unapproved", loc, "suppressed"))
        elif state == "candidate":
            candidates.add(command)
            if any(char in raw for char in "\x00\r\n"):
                issues.append(("tr2_spec_invalid", loc, "candidate_control_character"))
        elif row.get("verified_os") and row["verified_os"] != host:
            issues.append(("tr2_validation_command_os_mismatch", loc, "os_mismatch"))
        elif row.get("origin") in {"auto", "tr2"} and not row.get("verified_os"):
            issues.append(("tr2_validation_command_unverified", loc, "unverified"))
    if len(candidates) > MAX_CANDIDATES:
        issues.append(("tr2_spec_invalid", "edit_spec.gate.commands", "candidate_limit"))
    material = [{key: item[key] for key in
                 ("index", "command", "state", "registry_row_id", "origin", "verified_os")}
                for item in items]
    canonical = json.dumps(material, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {"fingerprint": fingerprint, "candidate_count": len(candidates),
            "all_candidate": bool(items) and all(i["state"] == "candidate" for i in items),
            "commands": items, "issues": issues}


def require_admitted(admission: dict) -> list[dict]:
    if admission["issues"]:
        code, loc, reason = admission["issues"][0]
        raise tr2.Tr2ValidationError(code, loc, {"reason": reason})
    return [{**item, "admission_state": item["state"]} for item in admission["commands"]]


def public_admission(admission: dict) -> dict:
    return {key: admission[key] for key in
            ("fingerprint", "candidate_count", "all_candidate", "commands")}
