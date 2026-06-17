from __future__ import annotations

from typing import Any

from poly_house.api.rate_limited_client import RateLimitedClient
from poly_house.api.rate_limiter import RateLimiter

rate_limiter = RateLimiter(interval=1, count=5)


class ClobClient:
    def __init__(self, http: RateLimitedClient, base_url: str) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")

    async def order_book(self, token_id: str) -> dict[str, Any] | None:
        """GET /book; None если orderbook недоступен (404)."""
        result = await self._http.get_or_none(
            f"{self._base_url}/book",
            params={"token_id": token_id},
            rate_limiter=rate_limiter,
            max_attempts=1,
        )
        return result if isinstance(result, dict) else None
