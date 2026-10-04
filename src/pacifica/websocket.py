"""Lazy multiplexed subscriptions, application heartbeats, bounded buffers and reconnects."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import aclosing, suppress
from typing import Any, Self

from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from .config import PacificaConfig
from .exceptions import ProtocolError, ReconnectExhaustedError, SubscriptionError
from .models import (
    AccountInfo,
    BestBidOffer,
    ConnectionEvent,
    DataEvent,
    Fill,
    Order,
    Orderbook,
    Position,
    Price,
    StreamEvent,
    Subscription,
    Trade,
    WireModel,
)


def parse_event(message: dict[str, Any], generation: int) -> DataEvent:
    channel = message.get("channel")
    types: dict[str, type[WireModel]] = {
        "prices": Price,
        "book": Orderbook,
        "bbo": BestBidOffer,
        "trades": Trade,
        "account_info": AccountInfo,
        "account_positions": Position,
        "account_order_updates": Order,
        "account_trades": Fill,
    }
    if channel not in types:
        raise ProtocolError("Unknown WebSocket data channel")
    data = message.get("data")
    model = types[channel]
    try:
        if channel in {"book", "bbo", "account_info"}:
            parsed: Any = model.model_validate(data)
        else:
            if not isinstance(data, list):
                raise ProtocolError("Stream channel requires an array")
            parsed = tuple(model.model_validate(row) for row in data)
        return DataEvent(channel=channel, data=parsed, generation=generation, li=message.get("li"))
    except ValidationError:
        raise ProtocolError(f"Invalid {channel} WebSocket payload") from None


class PacificaStream:
    """One consumer per stream. Use an async context manager to close on early exit."""

    def __init__(
        self,
        config: PacificaConfig,
        subscriptions: Sequence[Subscription],
        *,
        on_close: Callable[[PacificaStream], None] | None = None,
    ) -> None:
        if not subscriptions:
            raise ValueError("At least one subscription is required")
        self.config = config
        self.subscriptions = tuple(subscriptions)
        keys = [json.dumps(sub.params(), sort_keys=True) for sub in self.subscriptions]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate subscriptions are not allowed")
        for channel in {sub.source for sub in subscriptions}:
            if sum(sub.source == channel for sub in subscriptions) > 20:
                raise ValueError("At most 20 subscriptions per channel per connection")
        self._on_close = on_close
        self._socket: ClientConnection | None = None
        self._generator: AsyncGenerator[StreamEvent, None] | None = None
        self._reading = False
        self._closed = False
        self._stop = asyncio.Event()

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> StreamEvent:
        if self._closed:
            raise StopAsyncIteration
        if self._reading:
            raise RuntimeError("A stream supports only one concurrent reader")
        self._reading = True
        if self._generator is None:
            self._generator = self._run()
        try:
            return await anext(self._generator)
        finally:
            self._reading = False

    async def __aenter__(self) -> Self:
        if self._closed:
            raise RuntimeError("Stream is closed")
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._closed = True
        self._stop.set()
        if self._socket is not None:
            await self._socket.close()
        if self._generator is not None and not self._reading:
            await self._generator.aclose()
        if self._on_close is not None:
            self._on_close(self)

    @staticmethod
    def _message(raw: str | bytes) -> dict[str, Any]:
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ProtocolError("Invalid WebSocket JSON") from None
        if not isinstance(result, dict):
            raise ProtocolError("WebSocket message must be an object")
        if result.get("success") is False or result.get("error") is not None:
            raise SubscriptionError("Pacifica rejected the WebSocket subscription")
        return result

    def _check_scope(self, event: DataEvent) -> None:
        candidates = [sub for sub in self.subscriptions if sub.source == event.channel]
        if not candidates:
            raise ProtocolError("Received data for an unsubscribed channel")
        rows: tuple[Any, ...] = event.data if isinstance(event.data, tuple) else (event.data,)
        for row in rows:
            if event.channel in {"book", "bbo", "trades"}:
                if not any(sub.symbol == row.symbol for sub in candidates):
                    raise ProtocolError("Received data for an unsubscribed symbol")
            if event.channel in {"account_trades", "account_order_updates"}:
                # These channels explicitly echo account; require it, not just when present.
                if row.account is None or not any(sub.account == row.account for sub in candidates):
                    raise ProtocolError("Received data for an unsubscribed account")
        # Positions/info omit account in provider messages. Multiple account scopes
        # for these channels cannot be demultiplexed and are rejected by the client.

    async def _heartbeat(self, socket: ClientConnection, pong: asyncio.Event) -> None:
        while True:
            await asyncio.sleep(self.config.heartbeat_interval)
            pong.clear()
            await socket.send('{"method":"ping"}')
            try:
                await asyncio.wait_for(pong.wait(), self.config.heartbeat_timeout)
            except TimeoutError:
                await socket.close(code=1011, reason="Application heartbeat timeout")
                return

    async def _session(
        self, socket: ClientConnection, generation: int
    ) -> AsyncGenerator[StreamEvent, None]:
        pong = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(socket, pong))
        pending: deque[DataEvent] = deque()
        unacknowledged = {
            json.dumps(sub.params(), sort_keys=True): sub for sub in self.subscriptions
        }
        try:
            async with asyncio.timeout(self.config.subscription_timeout):
                for sub in self.subscriptions:
                    await socket.send(json.dumps({"method": "subscribe", "params": sub.params()}))
                while unacknowledged:
                    message = self._message(await socket.recv())
                    if message.get("channel") == "pong":
                        pong.set()
                        continue
                    if message.get("channel") == "subscribe":
                        params = message.get("data")
                        if not isinstance(params, dict):
                            raise SubscriptionError("Subscription acknowledgement omitted scope")
                        # A book ack may omit the default aggregation level.
                        if params.get("source") == "book" and "agg_level" not in params:
                            params = {**params, "agg_level": 1}
                        # Pacifica echoes its implicit compact account-info mode.
                        # Accept only the observed default, retaining account/scope checks.
                        if params.get("source") == "account_info" and params.get("full") is False:
                            params = {key: value for key, value in params.items() if key != "full"}
                        key = json.dumps(params, sort_keys=True)
                        if key not in unacknowledged:
                            raise SubscriptionError("Unexpected subscription acknowledgement")
                        del unacknowledged[key]
                    else:
                        event = parse_event(message, generation)
                        self._check_scope(event)
                        if len(pending) >= self.config.max_pending_messages:
                            raise ProtocolError("Subscription startup buffer exhausted")
                        pending.append(event)
            # Connected is a transport state. It never asserts that account state is fresh.
            yield ConnectionEvent(state="connected", generation=generation, stale=True)
            while pending:
                yield pending.popleft()
            while not self._closed:
                message = self._message(await socket.recv())
                if message.get("channel") == "pong":
                    pong.set()
                    continue
                event = parse_event(message, generation)
                self._check_scope(event)
                yield event
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, ConnectionClosed):
                await heartbeat

    async def _run(self) -> AsyncGenerator[StreamEvent, None]:
        generation = 0
        failures = 0
        headers = {"PF-API-KEY": self.config.api_config_key} if self.config.api_config_key else None
        try:
            while not self._closed:
                generation += 1
                try:
                    async with connect(
                        self.config.websocket_url,
                        additional_headers=headers,
                        open_timeout=self.config.subscription_timeout,
                        close_timeout=3,
                        ping_interval=None,
                        max_queue=self.config.websocket_max_queue,
                        max_size=self.config.websocket_max_size,
                        proxy=None,
                    ) as socket:
                        self._socket = socket
                        async with aclosing(self._session(socket, generation)) as session:
                            async for event in session:
                                if self._closed:
                                    return
                                # An ack alone must not reset repeated failure budgets.
                                if isinstance(event, DataEvent):
                                    failures = 0
                                yield event
                except (OSError, ConnectionClosed, TimeoutError, InvalidHandshake):
                    if self._closed:
                        return
                    failures += 1
                    yield ConnectionEvent(
                        state="disconnected",
                        generation=generation,
                        stale=True,
                        reason="Transport disconnected; reconcile REST and backfill fills",
                    )
                    if failures > self.config.max_reconnect_attempts:
                        raise ReconnectExhaustedError(
                            "WebSocket reconnect budget exhausted"
                        ) from None
                    delay = min(
                        self.config.reconnect_delay * 2 ** (failures - 1),
                        self.config.max_reconnect_delay,
                    )
                    with suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), delay)
                finally:
                    self._socket = None
        finally:
            self._closed = True
            if self._on_close is not None:
                self._on_close(self)
