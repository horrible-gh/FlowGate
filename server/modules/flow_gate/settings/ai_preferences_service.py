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
    system_max = policy.get_repeat_count_max()
    own = saved["repeat_count_max"]
    return {"title_provider": title,
            "title_provider_effective": ai_title_provider.value() if title == "auto" else title,
            "repeat_count_max": own,
            "repeat_count_max_effective": min(own if own is not None else system_max, system_max),
            "repeat_count_system_max": system_max, "repeat_count_min": policy.REPEAT_COUNT_MIN_MAX}


def patch(user_id, values):
    if "title_provider" in values:
        title = values["title_provider"]
        if (not isinstance(title, str) or not title or title != title.strip()
                or (title not in {"auto", "off", "executor"}
                    and ai_settings_service.selectable_provider(None, title, user_id) is None)):
            raise InvalidPreference("invalid_title_provider")
    if "repeat_count_max" in values:
        own, maximum = values["repeat_count_max"], policy.get_repeat_count_max()
        if own is not None and (type(own) is not int or not policy.REPEAT_COUNT_MIN_MAX <= own <= maximum):
            raise InvalidPreference("repeat_count_out_of_range", max=maximum)
    repo.patch(user_id, values)
    return payload(user_id)
