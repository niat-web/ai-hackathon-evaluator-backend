"""
Typed application errors (Phase 6).

Preserve the status-code semantics routes already use for common messages,
while distinguishing infrastructure failures from “not found” / validation.
"""

from __future__ import annotations

from fastapi import HTTPException, status


class AppError(Exception):
    """Base error with an HTTP status and client-facing detail string."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str | None = None

    def __init__(self, detail: str, *, code: str | None = None):
        self.detail = detail
        if code is not None:
            self.code = code
        super().__init__(detail)

    def client_detail(self) -> str | dict[str, str]:
        if self.code:
            return {"code": self.code, "message": self.detail}
        return self.detail


class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT


class TooManyRequestsError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS


class PayloadTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE


class InfrastructureError(AppError):
    """Firestore / Auth / network outages — not a missing document."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


def status_code_for_value_error_message(detail: str) -> int:
    """
    Map ValueError message text to HTTP status.

    Mirrors the historical ``_http_from_value_error`` rules in submissions
    so behaviour stays stable for existing service ``raise ValueError(...)``.
    """
    lower = detail.lower()
    if "not found" in lower:
        return status.HTTP_404_NOT_FOUND
    if "too large" in lower:
        return status.HTTP_413_CONTENT_TOO_LARGE
    if "already" in lower or "conflict" in lower:
        return status.HTTP_409_CONFLICT
    return status.HTTP_400_BAD_REQUEST


def http_exception_from_value_error(exc: ValueError) -> HTTPException:
    detail = str(exc)
    return HTTPException(
        status_code=status_code_for_value_error_message(detail),
        detail=detail,
    )


def http_exception_from_app_error(exc: AppError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.client_detail())
