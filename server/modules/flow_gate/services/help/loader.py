"""Validated JSON help assets. No service imports and no cached mutable payloads."""
import json
import re
from pathlib import Path
from .locale import normalize_locale

ROOT = Path(__file__).resolve().parents[2] / "help_content"
TOKEN = re.compile(r"\{\{(route:)?([a-zA-Z_][\w./-]*)\}\}")

class HelpContentError(ValueError):
    pass

def read_json(name, root=None):
    root = Path(root or ROOT).resolve()
    path = (root / (name + ".json")).resolve()
    if not path.is_relative_to(root):
        raise HelpContentError("path_outside_content_root")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise HelpContentError(f"invalid_asset:{name}") from exc

def manifest(root=None):
    return read_json("manifest", root)

def canonical(name, root=None):
    aliases = read_json("aliases", root)
    return aliases.get(name, name)

def definition(name, root=None):
    entries = manifest(root)["items"]
    return entries.get(name) or entries.get(name.split("/", 1)[0] + "/*")

def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for part in value.values():
            yield from strings(part)
    elif isinstance(value, list):
        for part in value:
            yield from strings(part)

def validate(item, spec, locale, routes):
    if not isinstance(item, dict) or item.get("locale") != locale:
        raise HelpContentError("locale_declaration_mismatch")
    if item.get("status") == "pending":
        return
    required = {"title", "summary", "purpose"}
    required |= {"request", "response", "errors"} if spec["kind"] == "usage" else {"body"}
    if not required.issubset(item) or any(not isinstance(item[k], (str, list, dict)) for k in required):
        raise HelpContentError("invalid_item_schema")
    for value in strings(item):
        for is_route, name in TOKEN.findall(value):
            if is_route:
                if name not in routes or name not in spec.get("routes", []):
                    raise HelpContentError("unknown_route:" + name)
            elif name not in spec.get("placeholders", {}):
                raise HelpContentError("unknown_placeholder:" + name)
        prose = TOKEN.sub("", value)
        if "{{" in prose or "}}" in prose:
            raise HelpContentError("malformed_placeholder")
        # Code and route examples are neutral; foreign scripts in prose are not.
        prose = re.sub(chr(96) + "[^" + chr(96) + "]*" + chr(96), "", prose)
        if re.search(r"/(?:api/|document/|flow/|remote/|help/|q/)", prose):
            raise HelpContentError("literal_api_address")
        if locale == "en" and re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", prose):
            raise HelpContentError("mixed_language:en")
        if locale == "ko" and re.search(r"[\u3040-\u30ff]", prose):
            raise HelpContentError("mixed_language:ko")
        if locale == "ja" and re.search(r"[\uac00-\ud7af]", prose):
            raise HelpContentError("mixed_language:ja")
        if locale in ("ko", "ja") and re.search(r"[A-Za-z]+(?:\s+[A-Za-z]+){3,}", prose) and not re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", prose):
            raise HelpContentError("mixed_language:" + locale)

def load_item(name, locale, root=None):
    name = canonical(name, root)
    spec = definition(name, root)
    if spec is None:
        raise HelpContentError("unknown_item:" + name)
    locale = normalize_locale(locale)
    root = Path(root or ROOT)
    ko_path = root / "ko" / (name + ".json")
    if not ko_path.is_file():
        if spec.get("dynamic") and not any((root / lang / (name + ".json")).is_file() for lang in ("en", "ja")):
            return None, locale, []
        raise HelpContentError("missing_korean:" + name)
    ko = read_json("ko/" + name, root)
    routes = read_json("routes", root)
    validate(ko, spec, "ko", routes)
    if ko.get("status") == "pending":
        return None, locale, []
    requested_path = root / locale / (name + ".json")
    item = read_json(locale + "/" + name, root) if requested_path.is_file() else None
    if item is not None:
        validate(item, spec, locale, routes)
    if item is None or item.get("status") == "pending":
        return ko, "ko", [name + ":missing_translation"] if locale != "ko" else []
    return item, locale, []

def load_mention(name, locale, root=None):
    return load_item("mention/" + name, locale, root)

def load_index(locale, root=None):
    root = Path(root or ROOT)
    requested = normalize_locale(locale)
    applied = requested if (root / requested / "_index.json").is_file() else "ko"
    item = read_json(applied + "/_index", root)
    if item.get("locale") != applied or set(item.get("areas", {})) != set(manifest(root)["areas"]):
        raise HelpContentError("invalid_index")
    return item, applied

def validate_catalog(root=None):
    data = manifest(root)
    routes = read_json("routes", root)
    for alias, target in read_json("aliases", root).items():
        if target not in data["items"] or alias not in data["items"][target].get("aliases", []):
            raise HelpContentError("invalid_alias:" + alias)
    for name, spec in data["items"].items():
        if spec.get("dynamic") or spec["kind"] == "area":
            continue
        for locale in ("ko", "en", "ja"):
            load_item(name, locale, root)
    return True
