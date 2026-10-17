"""Opt-in, read-only checks. Never signs requests or needs account credentials."""

import asyncio
import os
import time

import pytest

from pacifica import DataEvent, PacificaClient, Subscription

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PACIFICA_RUN_PUBLIC_INTEGRATION") != "1",
        reason="Set PACIFICA_RUN_PUBLIC_INTEGRATION=1 for live public checks",
    ),
]


@pytest.mark.parametrize("mainnet", [False, True])
async def test_public_rest(mainnet):
    async with PacificaClient(mainnet=mainnet) as client:
        assert await client.get_markets()
        assert await client.get_prices()
        assert (await client.get_orderbook("BTC")).symbol == "BTC"
        assert await client.get_recent_trades("BTC")
        assert (await client.get_funding_rate_history("BTC", limit=2)).data


@pytest.mark.parametrize("mainnet", [False, True])
async def test_public_streams(mainnet):
    async with PacificaClient(mainnet=mainnet) as client:
        subscriptions = [
            Subscription(source="prices"),
            Subscription(source="book", symbol="BTC", agg_level=1),
            Subscription(source="bbo", symbol="BTC"),
            Subscription(source="trades", symbol="BTC"),
        ]
        async with client.stream(subscriptions) as stream:
            seen = set()
            async with asyncio.timeout(30):
                async for event in stream:
                    if isinstance(event, DataEvent):
                        seen.add(event.channel)
                    if len(seen) == 4:
                        break


@pytest.mark.parametrize("mainnet", [False, True])
async def test_public_candles(mainnet):
    minute = 60_000
    async with PacificaClient(mainnet=mainnet) as client:
        now = int(time.time() * 1000) // minute * minute
        recent = await client.get_candles(
            "BTC", interval="1h", start_time=now - 24 * 60 * minute, end_time=now
        )
        marks = await client.get_mark_price_candles(
            "BTC", interval="1h", start_time=now - 24 * 60 * minute, end_time=now
        )
        assert marks
        for candle in recent:
            assert candle.low <= min(candle.open, candle.close)
            assert max(candle.open, candle.close) <= candle.high
        # Spans two requests; the boundary candle must not repeat and the end is exclusive.
        start = now - 4_100 * minute
        rows = [
            row
            async for row in client.iter_candles(
                "BTC", interval="1m", start_time=start, end_time=now
            )
        ]
        starts = [row.start_time for row in rows]
        assert starts == sorted(set(starts))
        assert all(start <= t < now for t in starts)
        assert client._rest.limiter.server_budget is not None
