from __future__ import annotations

from typing import Any

from poly_house.api.rate_limited_client import RateLimitedClient
from poly_house.api.rate_limiter import RateLimiter

rate_limiter = RateLimiter(interval=1, count=5)


class DataClient:
    def __init__(self, http: RateLimitedClient, base_url: str) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")

    async def trades_page(
        self, *, condition_id: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        """Страница /trades по маркету, новые первыми, только taker-сделки."""
        params: dict[str, Any] = {
            "market": condition_id,
            "takerOnly": "true",
            "limit": limit,
            "offset": offset,
        }
        return (
            await self._http.get(
                f"{self._base_url}/trades", params=params, rate_limiter=rate_limiter
            )
            or []
        )

    async def top_holders(
        self, *, condition_id: str, limit: int = 500
    ) -> list[dict[str, Any]] | None:
        """Топ держателей по маркету (single request, no pagination)."""
        return await self._http.get_or_none(
            f"{self._base_url}/holders",
            params={
                "market": condition_id,
                "limit": limit,
                "offset": 0,
            },
            rate_limiter=rate_limiter,
        )
