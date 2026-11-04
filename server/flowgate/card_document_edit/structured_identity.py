"""L#2 §2.15.2: request identity of a structured TS command (never the rendered body)."""
import hashlib
import json
from dataclasses import dataclass
from .contracts import STRUCTURED_FP_ALG

INPUT_TS_SPEC, INPUT_TS_BRIEF = "ts_spec", "ts_brief"


def canonical_json(value):
    """Sorted keys, no whitespace, code points as UTF-8, array order and null/absent kept."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


@dataclass(frozen=True)
class StructuredCommand:
    input_kind: str
    payload: dict  # only fields actually present in the request

    @property
    def fingerprint(self):
        text = canonical_json({"input_kind": self.input_kind, "payload": self.payload})
        return hashlib.sha256(text.encode("utf-8")).hexdigest()  # UnicodeError on lone surrogates

    @property
    def algorithm(self):
        return STRUCTURED_FP_ALG


def binding_fields(command):
    """Extra binding/event fields; common binding/revision/key stay separate comparison items."""
    return {"content_sha256": command.fingerprint, "fp_alg": command.algorithm,
            "input_kind": command.input_kind, "request_fingerprint": command.fingerprint,
            "fingerprint_algorithm": command.algorithm}
