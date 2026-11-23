"""TR submission validation shared by new and Agent edit; no storage side effects."""
import re

ERROR_CODE = "invalid_commit_message"
ERROR_DETAILS = {"reason": "tr_commit_message_invalid"}
ERROR_MESSAGE = (
    "TR commit_message is required: use one line of English (ASCII), "
    "at most 200 characters. Recommended format: type(scope): summary; "
    "e.g. fix(git): use fix/feat for TR approve subject."
)


def validate(raw):
    # Preserve new submission's coercion and whitespace/length semantics.
    raw = str(raw or "")
    normalized = re.sub(r"\s+", " ", raw).strip()
    if (not normalized or not raw.isascii()
            or any(ch in raw for ch in "\r\n\v\f\x1c\x1d\x1e")
            or len(normalized) > 200):
        raise ValueError(ERROR_MESSAGE)
    return normalized
