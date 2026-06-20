from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Self

import httpx
import structlog

from poly_house.api.rate_limiter import RateLimiter

logger = structlog.get_logger()
_MAX_ATTEMPTS = 5

class RateLimitedClient:
    """Async HTTP client with rate limiting (semaphore) and retry with exponential backoff."""

    def __init__(self) -> None:
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
        *,
        params: Mapping[str, Any] | None = None,
        rate_limiter: RateLimiter | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> Any:
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                if rate_limiter is not None:
                    await rate_limiter.acquire()
                resp = await self._client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                wait = self._backoff_base**attempt
                logger.warning(
                    "http_request_failed",
                    url=url,
                    params=params,
                    attempt=attempt,
                    wait=wait,
                    error=str(exc),
                )
                if attempt < max_attempts:
                    await asyncio.sleep(wait)

        raise RuntimeError(
            f"HTTP request failed after {max_attempts} attempts: {url}"
        ) from last_exc

    async def get_bytes(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        rate_limiter: RateLimiter | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> bytes:
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                if rate_limiter is not None:
                    await rate_limiter.acquire()
                resp = await self._client.get(url, params=params)
                resp.raise_for_status()
                return resp.content
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
                if attempt < max_attempts:
                    await asyncio.sleep(wait)

        raise RuntimeError(
            f"HTTP request failed after {max_attempts} attempts: {url}"
        ) from last_exc

    async def get_or_none(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        rate_limiter: RateLimiter | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> Any | None:
        """Like get(), but returns None on any failure instead of raising."""
        try:
            return await self.get(
                url, params=params, rate_limiter=rate_limiter, max_attempts=max_attempts
            )
        except RuntimeError:
            return None

    async def get_bytes_or_none(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        rate_limiter: RateLimiter | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
    ) -> bytes | None:
        """Like get_bytes(), but returns None on any failure instead of raising."""
        try:
            return await self.get_bytes(
                url, params=params, rate_limiter=rate_limiter, max_attempts=max_attempts
            )
        except RuntimeError:
            return None
