"""Map all errors to one envelope:

    {"detail": <message or validation list>, "error": {"code", "message", "details"}}

`error` is the structured form; `detail` mirrors FastAPI's default so generic clients
(including the React app's error parser) show a readable message.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.utils.exceptions import AppError

logger = logging.getLogger(__name__)


def _error(status_code: int, code: str, message: str, details=None, headers=None, detail=None) -> JSONResponse:
    body = {
        "detail": detail if detail is not None else message,
        "error": {"code": code, "message": message, "details": jsonable_encoder(details)},
    }
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return _error(exc.status_code, exc.code, exc.message, exc.details, headers)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(err.get("loc", [])), "msg": err.get("msg"), "type": err.get("type")}
            for err in exc.errors()
        ]
        return _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "Request validation failed", details, detail=details
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return _error(exc.status_code, code, str(exc.detail), headers=getattr(exc, "headers", None))

    @app.exception_handler(IntegrityError)
    async def integrity_handler(_: Request, exc: IntegrityError) -> JSONResponse:
        logger.warning("Integrity error: %s", exc.orig)
        return _error(status.HTTP_409_CONFLICT, "CONFLICT", "The request conflicts with existing data")

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "An unexpected error occurred")
