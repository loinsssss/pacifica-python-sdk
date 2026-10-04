import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from solders.pubkey import Pubkey
from solders.signature import Signature

from pacifica import (
    APIError,
    AuthenticationError,
    ClientClosedError,
    OrderNotFoundError,
    OrderRequest,
    OrderWaitTimeoutError,
    PacificaClient,
    PrecisionError,
    ProtocolError,
    RateLimitError,
    StopOrder,
    TransportError,
    UnknownOutcomeError,
)
from pacifica.signing import canonical_message
from tests.fixtures import (
    ACCOUNT,
    ACCOUNT_INFO,
    AGENT,
    CLOID,
    FILL,
    MARKET,
    OPEN_ORDER,
    ORDER,
    OWNER,
    POSITION,
    PRICE,
)


class Backend:
    def __init__(self):
        self.requests = []
        self.post_response = {"success": True, "data": {"order_id": 123}}
        self.open_orders = []
        self.history = [ORDER]
        self.fills = [FILL]

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path.removeprefix("/api/v1")
        if request.method == "POST":
            return httpx.Response(200, json=self.post_response)
        data = {
            "/info": [MARKET],
            "/info/prices": [PRICE],
            "/account": ACCOUNT_INFO,
            "/positions": [POSITION],
            "/orders": self.open_orders,
            "/orders/history": self.history,
            "/trades/history": self.fills,
            "/account/settings": {"margin_settings": []},
            "/funding/history": [],
            "/funding_rate/history": [],
        }[path]
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": data,
                "last_order_id": 10,
                "has_more": False,
                "next_cursor": None,
            },
        )


def make_client(config, backend, **kwargs):
    return PacificaClient(config, account=ACCOUNT, transport=httpx.MockTransport(backend), **kwargs)


async def test_exhausted_read_retries_raise_sdk_transport_error(config):
    requests = []

    def unavailable(request):
        requests.append(request)
        raise httpx.ConnectError("Unavailable", request=request)

    async with make_client(config, unavailable) as client:
        with pytest.raises(TransportError) as error:
            await client.get_markets()
    assert len(requests) == config.max_read_retries + 1
    assert isinstance(error.value.__cause__, httpx.ConnectError)


async def test_account_reads_need_no_signer(config):
    backend = Backend()
    async with make_client(config, backend) as client:
        snapshot = await client.get_account_snapshot()
        assert snapshot.positions.last_order_id == 10
        assert snapshot.positions.positions[0].signed_quantity == Decimal("0.001")
        assert snapshot.info.account_equity == Decimal("2010")
        assert await client.get_position("ETH") is None
        assert await client.get_account_settings() == {"margin_settings": []}
        with pytest.raises(AuthenticationError):
            await client.place_order(
                OrderRequest(symbol="BTC", side="bid", quantity="0.001", price="60000")
            )
    assert all(r.url.params.get("account") == ACCOUNT for r in backend.requests)


@pytest.mark.parametrize("market", [False, True])
@pytest.mark.parametrize("agent", [False, True])
async def test_order_wire_and_signature(config, market, agent):
    backend = Backend()
    signer = AGENT if agent else OWNER
    async with make_client(
        config, backend, signer=signer, agent_wallet=AGENT.address if agent else None
    ) as client:
        order = OrderRequest(
            symbol="BTC",
            side="ask",
            quantity="0.001",
            client_order_id=CLOID,
            **(
                {"order_type": "market", "slippage_percent": "0.5"}
                if market
                else {"price": "60000", "time_in_force": "ALO"}
            ),
        )
        submitted = await client.place_order(order)
        assert submitted.order_id == 123 and submitted.client_order_id == CLOID
    post = backend.requests[-1]
    assert post.url.path.endswith("/create_market" if market else "/create")
    body = json.loads(post.content)
    payload = {
        key: value
        for key, value in body.items()
        if key not in {"account", "agent_wallet", "signature", "timestamp", "expiry_window"}
    }
    assert body["account"] == ACCOUNT
    assert body.get("agent_wallet") == (AGENT.address if agent else None)
    assert payload["amount"] == "0.001"
    message = canonical_message(
        "create_market_order" if market else "create_order",
        payload,
        timestamp=body["timestamp"],
        expiry_window=body["expiry_window"],
    )
    assert Signature.from_string(body["signature"]).verify(
        Pubkey.from_string(signer.address), message
    )


async def test_invalid_precision_does_not_submit(config):
    backend = Backend()
    async with make_client(config, backend, signer=OWNER) as client:
        with pytest.raises(PrecisionError):
            await client.place_order(
                OrderRequest(symbol="BTC", side="bid", quantity="0.000011", price="60000")
            )
    assert all(r.method == "GET" for r in backend.requests)


async def test_concurrent_mutations_have_distinct_timestamps(config):
    backend = Backend()
    async with make_client(config, backend, signer=OWNER) as client:
        await asyncio.gather(
            client.cancel_order(1, symbol="BTC"), client.cancel_order(2, symbol="BTC")
        )
    stamps = [json.loads(r.content)["timestamp"] for r in backend.requests]
    assert stamps[1] > stamps[0]


@pytest.mark.parametrize("failure", ["timeout", "server", "invalid", "missing", "cancelled"])
async def test_uncertain_mutations_never_retry(config, failure):
    backend = Backend()
    posts = []

    async def handler(request):
        if request.method != "POST":
            return backend(request)
        posts.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("fake", request=request)
        if failure == "cancelled":
            raise asyncio.CancelledError
        if failure == "server":
            return httpx.Response(
                503, json={"error": "unavailable"}, headers={"x-request-id": "r1"}
            )
        if failure == "invalid":
            return httpx.Response(200, content=b"not json")
        return httpx.Response(200, json={"success": True, "data": {}})

    async with make_client(config, handler, signer=OWNER) as client:
        with pytest.raises(UnknownOutcomeError) as error:
            await client.place_order(
                OrderRequest(
                    symbol="BTC", side="bid", quantity="0.001", price="60000", client_order_id=CLOID
                )
            )
        assert error.value.context.client_order_id == CLOID
        assert "signature" not in str(error.value)
        assert len(posts) == 1
        if failure == "server":
            assert error.value.request_id == "r1"
        # Offline reconciliation finds the submitted UUID through REST history/fills.
        result = await client.reconcile_submission(error.value)
        assert result.resolved


async def test_read_retry_and_retry_after_budget(config):
    attempts = []

    def backend(request):
        attempts.append(request)
        return httpx.Response(
            503 if len(attempts) == 1 else 200,
            json={"success": True, "data": [MARKET]},
            headers={"retry-after": "0"},
        )

    async with make_client(config, backend) as client:
        assert len(await client.get_markets()) == 1
    assert len(attempts) == 2

    def limited(request):
        return httpx.Response(429, headers={"retry-after": "60"})

    async with make_client(config, limited) as client:
        with pytest.raises(RateLimitError) as error:
            await client.get_markets()
        assert error.value.retry_after == 60


async def test_explicit_rejection_and_empty_ack(config):
    async with make_client(
        config,
        lambda request: httpx.Response(400, json={"error": "signature secret", "code": 400}),
        signer=OWNER,
    ) as client:
        with pytest.raises(APIError) as error:
            await client.cancel_order(123, symbol="BTC")
        assert "secret" not in str(error.value)
    async with make_client(
        config, lambda request: httpx.Response(200, json={}), signer=OWNER
    ) as client:
        with pytest.raises(UnknownOutcomeError):
            await client.cancel_order(123, symbol="BTC")
    async with make_client(
        config,
        lambda request: httpx.Response(200, json={"success": True, "data": {"success": False}}),
        signer=OWNER,
    ) as client:
        with pytest.raises(UnknownOutcomeError):
            await client.cancel_order(123, symbol="BTC")


async def test_snapshot_failure_preserves_sdk_error_and_cancels_siblings(config):
    cancelled = []

    async def backend(request):
        if request.url.path.endswith("/account"):
            await asyncio.sleep(0)
            return httpx.Response(400, json={"error": "unavailable"})
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.append(request.url.path)
            raise

    async with make_client(config, backend) as client:
        with pytest.raises(APIError):
            await client.get_account_snapshot()
    assert len(cancelled) == 2


async def test_cursor_pagination_and_cycle_detection(config):
    requests = []

    def backend(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": [ORDER],
                "has_more": len(requests) == 1,
                "next_cursor": "cursor-2" if len(requests) == 1 else None,
            },
        )

    async with make_client(config, backend) as client:
        rows = [row async for row in client.iter_order_history()]
        assert len(rows) == 2
        assert requests[1].url.params["cursor"] == "cursor-2"
    async with make_client(
        config,
        lambda request: httpx.Response(
            200,
            json={
                "success": True,
                "data": [],
                "has_more": True,
                "next_cursor": "cycle",
            },
        ),
    ) as client:
        with pytest.raises(ProtocolError, match="cursor"):
            _ = [row async for row in client.iter_order_history()]


async def test_order_lookup_and_wait(config):
    backend = Backend()
    async with make_client(config, backend) as client:
        assert (await client.get_order(123)).terminal
        assert (await client.wait_for_order(123)).order_id == 123
        backend.history = []
        with pytest.raises(OrderNotFoundError):
            await client.get_order(999)
        with pytest.raises(OrderWaitTimeoutError):
            await client.wait_for_order(999, timeout=0.02, poll_interval=0.001)
    assert all(r.method == "GET" for r in backend.requests)


async def test_cancel_edit_leverage_and_agents(config):
    backend = Backend()
    async with make_client(config, backend, signer=AGENT, agent_wallet=AGENT.address) as client:
        backend.post_response = {"cancelled_count": 3}
        assert (await client.cancel_all_orders("BTC")).cancelled_count == 3
        body = json.loads(backend.requests[-1].content)
        assert body["all_symbols"] is False and body["symbol"] == "BTC"
        backend.post_response = {"order_id": 456}
        assert (await client.edit_order(123, symbol="BTC", price="59999")).order_id == 456
        backend.post_response = {"success": True}
        await client.update_leverage("BTC", 10)
        await client.update_margin_mode("BTC", isolated=False)
        await client.add_isolated_margin("BTC", "100")
        await client.set_position_tpsl("BTC", "ask", stop_loss=StopOrder(stop_price="59000"))
        body = json.loads(backend.requests[-1].content)
        assert body["stop_loss"]["stop_price"] == "59000"
        assert body["side"] == "ask"
        with pytest.raises(ValueError):
            await client.update_leverage("BTC", 100)
        await client.register_agent(AGENT.address, OWNER)
        body = json.loads(backend.requests[-1].content)
        message = canonical_message(
            "bind_agent_wallet",
            {"agent_wallet": AGENT.address},
            timestamp=body["timestamp"],
            expiry_window=body["expiry_window"],
        )
        assert Signature.from_string(body["signature"]).verify(Pubkey.from_string(ACCOUNT), message)
        backend.post_response = {"success": True, "data": [{"agent_wallet": AGENT.address}]}
        assert (await client.list_agents(OWNER)).agents[0]["agent_wallet"] == AGENT.address
        with pytest.raises(AuthenticationError):
            await client.revoke_agent(AGENT.address, AGENT)


async def test_closed_client_and_scope_checks(config):
    backend = Backend()
    client = make_client(config, backend)
    await client.aclose()
    with pytest.raises(ClientClosedError):
        await client.get_markets()
    assert not client._streams
    async with make_client(config, backend) as other:
        backend.fills = [{**FILL, "symbol": "ETH"}]
        with pytest.raises(ProtocolError):
            await other.get_trade_history(symbol="BTC")


async def test_api_key_header(config):
    from dataclasses import replace

    backend = Backend()
    async with make_client(replace(config, api_config_key="test_config_key"), backend) as client:
        await client.get_markets()
    assert backend.requests[0].headers["PF-API-KEY"] == "test_config_key"


async def test_lookup_checks_open_orders_first(config):
    backend = Backend()
    backend.open_orders = [OPEN_ORDER]
    async with make_client(config, backend) as client:
        assert (await client.get_order(client_order_id=CLOID)).order_id == 123
    assert len(backend.requests) == 1
