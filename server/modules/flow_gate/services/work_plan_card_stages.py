"""v0.2 card-owned instruction/result steps (0074 T0017; supersedes D0006 single-step design).

The optional card_stage_model marker isolates this shape from v0.1 plans. DS remains a document
kind, never a standalone v0.2 card. Legacy standalone DS records are retained as compatibility data.
"""
from copy import deepcopy

DESIGN_TYPES = ("D", "P", "L", "DB")
MODEL = "paired"


def is_instruction(step):
    return (step.get("type") == "DS" and step.get("pair_role") == "instruction"
            and str(step.get("pair_key") or "").split("#")[0] in DESIGN_TYPES)


def card_type(step):
    return step["pair_key"].split("#")[0] if is_instruction(step) else step.get("type")


def upgrade(body):
    """Copy an old card plan to real pairs without changing card identities or existing values.

    The legacy shared pre-instruction is copied once into the instruction stage. Its old result
    editing value is retained independently; subsequent edits never alias the two documents.
    """
    out = deepcopy(body)
    out["card_stage_model"] = MODEL
    steps = []
    for step in out.get("steps") or []:
        if step.get("type") not in DESIGN_TYPES or step.get("pair_role") != "single":
            steps.append(step)
            continue
        from .work_plan_service import make_step
        card_id = step.get("card_id") or step["key"]
        key = "DS" + step["key"]
        instruction = make_step("DS", step["ordinal"], step["key"], "instruction", card_id)
        instruction["key"] = key
        for field in ("provider_id", "provider_display_name", "pre_instruction_text", "pre_instruction_attachment"):
            instruction[field] = deepcopy(step.get(field))
        instruction["instruction_mode"] = step.pop("instruction_mode", None) or "use"
        step.update(card_id=card_id, pair_role="result", pair_key=key)
        steps.extend([instruction, step])
    out["steps"] = steps
    return out
