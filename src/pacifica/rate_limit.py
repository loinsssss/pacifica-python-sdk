"""Conservative weighted rolling credit budget, shared by one client's REST calls."""

from __future__ import annotations

import asyncio
import time
from collections import deque


class CreditLimiter:
    def __init__(self, quota: int | None, *, window: float = 60.0) -> None:
        self.quota = quota
        self.window = window
        self._spent: deque[tuple[float, float]] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        if self.quota is None:
            return
        if cost <= 0 or cost > self.quota:
            raise ValueError("cost must be positive and fit the quota")
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._spent and self._spent[0][0] <= now - self.window:
                    self._spent.popleft()
                if sum(row[1] for row in self._spent) + cost <= self.quota:
                    self._spent.append((now, cost))
                    return
                await asyncio.sleep(max(0, self._spent[0][0] + self.window - now))
