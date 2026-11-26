"""console-only card verification status and controls.

All three endpoints require the card Flow's write permission. Responses use the Graph
envelope and carry ``verification``. GET has no side effects. POST feedback is transient;
the client can show stopped/cancelled briefly, then resume GET. A 409 cancel means the
request has already started: refresh instead of turning a waiting cancel into a kill.
"""
from fastapi import APIRouter, Request

from modules.flow_gate.graph_workflow.api import envelope
from modules.flow_gate.graph_workflow.services import canvas_service
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services import tr_self_check_card as service

router = APIRouter(prefix='/api/v1/flows', tags=['TR Self-check'])


def _access(request, flow_id):
    user_id = envelope.authenticate(request)
    group = canvas_service.load_flow(flow_id)
    envelope.authorize(user_id, group, 'write')
    return user_id


@router.get('/{flow_id}/nodes/{node_key}/self-check')
def read_card(request: Request, flow_id: str, node_key: str):
    def handle():
        user_id = _access(request, flow_id)
        return {'verification': service.status(flow_id, node_key,
            lambda project_id: has_permission(user_id, project_id, 'project.document.read'))}
    return envelope.run(request, handle)


def _control(request, flow_id, node_key, run_id, action):
    def handle():
        _access(request, flow_id)
        return {'verification': service.control(flow_id, node_key, run_id, action)}
    return envelope.run(request, handle)


@router.post('/{flow_id}/nodes/{node_key}/self-check/runs/{run_id}/force-stop')
def force_stop(request: Request, flow_id: str, node_key: str, run_id: str):
    return _control(request, flow_id, node_key, run_id, 'force_stop')


@router.post('/{flow_id}/nodes/{node_key}/self-check/runs/{run_id}/cancel-waiting')
def cancel_waiting(request: Request, flow_id: str, node_key: str, run_id: str):
    return _control(request, flow_id, node_key, run_id, 'cancel_waiting')
