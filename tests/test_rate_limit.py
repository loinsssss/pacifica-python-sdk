import asyncio
import time

from pacifica.rate_limit import CreditLimiter


async def test_weighted_rolling_window_and_concurrent_pacing():
    limiter = CreditLimiter(2, window=0.03)
    await limiter.acquire(0.5)
    await limiter.acquire(0.5)
    await limiter.acquire(1)
    start = time.monotonic()
    await asyncio.gather(limiter.acquire(1), limiter.acquire(1))
    assert time.monotonic() - start >= 0.02
    assert sum(cost for _, cost in limiter._spent) == 2


async def test_unlimited_budget():
    limiter = CreditLimiter(None)
    for _ in range(100):
        await limiter.acquire(12)
    assert not limiter._spent
