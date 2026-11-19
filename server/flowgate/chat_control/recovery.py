"""Chat authority adapter for the shared Graph recovery service."""
from . import authz
from flowgate.graph_run import recovery as shared, recovery_basis


def view(caller, node_key=None):
    authz.read_caller(caller.token, caller.group["group_id"], caller.run)
    return recovery_basis.view(caller.group, node_key)


def change(caller, body, node_key=None):
    from flowgate.card_document_edit import chat_authz, chat_errors, chat_gate
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    holders = []
    def recheck():
        # Fresh token/claim read and a chat lifetime ticket for each guarded phase.
        holder = chat_gate.TicketHolder()
        holders.append(holder)
        try:
            chat_authz.chat_recheck(caller.run, caller.token, holder)
        except GraphApiError as exc:
            if exc.code == chat_errors.EDIT_MODE_DENIED:
                raise GraphApiError(403, authz.EDIT_MODE_REQUIRED) from exc
            raise
        if not authz.has_permission(caller.scope.user_id, caller.scope.project_id, authz.WRITE_PERMISSION):
            raise GraphApiError(403, "flow_access_denied")
    authz.write_caller(caller.token, caller.group["group_id"], caller.run)
    try:
        if node_key is not None:
            return shared.retry(caller.group, caller.scope.user_id, node_key, body, recheck)
        return shared.restart(caller.group, caller.scope.user_id, body, recheck)
    finally:
        for holder in holders:
            chat_gate.release(holder.ticket)
