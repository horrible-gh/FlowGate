"""Document CRUD + state-machine + storage integration module (aligned with D009/D013/D017)."""
from .document_service import (
    create_document,
    get_document,
    list_documents,
    update_document,
    delete_document,
    transition_state,
    close_group_documents,
)
from .document_types import (
    list_types,
    get_type,
    extend_type,
    delete_type,
)
from .template_service import (
    save_template,
    get_template,
    render_template,
)

__all__ = [
    "create_document",
    "get_document",
    "list_documents",
    "update_document",
    "delete_document",
    "transition_state",
    "close_group_documents",
    "list_types",
    "get_type",
    "extend_type",
    "delete_type",
    "save_template",
    "get_template",
    "render_template",
]
