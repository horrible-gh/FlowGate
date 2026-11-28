"""Compatibility exports for the modular help catalog."""
from .help.legacy.compat.locale import (FALLBACK_LOCALE, NOTICE_LINES, QUESTION_HELP_COPY, SUBMIT_SUMMARIES, SUBMIT_TITLES, SUMMARIES, SUPPORTED_LOCALES, TITLES, _AUTHORING_GUIDE_TITLES, _ITEM_NOTES, _TR_AUTHORING_GUIDE, _copy, normalize_locale)
from .help.tree import (ALWAYS_VISIBLE, AUTHORING_SCOPES, BULK_ITEM_MAX, BULK_ITEM_MIN, CATALOG_ORDER, GROUP_DOCUMENTS_LIMIT, GROUP_DOCUMENTS_MORE_LIMIT, GUIDE_TYPES, HelpSupplierError, INVESTIGATION_TYPES, ITEM_FORM, MUTATING_TYPES, USER_SESSION_VISIBLE, VERSION, _CATALOG)
from .help.visibility import (Decision, VISIBLE, _effective_doc_type, _is_design_type, context_envelope, decide_visibility, resolve_context, visible_names)
from .help import content as _compat_exports
globals().update({name:value for name,value in vars(_compat_exports).items() if not name.startswith("__")})
from .help.render import (BulkRequestError, _children_count, _design_type_rows, _item_notes, _summary_for, _title_for, build_bulk, build_child, build_index, build_item, enumerate_children, item_url, parse_bulk_names)
from .help.render import build_index, build_item, build_child, build_bulk, enumerate_children
from .help.visibility import decide_visibility
from . import chat_help_content
from . import tool_registry  # noqa: F401 - compatibility: callers patch help_catalog.tool_registry.resolve_registry
