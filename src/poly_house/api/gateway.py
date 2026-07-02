from __future__ import annotations

from datetime import datetime
from typing import Any, Self

import httpx
import structlog

from poly_house.config import Settings
from poly_house.models import PnlTimeSeries, TimeSeriesPoint

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

    async def pnl_time_series(self, *, user: str) -> PnlTimeSeries | None:
        """GET /pnl/user-pnl?user_address&interval=all&fidelity=1d → PnlTimeSeries."""
        try:
            resp = await self._client.get(
                f"{self._base}/pnl/user-pnl",
                params={"user_address": user, "interval": "all", "fidelity": "1d"},
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("gateway_pnl_time_series_failed", user=user, error=str(exc))
            return None

        if not data or not isinstance(data, list):
            return None

        points: list[TimeSeriesPoint] = []
        for item in data:
            try:
                t = item.get("t") or item.get("timestamp")
                p = item.get("p") or item.get("pnl")
                if t is not None and p is not None:
                    if isinstance(t, (int, float)):
                        ts = datetime.fromtimestamp(int(t))
                    else:
                        ts = datetime.fromisoformat(str(t))
                    points.append(TimeSeriesPoint(timestamp=ts, value=float(p)))
            except (ValueError, TypeError):
                continue

        if not points:
            return None

        return PnlTimeSeries(wallet_address=user, data=points)

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
