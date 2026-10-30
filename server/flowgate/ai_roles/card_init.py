"""L-1 §2.4: creation copy, WP precedence/redeploy, and one pending fill.

All writes share the caller's Flow guard. Account read failures are isolated by a
savepoint so a failed PostgreSQL SELECT cannot poison the draft transaction.
"""
import logging
from flowgate.ai_roles import availability, role_store
from flowgate.ai_roles.account import valid_value, same_value
from flowgate.ai_roles.resolve import applicable_roles
from flowgate.card_settings import fields, repository
from flowgate.ai_roles.legacy_adapter import get_store, in_transaction, held_guard

log = logging.getLogger(__name__)


def _require_guard(flow_id):
    if held_guard() != ("flow", flow_id) or not in_transaction():
        raise RuntimeError("card role writes require their Flow guard transaction")


def _read_account(user_id, flow_id, node_key, wp=False):
    if user_id is None:
        return None
    store = get_store()
    with store.transaction():
        # The Flow guard's first DML has already opened SQLite's deferred transaction.
        store._execute("SAVEPOINT fg_role_acc_read")
        try:
            account = role_store.account_roles(user_id)
        except Exception:
            store._execute("ROLLBACK TO SAVEPOINT fg_role_acc_read")
            store._execute("RELEASE SAVEPOINT fg_role_acc_read")
            if wp:
                log.warning("ACCOUNT_READ_FAILED_ON_WP_DEPLOY flow_id=%s node_key=%s", flow_id, node_key)
            return None
        store._execute("RELEASE SAVEPOINT fg_role_acc_read")
        return account


def slot_from_account(value, source="creation_copy", actor=None, role=None):
    if not valid_value(value):
        return None
    return role_store.RoleSlot(role, value, source, actor, role_store._now())


def wp_plan_roles(settings):
    values = dict(settings)
    return {role: values[name] for name, role in
            (("instruction_ai", "instruction"), ("run_ai", "execution"), ("review_ai", "review"))
            if isinstance(values.get(name), str) and values[name].strip()}


def initialize_new_card(flow_id, node_key, kind, creator_id):
    return _initialize(flow_id, node_key, kind, creator_id, {}, False)


def initialize_wp_card(flow_id, node_key, kind, deployer_id, plan_roles):
    return _initialize(flow_id, node_key, kind, deployer_id, plan_roles, True)


def _initialize(flow_id, node_key, kind, creator_id, plan_roles, wp):
    if not role_store.available() or not applicable_roles(kind):
        return "NO_CHANGE"
    _require_guard(flow_id)
    roles = applicable_roles(kind)
    acc = _read_account(creator_id, flow_id, node_key, wp) if any(r not in plan_roles for r in roles) else None
    for role in roles:
        if role in plan_roles:
            pid = plan_roles[role]
            value = role_store.RoleValue("assigned", pid, availability.display_name(pid))
            slot = role_store.RoleSlot(role, value, "wp_plan", creator_id, role_store._now())
        else:
            slot = slot_from_account(acc.values.get(role) if acc else None, "creation_copy", creator_id, role)
        if slot:
            role_store.write_slot(flow_id, node_key, slot)
    state = "initialized" if all(r in role_store.slots(flow_id, node_key) for r in roles) else "pending_init"
    role_store.create_card_init(flow_id, node_key, state, creator_id)
    repository.bump_version(flow_id, node_key)
    return "CHANGED"


def cards_created(group, user_id, nodes):
    if not role_store.available():
        return
    for node in nodes:
        initialize_new_card(group["group_id"], node["node_key"],
                            fields.card_kind(node.get("node_type"), node.get("doc_type")), user_id)


def _redeploy(flow_id, node_key, kind, plan_roles):
    roles = applicable_roles(kind)
    slots = role_store.slots(flow_id, node_key)
    puts, removes = {}, []
    marked = set(slots)
    for role in roles:
        old = slots.get(role)
        if role in plan_roles:
            if old is None or old.source != "wp_plan" or not same_value(old.value, role_store.RoleValue("assigned", plan_roles[role])):
                puts[role] = plan_roles[role]
            marked.add(role)
        elif old is not None and old.source == "wp_plan":
            removes.append(role)
            marked.discard(role)
    init = role_store.card_init(flow_id, node_key)
    initialize = bool(roles) and all(r in marked for r in roles) and (init is None or init.state == "pending_init")
    return puts, removes, initialize, init


def redeploy_role_changes(flow_id, node_key, kind, plan_roles):
    if not role_store.available() or not applicable_roles(kind):
        return False
    puts, removes, initialize, _ = _redeploy(flow_id, node_key, kind, plan_roles)
    return bool(puts or removes or initialize)


def apply_wp_redeploy_roles(flow_id, node_key, kind, deployer_id, plan_roles):
    if not role_store.available() or not applicable_roles(kind):
        return "NO_CHANGE"
    _require_guard(flow_id)
    puts, removes, initialize, init = _redeploy(flow_id, node_key, kind, plan_roles)
    for role, pid in puts.items():
        value = role_store.RoleValue("assigned", pid, availability.display_name(pid))
        role_store.write_slot(flow_id, node_key, role_store.RoleSlot(role, value, "wp_plan", deployer_id, role_store._now()))
    for role in removes:
        role_store.remove_wp_plan_slot(flow_id, node_key, role)
    if initialize:
        if init is None:
            role_store.create_card_init(flow_id, node_key, "initialized", None)
        else:
            role_store.mark_initialized(flow_id, node_key)
    if puts or removes or initialize:
        repository.bump_version(flow_id, node_key)
        return "CHANGED"
    return "NO_CHANGE"


def initialize_pending_card(flow_id, node_key, kind):
    if not role_store.available() or not applicable_roles(kind):
        return "NO_CHANGE"
    _require_guard(flow_id)
    init = role_store.card_init(flow_id, node_key)
    if init is None or init.state != "pending_init" or not init.creator_id:
        return "NO_CHANGE"
    acc = _read_account(init.creator_id, flow_id, node_key)
    if acc is None:
        return "NO_CHANGE"
    slots = role_store.slots(flow_id, node_key)
    changed = False
    for role in applicable_roles(kind):
        if role not in slots:
            slot = slot_from_account(acc.values.get(role), "pending_fill", None, role)
            if slot:
                changed = role_store.write_slot(flow_id, node_key, slot) or changed
    if all(r in role_store.slots(flow_id, node_key) for r in applicable_roles(kind)):
        changed = role_store.mark_initialized(flow_id, node_key) or changed
    if changed:
        repository.bump_version(flow_id, node_key)
    return "CHANGED" if changed else "NO_CHANGE"
