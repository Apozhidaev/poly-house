from __future__ import annotations

from typing import Any

from poly_house.api.rate_limited_client import RateLimitedClient
from poly_house.api.rate_limiter import RateLimiter

BASE_URL = "https://data-api.polymarket.com"
rate_limiter = RateLimiter(interval=1, count=5)


class DataClient:
    def __init__(self, http: RateLimitedClient) -> None:
        self._http = http

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
                f"{BASE_URL}/trades", params=params, rate_limiter=rate_limiter
            )
            or []
        )

    async def top_holders(
        self, *, condition_id: str, limit: int = 500
    ) -> list[dict[str, Any]] | None:
        """Топ держателей по маркету (single request, no pagination)."""
        return await self._http.get_or_none(
            f"{BASE_URL}/holders",
            params={
                "market": condition_id,
                "limit": limit,
                "offset": 0,
            },
            rate_limiter=rate_limiter,
        )
