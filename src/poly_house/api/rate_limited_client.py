from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Self

import httpx
import structlog

from poly_house.api.rate_limiter import RateLimiter

logger = structlog.get_logger()


class RateLimitedClient:
    """Async HTTP client with rate limiting (semaphore) and retry with exponential backoff."""

    def __init__(self, rate_limiter: RateLimiter) -> None:
        self._rate_limiter = rate_limiter
        self._max_retries = 3
        self._backoff_base = 2
        self._client = httpx.AsyncClient(timeout=30)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def get(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                await self._rate_limiter.acquire()
                resp = await self._client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                wait = self._backoff_base**attempt
                logger.warning(
                    "http_request_failed",
                    url=url,
                    attempt=attempt,
                    wait=wait,
                    error=str(exc),
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(wait)

        raise RuntimeError(
            f"HTTP request failed after {self._max_retries} retries: {url}"
        ) from last_exc

    async def get_or_none(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any | None:
        """Like get(), but returns None on any failure instead of raising."""
        try:
            return await self.get(url, params=params)
        except RuntimeError:
            return None
