"""Tests for moleculerpy_web.errors module."""

from __future__ import annotations

import pytest

from moleculerpy_web.errors import (
    BadRequestError,
    ForbiddenError,
    GatewayError,
    GatewayTimeoutError,
    InternalServerError,
    NotFoundError,
    RateLimitExceededError,
    ServiceUnavailableError,
    UnauthorizedError,
    UnprocessableEntityError,
    moleculer_error_to_http,
)


class TestGatewayErrorSubclasses:
    """Test each error subclass has correct defaults."""

    @pytest.mark.parametrize(
        ("cls", "expected_code", "expected_type"),
        [
            (BadRequestError, 400, "BAD_REQUEST"),
            (UnauthorizedError, 401, "UNAUTHORIZED"),
            (ForbiddenError, 403, "FORBIDDEN"),
            (NotFoundError, 404, "NOT_FOUND"),
            (UnprocessableEntityError, 422, "UNPROCESSABLE_ENTITY"),
            (RateLimitExceededError, 429, "RATE_LIMIT_EXCEEDED"),
            (InternalServerError, 500, "INTERNAL_SERVER_ERROR"),
            (ServiceUnavailableError, 503, "SERVICE_UNAVAILABLE"),
            (GatewayTimeoutError, 504, "GATEWAY_TIMEOUT"),
        ],
    )
    def test_defaults(
        self, cls: type[GatewayError], expected_code: int, expected_type: str
    ) -> None:
        err = cls("test message")
        assert err.status_code == expected_code
        assert err.type == expected_type
        assert err.name == cls.__name__
        assert err.message == "test message"
        assert err.data == {}
        assert isinstance(err, GatewayError)

    def test_custom_data(self) -> None:
        err = BadRequestError("bad", data={"field": "name"})
        assert err.data == {"field": "name"}

    def test_custom_type(self) -> None:
        err = BadRequestError("bad", type="CUSTOM_TYPE")
        assert err.type == "CUSTOM_TYPE"


class TestToResponseDict:
    """Test to_response_dict() output format."""

    def test_format(self) -> None:
        err = NotFoundError("not found", data={"action": "math.add"})
        result = err.to_response_dict()
        assert result == {
            "name": "NotFoundError",
            "message": "not found",
            "code": 404,
            "type": "NOT_FOUND",
            "data": {"action": "math.add"},
        }

    def test_base_class(self) -> None:
        err = GatewayError("base", 418, "TEAPOT")
        result = err.to_response_dict()
        assert result["code"] == 418
        assert result["type"] == "TEAPOT"
        assert result["name"] == "GatewayError"


class TestMoleculerErrorToHttp:
    """Test moleculer_error_to_http() mapping."""

    def test_service_not_found(self) -> None:
        from moleculerpy.errors import ServiceNotFoundError

        result = moleculer_error_to_http(ServiceNotFoundError("math.add"))
        assert isinstance(result, NotFoundError)
        assert result.status_code == 404

    def test_service_not_available(self) -> None:
        from moleculerpy.errors import ServiceNotAvailableError

        result = moleculer_error_to_http(ServiceNotAvailableError("math.add"))
        assert isinstance(result, ServiceUnavailableError)
        assert result.status_code == 503

    def test_validation_error(self) -> None:
        from moleculerpy.errors import ValidationError

        result = moleculer_error_to_http(ValidationError("invalid field"))
        assert isinstance(result, UnprocessableEntityError)
        assert result.status_code == 422

    def test_request_timeout(self) -> None:
        from moleculerpy.errors import RequestTimeoutError

        result = moleculer_error_to_http(RequestTimeoutError("math.add", timeout=5.0))
        assert isinstance(result, GatewayTimeoutError)
        assert result.status_code == 504

    def test_request_skipped(self) -> None:
        from moleculerpy.errors import RequestSkippedError

        result = moleculer_error_to_http(RequestSkippedError("math.add"))
        assert isinstance(result, ServiceUnavailableError)

    def test_queue_is_full(self) -> None:
        from moleculerpy.errors import QueueIsFullError

        result = moleculer_error_to_http(QueueIsFullError("math.add"))
        assert isinstance(result, ServiceUnavailableError)

    def test_max_call_level(self) -> None:
        from moleculerpy.errors import MaxCallLevelError

        result = moleculer_error_to_http(MaxCallLevelError("node-1", level=10))
        assert isinstance(result, InternalServerError)

    def test_moleculer_client_error(self) -> None:
        from moleculerpy.errors import MoleculerClientError

        result = moleculer_error_to_http(MoleculerClientError("bad request"))
        assert isinstance(result, BadRequestError)
        assert result.status_code == 400

    def test_moleculer_client_error_401_maps_to_unauthorized(self) -> None:
        """Regression for KNOWN-ISSUES #19: client errors with code=401 must
        surface as HTTP 401, not 400. Previously every MoleculerClientError was
        unconditionally mapped to BadRequestError, hiding auth failures."""
        from moleculerpy.errors import MoleculerClientError

        result = moleculer_error_to_http(
            MoleculerClientError("token expired", code=401, error_type="UNAUTHORIZED")
        )
        assert isinstance(result, UnauthorizedError)
        assert result.status_code == 401

    def test_moleculer_client_error_403_maps_to_forbidden(self) -> None:
        """Regression for KNOWN-ISSUES #19: client errors with code=403 must
        surface as HTTP 403."""
        from moleculerpy.errors import MoleculerClientError

        result = moleculer_error_to_http(
            MoleculerClientError("insufficient scope", code=403, error_type="FORBIDDEN")
        )
        assert isinstance(result, ForbiddenError)
        assert result.status_code == 403

    def test_moleculer_client_error_404_maps_to_not_found(self) -> None:
        """Regression for KNOWN-ISSUES #19: client errors with code=404 must
        surface as HTTP 404 even without a typed ServiceNotFoundError."""
        from moleculerpy.errors import MoleculerClientError

        result = moleculer_error_to_http(
            MoleculerClientError("resource gone", code=404, error_type="NOT_FOUND")
        )
        assert isinstance(result, NotFoundError)
        assert result.status_code == 404

    def test_moleculer_retryable_error(self) -> None:
        from moleculerpy.errors import MoleculerRetryableError

        result = moleculer_error_to_http(MoleculerRetryableError("retry"))
        assert isinstance(result, ServiceUnavailableError)

    def test_moleculer_error_base(self) -> None:
        from moleculerpy.errors import MoleculerError

        result = moleculer_error_to_http(MoleculerError("generic"))
        assert isinstance(result, InternalServerError)

    def test_unknown_exception(self) -> None:
        result = moleculer_error_to_http(RuntimeError("unexpected"))
        assert isinstance(result, InternalServerError)
        assert result.status_code == 500
        assert "unexpected" in result.message

    def test_preserves_data(self) -> None:
        from moleculerpy.errors import ServiceNotFoundError

        err = ServiceNotFoundError("math.add", node_id="node-1")
        result = moleculer_error_to_http(err)
        assert result.data["action"] == "math.add"
        assert result.data["nodeID"] == "node-1"
