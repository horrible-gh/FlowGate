"""API chat descriptors and CLI discovery for the same scoped read boundary."""
import json
from flowgate.card_document_edit import call_ledger, chat_authz, chat_errors
from flowgate.card_document_edit.chat_adapter import tool_result_limit, success_body, serialized_length
from . import authz, routes, service

TOOLS = {"read_flow_canvas": "canvas", "read_flow_current_run": "current"}


def _result(handler):
    response = routes.response(None, handler)
    return response.status_code, json.loads(response.body)


def call(run, ctx, name):
    def handle():
        current, token = chat_authz.authorize_chat_run(chat_authz.API, None, run)
        with call_ledger.dispatching(current, ctx) as record:
            if record.tool_name != name:
                raise chat_errors.call_identity_unavailable()
            call_ledger.require_tool_call_id(record)
            inp = record.input_copy()
            allowed = {"flow_id", "view"} if TOOLS[name] == "canvas" else {"flow_id"}
            if not isinstance(inp, dict) or set(inp) - allowed:
                authz.refuse("invalid_request", 422)
            body = success_body(service.read(token, inp.get("flow_id"), TOOLS[name], inp.get("view"), current))
            if serialized_length(body) > tool_result_limit():
                authz.refuse("result_too_large", 413)
            return body
    return _result(handle)


def stub(run, _input):
    def refuse():
        chat_authz.authorize_chat_run(chat_authz.API, None, run)
        raise chat_errors.call_identity_unavailable()
    return _result(refuse)


def cli_mention(api_base, _raw_token):
    base = api_base.rstrip("/")
    base = base[:-len("/api/v1")] if base.endswith("/api/v1") else base
    return ("[Bound Graph state read]\n"
            f"GET {base}/api/v2/ai/flows/{{flow_id}}/canvas\n"
            f"GET {base}/api/v2/ai/flows/{{flow_id}}/runs/current\n"
            "Use this run's existing worker/chat Bearer, never a user JWT. Only your bound group is readable; "
            "a card worker sees only its active node/attempt. Chat canvas defaults to latest; view=revision "
            "shows the execution definition. No write, execution, history or external-group access is granted. "
            "These reads report stored state and never reconcile or start work. Never log the Bearer.")


def descriptors():
    out = []
    for name, resource in TOOLS.items():
        props = {"flow_id": {"type": "string", "minLength": 1}}
        if resource == "canvas":
            props["view"] = {"type": "string", "enum": ["revision", "latest"]}
        tool = {"name": name, "description": "Read stored " + resource + " state of this chat's bound group only. "
                "No writes or execution; no other groups. A 413 requires the CLI read path.",
                "schema": {"type": "object", "properties": props, "required": ["flow_id"], "additionalProperties": False},
                "handler": stub, "own_validation": True, "completion": False,
                "call_handler": lambda run, ctx, name=name: call(run, ctx, name)}
        if resource == "canvas":
            tool["cli_mention"] = cli_mention
        out.append(tool)
    return out
