"""API/CLI recovery transports use the same service and caller boundaries."""
from . import authz, recovery
from modules.flow_gate.graph_workflow.services.errors import GraphApiError

GUIDE = ("Recovery: read_flow_recovery_basis (omit node_key for a new run; give it for a card retry) "
         "is read-only and reports new_attempt, existing_attempt and group_head separately. "
         "Unknown is not main/latest. An explicit retry uses predecessor results, else the inspected group HEAD; "
         "ordinary command windows and existing attempts keep their pinned base. "
         "For retry_flow_card / restart_flow_run send an unchanged context as expected_context and a fresh "
         "request_id. Reuse BOTH on transport retry. A conflicting request_id or changed context is refused. "
         "Only report actual_basis as materialized; selected_basis is the pinned intended input. "
         "Restart may return pending: stop accepted is not stop complete, and there is no new run yet. "
         "Explicitly repeat the same request to continue; no automatic wake or restart is scheduled. "
         "A draft must be confirmed and instructions written before restart. Holds, gate reservations and "
         "joins requiring a merge are not bypassed. These writes require edit mode and Flow write permission.")

FLOW = {"type": "string", "minLength": 1}
BASIS_SCHEMA = {"type": "object", "properties": {"flow_id": FLOW, "node_key": FLOW},
                "additionalProperties": False}
RESTART_SCHEMA = {"type": "object", "properties": {"flow_id": FLOW, "request_id":
                  {"type": "string", "minLength": 1, "maxLength": 128},
                  "expected_context": {"type": "object"}},
                  "required": ["request_id", "expected_context"], "additionalProperties": False}
RETRY_SCHEMA = {**RESTART_SCHEMA, "properties": {**RESTART_SCHEMA["properties"], "node_key": FLOW},
                "required": ["node_key", "request_id", "expected_context"]}


def call(run, data, operation):
    from .tools import _result
    def handle():
        if not isinstance(data, dict):
            raise GraphApiError(422, "invalid_request")
        body = dict(data)
        flow_id = body.pop("flow_id", None) or (run or {}).get("group_id")
        caller = authz.caller_from_run(run, flow_id, write=operation != "read")
        node_key = body.pop("node_key", None) if operation != "restart" else None
        if node_key is not None and (not isinstance(node_key, str) or not node_key):
            raise GraphApiError(422, "invalid_request", {"field": "node_key"})
        if operation == "read":
            if body:
                raise GraphApiError(422, "invalid_request", {"reason": "unknown_field"})
            return recovery.view(caller, node_key)
        if operation == "retry" and not node_key:
            raise GraphApiError(422, "invalid_request", {"field": "node_key"})
        return recovery.change(caller, body, node_key)
    return _result(handle)


def basis_call(run, data):
    return call(run, data, "read")


def retry_call(run, data):
    return call(run, data, "retry")


def restart_call(run, data):
    return call(run, data, "restart")


def cli_mention(api_base, raw_token):
    base = str(api_base or "").rstrip("/")
    base = base[:-len("/api/v1")] if base.endswith("/api/v1") else base
    return "\n".join(["## Explicit Flow recovery", GUIDE,
        f"GET {base}/api/v2/chat/flows/{{flow_id}}/recovery-basis",
        f"GET {base}/api/v2/chat/flows/{{flow_id}}/nodes/{{node_key}}/basis",
        f"POST {base}/api/v2/chat/flows/{{flow_id}}/nodes/{{node_key}}/retry",
        f"POST {base}/api/v2/chat/flows/{{flow_id}}/restart",
        f"Authorization: Bearer {raw_token}",
        'POST body: {"request_id": "<unique id>", "expected_context": <context from the matching GET>}'])


def descriptors():
    return [
        {"name": "read_flow_recovery_basis", "description": GUIDE, "schema": BASIS_SCHEMA,
         "handler": basis_call, "cli_mention": cli_mention, "prompt_hint": GUIDE},
        {"name": "retry_flow_card", "description": "Create a new attempt of one waiting/held-result work card. " + GUIDE,
         "schema": RETRY_SCHEMA, "handler": retry_call},
        {"name": "restart_flow_run", "description": "Stop, confirm actual termination, then create a NEW run ID. " + GUIDE,
         "schema": RESTART_SCHEMA, "handler": restart_call},
    ]
