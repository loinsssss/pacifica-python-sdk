"""Exact provider models; REST and abbreviated WebSocket fields share Python names."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Generic, Literal, TypeVar, get_args
from uuid import UUID, uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .config import validate_symbol
from .exceptions import PrecisionError, ProtocolError

Finite = Annotated[Decimal, Field(allow_inf_nan=False)]
Positive = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
Nonnegative = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
ID = Annotated[int, Field(strict=True, ge=0)]
T = TypeVar("T")


def alias(name: str, short: str, **kwargs: Any) -> Any:
    return Field(validation_alias=AliasChoices(name, short), **kwargs)


def exact(value: Decimal | str | int) -> Decimal:
    if isinstance(value, (float, bool)) or not isinstance(value, (Decimal, str, int)):
        raise PrecisionError("Use Decimal, decimal strings or integers, never floats")
    result = Decimal(value)
    if not result.is_finite():
        raise PrecisionError("Value must be finite")
    return result


def decimal_string(value: Decimal | str | int) -> str:
    return format(exact(value), "f")


def multiple(value: Decimal, step: Decimal, name: str) -> None:
    # Integer scaling avoids Decimal context rounding on high precision quantities.
    left, right = value.as_integer_ratio(), step.as_integer_ratio()
    numerator, denominator = left[0] * right[1], left[1] * right[0]
    if value <= 0 or numerator % denominator:
        raise PrecisionError(f"{name} must be positive and an exact multiple of {step}")


class WireModel(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)

    @field_validator("*", mode="before")
    @classmethod
    def no_floats(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, float):
            raise ValueError("Numeric fields must be exact strings or integers")
        if isinstance(value, bool) and info.field_name:
            annotation = cls.model_fields[info.field_name].annotation
            arguments = get_args(annotation)
            if (
                annotation is not bool
                and bool not in arguments
                and not any(type(argument) is bool for argument in arguments)
            ):
                raise ValueError("Booleans cannot represent numeric or string fields")
        return value


class OrderSide(StrEnum):
    BUY = "bid"
    SELL = "ask"


class OrderType(StrEnum):
    LIMIT = "limit"
    MARKET = "market"


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"
    POST_ONLY = "ALO"
    TOB = "TOB"


class Market(WireModel):
    symbol: str
    tick_size: Positive
    lot_size: Positive
    min_tick: Nonnegative
    max_tick: Positive
    min_order_size: Nonnegative
    max_order_size: Positive
    max_leverage: Annotated[int, Field(strict=True, gt=0)]
    isolated_only: bool
    funding_rate: Finite | None = None
    next_funding_rate: Finite | None = None
    instrument_type: str | None = None
    base_asset: str | None = None

    def validate_quantity(self, quantity: Decimal) -> None:
        multiple(quantity, self.lot_size, "quantity")

    def validate_price(self, price: Decimal) -> None:
        multiple(price, self.tick_size, "price")
        if not self.min_tick <= price <= self.max_tick:
            raise PrecisionError("price lies outside market bounds")

    def validate_notional(self, quantity: Decimal, price: Decimal, *, reduce_only: bool) -> None:
        notional = quantity * price
        if notional > self.max_order_size:
            raise PrecisionError("order notional exceeds maximum USD order size")
        # Closing dust is allowed for reduce-only orders.
        if not reduce_only and notional < self.min_order_size:
            raise PrecisionError("order notional is below minimum USD order size")


class Price(WireModel):
    symbol: str
    mark: Positive
    mid: Nonnegative
    oracle: Positive
    funding: Finite
    next_funding: Finite
    open_interest: Nonnegative
    timestamp: ID
    volume_24h: Nonnegative | None = None
    # Live testnet uses -1 when previous-day price is unavailable; preserve the sentinel.
    yesterday_price: Finite | None = None


class BookLevel(WireModel):
    price: Positive = alias("price", "p")
    amount: Nonnegative = alias("amount", "a")
    order_count: ID = alias("order_count", "n")


class Orderbook(WireModel):
    symbol: str = alias("symbol", "s")
    levels: tuple[tuple[BookLevel, ...], tuple[BookLevel, ...]] = alias("levels", "l")
    timestamp: ID = alias("timestamp", "t")
    li: ID | None = None

    @property
    def bids(self) -> tuple[BookLevel, ...]:
        return self.levels[0]

    @property
    def asks(self) -> tuple[BookLevel, ...]:
        return self.levels[1]


class BestBidOffer(WireModel):
    symbol: str = alias("symbol", "s")
    bid_price: Nonnegative = alias("bid_price", "b")
    bid_amount: Nonnegative = alias("bid_amount", "B")
    ask_price: Nonnegative = alias("ask_price", "a")
    ask_amount: Nonnegative = alias("ask_amount", "A")
    timestamp: ID = alias("timestamp", "t")
    li: ID | None = None


class SpotBalance(WireModel):
    symbol: str = alias("symbol", "s")
    amount: Finite = alias("amount", "a")
    available_to_withdraw: Finite = alias("available_to_withdraw", "aw")
    pending_balance: Finite = alias("pending_balance", "pb")


class AccountInfo(WireModel):
    balance: Finite = alias("balance", "b")
    account_equity: Finite = alias("account_equity", "ae")
    available_to_spend: Finite = alias("available_to_spend", "as")
    available_to_withdraw: Finite = alias("available_to_withdraw", "aw")
    fee_level: ID = alias("fee_level", "f")
    total_margin_used: Finite = alias("total_margin_used", "mu")
    cross_mmr: Finite = alias("cross_mmr", "cm")
    pending_balance: Finite = alias("pending_balance", "pb")
    positions_count: ID = alias("positions_count", "pc")
    orders_count: ID = alias("orders_count", "oc")
    stop_orders_count: ID = alias("stop_orders_count", "sc")
    updated_at: ID = alias("updated_at", "t")
    maker_fee: Finite | None = None
    taker_fee: Finite | None = None
    spot_balances: tuple[SpotBalance, ...] = alias("spot_balances", "sb", default=())


class Position(WireModel):
    symbol: str = alias("symbol", "s")
    side: OrderSide = alias("side", "d")
    amount: Nonnegative = alias("amount", "a")
    entry_price: Nonnegative = alias("entry_price", "p")
    margin: Finite | None = alias("margin", "m", default=None)
    funding: Finite = alias("funding", "f")
    isolated: bool = alias("isolated", "i")
    liquidation_price: Finite | None = alias("liquidation_price", "l", default=None)
    updated_at: ID = alias("updated_at", "t")
    li: ID | None = None

    @property
    def signed_quantity(self) -> Decimal:
        return self.amount if self.side == OrderSide.BUY else -self.amount


class PositionSnapshot(WireModel):
    positions: tuple[Position, ...]
    last_order_id: ID | None = None


class OpenOrder(WireModel):
    order_id: ID
    client_order_id: str | None = None
    symbol: str
    side: OrderSide
    price: Nonnegative
    initial_amount: Nonnegative
    filled_amount: Nonnegative
    cancelled_amount: Nonnegative
    reduce_only: bool
    order_type: str
    created_at: ID
    updated_at: ID
    li: ID | None = None


class OpenOrdersSnapshot(WireModel):
    orders: tuple[OpenOrder, ...]
    last_order_id: ID | None = None


class Order(WireModel):
    order_id: ID = alias("order_id", "i")
    client_order_id: str | None = alias("client_order_id", "I", default=None)
    account: str | None = alias("account", "u", default=None)
    symbol: str = alias("symbol", "s")
    side: OrderSide = alias("side", "d")
    initial_price: Nonnegative = alias("initial_price", "ip")
    average_filled_price: Nonnegative | None = alias("average_filled_price", "p", default=None)
    amount: Nonnegative = alias("amount", "a")
    filled_amount: Nonnegative = alias("filled_amount", "f")
    order_status: str = alias("order_status", "os")
    order_type: str = alias("order_type", "ot")
    reduce_only: bool = alias("reduce_only", "r")
    created_at: ID = alias("created_at", "ct")
    updated_at: ID = alias("updated_at", "ut")
    li: ID | None = None

    @property
    def terminal(self) -> bool:
        return self.order_status in {"filled", "cancelled", "rejected"}


class Trade(WireModel):
    history_id: ID | None = alias("history_id", "h", default=None)
    symbol: str | None = alias("symbol", "s", default=None)
    amount: Nonnegative = alias("amount", "a")
    price: Nonnegative = alias("price", "p")
    side: str = Field(validation_alias=AliasChoices("side", "ts", "d"))
    cause: str = alias("cause", "tc")
    created_at: ID = alias("created_at", "t")
    li: ID | None = None

    @property
    def signed_delta(self) -> Decimal:
        if self.side in {"open_long", "close_short"}:
            return self.amount
        if self.side in {"open_short", "close_long"}:
            return -self.amount
        raise ProtocolError(f"Unknown trade side {self.side!r}; cannot infer exposure")


class Fill(Trade):
    history_id: ID = alias("history_id", "h")
    order_id: ID = alias("order_id", "i")
    client_order_id: str | None = alias("client_order_id", "I", default=None)
    account: str | None = alias("account", "u", default=None)
    symbol: str = alias("symbol", "s")
    entry_price: Nonnegative = alias("entry_price", "o")
    fee: Finite = alias("fee", "f")
    pnl: Finite = alias("pnl", "n")
    event_type: str = alias("event_type", "te")


class FundingPayment(WireModel):
    history_id: ID
    symbol: str
    side: OrderSide
    amount: Nonnegative
    payout: Finite
    rate: Finite
    created_at: ID


class FundingRate(WireModel):
    oracle_price: Positive
    bid_impact_price: Nonnegative
    ask_impact_price: Nonnegative
    funding_rate: Finite
    next_funding_rate: Finite
    created_at: ID


CandleInterval = Literal[
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "1w", "1M"
]


class Candle(WireModel):
    """OHLCV bucket [start_time, end_time) in milliseconds. Buckets without trades are omitted."""

    start_time: ID = alias("start_time", "t")
    end_time: ID = alias("end_time", "T")
    symbol: str = alias("symbol", "s")
    interval: CandleInterval = alias("interval", "i")
    open: Positive = alias("open", "o")
    high: Positive = alias("high", "h")
    low: Positive = alias("low", "l")
    close: Positive = alias("close", "c")
    volume: Nonnegative = alias("volume", "v")
    trades: ID = alias("trades", "n")


class HistoryPage(WireModel, Generic[T]):
    data: tuple[T, ...]
    next_cursor: str | None = None
    has_more: bool

    @model_validator(mode="after")
    def valid_cursor(self) -> HistoryPage[T]:
        if self.has_more and not self.next_cursor:
            raise ValueError("has_more requires a nonempty next_cursor")
        return self


class StopOrder(WireModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    stop_price: Positive
    limit_price: Positive | None = None
    amount: Positive | None = None
    client_order_id: UUID | None = None
    trigger_price_type: Literal["mark_price", "last_trade_price", "mid_price"] = "mark_price"

    def payload(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", exclude_none=True)
        for key in ("stop_price", "limit_price", "amount"):
            if getattr(self, key) is not None:
                data[key] = decimal_string(getattr(self, key))
        return data


class OrderRequest(WireModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    symbol: str
    side: OrderSide
    quantity: Positive
    order_type: OrderType = OrderType.LIMIT
    price: Positive | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = Field(default=False, strict=True)
    slippage_percent: Annotated[Decimal, Field(gt=0, le=100, allow_inf_nan=False)] | None = None
    client_order_id: UUID = Field(default_factory=uuid4)
    take_profit: StopOrder | None = None
    stop_loss: StopOrder | None = None

    @field_validator("symbol")
    @classmethod
    def valid_symbol(cls, value: str) -> str:
        return validate_symbol(value)

    @model_validator(mode="after")
    def valid_order(self) -> OrderRequest:
        if self.order_type == OrderType.LIMIT:
            if self.price is None or self.slippage_percent is not None:
                raise ValueError("Limit orders require price and do not accept slippage_percent")
        else:
            if self.slippage_percent is None or self.price is not None:
                raise ValueError("Market orders require slippage_percent and do not accept price")
            if self.time_in_force != TimeInForce.GTC:
                raise ValueError("Market endpoint has no time-in-force field")
        return self

    def payload(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "symbol": self.symbol,
            "side": self.side.value,
            "amount": decimal_string(self.quantity),
            "reduce_only": self.reduce_only,
            "client_order_id": str(self.client_order_id),
        }
        if self.order_type == OrderType.LIMIT:
            assert self.price is not None
            data.update(price=decimal_string(self.price), tif=self.time_in_force.value)
        else:
            assert self.slippage_percent is not None
            data["slippage_percent"] = decimal_string(self.slippage_percent)
        for name in ("take_profit", "stop_loss"):
            stop = getattr(self, name)
            if stop is not None:
                data[name] = stop.payload()
        return data


class OrderSubmission(WireModel):
    order_id: Annotated[int, Field(strict=True, gt=0)]
    client_order_id: str | None = None


class Cancellation(WireModel):
    success: Literal[True]
    cancelled_count: ID | None = None


class OperationResult(WireModel):
    """Provider acknowledgement preserving any extra fields."""

    success: Literal[True]


class AgentList(WireModel):
    agents: tuple[dict[str, Any], ...]


class ApiConfigKey(WireModel):
    """A rate-limit key for the ``PF-API-KEY`` header; it carries no trading authority."""

    api_key: str


class ApiConfigKeys(WireModel):
    """The account's active config keys (at most 5). Unknown provider fields are kept."""

    active_api_keys: tuple[str, ...] = ()
    api_key_limit: int | None = None


class AccountSnapshot(WireModel):
    account: str
    info: AccountInfo
    positions: PositionSnapshot
    open_orders: OpenOrdersSnapshot


class SubmissionResolution(WireModel):
    orders: tuple[Order | OpenOrder, ...]
    fills: tuple[Fill, ...]
    resolved: bool


class Subscription(WireModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal[
        "prices",
        "book",
        "bbo",
        "trades",
        "account_info",
        "account_positions",
        "account_order_updates",
        "account_trades",
    ]
    symbol: str | None = None
    account: str | None = None
    agg_level: Literal[1, 10, 100, 1000, 10000] | None = None

    @model_validator(mode="after")
    def valid_scope(self) -> Subscription:
        from .signing import address

        if self.source in {"book", "bbo", "trades"}:
            if self.symbol is None:
                raise ValueError("Market subscription requires symbol")
            validate_symbol(self.symbol)
            if self.account is not None:
                raise ValueError("Market subscription cannot include account")
        elif self.source.startswith("account_"):
            if self.account is None:
                raise ValueError("Account subscription requires account")
            address(self.account)
            if self.symbol is not None:
                raise ValueError("Account subscriptions cover all symbols")
        elif self.symbol is not None or self.account is not None:
            raise ValueError("Prices subscription covers all symbols")
        if self.source != "book" and self.agg_level is not None:
            raise ValueError("agg_level is only valid for book")
        return self

    def params(self) -> dict[str, Any]:
        result = self.model_dump(exclude_none=True)
        if self.source == "book" and self.agg_level is None:
            result["agg_level"] = 1
        return result


class ConnectionEvent(WireModel):
    state: Literal["connected", "disconnected", "closed"]
    generation: ID
    stale: bool = True
    reason: str | None = None


class DataEvent(WireModel):
    channel: str
    data: (
        tuple[Price | Position | Order | Fill | Trade, ...] | Orderbook | BestBidOffer | AccountInfo
    )
    generation: ID
    li: ID | None = None


StreamEvent = ConnectionEvent | DataEvent
