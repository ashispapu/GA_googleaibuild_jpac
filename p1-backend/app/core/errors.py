"""One error shape for the whole API: ErrorResponse {code, message, trace_id, details?} (CONTRACTS C1)."""
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from plantguard_contracts.schemas.common import ErrorCode, ErrorResponse


class ApiError(Exception):
    """Raise this from any router or service to return a contract-shaped error with a chosen HTTP status."""

    def __init__(self, status: int, code: ErrorCode, message: str, **details):
        """status: HTTP status; code: contract ErrorCode; message: readable text; details: optional extra fields."""
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or None


def error_response(status: int, code: ErrorCode, message: str, details: dict | None = None,
                   headers: dict | None = None) -> JSONResponse:
    """Build the JSON ErrorResponse (with a fresh trace_id) for a status, code and message."""
    body = ErrorResponse(code=code, message=message, trace_id=uuid.uuid4().hex[:12], details=details)
    return JSONResponse(body.model_dump(mode="json"), status_code=status, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    """Register handlers so ApiError, KeyError, HTTP errors and validation errors all return ErrorResponse."""
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        """ApiError -> its own status, code and message."""
        return error_response(e.status, e.code, e.message, e.details)

    from app.repositories.incident_repository import ConcurrentUpdate

    @app.exception_handler(ConcurrentUpdate)
    async def _conc(_: Request, e: ConcurrentUpdate):
        """Another user or process changed the incident in the meantime -> 409, the UI reloads it."""
        return error_response(409, ErrorCode.CONFLICT, "This issue was just changed by someone else. Reload and try again.")

    @app.exception_handler(KeyError)
    async def _key(_: Request, e: KeyError):  # connectors raise KeyError for unknown ids -> 404
        """KeyError (raised by connectors for unknown ids) -> 404 NOT_FOUND."""
        return error_response(404, ErrorCode.NOT_FOUND, f"Not found: {e.args[0] if e.args else ''}")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        """Framework HTTP errors -> 404 NOT_FOUND, anything else VALIDATION."""
        code = ErrorCode.NOT_FOUND if e.status_code == 404 else ErrorCode.VALIDATION
        return error_response(e.status_code, code, str(e.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        """Bad request body/query -> 422 VALIDATION with the list of field errors in details."""
        errors = [{"loc": [str(x) for x in err["loc"]], "msg": err["msg"], "type": err["type"]} for err in e.errors()]
        return error_response(422, ErrorCode.VALIDATION, "Request validation failed", {"errors": errors})
