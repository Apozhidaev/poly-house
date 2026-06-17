from __future__ import annotations

from typing import Any

from poly_house.api.rate_limited_client import RateLimitedClient
from poly_house.api.rate_limiter import RateLimiter

BASE_URL = "https://gamma-api.polymarket.com"
rate_limiter = RateLimiter(interval=1, count=5)


class GammaClient:
    def __init__(self, http: RateLimitedClient) -> None:
        self._http = http

    async def markets_page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        tag_ids: list[int] | None = None,
        closed: bool | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Страница /markets/keyset по убыванию updatedAt: (markets, next_cursor)."""
        params: dict[str, Any] = {
            "include_tag": "true",
            "order": "updatedAt",
            "ascending": "false",
            "limit": limit,
        }
        if tag_ids:
            params["tag_id"] = tag_ids  # httpx развернёт в tag_id=1&tag_id=2
        if closed is not None:
            params["closed"] = "true" if closed else "false"
        if cursor:
            params["after_cursor"] = cursor
        payload = await self._http.get(
            f"{BASE_URL}/markets/keyset", params=params, rate_limiter=rate_limiter
        )
        markets = payload.get("markets") or []
        next_cursor = payload.get("next_cursor") or None
        return markets, next_cursor
