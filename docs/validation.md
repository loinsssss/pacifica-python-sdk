# Validation

Release: `0.1.0a1`, alpha. Initial validation was performed on **2026-10-02
(Asia/Singapore)**.
No signed network requests, trades, deposits, withdrawals, transfers or
account-setting changes have been sent by these checks.

## Read-only account validation: 2026-10-04

Local testnet key/address validation passes. This proves the configured keypair
matches the agent address, not that the exchange has registered that agent.

| Check | Result |
| --- | --- |
| `python scripts/run_tests.py` | **92 passed**, 8 integration tests deselected |
| `python scripts/run_tests.py --public` | **4 passed** across mainnet and testnet |
| `python scripts/run_tests.py --account` | **4 passed**, loading `.env.testnet` |
| Account positions, orders, settings and histories | Read successfully; positions/orders/histories empty |
| Account position WebSocket | Initial snapshot received successfully |
| Account equity/balance snapshot | Read successfully for a funded account |
| Combined account WebSocket | Initial position and account-info events received successfully |
| Ruff lint/format and strict mypy | Passed |

The selected environment is testnet. Funded-account checks passed.
Compact account-info acknowledgements include an implicit `full=false`,
which is normalized before scope verification. Live empty position snapshots
omit sequence IDs: `LocalPositions` accepts the snapshots, while `ExposureTracker`
requires actual sequence IDs to reconcile fills safely.

The test runner explicitly reads `.env.testnet` without executing shell code,
honors custom account transport URLs and the API config key, and keeps signer
private keys out of the network-test subprocess environment. Offline tests
remain the default; live checks require `--account` or `--public`. `--mainnet`
selects `.env` for account checks. Regression tests cover network file selection,
credential isolation, missing files and conflicting network settings.
Closed-client and exhausted-read failures use public SDK error types, and
transport regression coverage verifies the bounded retry count. Reconnect
defaults are three attempts with a five-second maximum backoff.

## Initial checks: 2026-10-02

- Offline unit tests cover configuration, exact numeric models, canonical signing,
  locally verified owner/agent signatures, nested TP/SL payloads, scoped reads,
  placement/cancellation/edit/risk configuration, monotonic mutation timestamps,
  read retries, quota headers, uncertain outcomes and bounded pagination.
- Local WebSocket servers exercise early snapshots before acknowledgements,
  multiplexed account subscriptions, JSON heartbeat/pong, missed heartbeat,
  disconnect/resubscribe generations, reconnect exhaustion, scope rejection,
  startup-buffer exhaustion, subscription timeout and concurrent reader rejection.
- State tests cover complete replacement, position closure, snapshot ordering,
  duplicate fills, equal-LI distinct fills, fills before initialization, delayed
  snapshots, cross-symbol row LI scopes, reconnect invalidation and journal bounds.
- Live **public** REST checks pass on mainnet and testnet: market metadata,
  all prices, BTC book, recent trades and historical funding.
- Live **public** multiplexed WebSocket checks pass on both environments:
  prices, BTC book, BTC BBO and BTC trades.
- Ruff lint/format and strict mypy are required checks.
- Wheel/sdist build and independent wheel consumer verification are required
  packaging checks; the consumer runs against a mocked backend without credentials.

Final results:

| Check | Result |
| --- | --- |
| `python -m pytest -q` | **77 passed**, 6 opt-in integration tests skipped |
| `PACIFICA_RUN_PUBLIC_INTEGRATION=1 python -m pytest tests/integration/test_public.py -q` | **4 passed**, covering both environments |
| `ruff check .` | Passed |
| `ruff format --check .` | Passed |
| `mypy` | Passed, 10 source modules |
| `python -m build` | Wheel and sdist built |
| Separate virtualenv wheel install + `tests/consumer_smoke.py` | Passed; imported from consumer site-packages |
| Wheel contents/source match | Passed, including `py.typed`; no packaged examples/tests |
| `pip check` | Passed |
| Public stream example, testnet | Connected and received two data events |
| Order lifecycle example without `--execute` | Preview only, without submitting |

The public network test run completed in approximately 27 seconds. Live signed
operations and account tests are deliberately excluded until your credentials
are configured.

## Configuration and account tests

Fill your actual values in a private application configuration, using
`.env.example` only as a list of field names:

1. Pacifica account public key and chosen environment.
2. Owner or already-bound agent signer key.
3. Agent public key when trading through an agent.
4. Optional API config key and your verified credit quota.

Read-only account gates are already present:

```bash
# Load .env.testnet and run only read-only account checks:
python scripts/run_tests.py --account
# Select .env for read-only mainnet checks:
python scripts/run_tests.py --account --mainnet
```

Configure `PACIFICA_ACCOUNT`, `PACIFICA_MAINNET` and any transport overrides
in `.env.testnet` for testnet or `.env` for mainnet. Process variables can supply
settings if the default file is absent. These checks read REST state and account
subscriptions; they do not trade.

## Still unverified without your account

- Agent bind/list/revoke acknowledgement shapes and live signature acceptance.
- Actual placement, replacement, cancellation, fills and TP/SL execution.
- Live leverage/margin-setting acceptance and environment-specific restrictions.
- Private-account stream fills/order changes and reconnect while an account trades.
- Live filled-position snapshots and execution-history recovery.

Run a deliberately small testnet lifecycle after account read gates pass,
using the explicit `--execute` example flag. A provider receipt alone does not
establish completed execution. Inspect positions, fills and orders.
Funded mainnet validation is a later explicit application decision.

## Scope

The SDK is installable independently. It provides Pacifica exchange access and
state helpers; strategies and orchestration belong to consuming applications.
This validation record predates registry publication. The SDK now uses the MIT
license; see the README for the current PyPI release process.
