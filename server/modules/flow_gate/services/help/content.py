"""Deprecated compatibility exports. New rendering uses the owning supplier directly."""
from importlib import import_module
_CONTENT_SUPPLIERS = {}
for _owner in ("compat", "docs_area", "cards_area"):
    try:
        _module = import_module(f"{__package__}.legacy.{_owner}.content")
    except ModuleNotFoundError as exc:
        if exc.name not in (f"{__package__}.legacy.{_owner}", f"{__package__}.legacy.{_owner}.content"):
            raise
        continue
    globals().update({key: value for key, value in vars(_module).items()
                      if not key.startswith("__") and key != "_CONTENT_SUPPLIERS"})
    _CONTENT_SUPPLIERS.update(_module._CONTENT_SUPPLIERS)
