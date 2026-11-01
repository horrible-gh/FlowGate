"""Keep the SPA GET fallback from claiming missing API paths as method mismatches."""
from fastapi.routing import APIRoute
from starlette.routing import Match


def api_aware_fallback(context: str) -> type[APIRoute]:
    prefixes = tuple(p for p in (context.strip("/"), "api") if p)

    class ClientFallbackRoute(APIRoute):
        def matches(self, scope):
            match, child = super().matches(scope)
            path = child.get("path_params", {}).get("full_path", "")
            if match == Match.PARTIAL and any(path == p or path.startswith(p + "/") for p in prefixes):
                # A real API route still contributes its own PARTIAL match (405).
                # An absent API route now has no match at all (404).
                return Match.NONE, {}
            return match, child

    return ClientFallbackRoute
