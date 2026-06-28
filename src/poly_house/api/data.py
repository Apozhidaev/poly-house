from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from typing import Any

from poly_house.api.rate_limited_client import RateLimitedClient
from poly_house.api.rate_limiter import RateLimiter
from poly_house.models import EquitySnapshot, LeaderboardStats

BASE_URL = "https://data-api.polymarket.com"
rate_limiter = RateLimiter(interval=1, count=5)


def _parse_valuation_time(raw: str) -> datetime:
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    return datetime.fromisoformat(raw)


def parse_equity_csv(text: str) -> list[EquitySnapshot]:
    reader = csv.DictReader(io.StringIO(text))
    rows: list[EquitySnapshot] = []
    for row in reader:
        rows.append(
            EquitySnapshot(
                cash_balance=float(row["cashBalance"]),
                positions_value=float(row["positionsValue"]),
                equity=float(row["equity"]),
                valuation_time=_parse_valuation_time(row["valuationTime"]),
            )
        )
    return rows


def parse_equity_zip(data: bytes) -> list[EquitySnapshot] | None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        try:
            text = archive.read("equity.csv").decode()
        except KeyError:
            return None
    return parse_equity_csv(text)


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

    async def positions_value(
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

    async def accounting_equity(
        self,
        *,
        user: str,
    ) -> list[EquitySnapshot] | None:
        """GET /v1/accounting/snapshot → rows from equity.csv or None."""
        data = await self._http.get_bytes_or_none(
            f"{BASE_URL}/v1/accounting/snapshot",
            params={"user": user},
            rate_limiter=rate_limiter,
        )
        if data is None:
            return None
        return parse_equity_zip(data)

    async def activity_page(
        self,
        *,
        user: str,
        limit: int,
        offset: int,
        market: str | None = None,
        event_id: int | None = None,
        activity_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Страница /activity по пользователю, новые первыми."""
        params: dict[str, Any] = {
            "user": user,
            "limit": limit,
            "offset": offset,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        }
        if event_id is not None:
            params["eventId"] = event_id
        elif market is not None:
            params["market"] = market
        if activity_type is not None:
            params["type"] = activity_type
        return (
            await self._http.get(
                f"{BASE_URL}/activity",
                params=params,
                rate_limiter=rate_limiter,
            )
            or []
        )

    async def closed_positions_page(
        self, *, user: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        """Страница /closed-positions по пользователю, новые первыми."""
        params: dict[str, Any] = {
            "user": user,
            "limit": limit,
            "offset": offset,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        }
        return (
            await self._http.get(
                f"{BASE_URL}/closed-positions",
                params=params,
                rate_limiter=rate_limiter,
            )
            or []
        )
