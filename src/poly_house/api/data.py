from __future__ import annotations

from typing import Any

from poly_house.api.rate_limited_client import RateLimitedClient
from poly_house.api.rate_limiter import RateLimiter
from poly_house.models import LeaderboardStats

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

    async def leaderboard_stats(
        self,
        *,
        user: str,
    ) -> LeaderboardStats | None:
        """GET /v1/leaderboard?timePeriod=ALL&user=... → PNL и vol за всё время или None."""
        url = f"{BASE_URL}/v1/leaderboard"
        params = {"timePeriod": "ALL", "user": user}
        data = await self._http.get(url, params=params, rate_limiter=rate_limiter)
        if isinstance(data, list) and len(data) > 0:
            entry = data[0]
            return LeaderboardStats(pnl=entry.get("pnl", 0), vol=entry.get("vol", 0))
        return None

    async def fetch_positions_value(
        self,
        *,
        user: str,
    ) -> float | None:
        """GET /value?user={user} → total position value (USDC) or None."""
        url = f"{BASE_URL}/value"
        params = {"user": user}
        data = await self._http.get_or_none(url, params=params)
        if isinstance(data, list) and len(data) > 0:
            entry = data[0]
            val = entry.get("value")
            if val is not None:
                return float(val)
        return None
