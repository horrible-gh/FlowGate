"""Manual Git path policy (0066 D#1): visibility and ownership are independent."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from modules.flow_gate.services import git_service, path_exclusion_rules, tr2_file_policy
from .credentials import GitServiceError

_log = logging.getLogger(__name__)
MAX_REQUEST_PATHS = 4096
MAX_PATH_BYTES = 4096


@dataclass(frozen=True)
class Ownership:
    paths: frozenset[str] | None
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.paths is not None

    def view(self) -> dict:
        return {"available": self.available, "reason": self.reason}

    def require(self) -> None:
        if not self.available:
            code = ("tr_history_recovery_required" if self.reason == "tr_history_recovery_required"
                    else "ownership_unavailable")
            raise GitServiceError(409, code, "Source ownership could not be verified",
                                  details={"reason": self.reason})


def read_ownership(group_id: str) -> Ownership:
    try:
        return Ownership(frozenset(tr2_file_policy.managed_paths(group_id)))
    except tr2_file_policy.Tr2OwnershipInvariantError:
        reason = "ownership_invariant"
    except tr2_file_policy.Tr2FilePolicyError as exc:
        reason = ("tr_history_recovery_required"
                  if exc.code == tr2_file_policy.TR_HISTORY_RECOVERY_REQUIRED
                  else "ownership_read_failed")
    except Exception:
        reason = "ownership_read_failed"
    # Never expose ledger details or turn an unreadable ledger into an empty set.
    _log.warning("Manual Git ownership unavailable for %s: %s", group_id, reason)
    return Ownership(None, reason)


def hidden(path: str) -> bool:
    return (not path_exclusion_rules.is_excluded_path(path)
            and git_service._is_hidden_source_path(path))


def normalize_paths(paths: list[str] | None, all_paths: bool) -> list[str]:
    if not isinstance(all_paths, bool) or (all_paths and paths is not None):
        raise GitServiceError(422, "invalid_request", "Choose paths or all")
    if not all_paths and (not isinstance(paths, list) or not paths):
        raise GitServiceError(422, "invalid_request", "A nonempty file list is required")
    if paths is not None and len(paths) > MAX_REQUEST_PATHS:
        raise GitServiceError(422, "invalid_request", "Too many file paths",
                              details={"reason": "too_many_paths"})
    result = []
    seen = set()
    for path in paths or []:
        if not isinstance(path, str):
            raise GitServiceError(422, "invalid_request", "Invalid file path",
                                  details={"reason": "invalid_path"})
        if len(path) > MAX_PATH_BYTES or len(path.encode("utf-8", errors="surrogatepass")) > MAX_PATH_BYTES:
            raise GitServiceError(422, "invalid_request", "File path is too long",
                                  details={"reason": "path_too_long"})
        value = path.replace("\\", "/")
        if (not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value)
                or "\0" in value or any(p in ("", ".", "..") for p in value.split("/"))):
            raise GitServiceError(422, "invalid_request", "Invalid file path",
                                  details={"reason": "invalid_path"})
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def guard_selected_visibility(paths: list[str], *, unstage: bool = False) -> None:
    # Before any existence/ownership lookup: identical refusal for absent secrets.
    if any(hidden(path) for path in paths):
        raise GitServiceError(409, "path_protected", "Protected file path",
                              details={"reason": "hidden_use_unstage_all" if unstage else "hidden"})


def classify(path: str, *, in_head: bool, staged: str | None, unstaged: str | None,
             untracked: bool, ownership: Ownership) -> dict:
    secret = hidden(path)
    excluded = path_exclusion_rules.is_excluded_path(path)
    kind = ("unverified" if not ownership.available else
            "managed" if path in ownership.paths else "excluded" if excluded else "normal")
    unsupported = ("unmerged" if staged == "U" else
                   "type_change" if "T" in (staged, unstaged) else None)
    protected = ("ownership_unavailable" if kind == "unverified" else
                 "managed" if kind == "managed" else
                 "excluded_new" if kind == "excluded" and not in_head else None)
    mutable = not secret and protected is None and unsupported is None
    return {
        "hidden": secret,
        "visible": not secret and not (kind == "excluded" and not in_head and untracked and not staged),
        "class": kind, "protected_reason": protected, "unsupported": unsupported,
        "can_stage": mutable,
        "can_unstage": bool(staged) and not secret,
        "can_revert": mutable and in_head,
        "can_remove": mutable and not in_head,
    }


def visible_paths(paths) -> list[str]:
    return [path for path in paths if not hidden(path)]


def redact_diagnostic(value: str, paths) -> str:
    for path in sorted((p for p in paths if hidden(p)), key=len, reverse=True):
        value = value.replace(path, "<hidden>").replace(path.replace("/", "\\"), "<hidden>")
    return value


def public_details(value, paths=()):
    """Filter nested upstream metadata before including it in a manual response."""
    if isinstance(value, dict):
        return {public_details(k, paths): public_details(v, paths) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_details(v, paths) for v in value]
    if isinstance(value, str):
        value = redact_diagnostic(value, paths)
        return re.sub(r"""[^\s"'<>[\]{},;]+""",
                      lambda match: "<hidden>" if hidden(match.group().replace("\\", "/"))
                      else match.group(), value)
    return value
