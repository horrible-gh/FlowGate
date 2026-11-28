"""Expand only declared references; never interpret braces inside substituted data."""
from copy import deepcopy
from .loader import TOKEN, HelpContentError

def fill(value, spec, values, routes, base_url):
    originals = []
    def resolve_route(name):
        if name not in spec.get("routes", []) or name not in routes:
            raise HelpContentError("unknown_route:" + name)
        route = routes[name]
        base = base_url.removesuffix("/api/v1") + "/api/" + route["api"] if route["api"].startswith("v2/") else base_url
        import re
        path = re.sub(r"\{(\w+)\}", lambda m: str(values.get(m[1], m[0])), route["path"])
        return {"method": route["method"], "url": base + path}
    def replacement(match, path):
        is_route, name = match.groups()
        if is_route:
            route = resolve_route(name)
            return route["method"] + " " + route["url"]
        kind = spec.get("placeholders", {}).get(name)
        if kind is None:
            raise HelpContentError("unknown_placeholder:" + name)
        if kind == "original":
            originals.append(path)
        return deepcopy(values.get(name, "{" + name + "}"))
    def walk(part, path):
        if isinstance(part, str):
            match = TOKEN.fullmatch(part)
            if match:
                return replacement(match, path)
            def inline(match):
                value = replacement(match, path)
                if isinstance(value, str):
                    return value
                # Embedded JSON values use JSON syntax, never Python repr.
                import json
                return json.dumps(value, ensure_ascii=False, allow_nan=False)
            return TOKEN.sub(inline, part)
        if isinstance(part, list):
            return [walk(v, path + "/" + str(i)) for i, v in enumerate(part)]
        if isinstance(part, dict):
            return {k: walk(v, path + "/" + k) for k, v in part.items()}
        return part
    return walk(value, ""), originals
