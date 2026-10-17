"""Credit budgeting: a local rolling estimate, corrected by the server's own headers.

Pacifica reports the caller's budget on every REST response:

    ratelimit: "credits";r=<remaining x10>;t=<seconds until refresh>
    ratelimit-policy: "credits";q=<quota x10>;w=<window seconds>

Request costs depend on the endpoint and the size of the result, so the local
estimate is only a starting point. Once a response carries these headers, they
are authoritative: they include credits spent by other processes sharing the
IP or account, and the real quota for the caller's tier.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from collections.abc import Mapping

_REMAINING = re.compile(r"\br=(\d+)\b.*?\bt=(\d+)\b")
_QUOTA = re.compile(r"\bq=(\d+)\b")


def parse_headers(headers: Mapping[str, str]) -> tuple[float, float, float] | None:
    """(remaining credits, seconds until refresh, quota) from response headers, if present."""
    remaining = _REMAINING.search(headers.get("ratelimit", ""))
    quota = _QUOTA.search(headers.get("ratelimit-policy", ""))
    if remaining is None or quota is None:
        return None
    return int(remaining.group(1)) / 10, float(remaining.group(2)), int(quota.group(1)) / 10


class CreditLimiter:
    """Shared by one client's REST calls.

    ``reserve`` is the share of the server quota that reads never spend, so orders
    and cancellations keep headroom while a long history download runs. With
    ``pace_reads`` the remaining read budget is spread evenly over the rest of the
    window instead of being spent in a burst.
    """

    def __init__(
        self,
        quota: int | None,
        *,
        window: float = 60.0,
        reserve: float = 0.2,
        pace_reads: bool = True,
    ) -> None:
        if not 0 <= reserve < 1:
            raise ValueError("reserve must be in [0, 1)")
        self.quota = quota
        self.window = window
        self.reserve = reserve
        self.pace_reads = pace_reads
        self._spent: deque[tuple[float, float]] = deque()
        self._lock = asyncio.Lock()
        # Server view: remaining credits (decremented locally until the next response),
        # monotonic time it refreshes, and the server's quota.
        self._remaining: float | None = None
        self._refresh_at = 0.0
        self._server_quota: float | None = None
        self._next_read = 0.0

    @property
    def server_budget(self) -> tuple[float, float, float] | None:
        """Last known (remaining, seconds until refresh, quota) from the server."""
        if self._remaining is None or self._server_quota is None:
            return None
        return (
            self._remaining,
            max(0.0, self._refresh_at - time.monotonic()),
            self._server_quota,
        )

    def observe(self, headers: Mapping[str, str]) -> None:
        parsed = parse_headers(headers)
        if parsed is None:
            return
        self._remaining, seconds, self._server_quota = parsed
        self._refresh_at = time.monotonic() + seconds

    async def acquire(self, cost: float = 1.0, *, read: bool = False) -> None:
        if cost <= 0:
            raise ValueError("cost must be positive")
        if self.quota is not None and cost > self.quota:
            raise ValueError("cost must fit the quota")
        while True:
            async with self._lock:
                wait = self._server_wait(cost, read)
                if wait is None:
                    wait = self._local_wait(cost)
                if wait == 0:
                    if self.quota is not None:
                        # Track spend locally too, for when server information lapses.
                        self._spent.append((time.monotonic(), cost))
                    return
            # Sleep without the lock, so an order or cancel is never stuck behind a
            # paced or blocked read.
            await asyncio.sleep(wait)

    def _local_wait(self, cost: float) -> float:
        """Rolling-window estimate, used until the server has reported a budget."""
        if self.quota is None:
            return 0.0
        now = time.monotonic()
        while self._spent and self._spent[0][0] <= now - self.window:
            self._spent.popleft()
        if sum(row[1] for row in self._spent) + cost <= self.quota:
            return 0.0
        return max(1e-3, self._spent[0][0] + self.window - now)

    def _server_wait(self, cost: float, read: bool) -> float | None:
        """Seconds to wait on the server-reported budget, 0 when granted.

        None when there is no current report, so the local estimate applies.
        """
        if self._remaining is None or self._server_quota is None:
            return None
        now = time.monotonic()
        if now >= self._refresh_at:
            # The window refreshed; the next response reports the new budget.
            self._remaining = None
            return None
        floor = self.reserve * self._server_quota if read else 0.0
        usable = self._remaining - floor
        if usable < cost:
            return max(1e-3, self._refresh_at - now)
        if read and self.pace_reads:
            if self._next_read > now:
                return self._next_read - now
            # Spread what is left over the time left: no burst, no stall at the end.
            self._next_read = now + (self._refresh_at - now) * cost / usable
        self._remaining -= cost
        return 0.0
