"""Explicit service registration for the legacy inbox/worker/read ports."""
from modules.flow_gate.graph_workflow.services import card_document_ports as ports


def dispatch(operation, *args, **kwargs):
    from . import agent_adapter, read_bridge
    handlers = {"edit": agent_adapter.handle, "preflight": agent_adapter.worker_preflight,
                "normalize": agent_adapter.normalize, "schema": agent_adapter.edit_schema,
                "read": read_bridge.read, "mention": read_bridge.mention}
    return handlers[operation](*args, **kwargs)


def dedicated(action, *args, **kwargs):
    from . import dedicated_windows, recovery_bridge, generated_bridge
    handlers = {"wp_save": dedicated_windows.wp_save, "wp_restore": dedicated_windows.wp_restore,
                "tr2_save": dedicated_windows.tr2_save, "tr2_create": dedicated_windows.tr2_create,
                "tr2_registration": dedicated_windows.tr2_registration,
                "recover": recovery_bridge.recover, "wp_heal": recovery_bridge.heal,
                "convert": generated_bridge.convert, "tsr": generated_bridge.tsr,
                "generated_boundary": generated_bridge.generated_boundary, "tsr_open": generated_bridge.open_tsr,
                "conversion_stage": generated_bridge.conversion_stage,
                "conversion_identity": generated_bridge.conversion_identity,
                "conversion_finish": generated_bridge.conversion_finish,
                "conversion_canvas": generated_bridge.conversion_canvas}
    return handlers[action](*args, **kwargs)


def register():
    from .pending import flush
    ports.register(ports.DEDICATED_DOCUMENT_WRITE, dedicated)
    ports.register(ports.DOCUMENT_WRITE_RELEASED, flush)
    ports.register(ports.CARD_DOCUMENT_EDIT_BOUNDARY, unavailable)
    ports.register(ports.CARD_DOCUMENT_EDIT, dispatch)


def unavailable(operation, *args, **kwargs):
    """Missing mutation port still owns verify → axes → Graph → object → unavailable."""
    from . import agent_adapter, agent_errors, agent_binding
    from .errors import GraphApiError, internal_error
    from fastapi.responses import JSONResponse
    from modules.flow_gate.services import graph_source_hooks
    if operation == "normalize":
        return agent_errors.normalize(*args, **kwargs)
    if operation == "schema":
        return args[1]
    if operation in {"read", "mention"}:
        raise RuntimeError("card_edit_unavailable")
    try:
        with graph_source_hooks.write_session():
            if operation == "edit":
                _, raw, body = args
                agent_adapter.check_boundary(raw, body)
            elif operation == "preflight":
                from modules.flow_gate.services.ai_invoke import worker
                run, raw, model_input, context = args
                agent_adapter.check_boundary(raw, worker._register_envelope(context, run, {}), run=run)
                if not isinstance(model_input, dict):
                    from .errors import invalid
                    raise invalid("invalid_request", "body", "invalid_format")
            else:
                raise ValueError(operation)
            raise agent_binding.missing("card_edit_unavailable")
    except agent_adapter.BoundaryRejected as exc:
        status, payload = exc.status, exc.value
    except GraphApiError as exc:
        status, payload = exc.status, agent_errors.failure(exc)
    except Exception:
        exc = internal_error()
        status, payload = exc.status, agent_errors.failure(exc)
    return JSONResponse(status_code=status, content=payload) if operation == "edit" else (status, payload)
