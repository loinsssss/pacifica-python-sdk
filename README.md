# Pacifica Python SDK

An independent, standalone asynchronous Python 3.11+ wrapper for Pacifica REST
and WebSocket APIs.
Distribution: `pacifica-python-sdk`; import: `pacifica`. Alpha version `0.1.0a2`.

Implemented:

- Market metadata, tick/lot validation, mark/oracle/mid prices, funding rates,
  recent trades, orderbooks and historical funding.
- Account equity/margin, settings, positions, open orders, cursor-paginated
  orders/fills/funding payments and reconciliation snapshots.
- Owner/agent Ed25519 signing; limit and market orders, IOC and post-only,
  reduce-only, TP/SL, cancellation, order replacement, leverage and margin settings.
- Explicit agent registration, listing and revocation using an owner signer.
- Multiplexed price, book, BBO, trade, position, fill, order and account-info streams.
- Application heartbeats, bounded buffering, reconnect/resubscription events,
  snapshot helpers and a fill-derived `ExposureTracker`.

Credentials and wallet addresses are intentionally blank in [.env.example](.env.example).
Standard mainnet/testnet transport endpoints are built in; custom endpoints
remain configurable. No wallet preparation, funded orders or withdrawals have
been performed. Live public checks pass on both environments; see the
[validation record](docs/validation.md) for the precise boundary.

## Install

Install the alpha release from PyPI:

```bash
python -m pip install pacifica-python-sdk==0.1.0a2
```

For local development:

```bash
python3.12 -m venv .venv  # any Python 3.11+ works
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

An independent consumer can install with
`pip install -e /absolute/path/to/pacifica-python-sdk` or use a built wheel.
Application-specific strategies and orchestration belong in the consuming application.

## Public data

```python
import asyncio
from pacifica import PacificaClient


async def main():
    async with PacificaClient(mainnet=False) as client:
        market = await client.get_market("BTC")
        price = await client.get_price("BTC")
        book = await client.get_orderbook("BTC")
        print(market.lot_size, price.mark, price.next_funding)
        print(book.bids[:1], book.asks[:1])


asyncio.run(main())
```

`PacificaClient()` defaults to mainnet. `PacificaClient(mainnet=False)` and
`PacificaConfig.testnet()` select testnet for both transports. All numbers used
in orders are `Decimal`, exact strings or integers; floats are rejected.
Symbols are case sensitive and preserved as supplied.

## Accounts and real-time exposure

Reads and subscriptions need an account address, **no private key**. The SDK
does not load dotenv files or read process environment variables implicitly.
Applications can call `load_environment(mainnet=False)` explicitly to read
`.env.testnet`, or `load_environment(mainnet=True)` to read `.env` from their
working directory. These files stay private and are excluded from distributions.

```python
import os
from pacifica import ConnectionEvent, LocalPositions, PacificaClient

# Inside an async function, after you supply your actual account address:
positions = LocalPositions()
async with PacificaClient(mainnet=False, account=os.environ["PACIFICA_ACCOUNT"]) as client:
    print(await client.get_account_snapshot())
    async with client.stream_account() as stream:
        async for event in stream:
            positions.apply(event)
            if isinstance(event, ConnectionEvent):
                print(event.state, "stale:", positions.stale)
            elif event.channel == "account_positions":
                print(
                    {symbol: row.signed_quantity for symbol, row in positions.positions.items()}
                )  # long positive, short negative
            elif event.channel == "account_trades":
                print("Immediate fills:", event.data)
```

`stream_account()` combines position initialization, fills, order updates and
equity/margin data on one socket. Pacifica position messages are complete
replacements, including empty snapshots when flat, and can lag fills. The
example exposes snapshots and immediate fills separately.

When the provider supplies `li` sequence labels, `ExposureTracker` can
deduplicate fills by history ID and retain them until a snapshot incorporates
them, preventing a delayed snapshot from overwriting newer exposure. Live
testnet snapshots for a newly funded flat account were observed without those
labels. Use `LocalPositions` for that snapshot view; `ExposureTracker` rejects
unsequenced baselines rather than guessing which fills they include.
Reconnects mark state stale and require a new snapshot.
The consuming application must backfill missed fill history for durable
execution/PnL accounting.

## Signed operations

```python
import os
from pacifica import LocalSigner, OrderRequest, PacificaClient, UnknownOutcomeError

# Use your account and an already-bound agent wallet, configured later.
client = PacificaClient(
    mainnet=False,
    account=os.environ["PACIFICA_ACCOUNT"],
    signer=LocalSigner(os.environ["PACIFICA_SIGNER_PRIVATE_KEY"]),
    agent_wallet=os.environ["PACIFICA_AGENT_WALLET"],
)

# Inside `async with client:`, using prices/sizes checked against current metadata:
request = OrderRequest(
    symbol="BTC",
    side="bid",
    quantity="0.001",
    price="60000",
    time_in_force="ALO",
)
try:
    receipt = await client.place_order(request)
except UnknownOutcomeError as error:
    # Persist the non-secret context before reconnect/recovery.
    print(error.context)
    print(await client.reconcile_submission(error))
    raise
else:
    print(await client.get_order(receipt.order_id))
    await client.cancel_order(receipt.order_id, symbol="BTC")
```

An acknowledgement is not a fill. Market orders require an explicit
`slippage_percent`; hard absolute price bounds use IOC limit orders.
`edit_order()` replaces the original with a new post-only order ID.
Signed POSTs are never automatically retried. A UUID is a recovery identifier,
not a promised idempotency key. Timeouts, cancellation during transmission,
server errors or unusable responses raise `UnknownOutcomeError`.

Use the owner signer for direct wallet trading, omitting `agent_wallet`.
Agent registration/revocation is explicit and requires the owner signer.
External signers implement `address` and async `sign_message(bytes) -> bytes`.
Signatures are verified locally before transmission.

## Examples and checks

```bash
python examples/public_rest.py --testnet
python examples/public_stream.py --testnet --events 3
# Configure PACIFICA_ACCOUNT in .env.testnet for these read-only examples:
python examples/account_reads.py --testnet
python examples/account_stream.py --testnet
# Preview only unless --execute is supplied:
python examples/order_lifecycle.py --testnet \
  --symbol BTC --side bid --quantity 0.001 --price 60000

python -m pytest
# Load .env.testnet and run read-only live account tests:
python scripts/run_tests.py --account
# Load .env and run read-only mainnet account tests:
python scripts/run_tests.py --account --mainnet
# Run public REST/WebSocket checks on both environments:
python scripts/run_tests.py --public
ruff check .
ruff format --check .
mypy
python -m build
PACIFICA_RUN_PUBLIC_INTEGRATION=1 python -m pytest tests/integration/test_public.py -v
```

Reserve `.env` for mainnet and `.env.testnet` for testnet. Set
`PACIFICA_MAINNET=true` in `.env` and `PACIFICA_MAINNET=false` in `.env.testnet`,
or omit the field and let network selection supply it. Examples use `.env`
unless `--testnet` selects `.env.testnet`. Both examples and the test runner
accept `--env-file /path/to/custom.env`; a conflicting `PACIFICA_MAINNET` is
rejected before network access. An existing file supplies all Pacifica settings
without merging process credentials. If the default file is absent, process
variables can supply configuration; testnet never falls back to `.env`.

`scripts/run_tests.py` runs offline tests by default. `--account` loads the
local `.env.testnet` (`--mainnet` selects `.env`), validates the configured
signer/address locally, then checks
account REST data and position-stream initialization without signing network
requests. `--public` checks public data on both environments. A `404 Account
not found` means the configured address has no trading-account record on the
selected environment; empty positions or a working subscription do not establish
that the account is ready to trade.

| Documentation | Purpose |
| --- | --- |
| [Developer guide](docs/developer-guide.md) | Credentials, workflows, configuration and recovery |
| [API reference](docs/api-reference.md) | Public methods, models, streams and errors |
| [Protocol contracts](docs/api-contracts.md) | Official sources, signing and stream semantics |
| [Validation record](docs/validation.md) | Checks performed and remaining credential-dependent work |

## License and releases

Distributed under the [MIT license](LICENSE).

Releases are published through the manual **Publish to PyPI** GitHub Actions
workflow on `main`. Only the repository owner's account can run its publishing
job. Before the first release, register a pending GitHub Trusted Publisher in
your PyPI account with these values:

| Field | Value |
| --- | --- |
| PyPI project name | `pacifica-python-sdk` |
| GitHub owner | `loinsssss` |
| Repository | `pacifica-python-sdk` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

Run the workflow on `main` with the exact package version, initially `0.1.0a1`.
It verifies the version, builds and validates the distributions, checks an
independent wheel installation, and publishes using a short-lived credential.
No PyPI API token needs to be stored in GitHub.
For subsequent releases, update the package version consistently before
running the workflow; PyPI release versions cannot be overwritten.
