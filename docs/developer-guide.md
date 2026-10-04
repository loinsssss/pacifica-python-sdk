# Developer guide

## Setup and configuration

Use Python 3.11 or later and install this folder with `pip install -e ".[dev]"`.
The included development environment was created with Python 3.12.14.
Imports never connect, create files, load dotenv or read credentials.
Construct clients inside application startup and close them with `async with`.

```python
from pacifica import PacificaClient, PacificaConfig

config = PacificaConfig.testnet(
    request_timeout=15,
    api_config_key=None,
)
client = PacificaClient(config)
```

Environment endpoints:

| Environment | REST base, including version prefix | WebSocket |
| --- | --- | --- |
| Mainnet | `https://api.pacifica.fi/api/v1` | `wss://ws.pacifica.fi/ws` |
| Testnet | `https://test-api.pacifica.fi/api/v1` | `wss://test-ws.pacifica.fi/ws` |

You can override `base_url` and `websocket_url` for proxies or local fixtures.
Account addresses and signer keys are separate from endpoint selection; select
the environment explicitly when configuring each account.
`.env.example` lists blank credential values for your application to load.
Reserve `.env` for mainnet and `.env.testnet` for testnet. The bundled examples
read `.env` by default; `--testnet` selects `.env.testnet`. Account tests default
to `.env.testnet`, with `--mainnet` selecting `.env`. Both accept `--env-file`.
Set `PACIFICA_MAINNET` to match the selection, or omit it; conflicting values
are rejected. Files supply all Pacifica settings without merging process
credentials. If a default file is missing, process variables are used.

Applications can explicitly call `load_environment(mainnet=False)` to get
testnet settings from `.env.testnet` in the working directory, then pass those
values into `PacificaConfig`, `LocalSigner` and `PacificaClient`. Use
`mainnet=True` for `.env`. The loader does not modify the process environment
or execute shell code. Normal client construction continues to use only its
explicit arguments.

| Configuration | Default | Meaning |
| --- | --- | --- |
| `mainnet` | `True` | Select environment defaults |
| `api_config_key` | `None` | Optional `PF-API-KEY` header; a quota key, not trading authority |
| `request_timeout` | `10` | HTTP request timeout, seconds |
| `max_read_retries` | `2` | Additional attempts for transient GET failures |
| `retry_delay`, `max_retry_delay` | `0.25`, `5` | Exponential retry delay, seconds |
| `expiry_window` | `5000` | Signature validity, milliseconds |
| `rest_credits_per_minute` | `100` | Local rolling 60-second credit budget; `None` disables pacing |
| `subscription_timeout` | `10` | Connection/subscription establishment timeout |
| `heartbeat_interval`, `heartbeat_timeout` | `20`, `10` | JSON ping cadence and pong deadline |
| `max_reconnect_attempts` | `3` | Consecutive reconnect attempts before terminal failure |
| `reconnect_delay`, `max_reconnect_delay` | `0.5`, `5` | Reconnect backoff, seconds |
| `websocket_max_queue` | `32` | WebSocket library receive-frame high-water mark |
| `websocket_max_size` | `8388608` | Maximum incoming message bytes |
| `max_pending_messages` | `128` | Data buffered while awaiting subscription acknowledgements |

Standard GETs consume one local credit. History GETs conservatively consume
12 without a config key, or 4 with one; cancellations consume 0.5 and other
mutations one. The local budget defaults to 100 even with a config key; set it
to your verified quota if you need more. Coordinate other clients/processes
sharing the same IP/account quota in the consuming application.
Reservations occur before signing to avoid waiting on a quota with an expiring
signature. HTTP `Retry-After` is honored within the configured delay budget.

## Account reads and signing

`account` is a canonical base58 Solana public key. Read-only account data needs
no signer. For owner trading, provide `LocalSigner(private_key)` and omit
`agent_wallet`. A base58 private key must encode a 64-byte Solana keypair;
`LocalSigner.from_seed()` explicitly accepts a 32-byte seed.

For an agent, use the owner's account address for `account`, the agent's key
for `signer`, and its public key for `agent_wallet`. The SDK verifies this
relationship and checks every supplied signature locally.
The agent must already be bound in the chosen environment.

```python
# Explicitly bind a supplied agent with the owner's signer; this mutates account settings.
await client.register_agent(agent_public_key, owner_signer)
# Explicitly inspect or revoke it.
agents = await client.list_agents(owner_signer)
await client.revoke_agent(agent_public_key, owner_signer)
```

Keys remain in memory. The SDK does not log signed request bodies or include
them in recovery errors. Keep secrets out of repository files and logs.
API config keys are hidden from config `repr`.
The SDK covers trading and account data; deposits, withdrawals and fund
transfers are outside its current API.

## Orders and risk controls

Use exchange market metadata before choosing quantities/prices. `lot_size` is
base-unit quantity precision; `tick_size` is price precision. `min_order_size`
and `max_order_size` are **USD notional**, not base quantity. Invalid precision
is rejected rather than rounded. Reduce-only orders allow minimum-notional dust
closes; provider checks remain authoritative.

The trading wrapper targets perpetual markets. Market-data methods also return
other instrument types, preserving provider metadata.
Markets are fetched afresh for order validation.
Market-order notional preflight uses a fresh mark price, so actual execution
and exchange acceptance can differ as prices move.

```python
from pacifica import OrderRequest, StopOrder

limit = OrderRequest(
    symbol="BTC",
    side="ask",
    quantity="0.001",
    price="60000",
    time_in_force="IOC",
    reduce_only=True,
)
market = OrderRequest(
    symbol="BTC",
    side="bid",
    quantity="0.001",
    order_type="market",
    slippage_percent="0.5",
)
stop = StopOrder(stop_price="59000", trigger_price_type="mark_price")
# On an existing long position, closing orders are asks:
await client.set_position_tpsl("BTC", "ask", stop_loss=stop)
```

`GTC`, `IOC`, `ALO` (post-only) and `TOB` are supported limit time-in-force values.
The market endpoint has no time-in-force field. TP/SL can also be attached to
new orders using `take_profit`/`stop_loss`. Omitted stop amount uses the
provider's full-position behavior.

`edit_order()` cancels the original and creates an ALO replacement with a new
exchange ID. `update_leverage()`, `update_margin_mode()` and
`add_isolated_margin()` expose account/position risk configuration.
The exchange restricts margin-mode changes on open positions and leverage
decreases on open positions.

## Uncertain outcomes and history

One `OrderRequest` owns a stable UUID. Persist it before submission if recovery
must survive process restart. A UUID does not establish idempotency.
`UnknownOutcomeError.context` includes operation, account, timestamp and relevant
symbol/order/client identifiers without credentials.

`reconcile_submission()` queries order state and, for placement, fill history.
It never resubmits. `resolved=True` means placement was observed, or a targeted
cancellation was observed as cancelled. It does not mean a placement fully filled.
An absent order is not proof of rejection. Edit, cancel-all and account-setting
uncertainties require direct current-state inspection.

There is no assumed direct `/orders/{id}` route. `get_order()` searches open
orders then bounded cursor-paginated history (default three pages).
`OrderNotFoundError` means absent within those bounds. Increase `max_pages` for
older orders. `wait_for_order()` polls within a deadline and never cancels on timeout;
for active trading prefer order-update/fill streams to expensive historical polling.

`iter_*_history()` methods reject repeated cursors and raise when `max_pages`
is exhausted. They do not silently truncate. SDK page caps are 1000 for account
history and 4000 for funding-rate history; upstream limits may be stricter.
History pages and concurrent snapshots can change while you paginate.

## Stream lifecycle and recovery

Streams are lazy: a socket opens on first iteration. Use an async context manager
even if you break early. Each stream supports one concurrent reader and owns one
socket; client closure closes its streams. Combine subscriptions through
`stream()` or `stream_account()` to reduce connection overhead.

Every successful subscription generation first emits `ConnectionEvent`.
`connected` deliberately carries `stale=True`: an acknowledgement is not a
fresh account view. `ExposureTracker` waits for a sequenced position snapshot,
buffers pre-initialization fills, and replays uncovered fills. `LocalPositions`
instead represents only the exchange's best-effort snapshot.

Transport loss emits a stale disconnect event, reconnects and resubscribes.
Heartbeats use Pacifica JSON ping/pong, not just protocol control frames.
Authorization/subscription rejection, malformed data and scope violations are
terminal; the caller must also invalidate state on any terminal exception.
The reconnect budget resets after data, not an acknowledgement alone.

On recovery, a fresh snapshot restores **current exposure**, but does not
restore a durable execution/PnL history. Backfill REST fills from a persisted
time checkpoint and deduplicate by history ID. Fill replay is not guaranteed.
Do not compare different symbols' row LI values. The SDK compares position-set
envelope LI only against that same account's prior snapshots.

`ExposureTracker` retains fills until a snapshot covers them, with a default
4096-record limit. Journal exhaustion or unsequenced/unknown-side fills marks
state stale and raises `ProtocolError`; reopen the stream for a new baseline.
It derives signed base quantity only, not average entry price or realized PnL.
Persist/checkpoint these in your application using provider fills and risk snapshots.

Live testnet initialization for a newly funded flat account can omit the
snapshot envelope LI entirely, and the REST position/order snapshots can omit
`last_order_id`. `ExposureTracker` cannot establish a fill-reconciliation
baseline without those labels. Use `LocalPositions` for the best-effort
snapshot view and consume fill events separately, as demonstrated by
`examples/account_stream.py`; do not manufacture sequence labels.
