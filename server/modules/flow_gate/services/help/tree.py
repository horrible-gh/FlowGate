"""Manifest reader; compatibility constants remain owned by legacy."""
from .loader import manifest, read_json
from .legacy.compat.tree import (VERSION, BULK_ITEM_MIN, BULK_ITEM_MAX, GROUP_DOCUMENTS_LIMIT,
    GROUP_DOCUMENTS_MORE_LIMIT, HelpSupplierError, INVESTIGATION_TYPES, MUTATING_TYPES,
    ALWAYS_VISIBLE, USER_SESSION_VISIBLE, AUTHORING_SCOPES, GUIDE_TYPES, ITEM_FORM,
    LEGACY_ORDER, _CATALOG)

CATALOG_ORDER = AREA_ORDER = tuple(manifest()["areas"])
AREA_LEAVES = {}
for _name, _spec in manifest()["items"].items():
    if "/" in _name and not _spec.get("dynamic") and not _name.startswith("mention/"):
        _area, _leaf = _name.split("/", 1)
        AREA_LEAVES.setdefault(_area, {})[_leaf] = _spec["visibility"]
