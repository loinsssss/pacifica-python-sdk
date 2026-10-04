"""Errors carry recovery identifiers, never signed bodies or private keys."""

from __future__ import annotations

from dataclasses import dataclass


class PacificaError(Exception):
    """Base SDK error."""


class ClientClosedError(PacificaError, RuntimeError):
    """An operation was attempted after the client was closed."""


class TransportError(PacificaError):
    """A network operation failed after its retry budget was exhausted."""


class ProtocolError(PacificaError):
    """Provider data does not match the documented contract."""


class AuthenticationError(PacificaError):
    """Missing credentials, wrong signer, or authentication rejection."""


class PrecisionError(PacificaError, ValueError):
    """Order precision or size is invalid."""


class SubscriptionError(PacificaError):
    """A subscription was rejected or acknowledged with the wrong scope."""


class ReconnectExhaustedError(TransportError):
    """Stream exhausted its configured reconnect budget."""


class APIError(PacificaError):
    def __init__(
        self, message: str, *, status_code: int, code: object = None, request_id: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.request_id = request_id


class RateLimitError(APIError):
    def __init__(self, *, retry_after: float | None = None, request_id: str | None = None) -> None:
        super().__init__("Pacifica rate limit exceeded", status_code=429, request_id=request_id)
        self.retry_after = retry_after


class OrderNotFoundError(PacificaError):
    """Order was absent from the bounded lookup; absence is not proof of rejection."""


class OrderWaitTimeoutError(PacificaError, TimeoutError):
    """Waiting expired; the SDK has not cancelled the order."""


@dataclass(frozen=True, slots=True)
class MutationContext:
    operation: str
    account: str
    timestamp: int
    symbol: str | None = None
    client_order_id: str | None = None
    order_id: int | None = None


class UnknownOutcomeError(PacificaError):
    def __init__(self, context: MutationContext, *, request_id: str | None = None) -> None:
        super().__init__(
            f"Outcome of {context.operation} is unknown; "
            "reconcile account/order state before retrying"
        )
        self.context = context
        self.request_id = request_id
