from http import HTTPStatus

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.errors import (
    AuthenticationError,
    ConflictError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    UpstreamError,
    ValidationError,
)
from logger import get_logger
from schema.common.responses import CommonResponse


logger = get_logger(__name__)

_DOMAIN_ERROR_STATUS: tuple[tuple[type[DomainError], HTTPStatus], ...] = (
    (ValidationError, HTTPStatus.BAD_REQUEST),
    (AuthenticationError, HTTPStatus.UNAUTHORIZED),
    (ForbiddenError, HTTPStatus.FORBIDDEN),
    (NotFoundError, HTTPStatus.NOT_FOUND),
    (ConflictError, HTTPStatus.CONFLICT),
    (UpstreamError, HTTPStatus.BAD_GATEWAY),
)


def status_for_domain_error(exc: DomainError) -> HTTPStatus:
    for error_type, status in _DOMAIN_ERROR_STATUS:
        if isinstance(exc, error_type):
            return status
    raise TypeError(f"Unmapped domain error type: {type(exc).__name__}")


async def domain_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """Render domain errors in the public CommonResponse shape."""
    if not isinstance(exc, DomainError):
        raise TypeError(f"Expected DomainError, got {type(exc).__name__}")
    status = status_for_domain_error(exc)
    return JSONResponse(
        status_code=status.value,
        content=CommonResponse(
            code=status.value,
            message=str(exc) or status.phrase,
        ).model_dump(),
    )


def _serialize_validation_errors(errors: list[dict]) -> list[dict]:
    serialized_errors: list[dict] = []
    for error in errors:
        serialized_error = dict(error)
        ctx = serialized_error.get("ctx")
        if isinstance(ctx, dict):
            serialized_error["ctx"] = {key: str(value) for key, value in ctx.items()}
        serialized_errors.append(serialized_error)
    return serialized_errors


async def request_validation_exception_handler(
    _: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Wrap request validation errors in CommonResponse."""
    return JSONResponse(
        status_code=422,
        content=CommonResponse(
            code=422,
            message="Request validation failed",
            data=_serialize_validation_errors(exc.errors()),
        ).model_dump(),
    )


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Wrap framework HTTP errors in CommonResponse."""
    return JSONResponse(
        status_code=exc.status_code,
        content=CommonResponse(
            code=exc.status_code,
            message=str(exc.detail),
        ).model_dump(),
        headers=exc.headers,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log unexpected API failures and return the public CommonResponse shape."""
    logger.error(
        "Unhandled request failed: %s %s",
        request.method,
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR.value,
        content=CommonResponse(
            code=HTTPStatus.INTERNAL_SERVER_ERROR.value,
            message="Internal server error",
        ).model_dump(),
    )
