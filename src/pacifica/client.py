"""Public asynchronous SDK interface."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from decimal import Decimal
from typing import Any, Self, TypeVar, get_args
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from .config import PacificaConfig, positive_int, validate_symbol
from .exceptions import (
    AuthenticationError,
    ClientClosedError,
    MutationContext,
    OrderNotFoundError,
    OrderWaitTimeoutError,
    ProtocolError,
    UnknownOutcomeError,
)
from .models import (
    AccountInfo,
    AccountSnapshot,
    AgentList,
    ApiConfigKey,
    ApiConfigKeys,
    Cancellation,
    Candle,
    CandleInterval,
    Fill,
    FundingPayment,
    FundingRate,
    HistoryPage,
    Market,
    OpenOrder,
    OpenOrdersSnapshot,
    OperationResult,
    Order,
    Orderbook,
    OrderRequest,
    OrderSide,
    OrderSubmission,
    OrderType,
    Position,
    PositionSnapshot,
    Price,
    StopOrder,
    SubmissionResolution,
    Subscription,
    Trade,
    decimal_string,
    exact,
)
from .rest import RestTransport, response_data
from .signing import Signer, address, signed_request
from .websocket import PacificaStream

M = TypeVar("M", bound=BaseModel)
R = TypeVar("R")

MAX_CANDLES_PER_REQUEST = 4000
# Fixed-length intervals in milliseconds; "1M" (calendar month) has no fixed length.
CANDLE_INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


def parse(model: type[M], data: Any) -> M:
    try:
        return model.model_validate(data)
    except ValidationError:
        raise ProtocolError(f"Invalid {model.__name__} response") from None


def parse_rows(model: type[M], data: Any) -> tuple[M, ...]:
    if not isinstance(data, list):
        raise ProtocolError("Expected an array response")
    return tuple(parse(model, row) for row in data)


def _times(start_time: int, end_time: int | None) -> None:
    for name, value in (("start_time", start_time), ("end_time", end_time)):
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"{name} must be a nonnegative integer of milliseconds")
    if end_time is not None and end_time < start_time:
        raise ValueError("end_time must not precede start_time")


def order_identifier(order_id: int | None, client_order_id: str | UUID | None) -> dict[str, Any]:
    if (order_id is None) == (client_order_id is None):
        raise ValueError("Supply exactly one of order_id or client_order_id")
    if order_id is not None:
        return {"order_id": positive_int(order_id, "order_id")}
    try:
        identifier = str(UUID(str(client_order_id)))
    except ValueError:
        raise ValueError("client_order_id must be a full UUID") from None
    return {"client_order_id": identifier}


class PacificaClient:
    def __init__(
        self,
        config: PacificaConfig | None = None,
        *,
        mainnet: bool | None = None,
        account: str | None = None,
        signer: Signer | None = None,
        agent_wallet: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if mainnet is not None and type(mainnet) is not bool:
            raise TypeError("mainnet must be bool")
        if config is not None and mainnet is not None and config.mainnet != mainnet:
            raise ValueError("config and mainnet select conflicting environments")
        self.config = config or PacificaConfig(mainnet=True if mainnet is None else mainnet)
        self.account = address(account) if account is not None else None
        self.signer = signer
        self.agent_wallet = address(agent_wallet) if agent_wallet is not None else None
        if signer is not None:
            if self.account is None:
                raise AuthenticationError("Signing requires an explicit account")
            if address(signer.address) != (self.agent_wallet or self.account):
                raise AuthenticationError("Signer must match account or configured agent wallet")
        self._rest = RestTransport(self.config, transport=transport)
        self._streams: set[PacificaStream] = set()
        self._mutation_lock = asyncio.Lock()
        self._last_timestamp = 0
        self._closed = False

    async def __aenter__(self) -> Self:
        self._ensure_open()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._closed = True
        await asyncio.gather(*(stream.aclose() for stream in tuple(self._streams)))
        await self._rest.aclose()

    def _ensure_open(self) -> None:
        if self._closed:
            raise ClientClosedError("Pacifica client is closed")

    def _account(self, account: str | None = None) -> str:
        self._ensure_open()
        selected = account if account is not None else self.account
        if selected is None:
            raise AuthenticationError("An account address is required for account reads")
        return address(selected)

    def _credentials(self) -> tuple[str, Signer]:
        selected = self._account()
        if self.signer is None:
            raise AuthenticationError("A signer is required for this operation")
        return selected, self.signer

    async def get_markets(self) -> tuple[Market, ...]:
        self._ensure_open()
        return parse_rows(Market, response_data(await self._rest.get("/info")))

    async def get_prices(self) -> tuple[Price, ...]:
        self._ensure_open()
        return parse_rows(Price, response_data(await self._rest.get("/info/prices")))

    async def get_price(self, symbol: str) -> Price:
        validate_symbol(symbol)
        for price in await self.get_prices():
            if price.symbol == symbol:
                return price
        raise ValueError("Symbol not found in prices")

    async def get_market(self, symbol: str) -> Market:
        validate_symbol(symbol)
        for market in await self.get_markets():
            if market.symbol == symbol:
                return market
        raise ValueError("Unknown Pacifica market")

    async def get_orderbook(self, symbol: str, *, agg_level: int = 1) -> Orderbook:
        self._ensure_open()
        if type(agg_level) is not int or agg_level not in {1, 10, 100, 1000, 10000}:
            raise ValueError("agg_level must be one of 1, 10, 100, 1000, 10000")
        book = parse(
            Orderbook,
            response_data(
                await self._rest.get(
                    "/book", params={"symbol": validate_symbol(symbol), "agg_level": agg_level}
                )
            ),
        )
        if book.symbol != symbol:
            raise ProtocolError("Orderbook response returned another symbol")
        return book

    async def get_recent_trades(self, symbol: str | None = None) -> tuple[Trade, ...]:
        self._ensure_open()
        params = {"symbol": validate_symbol(symbol)} if symbol is not None else None
        return parse_rows(Trade, response_data(await self._rest.get("/trades", params=params)))

    async def get_account_info(self, *, account: str | None = None) -> AccountInfo:
        return parse(
            AccountInfo,
            response_data(
                await self._rest.get("/account", params={"account": self._account(account)})
            ),
        )

    async def get_account_settings(self, *, account: str | None = None) -> dict[str, Any]:
        data = response_data(
            await self._rest.get("/account/settings", params={"account": self._account(account)})
        )
        if not isinstance(data, dict):
            raise ProtocolError("Account settings must be an object")
        return data

    async def get_position_snapshot(self, *, account: str | None = None) -> PositionSnapshot:
        body = await self._rest.get("/positions", params={"account": self._account(account)})
        return parse(
            PositionSnapshot,
            {"positions": response_data(body), "last_order_id": body.get("last_order_id")},
        )

    async def get_positions(self, *, account: str | None = None) -> tuple[Position, ...]:
        return (await self.get_position_snapshot(account=account)).positions

    async def get_position(self, symbol: str, *, account: str | None = None) -> Position | None:
        validate_symbol(symbol)
        return next(
            (
                position
                for position in await self.get_positions(account=account)
                if position.symbol == symbol
            ),
            None,
        )

    async def get_open_orders_snapshot(self, *, account: str | None = None) -> OpenOrdersSnapshot:
        body = await self._rest.get("/orders", params={"account": self._account(account)})
        return parse(
            OpenOrdersSnapshot,
            {"orders": response_data(body), "last_order_id": body.get("last_order_id")},
        )

    async def get_open_orders(self, *, account: str | None = None) -> tuple[OpenOrder, ...]:
        return (await self.get_open_orders_snapshot(account=account)).orders

    async def get_account_snapshot(self, *, account: str | None = None) -> AccountSnapshot:
        """Separate REST reads; not an atomic exchange snapshot."""
        selected = self._account(account)
        info = asyncio.create_task(self.get_account_info(account=selected))
        positions = asyncio.create_task(self.get_position_snapshot(account=selected))
        orders = asyncio.create_task(self.get_open_orders_snapshot(account=selected))
        try:
            await asyncio.gather(info, positions, orders)
        except BaseException:
            # Cancel sibling reads while preserving the original public SDK exception.
            for task in (info, positions, orders):
                task.cancel()
            await asyncio.gather(info, positions, orders, return_exceptions=True)
            raise
        return AccountSnapshot(
            account=selected,
            info=info.result(),
            positions=positions.result(),
            open_orders=orders.result(),
        )

    async def _history(
        self,
        path: str,
        model: type[M],
        *,
        limit: int,
        cursor: str | None,
        params: dict[str, Any],
        max_limit: int = 1000,
    ) -> HistoryPage[M]:
        self._ensure_open()
        positive_int(limit, "limit", maximum=max_limit)
        if cursor is not None and (not isinstance(cursor, str) or not cursor):
            raise ValueError("cursor must be a nonempty string")
        query = {**params, "limit": limit}
        if cursor is not None:
            query["cursor"] = cursor
        return parse(HistoryPage[model], await self._rest.get(path, params=query))  # type: ignore[valid-type]

    async def get_order_history(
        self, *, account: str | None = None, limit: int = 100, cursor: str | None = None
    ) -> HistoryPage[Order]:
        selected = self._account(account)
        result = await self._history(
            "/orders/history",
            Order,
            limit=limit,
            cursor=cursor,
            params={"account": selected},
        )
        if any(row.account is not None and row.account != selected for row in result.data):
            raise ProtocolError("Order history returned another account")
        return result

    async def get_trade_history(
        self,
        *,
        account: str | None = None,
        symbol: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> HistoryPage[Fill]:
        params: dict[str, Any] = {"account": self._account(account)}
        if symbol is not None:
            params["symbol"] = validate_symbol(symbol)
        for key, value in (("start_time", start_time), ("end_time", end_time)):
            if value is not None:
                params[key] = positive_int(value, key)
        if start_time is not None and end_time is not None and end_time < start_time:
            raise ValueError("end_time must not precede start_time")
        result = await self._history(
            "/trades/history", Fill, limit=limit, cursor=cursor, params=params
        )
        if any(row.account is not None and row.account != params["account"] for row in result.data):
            raise ProtocolError("Trade history returned another account")
        if symbol is not None and any(row.symbol != symbol for row in result.data):
            raise ProtocolError("Trade history returned another symbol")
        return result

    async def get_funding_history(
        self, *, account: str | None = None, limit: int = 100, cursor: str | None = None
    ) -> HistoryPage[FundingPayment]:
        return await self._history(
            "/funding/history",
            FundingPayment,
            limit=limit,
            cursor=cursor,
            params={"account": self._account(account)},
        )

    async def get_funding_rate_history(
        self, symbol: str, *, limit: int = 200, cursor: str | None = None
    ) -> HistoryPage[FundingRate]:
        return await self._history(
            "/funding_rate/history",
            FundingRate,
            limit=limit,
            cursor=cursor,
            params={"symbol": validate_symbol(symbol)},
            max_limit=4000,
        )

    async def _iterate(
        self,
        fetch: Callable[..., Awaitable[HistoryPage[R]]],
        *,
        max_pages: int,
        page_size: int,
        **kwargs: Any,
    ) -> AsyncIterator[R]:
        positive_int(max_pages, "max_pages")
        cursor = None
        seen: set[str] = set()
        for _ in range(max_pages):
            page = await fetch(limit=page_size, cursor=cursor, **kwargs)
            for row in page.data:
                yield row
            if not page.has_more:
                return
            cursor = page.next_cursor
            if cursor is None or cursor in seen:
                raise ProtocolError("History pagination repeated or omitted a cursor")
            seen.add(cursor)
        raise ProtocolError("History exceeded max_pages; results may be incomplete")

    def iter_order_history(
        self, *, account: str | None = None, page_size: int = 100, max_pages: int = 100
    ) -> AsyncIterator[Order]:
        return self._iterate(
            self.get_order_history, account=account, page_size=page_size, max_pages=max_pages
        )

    def iter_trade_history(
        self,
        *,
        account: str | None = None,
        symbol: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        page_size: int = 100,
        max_pages: int = 100,
    ) -> AsyncIterator[Fill]:
        return self._iterate(
            self.get_trade_history,
            account=account,
            symbol=symbol,
            start_time=start_time,
            end_time=end_time,
            page_size=page_size,
            max_pages=max_pages,
        )

    def iter_funding_history(
        self, *, account: str | None = None, page_size: int = 100, max_pages: int = 100
    ) -> AsyncIterator[FundingPayment]:
        return self._iterate(
            self.get_funding_history, account=account, page_size=page_size, max_pages=max_pages
        )

    def iter_funding_rate_history(
        self, symbol: str, *, page_size: int = 200, max_pages: int = 100
    ) -> AsyncIterator[FundingRate]:
        return self._iterate(
            self.get_funding_rate_history, symbol=symbol, page_size=page_size, max_pages=max_pages
        )

    async def get_candles(
        self,
        symbol: str,
        *,
        interval: CandleInterval,
        start_time: int,
        end_time: int | None = None,
        limit: int | None = None,
    ) -> tuple[Candle, ...]:
        """Trade candles, oldest first. Times are Unix milliseconds.

        Pacifica returns candles starting in ``[start_time, end_time]``, including one
        that starts exactly at ``end_time``. A range may span at most ``limit``
        intervals (default and maximum 4000), and longer ranges are rejected; use
        ``iter_candles`` for those. Minutes without trades are omitted, and an unknown
        symbol returns no candles rather than an error.
        """
        return await self._candles("/kline", symbol, interval, start_time, end_time, limit)

    async def get_mark_price_candles(
        self,
        symbol: str,
        *,
        interval: CandleInterval,
        start_time: int,
        end_time: int | None = None,
        limit: int | None = None,
    ) -> tuple[Candle, ...]:
        """Mark price candles, oldest first. Same limits as ``get_candles``."""
        return await self._candles("/kline/mark", symbol, interval, start_time, end_time, limit)

    async def iter_candles(
        self,
        symbol: str,
        *,
        interval: CandleInterval,
        start_time: int,
        end_time: int,
        mark_price: bool = False,
    ) -> AsyncIterator[Candle]:
        """Candles starting in ``[start_time, end_time)``, over any range.

        Fetched in windows of 4000 intervals. The end is exclusive, so consecutive
        calls over adjacent ranges never return the same candle twice.
        """
        if interval not in CANDLE_INTERVAL_MS:
            raise ValueError("iter_candles needs a fixed-length interval (not 1M)")
        _times(start_time, end_time)
        fetch = self.get_mark_price_candles if mark_price else self.get_candles
        span = CANDLE_INTERVAL_MS[interval] * MAX_CANDLES_PER_REQUEST
        last: int | None = None
        chunk_start = start_time
        while chunk_start < end_time:
            chunk_end = min(chunk_start + span, end_time)
            for candle in await fetch(
                symbol, interval=interval, start_time=chunk_start, end_time=chunk_end
            ):
                if candle.start_time >= end_time:
                    return
                # Adjacent windows can both return the candle on their shared boundary.
                if last is None or candle.start_time > last:
                    last = candle.start_time
                    yield candle
            chunk_start = chunk_end

    async def _candles(
        self,
        path: str,
        symbol: str,
        interval: CandleInterval,
        start_time: int,
        end_time: int | None,
        limit: int | None,
    ) -> tuple[Candle, ...]:
        self._ensure_open()
        validate_symbol(symbol)
        if interval not in get_args(CandleInterval):
            raise ValueError(f"interval must be one of {', '.join(get_args(CandleInterval))}")
        _times(start_time, end_time)
        cap = MAX_CANDLES_PER_REQUEST
        if limit is not None:
            cap = positive_int(limit, "limit", maximum=MAX_CANDLES_PER_REQUEST)
        if end_time is not None and interval in CANDLE_INTERVAL_MS:
            count = math.ceil((end_time - start_time) / CANDLE_INTERVAL_MS[interval])
            if count > cap:
                raise ValueError(
                    f"range spans {count} {interval} candles; one request returns at most "
                    f"{cap} (use iter_candles for longer ranges)"
                )
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "start_time": start_time}
        if end_time is not None:
            params["end_time"] = end_time
        if limit is not None:
            params["limit"] = limit
        rows = parse_rows(Candle, response_data(await self._rest.get(path, params=params)))
        if any(row.symbol != symbol or row.interval != interval for row in rows):
            raise ProtocolError("Candles returned another symbol or interval")
        return rows

    async def get_order(
        self,
        order_id: int | None = None,
        *,
        client_order_id: str | UUID | None = None,
        account: str | None = None,
        max_pages: int = 3,
    ) -> Order | OpenOrder:
        """Bounded lookup via open orders and history; there is no assumed /orders/{id} route."""
        identifier = order_identifier(order_id, client_order_id)
        positive_int(max_pages, "max_pages")

        def matches(row: Order | OpenOrder) -> bool:
            return all(getattr(row, key) == value for key, value in identifier.items())

        for opened in await self.get_open_orders(account=account):
            if matches(opened):
                return opened
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(max_pages):
            page = await self.get_order_history(account=account, cursor=cursor)
            for row in page.data:
                if matches(row):
                    return row
            if not page.has_more:
                break
            cursor = page.next_cursor
            if cursor is None or cursor in seen:
                raise ProtocolError("Order lookup pagination repeated a cursor")
            seen.add(cursor)
        raise OrderNotFoundError("Order not found within configured history bounds")

    async def _submit(
        self,
        path: str,
        operation: str,
        payload: dict[str, Any],
        model: type[M],
        *,
        owner_signer: Signer | None = None,
    ) -> M:
        account = self._account()
        if owner_signer is None:
            _, signer = self._credentials()
            agent_wallet = self.agent_wallet
        else:
            signer = owner_signer
            agent_wallet = None
            if address(signer.address) != account:
                raise AuthenticationError("Agent management requires the account owner signer")
        async with self._mutation_lock:
            await self._rest.reserve(0.5 if operation.startswith("cancel") else 1.0)
            self._ensure_open()
            timestamp = max(time.time_ns() // 1_000_000, self._last_timestamp + 1)
            self._last_timestamp = timestamp
            body = await signed_request(
                operation,
                payload,
                account=account,
                signer=signer,
                agent_wallet=agent_wallet,
                timestamp=timestamp,
                expiry_window=self.config.expiry_window,
            )
            if time.time_ns() // 1_000_000 - timestamp >= self.config.expiry_window:
                raise AuthenticationError("Signing exceeded expiry_window; request was not sent")
            context = MutationContext(
                operation=operation,
                account=account,
                timestamp=timestamp,
                symbol=payload.get("symbol"),
                order_id=payload.get("order_id"),
                client_order_id=payload.get("client_order_id"),
            )
            response = await self._rest.post_reserved(path, body, context)
            # Official examples document flat acknowledgements and live APIs may wrap data.
            result = response.get("data", response)
            if result is None:
                # e.g. {"success": true, "data": null}: the flat acknowledgement holds it.
                result = {k: v for k, v in response.items() if k != "data"}
            if isinstance(result, dict) and (
                ("success" in result and result["success"] is not True)
                or result.get("error") is not None
            ):
                raise UnknownOutcomeError(context)
            if model is AgentList and isinstance(result, list):
                result = {"agents": result}
            if model is ApiConfigKeys and isinstance(result, list):
                result = {"active_api_keys": result}
            if model in (Cancellation, OperationResult) and isinstance(result, dict):
                if response.get("success") is True or (
                    model is Cancellation
                    and type(result.get("cancelled_count")) is int
                    and result["cancelled_count"] >= 0
                ):
                    result = {**result, "success": True}
            try:
                parsed = parse(model, result)
            except ProtocolError:
                raise UnknownOutcomeError(context) from None
            return parsed

    async def place_order(self, request: OrderRequest) -> OrderSubmission:
        self._credentials()
        if not isinstance(request, OrderRequest):
            raise TypeError("request must be an OrderRequest")
        # Revalidate even if a caller used Pydantic model_construct.
        request = OrderRequest.model_validate(request.model_dump())
        market = await self.get_market(request.symbol)
        if market.instrument_type not in (None, "perpetual"):
            raise ValueError("Trading wrapper currently supports perpetual markets")
        market.validate_quantity(request.quantity)
        valuation = request.price
        if valuation is not None:
            market.validate_price(valuation)
        else:
            valuation = (await self.get_price(request.symbol)).mark
        market.validate_notional(request.quantity, valuation, reduce_only=request.reduce_only)
        for stop in (request.take_profit, request.stop_loss):
            if stop is not None:
                market.validate_price(stop.stop_price)
                if stop.limit_price is not None:
                    market.validate_price(stop.limit_price)
                if stop.amount is not None:
                    market.validate_quantity(stop.amount)
        is_market = request.order_type == OrderType.MARKET
        result = await self._submit(
            "/orders/create_market" if is_market else "/orders/create",
            "create_market_order" if is_market else "create_order",
            request.payload(),
            OrderSubmission,
        )
        return result.model_copy(update={"client_order_id": str(request.client_order_id)})

    async def cancel_order(
        self,
        order_id: int | None = None,
        *,
        symbol: str,
        client_order_id: str | UUID | None = None,
    ) -> Cancellation:
        self._credentials()
        return await self._submit(
            "/orders/cancel",
            "cancel_order",
            {"symbol": validate_symbol(symbol), **order_identifier(order_id, client_order_id)},
            Cancellation,
        )

    async def cancel_all_orders(
        self, symbol: str | None = None, *, exclude_reduce_only: bool = False
    ) -> Cancellation:
        self._credentials()
        if type(exclude_reduce_only) is not bool:
            raise TypeError("exclude_reduce_only must be bool")
        payload: dict[str, Any] = {
            "all_symbols": symbol is None,
            "exclude_reduce_only": exclude_reduce_only,
        }
        if symbol is not None:
            payload["symbol"] = validate_symbol(symbol)
        return await self._submit("/orders/cancel_all", "cancel_all_orders", payload, Cancellation)

    async def edit_order(
        self,
        order_id: int | None = None,
        *,
        symbol: str,
        price: Decimal | str | int,
        quantity: Decimal | str | int | None = None,
        client_order_id: str | UUID | None = None,
    ) -> OrderSubmission:
        """Exchange cancels original and replaces with ALO/post-only and a new order ID."""
        self._credentials()
        payload = {"symbol": validate_symbol(symbol), **order_identifier(order_id, client_order_id)}
        market = await self.get_market(symbol)
        value = exact(price)
        market.validate_price(value)
        payload["price"] = decimal_string(value)
        if quantity is not None:
            amount = exact(quantity)
            market.validate_quantity(amount)
            payload["amount"] = decimal_string(amount)
        return await self._submit("/orders/edit", "edit_order", payload, OrderSubmission)

    async def update_leverage(self, symbol: str, leverage: int) -> OperationResult:
        self._credentials()
        positive_int(leverage, "leverage")
        market = await self.get_market(symbol)
        if leverage > market.max_leverage:
            raise ValueError("leverage exceeds the market maximum")
        return await self._submit(
            "/account/leverage",
            "update_leverage",
            {"symbol": symbol, "leverage": leverage},
            OperationResult,
        )

    async def register_agent(self, agent_wallet: str, owner_signer: Signer) -> OperationResult:
        return await self._submit(
            "/agent/bind",
            "bind_agent_wallet",
            {"agent_wallet": address(agent_wallet)},
            OperationResult,
            owner_signer=owner_signer,
        )

    async def revoke_agent(self, agent_wallet: str, owner_signer: Signer) -> OperationResult:
        return await self._submit(
            "/agent/revoke",
            "revoke_agent_wallet",
            {"agent_wallet": address(agent_wallet)},
            OperationResult,
            owner_signer=owner_signer,
        )

    async def list_agents(self, owner_signer: Signer) -> AgentList:
        return await self._submit(
            "/agent/list", "list_agent_wallets", {}, AgentList, owner_signer=owner_signer
        )

    async def create_api_config_key(self, signer: Signer | None = None) -> ApiConfigKey:
        """Create a rate-limit config key for ``PacificaConfig.api_config_key``.

        Signed by ``signer`` as the account owner, or, when omitted, by the client's
        configured signer (an agent wallet when ``agent_wallet`` is set). The key is
        returned only once: store it securely. Each account can hold up to 5 keys.

        Pacifica answers HTTP 500 until the account has been funded (deposited into),
        and an agent signer must be IP-whitelisted for the calling machine.
        """
        return await self._config_key_call(
            "/account/api_keys/create", "create_api_key", {}, ApiConfigKey, signer
        )

    async def revoke_api_config_key(
        self, api_key: str, signer: Signer | None = None
    ) -> OperationResult:
        if not isinstance(api_key, str) or not api_key or any(c.isspace() for c in api_key):
            raise ValueError("api_key must be a nonempty key without whitespace")
        return await self._config_key_call(
            "/account/api_keys/revoke",
            "revoke_api_key",
            {"api_key": api_key},
            OperationResult,
            signer,
        )

    async def list_api_config_keys(self, signer: Signer | None = None) -> ApiConfigKeys:
        return await self._config_key_call(
            "/account/api_keys", "list_api_keys", {}, ApiConfigKeys, signer
        )

    async def _config_key_call(
        self,
        path: str,
        operation: str,
        payload: dict[str, Any],
        model: type[M],
        signer: Signer | None,
    ) -> M:
        if signer is None:
            return await self._submit(path, operation, payload, model)
        return await self._submit(path, operation, payload, model, owner_signer=signer)

    async def update_margin_mode(self, symbol: str, *, isolated: bool) -> OperationResult:
        self._credentials()
        if type(isolated) is not bool:
            raise TypeError("isolated must be bool")
        market = await self.get_market(symbol)
        if market.isolated_only and not isolated:
            raise ValueError("This market requires isolated margin")
        return await self._submit(
            "/account/margin",
            "update_margin_mode",
            {"symbol": symbol, "is_isolated": isolated},
            OperationResult,
        )

    async def add_isolated_margin(
        self, symbol: str, amount: Decimal | str | int
    ) -> OperationResult:
        self._credentials()
        value = exact(amount)
        if value <= 0:
            raise ValueError("amount must be positive")
        return await self._submit(
            "/positions/add_isolated_margin",
            "add_isolated_margin",
            {"symbol": validate_symbol(symbol), "amount": decimal_string(value)},
            OperationResult,
        )

    async def set_position_tpsl(
        self,
        symbol: str,
        side: OrderSide | str,
        *,
        take_profit: StopOrder | None = None,
        stop_loss: StopOrder | None = None,
    ) -> OperationResult:
        """Set exit orders on an existing position; side is the closing order side."""
        self._credentials()
        selected_side = OrderSide(side)
        if take_profit is None and stop_loss is None:
            raise ValueError("Supply take_profit or stop_loss")
        market = await self.get_market(symbol)
        payload: dict[str, Any] = {"symbol": symbol, "side": selected_side.value}
        for name, stop in (("take_profit", take_profit), ("stop_loss", stop_loss)):
            if stop is not None:
                stop = StopOrder.model_validate(stop.model_dump())
                market.validate_price(stop.stop_price)
                if stop.limit_price is not None:
                    market.validate_price(stop.limit_price)
                if stop.amount is not None:
                    market.validate_quantity(stop.amount)
                payload[name] = stop.payload()
        return await self._submit("/positions/tpsl", "set_position_tpsl", payload, OperationResult)

    async def wait_for_order(
        self,
        order_id: int,
        *,
        timeout: float = 30,  # noqa: ASYNC109 -- enforced with asyncio.timeout
        poll_interval: float = 1,
        max_pages: int = 1,
    ) -> Order:
        positive_int(order_id, "order_id")
        for name, value in (("timeout", timeout), ("poll_interval", poll_interval)):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        try:
            async with asyncio.timeout(timeout):
                while True:
                    try:
                        order = await self.get_order(order_id, max_pages=max_pages)
                        if isinstance(order, Order) and order.terminal:
                            return order
                    except OrderNotFoundError:
                        pass
                    await asyncio.sleep(poll_interval)
        except TimeoutError:
            raise OrderWaitTimeoutError(f"Timed out waiting for order {order_id}") from None

    async def reconcile_submission(
        self, error: UnknownOutcomeError, *, max_pages: int = 10
    ) -> SubmissionResolution:
        """Read-only recovery. Absence is not rejection; never automatically resubmits."""
        context = error.context
        if context.account != self._account():
            raise ValueError("Unknown submission belongs to another account")
        positive_int(max_pages, "max_pages")
        found: list[Order | OpenOrder] = []
        fills: list[Fill] = []
        if context.order_id is not None or context.client_order_id is not None:
            try:
                found.append(
                    await self.get_order(
                        context.order_id,
                        client_order_id=context.client_order_id
                        if context.order_id is None
                        else None,
                        max_pages=max_pages,
                    )
                )
            except OrderNotFoundError:
                pass
        if context.operation in {"create_order", "create_market_order"}:
            async for fill in self.iter_trade_history(
                symbol=context.symbol,
                start_time=max(1, context.timestamp - 5000),
                max_pages=max_pages,
            ):
                if fill.client_order_id == context.client_order_id:
                    fills.append(fill)
            resolved = bool(found or fills)
        elif context.operation == "cancel_order":
            resolved = any(
                isinstance(row, Order) and row.order_status == "cancelled" for row in found
            )
        else:
            resolved = False
        return SubmissionResolution(orders=tuple(found), fills=tuple(fills), resolved=resolved)

    def stream(self, subscriptions: Sequence[Subscription]) -> PacificaStream:
        self._ensure_open()
        subscriptions = tuple(
            Subscription.model_validate(sub.model_dump()) for sub in subscriptions
        )
        for source in ("account_positions", "account_info"):
            scopes = {sub.account for sub in subscriptions if sub.source == source}
            if len(scopes) > 1:
                raise ValueError(
                    f"{source} omits account identity; use separate streams per account"
                )
        stream = PacificaStream(self.config, subscriptions, on_close=self._streams.discard)
        self._streams.add(stream)
        return stream

    def stream_prices(self) -> PacificaStream:
        return self.stream([Subscription(source="prices")])

    def stream_orderbook(self, symbol: str, *, agg_level: int = 1) -> PacificaStream:
        return self.stream([Subscription(source="book", symbol=symbol, agg_level=agg_level)])  # type: ignore[arg-type]

    def stream_bbo(self, symbol: str) -> PacificaStream:
        return self.stream([Subscription(source="bbo", symbol=symbol)])

    def stream_trades(self, symbol: str) -> PacificaStream:
        return self.stream([Subscription(source="trades", symbol=symbol)])

    def stream_positions(self, *, account: str | None = None) -> PacificaStream:
        return self.stream(
            [Subscription(source="account_positions", account=self._account(account))]
        )

    def stream_orders(self, *, account: str | None = None) -> PacificaStream:
        return self.stream(
            [Subscription(source="account_order_updates", account=self._account(account))]
        )

    def stream_fills(self, *, account: str | None = None) -> PacificaStream:
        return self.stream([Subscription(source="account_trades", account=self._account(account))])

    def stream_account_info(self, *, account: str | None = None) -> PacificaStream:
        return self.stream([Subscription(source="account_info", account=self._account(account))])

    def stream_account(self, *, account: str | None = None) -> PacificaStream:
        """One socket for position initialization, fills, order updates and risk metrics."""
        selected = self._account(account)
        return self.stream(
            [
                Subscription(source=source, account=selected)
                for source in (
                    "account_positions",
                    "account_trades",
                    "account_order_updates",
                    "account_info",
                )
            ]
        )
