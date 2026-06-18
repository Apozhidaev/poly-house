from __future__ import annotations

from datetime import datetime
from typing import Any

from poly_house.api.rate_limited_client import RateLimitedClient
from poly_house.api.rate_limiter import RateLimiter
from poly_house.models import PnlTimeSeries, TimeSeriesPoint

BASE_URL = "https://user-pnl-api.polymarket.com"
rate_limiter = RateLimiter(interval=1, count=5)


class PnlClient:
    def __init__(self, http: RateLimitedClient) -> None:
        self._http = http

    async def fetch_pnl_time_series(
        self,
        address: str,
    ) -> PnlTimeSeries | None:
        """GET /user-pnl?user_address={addr}&interval=all&fidelity=1d → PnlTimeSeries."""
        url = f"{BASE_URL}/user-pnl"
        params = {
            "user_address": address,
            "interval": "all",
            "fidelity": "1d",
        }
        data = await self._http.get_or_none(
            url, params=params, rate_limiter=rate_limiter
        )

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

        return PnlTimeSeries(wallet_address=address, data=points)
