from __future__ import annotations

from typing import Any

from poly_house.api.rate_limited_client import RateLimitedClient


class GammaClient:
    def __init__(self, http: RateLimitedClient, base_url: str) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")

    async def markets_page(
        self, *, limit: int, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Страница /markets/keyset по убыванию updatedAt: (markets, next_cursor)."""
        params: dict[str, Any] = {
            "include_tag": "true",
            "order": "updatedAt",
            "ascending": "false",
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor
        payload = await self._http.get(f"{self._base_url}/markets/keyset", params=params)
        markets = payload.get("markets") or []
        next_cursor = payload.get("next_cursor") or None
        return markets, next_cursor
