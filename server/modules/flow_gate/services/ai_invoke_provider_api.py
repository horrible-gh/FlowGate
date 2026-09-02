# ────────────────────── ai_invoke_service part — provider API transport (HTTP) ──────────────────────
#
# Not imported on its own in production: ai_invoke_service._load_parts() executes this
# file in THAT module's globals() (flowgate.default.0501 T4 — split out of
# ai_invoke_part2_worker.py, itself flowgate.default.0497 T0009's part 2 of 3). The lines
# below were carried over verbatim from ai_invoke_part2_worker.py, nothing was rewritten.
# See the file-split note in ai_invoke_service.py's module docstring.
#
# Holds: HTTP/API transport to OpenAI/Anthropic-compatible endpoints — the raw model call
# (_call_openai / _call_anthropic / _http_post_json), the API-agent prompt shaping and
# provider-shape helpers (_api_system_prompt / _api_help_prompt / _is_glm_openai_provider),
# and the self-reachable API base address the server dials itself at
# (_resolve_agent_api_base / _sanitize_diagnostic_base / _resolve_transport_api_base — pure
# config resolution, cached on `run` but never reading chain/review/lease state).
#
# Deliberately NOT here: FlowGate-tool dispatch (register/inbox/question/workflow-decide/
# conversation — provider-neutral, both API and CLI providers can trigger these) and the API
# agent's outer turn loop (_api_execute), which mixes that dispatch into its turn-taking
# closely enough that splitting it further risked behavior change (T4 §22: when ambiguous,
# move less). Both stay in ai_invoke_worker.py, which calls the
# functions in THIS module for the raw HTTP leg only.
#
# FORBIDDEN here (T4 import-dependency guard): importing ai_invoke_part3_chain, review/
# rework/chain-progression logic, group lease DB access, or the runtime registry owner
# (ai_invoke_runtime.py's _runs/_runs_lock ownership). This module must not know a *run*
# exists past "conversation in, model reply out".

from __future__ import annotations

import json
import urllib.request
from typing import Optional

# Imported directly (tooling that walks the package) rather than executed by
# _load_parts(): the earlier parts' names are missing, so take them from the
# assembled module. Under _load_parts() they are already here and this is a no-op.
if "RUN_TIMEOUT_BASE_SEC" not in globals():
    from modules.flow_gate.services import ai_invoke_service as _assembled
    globals().update({k: v for k, v in vars(_assembled).items()
                      if not k.startswith("__")})


def _resolve_agent_api_base(operator_api_base: str) -> str:
    """Compute the address this server can reach itself at.

    Shared by external CLI process launch and, since 0505 T0008, the API provider's
    server-mediated self-HTTP (`_resolve_transport_api_base`) -- both ask the same
    question, "what address can this server use to reach itself?", and now get the
    same answer. The browser/operator origin remains in the stored run and in the
    help text/prompts a person reads. A configured agent origin wins; otherwise the
    operator scheme and explicit port are retained while the host becomes loopback.
    When the operator origin has no explicit port, the trusted local
    ``FLOWGATE_PORT`` is used.
    """
    from urllib.parse import urlsplit, urlunsplit

    if not operator_api_base:
        return operator_api_base
    parts = urlsplit(operator_api_base)
    try:
        operator_port = parts.port
    except ValueError as exc:
        raise ValueError(f"Invalid operator API base port: {exc}") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(
            "operator API base must be an absolute http(s) URL with a hostname"
        )

    from config import settings as _settings

    configured = getattr(_settings, "FLOWGATE_AGENT_API_BASE", None)
    if configured is not None:
        setting = str(configured).strip()
        if not setting:
            raise ValueError(
                "FLOWGATE_AGENT_API_BASE must not be empty or whitespace"
            )
        agent = urlsplit(setting)
        try:
            agent_port = agent.port
        except ValueError as exc:
            raise ValueError(f"Invalid FLOWGATE_AGENT_API_BASE port: {exc}") from exc
        if (
            agent.scheme not in ("http", "https")
            or not agent.hostname
            or agent.username is not None
            or agent.password is not None
            or agent.path not in ("", "/")
            or agent.query
            or agent.fragment
        ):
            raise ValueError(
                "FLOWGATE_AGENT_API_BASE must be an http(s) origin "
                "(scheme://host[:port])"
            )
        netloc = agent.hostname
        if ":" in netloc:
            netloc = f"[{netloc}]"
        if agent_port is not None:
            netloc += f":{agent_port}"
        path = parts.path.rstrip("/")
        return urlunsplit((agent.scheme, netloc, path, parts.query, ""))

    port = operator_port
    if port is None:
        port = int(_settings.FLOWGATE_PORT)
        if not 1 <= port <= 65535:
            raise ValueError("FLOWGATE_PORT must be between 1 and 65535")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, f"127.0.0.1:{port}", path, parts.query, ""))


def _sanitize_diagnostic_base(url: Optional[str]) -> Optional[str]:
    """Strip a live `api_base_url` down to a safe diagnostic snapshot (DB0005 2, 3.3, 5-5).

    `operator_api_base`/`transport_api_base` store what this returns, never the raw
    value: run["api_base_url"] is browser/operator-supplied and unvalidated end to end
    (ai_invoke_routes._operator_facing_api_base -> token_routes._build_api_base ->
    str(request.base_url) -> ai_invoke_service.start_run(api_base_url=...)), and
    _resolve_agent_api_base's userinfo check only fires on its FLOWGATE_AGENT_API_BASE
    branch -- CLI providers, never this API path. Anything that fails to parse as an
    absolute http(s) URL, has no hostname, or carries an unparsable port becomes None
    outright rather than a partially-sanitized value: a NULL diagnostic column is
    honest about "could not be read safely"; a best-effort fragment is not.
    """
    if not url:
        return None
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = parts.hostname
    if ":" in netloc:
        netloc = f"[{netloc}]"
    if port is not None:
        netloc += f":{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _resolve_transport_api_base(run: dict) -> str:
    """The address this hop's server-mediated self-HTTP should dial (0505 T0008).

    All six mediated self-HTTP call sites (conversation_context, conversation_turn_
    register, api_bound_request, inbox_register, resolve_conflict, workflow_decide)
    used to send `run["api_base_url"]` -- the operator/browser origin -- straight
    back to themselves. That is fine when operator and agent origins coincide, but
    wrong in exactly the topology 0472 B0001 hit for the CLI path: a public/proxy
    origin that this server cannot dial as itself. `_resolve_agent_api_base` already
    solved this for CLI launch; this wraps it for the API provider's self-HTTP and
    caches the result on `run` so all six sites agree within one hop.
    """
    cached = run.get("_transport_api_base_resolved")
    if cached:
        return cached
    operator_base = run.get("api_base_url") or ""
    try:
        resolved = _resolve_agent_api_base(operator_base)
    except ValueError:
        logger.warning(
            "ai-invoke %s: transport base resolution failed for operator base %r, "
            "falling back to operator base for self-HTTP",
            run.get("run_id"), operator_base,
        )
        resolved = operator_base
    run["_transport_api_base_resolved"] = resolved
    return resolved


# ── API adapter: minimal agent loop (L0006 §2.4) ─────────────────────────────

def _api_system_prompt() -> str:
    """Non-negotiable API-agent contract shared by OpenAI-compatible providers."""
    return (
        "You are a FlowGate API agent. Use the supplied tools to perform the bound work. "
        "A natural-language claim of completion never registers, replies, decides, or completes "
        "the work: call the required tool with its complete payload. Only use exposed tools and "
        "their declared JSON schemas."
    )


def _api_help_prompt(prompt: str) -> str:
    """Match API-provider guidance to mediated tools; CLI mentions remain unchanged."""
    lines = [line for line in prompt.splitlines()
             if not ("GET " in line and "/help" in line)]
    guidance = (
        "Use the `read_help` tool for personalized FlowGate help: empty input returns "
        "the index, item returns one item, and item plus child returns one child."
    )
    return guidance + "\n\n" + "\n".join(lines)


def _is_glm_openai_provider(provider: dict) -> bool:
    """Identify GLM even when its OpenAI-compatible endpoint is configured as custom."""
    kind = str(provider.get("kind") or "").lower()
    base_url = str(provider.get("api_base_url") or "").lower()
    model = str(provider.get("api_model") or "").lower()
    return (
        kind in {"glm", "zhipu", "zai"}
        or model.startswith("glm-")
        or "bigmodel.cn" in base_url
        or "z.ai" in base_url
    )


def _http_post_json(url: str, headers: dict, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _call_anthropic(
    base_url: str, model: str, key: str, conversation: list[dict], timeout: float,
    tool_name: str, tool_desc: str, tool_schema: dict, force_tool: bool = False,
) -> tuple[Optional[str], Optional[dict], dict]:
    multi = isinstance(tool_name, list)
    specs = tool_name if multi else [{"name": tool_name, "description": tool_desc, "schema": tool_schema}]
    data = _http_post_json(
        f"{base_url}/v1/messages",
        {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
        {
            "model": model,
            "max_tokens": API_MAX_TOKENS,
            "messages": conversation,
            "tools": [{"name": spec["name"], "description": spec["description"], "input_schema": spec["schema"]} for spec in specs],
            **({"tool_choice": ({"type": "any"} if multi else {"type": "tool", "name": specs[0]["name"]})} if force_tool else {}),
        },
        timeout,
    )
    content = data.get("content") or []
    text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
    tool_calls = []
    exposed = {spec["name"] for spec in specs}
    for block in content:
        if block.get("type") == "tool_use":
            name = block.get("name")
            tool_calls.append({"id": block.get("id"), "name": name, "input": block.get("input")})
    assistant_msg = {"role": "assistant", "content": content}
    return ("\n".join(p for p in text_parts if p) or None), tool_calls, assistant_msg


def _call_openai(
    base_url: str, model: str, key: str, conversation: list[dict], timeout: float,
    tool_name: str, tool_desc: str, tool_schema: dict, force_tool: bool = False,
) -> tuple[Optional[str], Optional[dict], dict]:
    multi = isinstance(tool_name, list)
    specs = tool_name if multi else [{"name": tool_name, "description": tool_desc, "schema": tool_schema}]
    data = _http_post_json(
        f"{base_url.rstrip('/')}/chat/completions",
        {"Authorization": f"Bearer {key}"},
        {
            "model": model,
            "messages": conversation,
            "tools": [{"type": "function", "function": {"name": spec["name"], "description": spec["description"], "parameters": spec["schema"]}} for spec in specs],
            **({"tool_choice": ("required" if multi else {"type": "function", "function": {"name": specs[0]["name"]}})} if force_tool else {}),
        },
        timeout,
    )
    choices = data.get("choices") or []
    message = (choices[0].get("message") if choices else None) or {}
    tool_calls = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments", {})
        if isinstance(raw_args, dict):
            args = raw_args
        elif isinstance(raw_args, str):
            try:
                args = json.loads(raw_args or "{}")
            except (TypeError, ValueError):
                args = None
        else:
            args = None
        tool_calls.append({"id": tc.get("id"), "name": fn.get("name"), "input": args})
    # Keep every received call, including unknown names and malformed inputs, so the
    # dispatcher can emit a call-id-specific error instead of misclassifying it as a miss.
    return message.get("content"), tool_calls, message
