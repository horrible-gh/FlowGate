"""L-1 §2.5/2.6/2.9: read-only role resolution and legacy truth tables."""
from dataclasses import dataclass, field
import logging
from flowgate.ai_roles import availability, assignment_store, role_store
from flowgate.ai_roles.account import valid_value
from flowgate.ai_roles.legacy_adapter import isolated_read
from flowgate.card_settings import fields, repository

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resolution:
    role: str
    outcome: str
    source: str = "none"
    provider_id: str | None = None
    original: dict | None = None
    cause: str | None = None
    pending_init: bool = False
    legacy_flag: bool = False


@dataclass
class CardRoleInputs:
    slots: dict = field(default_factory=dict)
    init: object = None
    stored: dict = field(default_factory=dict)
    node: dict | None = None
    store_error: bool = False


def applicable_roles(kind):
    if kind in (fields.RUN, fields.TEST):
        return role_store.ROLES
    return ("execution", "review") if kind == fields.PLAN else ()


def read_card_role_inputs(flow_id, node):
    def read():
        if not repository.available() or not role_store.available():
            return CardRoleInputs(store_error=True)
        return CardRoleInputs(slots=role_store.slots(flow_id, node["node_key"]),
                              init=role_store.card_init(flow_id, node["node_key"]),
                              stored=repository.values(flow_id, node["node_key"]))
    try:
        inputs = isolated_read(read, "fg_role_card_read")
    except Exception:
        return CardRoleInputs(store_error=True)
    if inputs.store_error:
        return inputs
    try:
        inputs.node = node if "assignee_kind" in node else isolated_read(
            lambda: repository.card_node(flow_id, node["node_key"]), "fg_role_node_read")
    except Exception:
        inputs.node = None
    return inputs


def with_availability(role, provider_id, name, source, project_id=None, legacy_flag=False):
    cause = availability.check_availability(provider_id, availability.CARD, project_id=project_id)
    return Resolution(role, "unavailable" if cause else "ai", source, provider_id,
                      {"provider_id": provider_id, "name": name} if cause else None,
                      cause=cause, legacy_flag=legacy_flag)


def _id_state(value, assignee=False):
    if value is None or value == "":
        return "absent"
    if not isinstance(value, str) or (not assignee and not value.strip()):
        return "broken"
    return "present"


def resolve_legacy(kind, role, inputs, ctx=None, *, project_id=None):
    project_id = project_id if project_id is not None else (ctx or {}).get("project_id")
    def result(outcome, source="legacy_fields"):
        return Resolution(role, outcome, source, legacy_flag=True)
    def ai(pid):
        return with_availability(role, pid, None, "legacy_fields", project_id, True)
    stored, node = inputs.stored, inputs.node
    if role == "review":
        count = stored.get("review_count")
        if count is None:
            return result("no_review")                          # R-1
        if type(count) is not int:
            return result("needs_setup", "store_error")         # R-2
        if count == 0:
            return result("no_review")                          # R-3
        value = stored.get("review_ai")
        state = _id_state(value)
        if state == "broken":
            return result("needs_setup", "store_error")         # R-4
        return ai(value) if state == "present" else result("needs_setup")  # R-5/6
    if node is None:
        return result("needs_setup", "store_error")             # C-2
    actor = node.get("assignee_kind")
    if actor not in (None, "human", "ai"):
        return result("needs_setup", "store_error")             # I-1/E-1
    if actor == "human":
        return result("person")                                 # I-2/E-2
    if role == "instruction" and kind == fields.RUN:
        value = stored.get("instruction_ai")
        state = _id_state(value)
        if state == "broken":
            return result("needs_setup", "store_error")         # I-3
        if state == "present":
            return ai(value)                                    # I-4
    value = stored.get("run_ai")
    state = _id_state(value)
    if state == "broken":
        return result("needs_setup", "store_error")             # I-5/E-3
    if state == "present":
        return ai(value)                                        # I-6/E-4
    pid = node.get("assignee_provider_id")
    if actor == "ai":
        return ai(pid) if _id_state(pid, True) == "present" else result("needs_setup")  # I-7/8,E-5/6
    if role == "execution" and kind == fields.PLAN:
        return result("person")                                 # E-7
    return result("needs_setup")                                # I-9/E-8


def resolve_role(flow_id, node, role, ctx=None, *, project_id=None, inputs=None):
    kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
    if role not in applicable_roles(kind):
        return Resolution(role, "not_applicable")
    inputs = inputs if inputs is not None else read_card_role_inputs(flow_id, node)
    if inputs.store_error:
        return Resolution(role, "needs_setup", "store_error")
    project_id = project_id if project_id is not None else (ctx or {}).get("project_id")
    slot = inputs.slots.get(role)
    init_state = inputs.init.state if inputs.init else "legacy"
    if slot is not None:
        if not valid_value(slot.value) or slot.source not in role_store.SLOT_SOURCES:
            log.warning("ROLE_SLOT_MALFORMED flow_id=%s node_key=%s", flow_id, node.get("node_key"))
            return Resolution(role, "needs_setup", "store_error")
        if slot.value.kind == "not_used":
            return Resolution(role, "person", slot.source)
        return with_availability(role, slot.value.provider_id, slot.value.provider_name, slot.source, project_id)
    if init_state == "pending_init":
        return Resolution(role, "needs_setup", pending_init=True, legacy_flag=True)
    if init_state == "legacy":
        return resolve_legacy(kind, role, inputs, project_id=project_id)
    return Resolution(role, "needs_setup")


def card_execution_is_ai(flow_id, node, attempt_id=None):
    kind = fields.card_kind(node.get("node_type"), node.get("doc_type"))
    if "execution" not in applicable_roles(kind):
        return node.get("assignee_kind") == "ai"
    if attempt_id and assignment_store.available():
        assignment = assignment_store.latest(attempt_id, "result")
        if assignment is not None:
            return assignment["actor"] == "ai"
    from modules.flow_gate.db import groups
    group = groups.get_by_id(flow_id) or {}
    return resolve_role(flow_id, node, "execution", project_id=group.get("project_id")).outcome == "ai"
