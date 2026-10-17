"""Registration of the Graph pre-run validation: how the Graph workflow gets its decided rules (D0006 §2.3).

``modules.flow_gate.graph_workflow.services.graph_validation_ports`` defines the port; the composition root
(``startup.py``) calls :func:`register` once at startup. Registered, ``graph_service.check_draft`` (draft
validation and confirmation) validates with ``validator.validate_graph``; unregistered, the Graph workflow's
own ``graph_validator.validate_graph`` runs, as before 0061.
"""
from __future__ import annotations

from flowgate.graph_validation import legacy_adapter, validator
from flowgate.graph_validation.run_scope import run_scope


def register() -> None:
    legacy_adapter.register_scope(run_scope)
    legacy_adapter.register_validator(validator.validate_graph)


def unregister() -> None:
    legacy_adapter.unregister_validator()
