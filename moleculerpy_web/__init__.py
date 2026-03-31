"""MoleculerPy Web — HTTP API Gateway for MoleculerPy microservices."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("moleculerpy-web")
except PackageNotFoundError:
    __version__ = "0.14.1a1"

from moleculerpy_web.alias import AliasMatch, AliasResolver
from moleculerpy_web.errors import (
    BadRequestError,
    ForbiddenError,
    GatewayError,
    GatewayTimeoutError,
    InternalServerError,
    NotFoundError,
    PayloadTooLargeError,
    RateLimitExceededError,
    ServiceUnavailableError,
    UnauthorizedError,
    UnprocessableEntityError,
)
from moleculerpy_web.handler import handle_request
from moleculerpy_web.route import GatewaySettings, RouteConfig
from moleculerpy_web.service import ApiGatewayService

__all__ = [
    "__version__",
    "ApiGatewayService",
    "GatewayError",
    "BadRequestError",
    "UnauthorizedError",
    "ForbiddenError",
    "NotFoundError",
    "UnprocessableEntityError",
    "RateLimitExceededError",
    "InternalServerError",
    "PayloadTooLargeError",
    "ServiceUnavailableError",
    "GatewayTimeoutError",
    "AliasResolver",
    "AliasMatch",
    "RouteConfig",
    "GatewaySettings",
    "handle_request",
]
