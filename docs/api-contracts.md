# Protocol contracts and source record

Protocol research and public verification date: **2026-10-02 (Asia/Singapore)**.
Implementation used the official Pacifica documentation and its official
Python API examples.
This project is an independent wrapper, not a claim of official maintenance.

## Primary sources

- [Documentation index](https://docs.pacifica.fi/llms.txt)
- [REST environments](https://docs.pacifica.fi/api-documentation/api/rest-api)
- [WebSocket environments and heartbeat](https://docs.pacifica.fi/api-documentation/api/websocket)
- [Signing implementation](https://docs.pacifica.fi/api-documentation/api/signing/implementation)
- [Operation types](https://docs.pacifica.fi/api-documentation/api/signing/operation-types)
- [Agent signing](https://docs.pacifica.fi/api-documentation/api/signing/api-agent-keys)
- [Rate limits](https://docs.pacifica.fi/api-documentation/api/rate-limits)
- [Config-key header](https://docs.pacifica.fi/api-documentation/api/rate-limits/api-config-keys)
- [Market metadata](https://docs.pacifica.fi/api-documentation/api/rest-api/markets/get-market-info)
- [Prices and funding](https://docs.pacifica.fi/api-documentation/api/rest-api/markets/get-prices)
- [Position REST snapshot](https://docs.pacifica.fi/api-documentation/api/rest-api/account/get-positions)
- [Open orders](https://docs.pacifica.fi/api-documentation/api/rest-api/orders/get-open-orders)
- [Order history](https://docs.pacifica.fi/api-documentation/api/rest-api/orders/get-order-history)
- [Trade history](https://docs.pacifica.fi/api-documentation/api/rest-api/account/get-trade-history)
- [Position subscriptions](https://docs.pacifica.fi/api-documentation/api/websocket/subscriptions/account-positions)
- [Fill subscriptions](https://docs.pacifica.fi/api-documentation/api/websocket/subscriptions/account-trades)
- [Order updates](https://docs.pacifica.fi/api-documentation/api/websocket/subscriptions/account-order-updates)
- [Snapshot delay FAQ](https://docs.pacifica.fi/api-documentation/api/api-faq/delayed-account_positions)
- [Last-ID scope](https://docs.pacifica.fi/api-documentation/api/last-id)
- [Order replacement](https://docs.pacifica.fi/api-documentation/api/rest-api/orders/edit-order)
- [Position TP/SL](https://docs.pacifica.fi/api-documentation/api/rest-api/orders/create-position-tp-sl)
- [Official Python examples](https://github.com/pacifica-fi/python-sdk)

## Signing and acknowledgements

All POSTs are signed; GETs and subscriptions do not require signatures.
The signed message is recursively key-sorted compact JSON:

```json
{"data":{"amount":"0.1","price":"100000","reduce_only":false,"side":"bid","symbol":"BTC","tif":"GTC"},"expiry_window":5000,"timestamp":1748970123456,"type":"create_order"}
```

Ed25519 signs those UTF-8 bytes. The signature is base58 encoded.
Actual request fields are flattened, with `account`, optional `agent_wallet`,
`signature`, `timestamp` and `expiry_window`; the signed `type` and `data`
wrapper do not appear on the HTTP request.
For bind/revoke, the owner signs `agent_wallet` as an operation payload field.
For trading with an agent, the configured `agent_wallet` identifies the signer
in the request header.

| SDK operation | POST route | Signed type |
| --- | --- | --- |
| Limit placement | `/orders/create` | `create_order` |
| Market placement | `/orders/create_market` | `create_market_order` |
| Cancel one | `/orders/cancel` | `cancel_order` |
| Cancel all | `/orders/cancel_all` | `cancel_all_orders` |
| Replace/edit | `/orders/edit` | `edit_order` |
| Leverage | `/account/leverage` | `update_leverage` |
| Margin mode | `/account/margin` | `update_margin_mode` |
| Isolated margin | `/positions/add_isolated_margin` | `add_isolated_margin` |
| Existing position TP/SL | `/positions/tpsl` | `set_position_tpsl` |
| Bind agent | `/agent/bind` | `bind_agent_wallet` |
| List agents | `/agent/list` | `list_agent_wallets` |
| Revoke agent | `/agent/revoke` | `revoke_agent_wallet` |

Official examples document flat trading acknowledgements, while public reads
use `success`/`data` envelopes. The mutation parser accepts flat and enveloped
acknowledgements without manufacturing an order ID. Missing/malformed success
data is an uncertain outcome. Actual signed acknowledgements remain to be
verified with your account.

## Positions, fills and Last ID

Position snapshots replace the whole set; closed positions disappear and an
empty array means flat. WebSocket envelope `li` and REST `last_order_id`
remain meaningful for empty sets. Snapshot rows have their own nullable LI.
Position snapshots can lag under load; event-driven account fills/order updates
are the preferred immediate state inputs.

LI is a sequence label, not wall-clock time. Symbols' row labels must not be
globally compared to each other. A numerically larger label is not necessarily
a consecutive event: these channels can legitimately skip engine events.
The wrapper does not demand `new_li == old_li + 1`.
Fills are deduplicated by history ID (`h`/`history_id`), not by order ID or LI.

Live testnet checks on 2026-10-04 observed missing envelope `li` and missing
REST `last_order_id` for a newly funded account with no positions or orders.
The parser preserves these as `None`; `LocalPositions` still exposes the full
snapshot. `ExposureTracker` requires a sequenced baseline to reconcile fills
and rejects these unsequenced snapshots. No sequence label is synthesized.

On reconnect, the initialization snapshot is a new baseline and history
recovery is the caller's responsibility. `ExposureTracker` retains fills
newer than a position row's baseline; absent symbols use the account-set
envelope baseline, including flat snapshots.

## WebSocket lifecycle

Subscriptions send `{"method":"subscribe","params":{...}}`; an acknowledgement
has channel `subscribe` and echoes the subscribed scope in `data`.
The wrapper verifies acknowledgements and data symbol/account scope. It retains
data received before all multiplexed acknowledgements, within a fixed bound.
Live `account_info` acknowledgements also echo the implicit `full: false`
compact-format default. The wrapper normalizes that specific default while
continuing to verify the subscribed account and reject an unexpected full mode.

Pacifica closes idle connections after 60 seconds without client messages and
limits connection lifetime to 24 hours. The wrapper sends application JSON
`{"method":"ping"}` and requires channel `pong`, reconnecting on heartbeat loss.
The library's TCP/WebSocket ping control frames alone do not satisfy this contract.

The published book stream supplies replacement aggregated depth, so
`LocalOrderbook` replaces its levels.
Account position/info messages omit the account in data; separate streams are
required when observing multiple accounts for those channels.

## Observed provider details

Live testnet prices can supply `yesterday_price="-1"` for unavailable history.
The model retains the sentinel as a finite Decimal; it does not fail the
entire market-price response. Unknown additive fields, such as market
`execution_modes`, are retained.

Public endpoints and all four public stream types were exercised on mainnet
and testnet. On 2026-10-04, funded testnet account reads and multiplexed
position/account-info initialization were also exercised. Signed REST operations
and live fill/order-change events remain to be validated; fixtures reflect
documented formats.
