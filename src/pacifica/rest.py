"""GET retries and single-attempt signed mutations."""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .config import PacificaConfig
from .exceptions import (
    APIError,
    ClientClosedError,
    MutationContext,
    ProtocolError,
    RateLimitError,
    TransportError,
    UnknownOutcomeError,
)
from .rate_limit import CreditLimiter


def retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            delay = (date - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, delay) if math.isfinite(delay) else None


class RestTransport:
    def __init__(
        self, config: PacificaConfig, *, transport: httpx.AsyncBaseTransport | None
    ) -> None:
        self.config = config
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if config.api_config_key:
            headers["PF-API-KEY"] = config.api_config_key
        self._http = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/") + "/",
            headers=headers,
            timeout=config.request_timeout,
            transport=transport,
            follow_redirects=False,
        )
        self.limiter = CreditLimiter(
            config.rest_credits_per_minute,
            reserve=config.rest_credit_reserve,
            pace_reads=config.pace_reads,
        )
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True
        await self._http.aclose()

    async def reserve(self, cost: float, *, read: bool = False) -> None:
        if self.closed:
            raise ClientClosedError("Pacifica client is closed")
        await self.limiter.acquire(cost, read=read)

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        # Costs vary with the endpoint and result size, so budget heavy reads at the
        # highest published cost; the server's ratelimit headers then correct the estimate.
        heavy = "history" in path or path.lstrip("/").startswith("kline")
        cost = (4.0 if self.config.api_config_key else 12.0) if heavy else 1.0
        for attempt in range(self.config.max_read_retries + 1):
            await self.reserve(cost, read=True)
            delay = min(self.config.retry_delay * 2**attempt, self.config.max_retry_delay)
            try:
                response = await self._http.get(path.lstrip("/"), params=params)
            except httpx.TransportError as exc:
                if attempt == self.config.max_read_retries:
                    raise TransportError("Pacifica GET failed after bounded retries") from exc
            else:
                self.limiter.observe(response.headers)
                if response.status_code not in {429, 500, 502, 503, 504}:
                    return self._decode(response)
                wait = retry_after(response)
                if wait is None and response.status_code == 429:
                    budget = self.limiter.server_budget
                    wait = budget[1] if budget else None
                if attempt == self.config.max_read_retries or (
                    wait is not None and wait > self.config.max_retry_delay
                ):
                    if response.status_code == 429:
                        raise RateLimitError(
                            retry_after=wait, request_id=response.headers.get("x-request-id")
                        )
                    return self._decode(response)
                delay = wait if wait is not None else delay
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def post_reserved(
        self, path: str, body: dict[str, Any], context: MutationContext
    ) -> dict[str, Any]:
        # Reservation happens before signing, so pacing cannot expire the signature.
        try:
            response = await self._http.post(path.lstrip("/"), json=body)
        except asyncio.CancelledError:
            raise UnknownOutcomeError(context) from None
        except httpx.TransportError:
            raise UnknownOutcomeError(context) from None
        self.limiter.observe(response.headers)
        request_id = response.headers.get("x-request-id")
        if (
            response.status_code >= 500
            or response.status_code == 408
            or (300 <= response.status_code < 400)
        ):
            raise UnknownOutcomeError(context, request_id=request_id)
        try:
            return self._decode(response)
        except ProtocolError:
            raise UnknownOutcomeError(context, request_id=request_id) from None

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        request_id = response.headers.get("x-request-id")
        if response.status_code == 429:
            raise RateLimitError(retry_after=retry_after(response), request_id=request_id)
        try:
            body = response.json()
        except ValueError:
            if response.is_error:
                raise APIError(
                    "Pacifica returned an HTTP error",
                    status_code=response.status_code,
                    request_id=request_id,
                ) from None
            raise ProtocolError("Pacifica returned invalid JSON") from None
        if not isinstance(body, dict):
            raise ProtocolError("Pacifica response must be an object")
        if response.is_error or body.get("success") is False or body.get("error") is not None:
            # Provider messages may contain echoed input; avoid exposing signed/key material.
            raise APIError(
                "Pacifica rejected the request",
                status_code=response.status_code,
                code=body.get("code"),
                request_id=request_id,
            )
        if "success" in body and body["success"] is not True:
            raise ProtocolError("Pacifica success flag must be boolean")
        return body


def response_data(body: dict[str, Any]) -> Any:
    if "data" not in body:
        raise ProtocolError("Pacifica response omitted data")
    return body["data"]
