from typing import Any

from schema.common.responses import CommonResponse


RouteResponses = dict[int | str, dict[str, Any]]

# 401 / 403 are now driven from the route's auth dependencies; this dict only
# carries shared error responses that have to be declared per-route
COMMON_ERROR_RESPONSES: RouteResponses = {
    422: {"description": "Validation error", "model": CommonResponse},
}

BAD_REQUEST_RESPONSE: RouteResponses = {
    400: {"description": "Bad request", "model": CommonResponse},
}

FORBIDDEN_RESPONSE: RouteResponses = {
    403: {"description": "Forbidden", "model": CommonResponse},
}

INTERNAL_ERROR_RESPONSE: RouteResponses = {
    500: {"description": "Internal server error", "model": CommonResponse},
}

CONFLICT_RESPONSE: RouteResponses = {
    409: {"description": "Conflict", "model": CommonResponse},
}

BAD_GATEWAY_RESPONSE: RouteResponses = {
    502: {"description": "Upstream failure", "model": CommonResponse},
}


def not_found_response(resource: str) -> RouteResponses:
    return {404: {"description": f"{resource} not found", "model": CommonResponse}}
