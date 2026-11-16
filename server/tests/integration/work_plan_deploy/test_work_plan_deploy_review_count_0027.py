"""flowgate.v02test.0027 (0007-T, A-server): the WP first deploy keeps a plan step's explicit review count, 0 included,
over the AI role group's default review count; a step with no count leaves the card to that default."""
from test_work_plan_deploy_0074 import db_path, env, dep, _flow, FA  # noqa: F401 - fixtures
from test_work_plan_deploy_roles_0113 import plan
from test_fg_ai_roles_l1_card_init import group_defaults


def group_count(value, type_key="common"):
    from modules.flow_gate.db import ai_group_role_profiles as profiles
    from modules.flow_gate.db.connection import get_store
    group_defaults({}, type_key)
    with get_store().transaction():
        profiles.set_review_count("roles", type_key, value)


def deployed_count(dep, doc):
    from test_work_plan_deploy_0074 import _execute
    from flowgate.card_settings import repository
    [key] = _execute(dep, doc, "first")["created_node_keys"]
    return repository.stored(FA, key, "review_count")


def test_first_deploy_explicit_zero_wins_over_group_default(dep):
    group_count(2)
    group_count(3, "T")
    _, doc, _ = _flow(dep)
    plan(doc, review=None, first=True)                 # the result step's review_count is 0
    assert deployed_count(dep, doc) == 0


def test_first_deploy_explicit_count_wins_over_group_default(dep):
    group_count(3)
    _, doc, _ = _flow(dep)
    plan(doc, first=True)                              # the result step's review_count is 1
    assert deployed_count(dep, doc) == 1


def test_initial_settings_keep_explicit_zero_and_leave_missing_count_out():
    from flowgate.work_plan_deploy import plan as deploy_plan
    def target(count):
        result = {"pair_role": "result", "provider_id": "aip_x"}
        if count is not None:
            result["review_count"] = count
        return deploy_plan.Target("c1", "T", 0, [{"pair_role": "instruction", "review_count": 0}, result])
    assert ("review_count", 0) in deploy_plan.initial_settings(target(0))
    assert ("review_count", 2) in deploy_plan.initial_settings(target(2))
    assert not [v for n, v in deploy_plan.initial_settings(target(None)) if n == "review_count"]
    # The paired redeploy's explicit settings (clears included) are unchanged.
    assert ("review_count", 0) in deploy_plan.stage_settings(target(0))
