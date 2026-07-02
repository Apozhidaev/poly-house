from __future__ import annotations

from typing import Any, Self

import httpx
import structlog

from poly_house.config import Settings

logger = structlog.get_logger()


class GatewayClient:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.gateway_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=60)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def events_page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        tag_ids: list[int] | None = None,
        exclude_tag_ids: list[int] | None = None,
        closed: bool | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Страница events (keyset) по убыванию updatedAt: (events, next_cursor)."""
        params: dict[str, Any] = {
            "order": "updatedAt",
            "ascending": False,
            "limit": limit,
        }
        if tag_ids:
            params["tag_id"] = tag_ids
            params["related_tags"] = True
        if exclude_tag_ids:
            params["exclude_tag_id"] = exclude_tag_ids
        if closed is not None:
            params["closed"] = closed
        if cursor:
            params["after_cursor"] = cursor
        resp = await self._client.get(f"{self._base}/gamma/events", params=params)
        resp.raise_for_status()
        payload = resp.json()
        events = payload.get("events") or []
        next_cursor = payload.get("next_cursor") or None
        return events, next_cursor

    async def public_profile(self, *, address: str) -> dict[str, Any] | None:
        try:
            resp = await self._client.get(
                f"{self._base}/gamma/public-profile",
                params={"address": address},
            )
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "gateway_public_profile_failed", address=address, error=str(exc)
            )
            return None

    async def event_by_slug(self, *, slug: str) -> dict[str, Any] | None:
        try:
            resp = await self._client.get(f"{self._base}/gamma/events/slug/{slug}")
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("gateway_event_by_slug_failed", slug=slug, error=str(exc))
            return None

    async def order_books(self, token_ids: list[str]) -> list[dict[str, Any]]:
        """POST /clob/order-books; [] при недоступности."""
        if not token_ids:
            return []
        try:
            resp = await self._client.post(
                f"{self._base}/clob/order-books",
                json=[{"token_id": t} for t in token_ids],
            )
            resp.raise_for_status()
            result = resp.json()
            return result if isinstance(result, list) else []
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("gateway_order_books_failed", error=str(exc))
            return []
