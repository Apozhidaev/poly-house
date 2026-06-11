from __future__ import annotations

from typing import Any

from poly_house.api.rate_limited_client import RateLimitedClient


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
        return await self._http.get(f"{self._base_url}/trades", params=params) or []
