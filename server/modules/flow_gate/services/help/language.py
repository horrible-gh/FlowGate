"""Response-wide language decisions, independent of suppliers."""
from .locale import normalize_locale

def decide(requested, fragments):
    requested = normalize_locale(requested)
    reasons = [name for name, locale in fragments if locale != requested]
    return {"requested_locale": requested, "resolved_locale": "ko" if reasons else requested,
            "fallback_fragments": reasons, "omitted_fragments": []}

def metadata(requested, resolved, reasons=(), omitted=()):
    return {"requested_locale": requested, "resolved_locale": resolved,
            "fallback_fragments": list(dict.fromkeys(reasons)),
            "omitted_fragments": list(dict.fromkeys(omitted))}
