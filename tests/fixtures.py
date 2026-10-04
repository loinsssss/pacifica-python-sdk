"""Synthetic values following Pacifica's official documented wire examples."""

from pacifica import LocalSigner

OWNER = LocalSigner.from_seed(bytes(range(32)))
AGENT = LocalSigner.from_seed(bytes(range(32, 64)))
ACCOUNT = OWNER.address
CLOID = "12345678-1234-1234-1234-123456789abc"

MARKET = {
    "symbol": "BTC",
    "tick_size": "1",
    "min_tick": "0",
    "max_tick": "1000000",
    "lot_size": "0.00001",
    "max_leverage": 50,
    "isolated_only": False,
    "min_order_size": "10",
    "max_order_size": "5000000",
    "instrument_type": "perpetual",
}
PRICE = {
    "symbol": "BTC",
    "mark": "60000",
    "mid": "60000",
    "oracle": "60000",
    "funding": "0.0000125",
    "next_funding": "-0.00001",
    "open_interest": "100",
    "timestamp": 1700000000000,
    "yesterday_price": "-1",
}
POSITION = {
    "symbol": "BTC",
    "side": "bid",
    "amount": "0.001",
    "entry_price": "60000",
    "margin": "0",
    "funding": "-0.01",
    "isolated": False,
    "liquidation_price": None,
    "updated_at": 1700000000000,
    "li": 10,
}
WS_POSITION = {
    "s": "BTC",
    "d": "bid",
    "a": "0.001",
    "p": "60000",
    "m": "0",
    "f": "-0.01",
    "i": False,
    "l": None,
    "t": 1700000000000,
    "li": 10,
}
ACCOUNT_INFO = {
    "balance": "2000",
    "account_equity": "2010",
    "available_to_spend": "1800",
    "available_to_withdraw": "1800",
    "fee_level": 0,
    "total_margin_used": "210",
    "cross_mmr": "20",
    "pending_balance": "0",
    "positions_count": 1,
    "orders_count": 1,
    "stop_orders_count": 0,
    "updated_at": 1700000000000,
}
OPEN_ORDER = {
    "order_id": 123,
    "client_order_id": CLOID,
    "symbol": "BTC",
    "side": "bid",
    "price": "60000",
    "initial_amount": "0.001",
    "filled_amount": "0",
    "cancelled_amount": "0",
    "reduce_only": False,
    "order_type": "limit",
    "created_at": 1700000000000,
    "updated_at": 1700000000000,
    "li": 10,
}
ORDER = {
    "order_id": 123,
    "client_order_id": CLOID,
    "symbol": "BTC",
    "side": "bid",
    "initial_price": "60000",
    "average_filled_price": "60000",
    "amount": "0.001",
    "filled_amount": "0.001",
    "order_status": "filled",
    "order_type": "limit",
    "reduce_only": False,
    "created_at": 1700000000000,
    "updated_at": 1700000000001,
}
FILL = {
    "history_id": 50,
    "order_id": 123,
    "client_order_id": CLOID,
    "symbol": "BTC",
    "price": "60000",
    "entry_price": "60000",
    "amount": "0.001",
    "fee": "0.01",
    "pnl": "0",
    "event_type": "fulfill_taker",
    "side": "open_long",
    "cause": "normal",
    "created_at": 1700000000001,
    "li": 11,
}
WS_FILL = {
    "h": 50,
    "i": 123,
    "I": CLOID,
    "u": ACCOUNT,
    "s": "BTC",
    "p": "60000",
    "o": "60000",
    "a": "0.001",
    "te": "fulfill_taker",
    "ts": "open_long",
    "tc": "normal",
    "f": "0.01",
    "n": "0",
    "t": 1700000000001,
    "li": 11,
}
BOOK = {
    "s": "BTC",
    "l": [[{"p": "59999", "a": "1", "n": 1}], [{"p": "60001", "a": "2", "n": 2}]],
    "t": 1700000000000,
    "li": 10,
}
