from decimal import Decimal

import httpx
import pytest

from pacifica import PacificaClient, ProtocolError

MINUTE = 60_000


def candle(start, symbol="BTC", interval="1m", length=MINUTE):
    return {
        "t": start,
        "T": start + length,
        "s": symbol,
        "i": interval,
        "o": "105376",
        "c": "105380.5",
        "h": "105390",
        "l": "105370",
        "v": "0.00022",
        "n": 2,
    }


def ok(rows):
    return httpx.Response(200, json={"success": True, "data": rows, "error": None, "code": None})


async def test_candles_request_and_exact_values(config):
    def backend(request):
        assert request.url.path.endswith("/kline")
        assert dict(request.url.params) == {
            "symbol": "BTC",
            "interval": "1m",
            "start_time": "1000",
            "end_time": str(1000 + 3 * MINUTE),
            "limit": "3",
        }
        return ok([candle(60_000), candle(120_000)])

    async with PacificaClient(config, transport=httpx.MockTransport(backend)) as client:
        rows = await client.get_candles(
            "BTC", interval="1m", start_time=1000, end_time=1000 + 3 * MINUTE, limit=3
        )
    assert [row.start_time for row in rows] == [60_000, 120_000]
    first = rows[0]
    assert first.end_time - first.start_time == MINUTE
    assert first.close == Decimal("105380.5")
    assert first.volume == Decimal("0.00022")
    assert first.trades == 2


async def test_mark_price_candles_use_their_own_endpoint(config):
    def backend(request):
        assert request.url.path.endswith("/kline/mark")
        assert "end_time" not in request.url.params
        return ok([candle(0, "ETH", "1h", 3_600_000)])

    async with PacificaClient(config, transport=httpx.MockTransport(backend)) as client:
        rows = await client.get_mark_price_candles("ETH", interval="1h", start_time=0)
    assert rows[0].symbol == "ETH" and rows[0].interval == "1h"


@pytest.mark.parametrize(
    "rows",
    [
        [candle(0, symbol="ETH")],  # another symbol
        [candle(0, interval="5m")],  # another interval
        [{**candle(0), "o": 105376.0}],  # float on the wire
    ],
)
async def test_inconsistent_candles_are_rejected(config, rows):
    transport = httpx.MockTransport(lambda request: ok(rows))
    async with PacificaClient(config, transport=transport) as client:
        with pytest.raises(ProtocolError):
            await client.get_candles("BTC", interval="1m", start_time=0)


async def test_candle_arguments_are_validated_without_a_request(config):
    def backend(request):
        raise AssertionError("must not send a request")

    async with PacificaClient(config, transport=httpx.MockTransport(backend)) as client:
        with pytest.raises(ValueError, match="interval"):
            await client.get_candles("BTC", interval="7m", start_time=0)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="iter_candles"):
            # 4001 one-minute candles cannot come back from one request.
            await client.get_candles("BTC", interval="1m", start_time=0, end_time=4001 * MINUTE)
        with pytest.raises(ValueError, match="iter_candles"):
            await client.get_candles(
                "BTC", interval="1m", start_time=0, end_time=4 * MINUTE, limit=3
            )
        with pytest.raises(ValueError):
            await client.get_candles("BTC", interval="1m", start_time=10, end_time=5)
        with pytest.raises(ValueError):
            await client.get_candles("BTC", interval="1m", start_time=-1)
        with pytest.raises(ValueError):
            await client.get_candles("BTC", interval="1m", start_time=0, limit=4001)


async def test_iter_candles_splits_long_ranges_and_drops_boundary_repeats(config):
    windows = []

    def backend(request):
        start = int(request.url.params["start_time"])
        end = int(request.url.params["end_time"])
        windows.append((start, end))
        # The provider includes the candle starting at end_time; iteration must drop it.
        return ok([candle(t) for t in range(start, end + 1, MINUTE)])

    end = 10_000 * MINUTE
    async with PacificaClient(config, transport=httpx.MockTransport(backend)) as client:
        rows = [
            row
            async for row in client.iter_candles("BTC", interval="1m", start_time=0, end_time=end)
        ]
    assert windows == [(0, 4000 * MINUTE), (4000 * MINUTE, 8000 * MINUTE), (8000 * MINUTE, end)]
    starts = [row.start_time for row in rows]
    assert starts == sorted(set(starts))
    assert starts[0] == 0 and starts[-1] == end - MINUTE  # end is exclusive


async def test_iter_candles_needs_fixed_length_interval(config):
    async with PacificaClient(config, transport=httpx.MockTransport(lambda r: ok([]))) as client:
        with pytest.raises(ValueError, match="1M"):
            _ = [
                r async for r in client.iter_candles("BTC", interval="1M", start_time=0, end_time=1)
            ]
