from __future__ import annotations

import asyncio
import time


class RateLimiter:
    def __init__(self, *, interval: float, count: int, raw: bool = False):
        self.interval = interval
        self.count = count
        self.raw = raw
        self._releases: list[float] = []
        self._lock = asyncio.Lock()

    async def _wait_for_release(self, counter: int) -> None:
        while True:
            now = time.monotonic()
            expiry_time = now - self.interval
            self._releases = [s for s in self._releases if s > expiry_time]

            if len(self._releases) == 0:
                return
            if len(self._releases) + counter <= self.count:
                return

            if counter > self.count:
                # while in window — wait for full empty
                deadline = self._releases[-1] + self.interval
            else:
                k = len(self._releases) + counter - self.count
                deadline = self._releases[k - 1] + self.interval

            wait = deadline - now
            if wait > 0:
                await asyncio.sleep(wait)

    def _release(self, counter: int) -> None:
        release_time = time.monotonic()
        remaining = self.count
        for _ in range(counter):
            self._releases.append(release_time)
            remaining -= 1
            if remaining == 0:
                release_time += self.interval
                remaining = self.count

    async def acquire(self, weight: int = 1) -> None:
        async with self._lock:
            counter = 1 if self.raw else weight
            await self._wait_for_release(counter)
            self._release(counter)
