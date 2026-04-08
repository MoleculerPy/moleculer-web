"""HTTP-oriented error hierarchy for moleculerpy-web API Gateway.

Provides gateway-specific errors with HTTP status codes and a mapping
function to convert core MoleculerPy errors to HTTP responses.
"""

from __future__ import annotations

from typing import Any


class GatewayError(Exception):
    """Base error for all moleculerpy-web gateway errors.

    Attributes:
        message: Human-readable error message.
        status_code: HTTP status code.
        name: Error class name.
        type: Machine-readable error type identifier.
        data: Additional error context.
    """

    def __init__(
        self,
        message: str,
        status_code: int,
        type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Initialize a GatewayError.

        Args:
            message: Human-readable error description.
            status_code: HTTP status code (e.g., 400, 500).
            type: Machine-readable error type identifier.
            data: Additional error context data.
        """
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.name = self.__class__.__name__
        self.type = type
        self.data = data or {}

    def to_response_dict(self) -> dict[str, Any]:
        """Serialize error to a Node.js moleculer-web compatible response dict.

        Returns:
            Dictionary suitable for JSON HTTP error responses.
        """
        return {
            "name": self.name,
            "message": self.message,
            "code": self.status_code,
            "type": self.type,
            "data": self.data,
        }


class BadRequestError(GatewayError):
    """HTTP 400 Bad Request."""

    def __init__(
        self, message: str, type: str = "BAD_REQUEST", data: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, 400, type, data)


class UnauthorizedError(GatewayError):
    """HTTP 401 Unauthorized."""

    def __init__(
        self, message: str, type: str = "UNAUTHORIZED", data: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, 401, type, data)


class ForbiddenError(GatewayError):
    """HTTP 403 Forbidden."""

    def __init__(
        self, message: str, type: str = "FORBIDDEN", data: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, 403, type, data)


class NotFoundError(GatewayError):
    """HTTP 404 Not Found."""

    def __init__(
        self, message: str, type: str = "NOT_FOUND", data: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, 404, type, data)


class UnprocessableEntityError(GatewayError):
    """HTTP 422 Unprocessable Entity."""

    def __init__(
        self, message: str, type: str = "UNPROCESSABLE_ENTITY", data: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, 422, type, data)


class PayloadTooLargeError(GatewayError):
    """HTTP 413 Payload Too Large."""

    def __init__(
        self, message: str, type: str = "PAYLOAD_TOO_LARGE", data: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, 413, type, data)


class RateLimitExceededError(GatewayError):
    """HTTP 429 Too Many Requests."""

    def __init__(
        self, message: str, type: str = "RATE_LIMIT_EXCEEDED", data: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, 429, type, data)


class InternalServerError(GatewayError):
    """HTTP 500 Internal Server Error."""

    def __init__(
        self, message: str, type: str = "INTERNAL_SERVER_ERROR", data: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, 500, type, data)


class ServiceUnavailableError(GatewayError):
    """HTTP 503 Service Unavailable."""

    def __init__(
        self, message: str, type: str = "SERVICE_UNAVAILABLE", data: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, 503, type, data)


class GatewayTimeoutError(GatewayError):
    """HTTP 504 Gateway Timeout."""

    def __init__(
        self, message: str, type: str = "GATEWAY_TIMEOUT", data: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, 504, type, data)


def moleculer_error_to_http(err: Exception) -> GatewayError:
    """Convert a MoleculerPy core error to an HTTP gateway error.

    Maps MoleculerError subclasses to appropriate HTTP status codes.
    Order matters: most specific classes are checked first.

    Args:
        err: A MoleculerPy core exception (or any Exception).

    Returns:
        Corresponding GatewayError subclass with preserved message and data.
    """
    from moleculerpy.errors import (
        MaxCallLevelError,
        MoleculerClientError,
        MoleculerError,
        MoleculerRetryableError,
        QueueIsFullError,
        RequestSkippedError,
        RequestTimeoutError,
        ServiceNotAvailableError,
        ServiceNotFoundError,
        ValidationError,
    )

    message = str(err)
    data: dict[str, Any] = getattr(err, "data", {}) or {}

    # Most specific first
    if isinstance(err, ServiceNotFoundError):
        return NotFoundError(message, data=data)
    if isinstance(err, ServiceNotAvailableError):
        return ServiceUnavailableError(message, data=data)
    if isinstance(err, ValidationError):
        return UnprocessableEntityError(message, data=data)
    if isinstance(err, RequestTimeoutError):
        return GatewayTimeoutError(message, data=data)
    if isinstance(err, RequestSkippedError):
        return ServiceUnavailableError(message, data=data)
    if isinstance(err, QueueIsFullError):
        return ServiceUnavailableError(message, data=data)
    if isinstance(err, MaxCallLevelError):
        return InternalServerError(message, data=data)
    if isinstance(err, MoleculerClientError):
        # Honour the Moleculer error code: MoleculerClientError carries an HTTP
        # status-aligned code (e.g. 401/403/404). Previously this branch mapped
        # every client error to 400, which masked auth failures behind a
        # misleading "Bad Request". Fall back to 400 only when the code is not
        # an informative 4xx status.
        raw_code = getattr(err, "code", None)
        if isinstance(raw_code, int):
            if raw_code == 401:
                return UnauthorizedError(message, data=data)
            if raw_code == 403:
                return ForbiddenError(message, data=data)
            if raw_code == 404:
                return NotFoundError(message, data=data)
        return BadRequestError(message, data=data)
    if isinstance(err, MoleculerRetryableError):
        return ServiceUnavailableError(message, data=data)
    if isinstance(err, MoleculerError):
        return InternalServerError(message, data=data)

    # Unknown / non-Moleculer exception
    return InternalServerError(message)
