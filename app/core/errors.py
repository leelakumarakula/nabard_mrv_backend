import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import request_id_var

logger = logging.getLogger("mrv")


class AppError(Exception):
    """Business-rule error rendered with the consistent error envelope."""

    def __init__(self, status_code: int, code: str, message: str, details: Any = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class NotFound(AppError):
    def __init__(self, entity: str = "Record") -> None:
        super().__init__(404, "not_found", f"{entity} not found")


class Forbidden(AppError):
    def __init__(self, message: str = "You do not have permission to perform this action") -> None:
        super().__init__(403, "forbidden", message)


class Conflict(AppError):
    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(409, "conflict", message, details)


class InvalidState(AppError):
    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(422, "invalid_state", message, details)


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}, "request_id": request_id_var.get()}


_HTTP_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
               409: "conflict", 413: "payload_too_large", 415: "unsupported_media_type", 422: "validation_error"}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message, exc.details))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "error")
        return JSONResponse(status_code=exc.status_code, content=error_body(code, str(exc.detail)),
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content=error_body("validation_error", "Request validation failed", details))

    @app.exception_handler(IntegrityError)
    async def integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        text = str(exc.orig).lower()
        if "unique" in text or "duplicate" in text:
            return JSONResponse(status_code=409, content=error_body(
                "duplicate", "A record with the same unique code or combination already exists"))
        if "foreign key" in text:
            return JSONResponse(status_code=422, content=error_body(
                "invalid_reference", "A referenced record does not exist"))
        logger.exception("Integrity error", extra={"request_id": request_id_var.get()})
        return JSONResponse(status_code=409, content=error_body("integrity_error", "The change violates a data constraint"))

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", extra={"request_id": request_id_var.get()})
        return JSONResponse(status_code=500, content=error_body("internal_error", "An unexpected error occurred"))
