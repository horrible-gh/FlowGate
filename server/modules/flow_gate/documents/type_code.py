"""Canonical document type and document-code grammar (flowgate.default.0565).

The grammar itself lives in ``modules.flow_gate.utils.type_code`` (a leaf module with no
package-level imports) so ``utils.id_validators`` does not depend on this package. This module
keeps the original import path."""
from __future__ import annotations

from modules.flow_gate.utils.type_code import (  # noqa: F401
    DOC_CODE_RE,
    DOC_ID_TAIL_RE,
    STEP_KEY_RE,
    TYPE_CODE_MAX_LEN,
    TYPE_CODE_PATTERN,
    TYPE_CODE_RE,
    doc_code_seq_text,
    doc_code_type,
    doc_id_type_code,
    is_valid_type_code,
    parse_doc_code,
    parse_step_key,
)
