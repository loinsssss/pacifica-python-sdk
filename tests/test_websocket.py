import asyncio
import json
from dataclasses import replace

import pytest
from websockets.asyncio.server import serve

from pacifica import (
    ConnectionEvent,
    DataEvent,
    PacificaClient,
    ProtocolError,
    ReconnectExhaustedError,
    Subscription,
    SubscriptionError,
)
from tests.fixtures import ACCOUNT, ACCOUNT_INFO, BOOK, PRICE, WS_FILL, WS_POSITION


def server_config(config, server, **kwargs):
    port = server.sockets[0].getsockname()[1]
    return replace(config, websocket_url=f"ws://127.0.0.1:{port}", **kwargs)


async def acknowledge(socket):
    message = json.loads(await socket.recv())
    await socket.send(json.dumps({"channel": "subscribe", "data": message["params"]}))
    return message["params"]


async def test_early_snapshot_before_ack_is_preserved(config):
    async def handler(socket):
        request = json.loads(await socket.recv())
        await socket.send(
            json.dumps(
                {
                    "channel": "account_positions",
                    "data": [WS_POSITION],
                    "li": 10,
                }
            )
        )
        await socket.send(json.dumps({"channel": "subscribe", "data": request["params"]}))
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        async with PacificaClient(server_config(config, server), account=ACCOUNT) as client:
            async with client.stream_positions() as stream:
                assert isinstance(await anext(stream), ConnectionEvent)
                event = await anext(stream)
                assert event.li == 10 and event.data[0].symbol == "BTC"
            assert not client._streams


async def test_multiplexed_account_and_heartbeat(config):
    received = []
    ping_seen = asyncio.Event()

    async def handler(socket):
        for _ in range(4):
            received.append(await acknowledge(socket))
        await socket.send(
            json.dumps(
                {
                    "channel": "account_positions",
                    "data": [],
                    "li": 10,
                }
            )
        )
        async for raw in socket:
            message = json.loads(raw)
            assert message == {"method": "ping"}
            ping_seen.set()
            await socket.send('{"channel":"pong"}')
            await socket.send(json.dumps({"channel": "account_trades", "data": [WS_FILL]}))

    async with serve(handler, "127.0.0.1", 0) as server:
        async with PacificaClient(
            server_config(config, server, heartbeat_interval=0.01, heartbeat_timeout=0.1),
            account=ACCOUNT,
        ) as client:
            async with client.stream_account() as stream:
                async with asyncio.timeout(2):
                    assert isinstance(await anext(stream), ConnectionEvent)
                    assert (await anext(stream)).data == ()
                    assert (await anext(stream)).channel == "account_trades"
                    assert ping_seen.is_set()
    assert {params["source"] for params in received} == {
        "account_positions",
        "account_order_updates",
        "account_trades",
        "account_info",
    }
    assert all(params["account"] == ACCOUNT for params in received)


@pytest.mark.parametrize("full", [False, True])
async def test_account_info_acknowledgement_compact_default(config, full):
    async def handler(socket):
        request = json.loads(await socket.recv())
        await socket.send(
            json.dumps(
                {
                    "channel": "subscribe",
                    "data": {**request["params"], "full": full},
                }
            )
        )
        if full is False:
            await socket.send(json.dumps({"channel": "account_info", "data": ACCOUNT_INFO}))
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        async with PacificaClient(server_config(config, server), account=ACCOUNT) as client:
            async with client.stream_account_info() as stream:
                if full is False:
                    assert isinstance(await anext(stream), ConnectionEvent)
                    event = await anext(stream)
                    assert event.channel == "account_info"
                    assert event.data.balance == 2000
                else:
                    with pytest.raises(SubscriptionError):
                        await anext(stream)


async def test_disconnect_resubscribe_and_generation(config):
    attempts = []

    async def handler(socket):
        attempts.append(await acknowledge(socket))
        await socket.send(json.dumps({"channel": "prices", "data": [PRICE]}))
        if len(attempts) == 1:
            await socket.close()
        else:
            await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        async with PacificaClient(server_config(config, server)) as client:
            async with client.stream_prices() as stream:
                async with asyncio.timeout(2):
                    events = [await anext(stream) for _ in range(5)]
    assert [getattr(e, "state", None) for e in events] == [
        "connected",
        None,
        "disconnected",
        "connected",
        None,
    ]
    assert events[0].generation == 1 and events[-1].generation == 2
    assert events[2].stale
    assert len(attempts) == 2


async def test_ack_only_connections_do_not_reset_retry_budget(config):
    attempts = []

    async def handler(socket):
        attempts.append(await acknowledge(socket))
        await socket.close()

    async with serve(handler, "127.0.0.1", 0) as server:
        async with PacificaClient(
            server_config(config, server, max_reconnect_attempts=1)
        ) as client:
            async with client.stream_prices() as stream:
                with pytest.raises(ReconnectExhaustedError):
                    async with asyncio.timeout(2):
                        async for _ in stream:
                            pass
    assert len(attempts) == 2


@pytest.mark.parametrize(
    "kind", ["rejected", "wrong_ack", "wrong_symbol", "wrong_account", "bad_json"]
)
async def test_protocol_and_scope_errors_are_terminal(config, kind):
    async def handler(socket):
        request = json.loads(await socket.recv())
        if kind == "rejected":
            await socket.send('{"channel":"subscribe","success":false,"error":"No"}')
        elif kind == "wrong_ack":
            await socket.send('{"channel":"subscribe","data":{"source":"prices"}}')
        else:
            await socket.send(json.dumps({"channel": "subscribe", "data": request["params"]}))
            if kind == "wrong_symbol":
                await socket.send(json.dumps({"channel": "book", "data": {**BOOK, "s": "ETH"}}))
            elif kind == "wrong_account":
                await socket.send(
                    json.dumps(
                        {
                            "channel": "account_trades",
                            "data": [{**WS_FILL, "u": "wrong"}],
                        }
                    )
                )
            else:
                await socket.send("not JSON")
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        async with PacificaClient(server_config(config, server), account=ACCOUNT) as client:
            source = (
                client.stream_fills() if kind == "wrong_account" else client.stream_orderbook("BTC")
            )
            async with source as stream:
                with pytest.raises((SubscriptionError, ProtocolError)):
                    async with asyncio.timeout(2):
                        async for _ in stream:
                            pass


async def test_heartbeat_without_pong_reconnects(config):
    async def handler(socket):
        await acknowledge(socket)
        await socket.recv()  # Receive ping, intentionally omit pong.
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        async with PacificaClient(
            server_config(
                config,
                server,
                heartbeat_interval=0.01,
                heartbeat_timeout=0.01,
                max_reconnect_attempts=0,
            )
        ) as client:
            async with client.stream_prices() as stream:
                async with asyncio.timeout(2):
                    assert (await anext(stream)).state == "connected"
                    assert (await anext(stream)).state == "disconnected"
                    with pytest.raises(ReconnectExhaustedError):
                        await anext(stream)


async def test_startup_buffer_overflow_and_subscription_timeout(config):
    async def flooding(socket):
        await socket.recv()
        for _ in range(2):
            await socket.send(json.dumps({"channel": "prices", "data": [PRICE]}))
        await socket.wait_closed()

    async with serve(flooding, "127.0.0.1", 0) as server:
        async with PacificaClient(server_config(config, server, max_pending_messages=1)) as client:
            async with client.stream_prices() as stream:
                with pytest.raises(ProtocolError, match="buffer"):
                    await anext(stream)

    async def no_ack(socket):
        await socket.recv()
        await socket.wait_closed()

    async with serve(no_ack, "127.0.0.1", 0) as server:
        async with PacificaClient(
            server_config(
                config,
                server,
                subscription_timeout=0.01,
                max_reconnect_attempts=0,
            )
        ) as client:
            async with client.stream_prices() as stream:
                assert (await anext(stream)).state == "disconnected"
                with pytest.raises(ReconnectExhaustedError):
                    await anext(stream)


async def test_one_reader_close_during_receive_and_lazy_start(config):
    started = asyncio.Event()

    async def handler(socket):
        await acknowledge(socket)
        started.set()
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        async with PacificaClient(server_config(config, server)) as client:
            stream = client.stream_prices()
            assert not started.is_set()
            await anext(stream)
            reader = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            with pytest.raises(RuntimeError, match="reader"):
                await anext(stream)
            await stream.aclose()
            with pytest.raises(StopAsyncIteration):
                await reader
            assert not client._streams


def test_duplicate_and_ambiguous_subscriptions(config):
    client = PacificaClient(config)
    sub = Subscription(source="prices")
    with pytest.raises(ValueError):
        client.stream([sub, sub])
    with pytest.raises(ValueError):
        client.stream(
            [
                Subscription(source="account_positions", account=ACCOUNT),
                Subscription(
                    source="account_positions", account="11111111111111111111111111111111"
                ),
            ]
        )


async def test_websocket_api_config_header(config):
    seen = []

    async def handler(socket):
        seen.append(socket.request.headers["PF-API-KEY"])
        await acknowledge(socket)
        await socket.send(json.dumps({"channel": "prices", "data": [PRICE]}))
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        async with PacificaClient(
            server_config(config, server, api_config_key="test_key")
        ) as client:
            async with client.stream_prices() as stream:
                await anext(stream)
                assert isinstance(await anext(stream), DataEvent)
    assert seen == ["test_key"]
