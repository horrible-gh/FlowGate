"""Personal choices, validated before any field is written."""
from modules.flow_gate.db import user_ai_preferences as repo
from modules.flow_gate.settings import ai_title_provider, ai_settings_service
from modules.flow_gate.settings import ai_execution_policy_service as policy


class InvalidPreference(ValueError):
    def __init__(self, code, **extra):
        self.detail = {"code": code, **extra}
        super().__init__(code)


def payload(user_id):
    saved = repo.get(user_id)
    title = saved["title_provider"]
    default = policy.get_repeat_count_max()
    own = saved["repeat_count_max"]
    # v02test.0096 F96-11: my value applies as saved (1..30); the stored system value is only the default.
    return {"title_provider": title,
            "title_provider_effective": ai_title_provider.value() if title == "auto" else title,
            "repeat_count_max": own,
            "repeat_count_max_effective": own if policy.valid_user_repeat_count_max(own) else default,
            "repeat_count_default": default, "repeat_count_min": policy.REPEAT_COUNT_MIN_MAX,
            "repeat_count_hard_max": policy.REPEAT_COUNT_HARD_MAX}


def patch(user_id, values):
    if "title_provider" in values:
        title = values["title_provider"]
        if (not isinstance(title, str) or not title or title != title.strip()
                or (title not in {"auto", "off", "executor"}
                    and ai_settings_service.selectable_provider(None, title, user_id) is None)):
            raise InvalidPreference("invalid_title_provider")
    if "repeat_count_max" in values:
        own = values["repeat_count_max"]
        if own is not None and not policy.valid_user_repeat_count_max(own):
            raise InvalidPreference("repeat_count_out_of_range", max=policy.REPEAT_COUNT_HARD_MAX)
    repo.patch(user_id, values)
    return payload(user_id)
