from decimal import Decimal

import pytest

from pacifica import (
    ConnectionEvent,
    ExposureTracker,
    LocalOrderbook,
    LocalPositions,
    ProtocolError,
)
from pacifica.websocket import parse_event
from tests.fixtures import BOOK, WS_FILL, WS_POSITION


def connected(generation=1):
    return ConnectionEvent(state="connected", generation=generation)


def positions(rows=None, li=10, generation=1):
    return parse_event(
        {
            "channel": "account_positions",
            "data": [WS_POSITION] if rows is None else rows,
            "li": li,
        },
        generation,
    )


def fills(rows=None, generation=1):
    return parse_event(
        {
            "channel": "account_trades",
            "data": [WS_FILL] if rows is None else rows,
        },
        generation,
    )


def test_snapshot_replacement_closure_and_staleness():
    state = LocalPositions()
    state.apply(connected())
    assert state.apply(positions())
    assert state.signed_quantity("BTC") == Decimal("0.001")
    assert not state.apply(positions([], li=9))
    assert state.signed_quantity("BTC") == Decimal("0.001")
    assert state.apply(positions([], li=11))
    assert state.signed_quantity("BTC") == 0
    state.apply(ConnectionEvent(state="disconnected", generation=1))
    with pytest.raises(ProtocolError, match="stale"):
        state.signed_quantity("BTC")
    state.apply(connected(2))
    state.apply(positions(li=1, generation=2))
    assert not state.stale


def test_unsequenced_empty_snapshot_is_observable_but_not_a_fill_ledger_baseline():
    event = positions([], li=None)
    state = LocalPositions()
    state.apply(connected())
    assert state.apply(event)
    assert state.signed_quantity("BTC") == 0
    tracker = ExposureTracker()
    tracker.apply(connected())
    with pytest.raises(ProtocolError, match="envelope li"):
        tracker.apply(event)
    assert tracker.stale


def test_local_book_is_a_snapshot_not_delta():
    book = LocalOrderbook("BTC")
    book.apply(connected())
    book.apply(parse_event({"channel": "book", "data": BOOK}, 1))
    assert len(book.snapshot.bids) == 1
    empty = {**BOOK, "l": [[], []], "li": 11}
    book.apply(parse_event({"channel": "book", "data": empty}, 1))
    assert book.snapshot.bids == ()
    assert not book.apply(parse_event({"channel": "book", "data": BOOK}, 1))
    book.apply(ConnectionEvent(state="disconnected", generation=1))
    assert book.stale


def test_fills_do_not_double_count_and_snapshots_cannot_roll_back_exposure():
    tracker = ExposureTracker()
    tracker.apply(connected())
    tracker.apply(positions())
    tracker.apply(fills())
    assert tracker.signed_quantity("BTC") == Decimal("0.002")
    assert not tracker.apply(fills())
    tracker.apply(positions())
    assert tracker.signed_quantity("BTC") == Decimal("0.002")
    # Snapshot now incorporates that fill; journal prunes it.
    tracker.apply(positions([{**WS_POSITION, "a": "0.002", "li": 11}], li=11))
    assert tracker.signed_quantity("BTC") == Decimal("0.002")
    assert not tracker.apply(fills())
    # Closing fill is immediate; later empty snapshot confirms a flat account.
    tracker.apply(fills([{**WS_FILL, "h": 51, "a": "0.002", "ts": "close_long", "li": 12}]))
    assert tracker.signed_quantity("BTC") == 0
    tracker.apply(positions([], li=12))
    assert tracker.signed_quantity("BTC") == 0


def test_fills_before_initial_snapshot_and_same_li_distinct_history_ids():
    tracker = ExposureTracker()
    tracker.apply(connected())
    tracker.apply(fills())
    assert tracker.stale
    tracker.apply(fills([{**WS_FILL, "h": 51, "a": "0.002"}]))
    tracker.apply(positions())
    assert tracker.signed_quantity("BTC") == Decimal("0.004")


def test_exposure_reconnect_uses_new_baseline():
    tracker = ExposureTracker()
    tracker.apply(connected())
    tracker.apply(positions())
    tracker.apply(fills())
    tracker.apply(ConnectionEvent(state="disconnected", generation=1))
    with pytest.raises(ProtocolError):
        tracker.signed_quantity("BTC")
    tracker.apply(connected(2))
    tracker.apply(positions([], li=20, generation=2))
    assert tracker.signed_quantity("BTC") == 0


def test_unknown_li_or_side_and_journal_overflow_mark_exposure_stale():
    for invalid in [
        {**WS_FILL, "li": None},
        {**WS_FILL, "ts": "future_unknown_side"},
    ]:
        tracker = ExposureTracker()
        tracker.apply(connected())
        tracker.apply(positions())
        with pytest.raises(ProtocolError):
            tracker.apply(fills([invalid]))
        assert tracker.stale
    tracker = ExposureTracker(max_pending_fills=1)
    tracker.apply(connected())
    tracker.apply(fills())
    with pytest.raises(ProtocolError, match="journal"):
        tracker.apply(fills([{**WS_FILL, "h": 51}]))
    assert tracker.stale


def test_symbol_scopes_and_out_of_order_fills():
    tracker = ExposureTracker()
    tracker.apply(connected())
    # Different symbols' row LI values are never compared with each other.
    tracker.apply(
        positions(
            [
                WS_POSITION,
                {**WS_POSITION, "s": "ETH", "li": 1000, "a": "1"},
            ],
            li=1000,
        )
    )
    tracker.apply(fills([{**WS_FILL, "li": 12, "h": 52}]))
    tracker.apply(fills([{**WS_FILL, "li": 11, "h": 51}]))
    assert tracker.signed_quantity("BTC") == Decimal("0.003")
    assert tracker.signed_quantity("ETH") == 1
