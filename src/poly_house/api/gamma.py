from __future__ import annotations

from typing import Any

from poly_house.api.rate_limited_client import RateLimitedClient
from poly_house.api.rate_limiter import RateLimiter

BASE_URL = "https://gamma-api.polymarket.com"
rate_limiter = RateLimiter(interval=1, count=5)


class GammaClient:
    def __init__(self, http: RateLimitedClient) -> None:
        self._http = http

    async def events_page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        tag_ids: list[int] | None = None,
        exclude_tag_ids: list[int] | None = None,
        closed: bool | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Страница /events/keyset по убыванию updatedAt: (events, next_cursor)."""
        params: dict[str, Any] = {
            "order": "updatedAt",
            "ascending": "false",
            "limit": limit,
        }
        if tag_ids:
            params["tag_id"] = tag_ids
            params["related_tags"] = "true"
        if exclude_tag_ids:
            params["exclude_tag_id"] = exclude_tag_ids
        if closed is not None:
            params["closed"] = "true" if closed else "false"
        if cursor:
            params["after_cursor"] = cursor
        payload = await self._http.get(
            f"{BASE_URL}/events/keyset", params=params, rate_limiter=rate_limiter
        )
        events = payload.get("events") or []
        next_cursor = payload.get("next_cursor") or None
        return events, next_cursor

    async def public_profile(self, *, address: str) -> dict[str, Any] | None:
        return await self._http.get_or_none(
            f"{BASE_URL}/public-profile",
            params={"address": address},
            rate_limiter=rate_limiter,
        )
