import asyncio
from dataclasses import replace

import httpx
import pytest

from pacifica import PacificaClient, RateLimitError
from pacifica import rate_limit as rate_limit_module
from pacifica.rate_limit import CreditLimiter, parse_headers
from tests.fixtures import MARKET

_real_sleep = asyncio.sleep


class FakeClock:
    """Virtual time for the limiter: sleeping advances the clock instantly."""

    def __init__(self):
        self.now = 1_000.0
        self.slept = 0.0

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.now += max(0.0, seconds)
        self.slept += max(0.0, seconds)
        await _real_sleep(0)


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(rate_limit_module.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(rate_limit_module.asyncio, "sleep", fake.sleep)
    return fake


def budget(remaining, seconds, quota):
    """Pacifica reports credits multiplied by 10."""
    return {
        "ratelimit": f'"credits";r={int(remaining * 10)};t={seconds}',
        "ratelimit-policy": f'"credits";q={int(quota * 10)};w=60',
    }


def test_header_parsing():
    assert parse_headers(budget(52, 37, 100)) == (52.0, 37.0, 100.0)
    assert parse_headers(budget(0.5, 3, 300)) == (0.5, 3.0, 300.0)
    assert parse_headers({}) is None
    assert parse_headers({"ratelimit": '"credits";r=10;t=3'}) is None


async def test_reads_leave_the_reserve_for_orders(clock):
    limiter = CreditLimiter(100, reserve=0.2, pace_reads=False)
    limiter.observe(budget(25, 40, 100))  # 20 of 25 are the reserve
    await limiter.acquire(12)  # a write may spend the reserve
    assert clock.slept == 0
    await limiter.acquire(4, read=True)  # 13 left, only 13 - 20 < 4 usable by reads
    assert clock.slept == pytest.approx(40)


async def test_reads_are_spread_over_the_window(clock):
    limiter = CreditLimiter(100, reserve=0.2)
    limiter.observe(budget(100, 60, 100))  # 80 usable over 60s
    for _ in range(4):
        await limiter.acquire(1, read=True)
    # First read goes immediately; each later one waits about 60s / remaining credits.
    assert 2.0 < clock.slept < 2.5


async def test_pacing_can_be_disabled(clock):
    limiter = CreditLimiter(100, pace_reads=False)
    limiter.observe(budget(100, 60, 100))
    for _ in range(20):
        await limiter.acquire(1, read=True)
    assert clock.slept == 0


async def test_server_quota_replaces_the_local_estimate(clock):
    # The local default is 100/min, but a config key's real quota is 300.
    limiter = CreditLimiter(100, pace_reads=False)
    limiter.observe(budget(300, 60, 300))
    for _ in range(20):
        await limiter.acquire(12)
    assert clock.slept == 0


async def test_falls_back_to_local_estimate_after_the_window_refreshes(clock):
    limiter = CreditLimiter(24, pace_reads=False)
    limiter.observe(budget(0, 5, 100))
    await limiter.acquire(12)  # waits out the exhausted window
    assert clock.slept == pytest.approx(5)
    assert limiter.server_budget is None
    await limiter.acquire(12)  # local estimate: 24 credits per 60s
    assert clock.slept == pytest.approx(5)
    await limiter.acquire(12)
    assert clock.slept == pytest.approx(65)


async def test_client_tracks_server_budget_from_responses(config):
    def backend(request):
        return httpx.Response(
            200, json={"success": True, "data": [MARKET]}, headers=budget(73.5, 21, 100)
        )

    async with PacificaClient(config, transport=httpx.MockTransport(backend)) as client:
        await client.get_markets()
        remaining, seconds, quota = client._rest.limiter.server_budget
    assert (remaining, quota) == (73.5, 100)
    assert 20 < seconds <= 21


async def test_rate_limited_read_reports_when_the_budget_refreshes(config):
    def backend(request):
        return httpx.Response(429, headers=budget(0, 37, 100))

    async with PacificaClient(
        replace(config, max_retry_delay=5), transport=httpx.MockTransport(backend)
    ) as client:
        with pytest.raises(RateLimitError) as error:
            await client.get_markets()
    assert 36 < error.value.retry_after <= 37


async def test_orders_are_not_stuck_behind_a_waiting_read(clock):
    limiter = CreditLimiter(100, reserve=0.2)
    limiter.observe(budget(21, 50, 100))  # reads have 1 usable credit, writes 21
    await limiter.acquire(1, read=True)
    blocked_read = asyncio.create_task(limiter.acquire(1, read=True))
    await _real_sleep(0)
    await limiter.acquire(1)  # an order goes through while the read waits for the refresh
    assert not blocked_read.done()
    await blocked_read
