"""Semantic projections of existing contracts; never rewrite contract prose."""
from .content import _CONTENT_SUPPLIERS, _question_policy_block
from .tree import AREA_LEAVES


def leaf_content(area, leaf, ctx):
    source = AREA_LEAVES[area][leaf]
    if leaf.startswith("check_"):
        leaf = leaf.replace("check_", "self_check_", 1)
    if area == "attachments" and leaf == "request_rules":
        return _CONTENT_SUPPLIERS["document_attachments"](ctx).get("chat", {})
    if area == "cards" and leaf != "questions":
        from flowgate.chat_control import tools
        if leaf == "state":
            base = ctx["base_url"].removesuffix("/api/v1")
            return {"method": "GET", "urls": [base + "/api/v2/ai/flows/{flow_id}/canvas", base + "/api/v2/ai/flows/{flow_id}/runs/current"], "note": "Use the bound worker/chat Bearer. Read-only stored state. Chat returns applied chat_mode. Failed edit_once yields read_only. Say flow_name and nodes[].card_name (F77(name), F77-12(card title)); use flow_id/node_key/doc_id in API calls."}
        if leaf == "topology":
            return {"body": tools.TOPOLOGY_GUIDE}
        body = getattr(tools, "contract_" + leaf)(ctx["base_url"], "<YOUR_CHAT_TOKEN>")
        if leaf == "place":
            body = body.replace(tools.TOPOLOGY_GUIDE, "See cards/topology for explicit fork/join.")
        if leaf == "roles":
            body = body.split("\nflow_id is this chat")[0]
        if leaf == "run":
            body += "\nflow_id is this chat" + tools.contract_roles(ctx["base_url"], "<YOUR_CHAT_TOKEN>").split("\nflow_id is this chat", 1)[1]
        return {"body": body}
    data = _CONTENT_SUPPLIERS[source](ctx)
    if area == "documents" and source == "chat_documents":
        return {"sections": [data["sections"][0 if leaf == "edit" else 1]]}
    if area == "submit" and source == "tr_self_check":
        if leaf == "self_check":
            return {key: data[key] for key in ("role", "verification", "stage", "no_fallback", "tools") if key in data}
        if leaf.startswith("self_check_errors_"):
            category = leaf.removeprefix("self_check_errors_")
            def bucket(error):
                code = error["code"]
                if any(word in code for word in ("already_running", "foreign", "source_busy")):
                    return "busy"
                if any(word in code for word in ("ownership", "tree_unproven", "owner_scope")):
                    return "ownership"
                if any(word in code for word in ("invalid", "shell_operator", "inline_execution", "program_denied")):
                    return "request"
                if any(word in code for word in ("cancel", "ownership", "tree_unproven", "recovery", "foreign", "already_running")):
                    return "recovery"
                return "execution"
            return {"errors": [error for error in data["errors"] if bucket(error) == category]}
        if leaf in ("self_check_http", "self_check_group"):
            group_keys = ("group_note", "group_operations")
            return {key: value for key, value in data["http"].items() if (key in group_keys) == (leaf == "self_check_group")}
        if leaf in ("self_check_baseline", "self_check_baseline_conditions"):
            return {key: value for key, value in data["baseline"].items() if (key in ("conditions", "summary_status")) == (leaf == "self_check_baseline_conditions")}
        key = {"self_check_attempt": "graph_attempt"}.get(leaf, leaf.removeprefix("self_check_"))
        return {key: data.get(key, {})}
    if area == "documents" and source == "document_access":
        keys = {"access": ("body", "path", "answers", "note", "conversation_history"), "partial": ("partial",), "unreadable": ("content_state", "rules")}[leaf]
        return {key: data[key] for key in keys if key in data}
    if area == "attachments":
        keys = ("list", "read", "copy", "permission", "note") if leaf == "read" else ("upload_request", "permission", "absent", "note")
        return {key: data[key] for key in keys if key in data}
    if area == "question":
        keys = {"register": ("note", "options_requires", "transport", "example"), "precheck": ("before_asking", "registration_precheck", "search"), "answers": ("answers",), "policy": ("policy",)}[leaf]
        return {key: data[key] for key in keys if key in data}
    if area == "conversation" and leaf in ("turn", "encoding"):
        from modules.flow_gate.services import prompt_blocks
        probe = prompt_blocks.detail("conversation.encoding", "help-example-not-a-live-token")
        encoding = "\n".join(probe).replace(probe[2], "<encoding_probe supplied in this invocation; never copy this placeholder>")
        if leaf == "encoding":
            return {"body": encoding}
        return {"sections": [section.replace(encoding, "See conversation/encoding for probe and transport rules.") for section in data["sections"]]}
    return data
