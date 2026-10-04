"""Snapshot state helpers. They never turn a disconnected transport into fresh state."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType
from typing import cast

from .config import positive_int
from .exceptions import ProtocolError
from .models import ConnectionEvent, Fill, Orderbook, Position, PositionSnapshot, StreamEvent


class LocalPositions:
    """Full replacement position snapshots, including flat accounts and out-of-order suppression.

    This is the exchange's best-effort snapshot view, not an immediate fill-derived ledger.
    Consume account_trades separately for latency-sensitive exposure decisions.
    """

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}
        self.last_order_id: int | None = None
        self.generation: int | None = None
        self.stale = True

    @property
    def positions(self) -> Mapping[str, Position]:
        return MappingProxyType(self._positions)

    def signed_quantity(self, symbol: str) -> Decimal:
        if self.stale:
            raise ProtocolError("Position state is stale")
        row = self._positions.get(symbol)
        return row.signed_quantity if row is not None else Decimal(0)

    def replace(self, snapshot: PositionSnapshot) -> bool:
        if (
            self.last_order_id is not None
            and snapshot.last_order_id is not None
            and snapshot.last_order_id < self.last_order_id
        ):
            return False
        if self.last_order_id is not None and snapshot.last_order_id is None:
            raise ProtocolError("Cannot order an unsequenced snapshot after a sequenced snapshot")
        symbols = [row.symbol for row in snapshot.positions]
        if len(set(symbols)) != len(symbols):
            raise ProtocolError("Position snapshot contains duplicate symbols")
        self._positions = {row.symbol: row for row in snapshot.positions}
        self.last_order_id = snapshot.last_order_id
        self.stale = False
        return True

    def apply(self, event: StreamEvent) -> bool:
        if isinstance(event, ConnectionEvent):
            self.stale = True
            if event.state == "connected":
                self.generation = event.generation
                # Initialization snapshots are the new baseline after resubscription.
                self.last_order_id = None
            return False
        if event.channel != "account_positions":
            return False
        if self.generation != event.generation:
            self.stale = True
            raise ProtocolError("Position data belongs to an uninitialized connection generation")
        if not isinstance(event.data, tuple) or any(
            not isinstance(row, Position) for row in event.data
        ):
            raise ProtocolError("Position event contains invalid data")
        rows = cast(tuple[Position, ...], event.data)
        return self.replace(PositionSnapshot(positions=rows, last_order_id=event.li))


class LocalOrderbook:
    """Each Pacifica book update replaces the published depth."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.snapshot: Orderbook | None = None
        self.generation: int | None = None
        self.stale = True

    def apply(self, event: StreamEvent) -> bool:
        if isinstance(event, ConnectionEvent):
            self.stale = True
            if event.state == "connected":
                self.generation = event.generation
                self.snapshot = None
            return False
        if event.channel != "book":
            return False
        if not isinstance(event.data, Orderbook) or event.data.symbol != self.symbol:
            self.stale = True
            raise ProtocolError("Book event has the wrong symbol or shape")
        if event.generation != self.generation:
            self.stale = True
            raise ProtocolError("Book data belongs to an uninitialized connection generation")
        if self.snapshot is not None:
            if self.snapshot.li is not None and event.data.li is not None:
                if event.data.li < self.snapshot.li:
                    return False
            elif event.data.timestamp < self.snapshot.timestamp:
                return False
        self.snapshot = event.data
        self.stale = False
        return True


class ExposureTracker:
    """Fill-derived base exposure reconciled against full account position snapshots.

    Feed one account's stream_account() into this helper. Fills are deduplicated by
    history ID and retained until a snapshot covers them. Delayed snapshots cannot
    roll back later fills. A reconnect requires a new sequenced initialization
    snapshot; historical gaps still require application-level REST backfill.
    """

    def __init__(self, *, max_pending_fills: int = 4096) -> None:
        self.max_pending_fills = positive_int(max_pending_fills, "max_pending_fills")
        self.snapshots = LocalPositions()
        self._fills: dict[int, Fill] = {}
        self._quantities: dict[str, Decimal] = {}
        self.stale = True
        self._initialized = False

    @property
    def quantities(self) -> Mapping[str, Decimal]:
        return MappingProxyType(self._quantities)

    def signed_quantity(self, symbol: str) -> Decimal:
        if self.stale:
            raise ProtocolError("Exposure is stale; obtain a fresh initialization snapshot")
        return self._quantities.get(symbol, Decimal(0))

    def _watermark(self, symbol: str) -> int:
        row = self.snapshots.positions.get(symbol)
        if row is not None and row.li is not None:
            return row.li
        if self.snapshots.last_order_id is None:
            raise ProtocolError("Exposure reconstruction requires sequenced position snapshots")
        return self.snapshots.last_order_id

    def _rebuild(self) -> None:
        self._quantities = {
            symbol: row.signed_quantity for symbol, row in self.snapshots.positions.items()
        }
        retained: dict[int, Fill] = {}
        for history_id, fill in self._fills.items():
            assert fill.li is not None
            if fill.li > self._watermark(fill.symbol):
                self._quantities[fill.symbol] = (
                    self._quantities.get(fill.symbol, Decimal(0)) + fill.signed_delta
                )
                retained[history_id] = fill
        self._fills = retained

    def apply(self, event: StreamEvent) -> bool:
        if isinstance(event, ConnectionEvent):
            self.snapshots.apply(event)
            self.stale = True
            self._initialized = False
            self._fills.clear()
            return False
        if event.generation != self.snapshots.generation:
            self.stale = True
            raise ProtocolError("Exposure event belongs to an uninitialized generation")
        try:
            if event.channel == "account_positions":
                if event.li is None:
                    raise ProtocolError("Exposure reconstruction requires an envelope li")
                if not self.snapshots.apply(event):
                    return False
                self._initialized = True
                self._rebuild()
                self.stale = False
                return True
            if event.channel != "account_trades":
                return False
            if not isinstance(event.data, tuple):
                raise ProtocolError("Fill channel requires an array")
            changed = False
            for fill in event.data:
                if not isinstance(fill, Fill) or fill.li is None:
                    raise ProtocolError("Exposure reconstruction requires sequenced fills")
                if fill.history_id in self._fills:
                    if fill != self._fills[fill.history_id]:
                        raise ProtocolError("Conflicting fill records share a history ID")
                    continue
                if self._initialized and fill.li <= self._watermark(fill.symbol):
                    continue
                if len(self._fills) >= self.max_pending_fills:
                    raise ProtocolError("Fill journal exhausted; obtain a fresh baseline")
                # Validate unknown trade sides before altering state.
                _ = fill.signed_delta
                self._fills[fill.history_id] = fill
                changed = True
            if self._initialized and changed:
                self._rebuild()
            return changed
        except ProtocolError:
            self.stale = True
            raise
