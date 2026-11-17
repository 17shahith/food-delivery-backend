"""Domain exceptions translated into a consistent JSON error envelope."""

from typing import Any


class AppError(Exception):
    status_code: int = 400
    code: str = "BAD_REQUEST"
    default_message: str = "Bad request"

    def __init__(self, message: str | None = None, *, code: str | None = None, details: Any = None):
        self.message = message or self.default_message
        if code:
            self.code = code
        self.details = details
        super().__init__(self.message)


class BadRequestError(AppError):
    status_code = 400
    code = "BAD_REQUEST"


class AuthenticationError(AppError):
    status_code = 401
    code = "UNAUTHORIZED"
    default_message = "Authentication required"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "FORBIDDEN"
    default_message = "You do not have permission to perform this action"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    default_message = "Resource not found"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    default_message = "Request conflicts with the current state of the resource"


class InvalidStatusTransitionError(ConflictError):
    code = "INVALID_STATUS_TRANSITION"


class RateLimitedError(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    default_message = "Too many requests"


class ExternalServiceError(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    default_message = "An upstream service is unavailable"
