from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Self

import httpx
import structlog

from poly_house.config import Settings
from poly_house.models import (
    EquitySnapshot,
    LeaderboardStats,
    PnlTimeSeries,
    TimeSeriesPoint,
)
from poly_house.models.activity import ActivityType

logger = structlog.get_logger()


def _page(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    data = payload.get("data") or []
    pagination = payload.get("pagination") or {}
    next_cursor = pagination.get("next_cursor")
    return (data if isinstance(data, list) else [], next_cursor if next_cursor else None)


class PolyGateClient:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.gateway_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=None)

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
        resp = await self._client.post(f"{self._base}/gamma/events", params=params)
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
            resp = await self._client.post(f"{self._base}/gamma/events/slug/{slug}")
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("gateway_event_by_slug_failed", slug=slug, error=str(exc))
            return None

    async def pnl_time_series(self, *, user: str) -> PnlTimeSeries | None:
        """GET /data/user-pnl?user&interval=all&fidelity=1d → PnlTimeSeries."""
        try:
            resp = await self._client.get(
                f"{self._base}/data/user-pnl",
                params={"user": user, "interval": "all", "fidelity": "1d"},
            )
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("gateway_pnl_time_series_failed", user=user, error=str(exc))
            return None

        if not isinstance(payload, dict):
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        raw_points = data.get("points")
        if not isinstance(raw_points, list):
            return None

        points: list[TimeSeriesPoint] = []
        for item in raw_points:
            if not isinstance(item, dict):
                continue
            try:
                t = item.get("timestamp")
                p = item.get("realized_pnl")
                if t is None or p is None:
                    continue
                ts = datetime.fromtimestamp(int(t))
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

    async def trades_page(
        self, *, condition_id: str, limit: int, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Страница /data/trades по condition, новые первыми, только taker-сделки."""
        params: dict[str, Any] = {
            "condition": condition_id,
            "taker_only": True,
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor
        resp = await self._client.get(f"{self._base}/data/trades", params=params)
        resp.raise_for_status()
        return _page(resp.json())

    async def top_holders(
        self, *, condition_id: str, limit: int = 500
    ) -> list[dict[str, Any]] | None:
        """Топ держателей по condition (single request, no pagination)."""
        try:
            resp = await self._client.get(
                f"{self._base}/data/holders",
                params={"condition": condition_id, "limit": limit},
            )
            resp.raise_for_status()
            data, _ = _page(resp.json())
            return data
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "gateway_top_holders_failed",
                condition_id=condition_id,
                error=str(exc),
            )
            return None

    async def leaderboard_stats(self, *, user: str) -> LeaderboardStats | None:
        """GET /data/leaderboard?time_period=all&user=... → PNL и volume за всё время или None."""
        try:
            resp = await self._client.get(
                f"{self._base}/data/leaderboard",
                params={"time_period": "all", "user": user},
            )
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning("gateway_leaderboard_failed", user=user, error=str(exc))
            return None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None
        return LeaderboardStats(pnl=data.get("pnl", 0), vol=data.get("volume", 0))

    async def accounting_equity(self, *, user: str) -> EquitySnapshot | None:
        """GET /data/accounting/snapshot → EquitySnapshot или None."""
        try:
            resp = await self._client.get(
                f"{self._base}/data/accounting/snapshot",
                params={"user": user},
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            logger.warning(
                "gateway_accounting_equity_failed", user=user, error=str(exc)
            )
            return None
        if not isinstance(data, dict):
            return None
        try:
            return EquitySnapshot(
                cash_balance=float(data["cashBalance"]),
                positions_value=float(data["positionsValue"]),
                equity=float(data["equity"]),
                valuation_time=datetime.fromisoformat(str(data["valuationTime"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "gateway_accounting_equity_parse_failed", user=user, error=str(exc)
            )
            return None

    async def activity_page(
        self,
        *,
        user: str,
        limit: int,
        cursor: str | None = None,
        market: str | None = None,
        event_id: int | None = None,
        activity_types: Sequence[ActivityType] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Страница /data/activity по пользователю, новые первыми."""
        params: dict[str, Any] = {
            "user": user,
            "limit": limit,
            "sort_by": "TIMESTAMP",
            "sort_direction": "DESC",
        }
        if cursor:
            params["cursor"] = cursor
        if event_id is not None:
            params["event_id"] = str(event_id)
        elif market is not None:
            params["condition"] = market
        if activity_types is not None:
            params["type"] = ",".join(t.value for t in activity_types)
        resp = await self._client.get(f"{self._base}/data/activity", params=params)
        resp.raise_for_status()
        return _page(resp.json())

    async def closed_positions_page(
        self, *, user: str, limit: int, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Страница /data/positions?status=CLOSED по пользователю, новые первыми."""
        params: dict[str, Any] = {
            "user": user,
            "limit": limit,
            "status": "CLOSED",
            "sort_by": "TIMESTAMP",
            "sort_direction": "DESC",
        }
        if cursor:
            params["cursor"] = cursor
        resp = await self._client.get(f"{self._base}/data/positions", params=params)
        resp.raise_for_status()
        rows, next_cursor = _page(resp.json())
        normalized = [
            {
                "proxy_wallet": row["proxy_wallet"],
                "event_slug": row["event_slug"],
                "timestamp": row["last_event_at"],
            }
            for row in rows
        ]
        return normalized, next_cursor
