# Public API reference

All methods below belong to `PacificaClient` unless stated otherwise.
REST methods are asynchronous. `stream_*()` returns a lazy `PacificaStream`;
`iter_*()` returns an asynchronous iterator.
Models are immutable Pydantic models and preserve additive unknown provider
fields except request/subscription models, which reject unsupported fields.
Numeric financial fields use exact `Decimal`.

## Explicit environment loading

`load_environment(*, mainnet, env_file=None, directory=None) -> dict[str, str]`
is a synchronous module-level helper, exported from `pacifica`. It selects
`.env` for mainnet and `.env.testnet` for testnet in `directory` (the current
working directory by default). `env_file` overrides the selected filename.
It returns Pacifica settings without changing `os.environ`; clients never
invoke it implicitly. Existing files are not merged with process credentials.
If the default file is absent, process variables can supply settings. Explicit
missing files and conflicting `PACIFICA_MAINNET` values raise errors.

## Market data

| Method | Return | Upstream route |
| --- | --- | --- |
| `get_markets()` | `tuple[Market, ...]` | `GET /info` |
| `get_market(symbol)` | `Market` | Select exact symbol from `/info` |
| `get_prices()` | `tuple[Price, ...]` | `GET /info/prices` |
| `get_price(symbol)` | `Price` | Select exact symbol from prices |
| `get_orderbook(symbol, agg_level=1)` | `Orderbook` | `GET /book` |
| `get_recent_trades(symbol=None)` | `tuple[Trade, ...]` | `GET /trades` |
| `get_funding_rate_history(symbol, limit=200, cursor=None)` | `HistoryPage[FundingRate]` | `GET /funding_rate/history` |
| `iter_funding_rate_history(symbol, page_size=200, max_pages=100)` | `AsyncIterator[FundingRate]` | Cursor traversal |

`agg_level` is one of `1, 10, 100, 1000, 10000`.
`Orderbook.bids`/`.asks` hold `BookLevel(price, amount, order_count)`.
`Price` exposes `mark`, `mid`, `oracle`, `funding`, `next_funding`,
`open_interest`, `timestamp` and optional daily statistics.
`yesterday_price=-1` is preserved when supplied by testnet.

## Account data

Each read accepts optional keyword `account`; it defaults to the client account.

| Method | Return |
| --- | --- |
| `get_account_info()` | `AccountInfo` |
| `get_account_settings()` | Provider settings `dict` |
| `get_positions()` | `tuple[Position, ...]` |
| `get_position(symbol)` | `Position | None` |
| `get_position_snapshot()` | `PositionSnapshot(positions, last_order_id)` |
| `get_open_orders()` | `tuple[OpenOrder, ...]` |
| `get_open_orders_snapshot()` | `OpenOrdersSnapshot(orders, last_order_id)` |
| `get_account_snapshot()` | `AccountSnapshot(account, info, positions, open_orders)` |
| `get_order_history(limit=100, cursor=None)` | `HistoryPage[Order]` |
| `get_trade_history(symbol=None, start_time=None, end_time=None, limit=100, cursor=None)` | `HistoryPage[Fill]` |
| `get_funding_history(limit=100, cursor=None)` | `HistoryPage[FundingPayment]` |
| `iter_order_history(page_size=100, max_pages=100)` | `AsyncIterator[Order]` |
| `iter_trade_history(symbol=None, start_time=None, end_time=None, page_size=100, max_pages=100)` | `AsyncIterator[Fill]` |
| `iter_funding_history(page_size=100, max_pages=100)` | `AsyncIterator[FundingPayment]` |
| `get_order(order_id=None, client_order_id=None, max_pages=3)` | `Order | OpenOrder` |

History timestamps are Unix **milliseconds**. `HistoryPage` has `data`,
`has_more` and `next_cursor`. Position/open-order responses preserve set-level
`last_order_id`, including an empty set.

`Position.signed_quantity` is positive for bids/longs and negative for asks/shorts.
`Fill.signed_delta` increases exposure for `open_long`/`close_short`, decreases
it for `open_short`/`close_long`, and raises for unknown sides.
`Order.terminal` recognizes filled, cancelled and rejected.
`OpenOrder` has no synthetic terminal status.

## Trading and account configuration

| Method | Return |
| --- | --- |
| `place_order(OrderRequest)` | `OrderSubmission(order_id, client_order_id)` |
| `cancel_order(order_id=None, symbol=..., client_order_id=None)` | `Cancellation` |
| `cancel_all_orders(symbol=None, exclude_reduce_only=False)` | `Cancellation` |
| `edit_order(order_id=None, symbol=..., price=..., quantity=None, client_order_id=None)` | `OrderSubmission` with replacement ID |
| `update_leverage(symbol, leverage)` | `OperationResult` |
| `update_margin_mode(symbol, isolated=...)` | `OperationResult` |
| `add_isolated_margin(symbol, amount)` | `OperationResult` |
| `set_position_tpsl(symbol, side, take_profit=None, stop_loss=None)` | `OperationResult` |
| `register_agent(agent_wallet, owner_signer)` | `OperationResult` |
| `revoke_agent(agent_wallet, owner_signer)` | `OperationResult` |
| `list_agents(owner_signer)` | `AgentList(agents)` |
| `wait_for_order(order_id, timeout=30, poll_interval=1, max_pages=1)` | Terminal `Order` |
| `reconcile_submission(UnknownOutcomeError, max_pages=10)` | `SubmissionResolution(orders, fills, resolved)` |

Order identifiers accept **exactly one** of positive integer exchange ID or a
full UUID client ID. No method silently resubmits an uncertain mutation.
Omitting `symbol` from cancel-all cancels across all symbols.

`OrderRequest`: `symbol`, `side` (`bid`/`ask`), `quantity`, `order_type`
(`limit` default or `market`), `price`, `time_in_force`, `reduce_only`,
`slippage_percent`, generated UUID `client_order_id`, optional `take_profit`
and `stop_loss`.
Limit requests require price; market requests require explicit positive
slippage percentage and forbid price/time-in-force options.
Post-only is represented by `TimeInForce.POST_ONLY` / `"ALO"`.

`StopOrder`: positive `stop_price`, optional positive `limit_price`, optional
base-unit `amount`, optional UUID `client_order_id`, `trigger_price_type`
(default `mark_price`; also `last_trade_price`/`mid_price`).
`set_position_tpsl` takes the **exit order side**, e.g. ask for a long.

## Streams and local state

| Method | Provider channels |
| --- | --- |
| `stream_prices()` | `prices` |
| `stream_orderbook(symbol, agg_level=1)` | `book` |
| `stream_bbo(symbol)` | `bbo` |
| `stream_trades(symbol)` | `trades` |
| `stream_positions(account=None)` | `account_positions` |
| `stream_orders(account=None)` | `account_order_updates` |
| `stream_fills(account=None)` | `account_trades` |
| `stream_account_info(account=None)` | `account_info` |
| `stream_account(account=None)` | All four account channels above on one socket |
| `stream(sequence_of_Subscription)` | Explicit multiplexed selection |

`Subscription` contains `source` and source-appropriate `symbol`, `account`,
`agg_level`. Up to 20 subscriptions per source per connection; duplicates are
rejected. Position/info subscriptions omit account identity in upstream data,
so those sources cannot mix multiple accounts on one stream.

`DataEvent(channel, data, generation, li)` uses normalized model names:
arrays contain `Price`, `Position`, `Order`, `Fill` or `Trade`; book/BBO/info
messages contain a single model. `generation` is a per-stream connection count.
`ConnectionEvent(state, generation, stale, reason)` reports connected/disconnected
transport states. Account state remains stale until independently initialized.

`LocalPositions.apply(event)` replaces the entire position set and orders
snapshots by envelope LI. `signed_quantity(symbol)` rejects stale state.
`LocalOrderbook(symbol).apply(event)` replaces published depth and tracks
staleness. Each book update replaces the published depth.
`ExposureTracker(max_pending_fills=4096).apply(event)` consumes one account's
position and fill channels, preserves uncovered fills through delayed snapshots
and exposes `signed_quantity(symbol)` and `quantities`.

## Errors

| Error | Meaning |
| --- | --- |
| `PacificaError` | Base SDK error |
| `APIError` | Definitive HTTP/provider rejection; status, provider code, request ID |
| `RateLimitError` | HTTP 429 with optional `retry_after` |
| `AuthenticationError` | Missing/mismatched signer, bad signature or signing expiry |
| `PrecisionError` | Invalid tick/lot/size boundary |
| `ProtocolError` | Malformed provider data, pagination/state inconsistency |
| `UnknownOutcomeError` | Mutation may have reached provider; non-secret `context` |
| `SubscriptionError` | Rejected or mismatched acknowledgement |
| `ReconnectExhaustedError` | Stream exhausted reconnect budget |
| `ClientClosedError` | Operation attempted after the client was closed |
| `TransportError` | Read exhausted its transport retry budget; also the base for reconnect exhaustion |
| `OrderNotFoundError` | Order absent within configured lookup bounds |
| `OrderWaitTimeoutError` | Poll deadline expired, without cancellation |

Invalid arguments/request models raise `ValueError`, `TypeError` or Pydantic
`ValidationError`. Exhausted GET transport failures retain HTTPX transport errors.
