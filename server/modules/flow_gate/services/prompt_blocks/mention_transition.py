"""Bounded deployment probe. No tokens or help bodies are retained in evidence."""
from datetime import datetime, timezone
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..help import loader
from .mention import MODES, unresolved_context
from .matrix import transition_leaves


def verify_transition(mode, token_mode, base_url, raw_token, requirements, *, timeout=3, budget=50):
    """Check all supplied mode leaves, including conditional leaves.

    requirements maps leaf -> nonempty expected text fragments for migrated rules.
    The calling token issuer must supply its authoritative token_mode. Success is
    evidence only: deployment/review must persist it and explicitly enable the mode.
    """
    record = {"mode": mode, "token_mode": token_mode,
              "checked_at": datetime.now(timezone.utc).isoformat(), "passed": False, "items": {}}
    if mode not in MODES or mode != token_mode or not requirements:
        record["reason"] = "mode_mismatch_or_empty_requirements"
        return record
    if not set(transition_leaves(mode)).issubset(requirements):
        record["reason"] = "incomplete_mode_coverage"
        return record
    deadline = time.monotonic() + min(max(budget, 1), 55)
    for name, fragments in requirements.items():
        row = {"visible": False, "response": False, "context": False}
        record["items"][name] = row
        if not re.fullmatch(r"[A-Za-z_][\w.-]*(?:/[A-Za-z_][\w.-]*)?", name) or not isinstance(fragments, (list, tuple)) or not fragments or not all(isinstance(part, str) and part for part in fragments):
            row["reason"] = "invalid_requirement"
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            row["reason"] = "budget_exhausted"
            continue
        request = Request(base_url.rstrip("/") + "/help/items/" + name,
                          headers={"Authorization": "Bearer " + raw_token})
        try:
            with urlopen(request, timeout=min(timeout, remaining)) as response:
                payload = json.load(response)
            row["visible"] = payload.get("ok") is True
            response_body = payload.get("content") or payload.get("children")
            values = tuple(loader.strings(response_body))
            row["response"] = bool(response_body) and all(any(fragment in value for value in values) for fragment in fragments)
            row["context"] = not unresolved_context(name, response_body)
        except HTTPError as exc:
            row["reason"] = "http_" + str(exc.code)
        except (URLError, TimeoutError, ValueError, OSError):
            row["reason"] = "request_failed"
    record["passed"] = all(all(row.get(k) is True for k in ("visible", "response", "context"))
                           for row in record["items"].values())
    return record
