from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class Profile(BaseModel):
    """User profile metrics materialized from Polymarket API."""

    proxy_wallet: str
    name: str
    pnl: float | None = None
    volume: float | None = None
    stable_growth: float | None = None
    positions_value: float | None = None
    cash_balance: float | None = None
    total_balance: float | None = None
    house_pnl: float | None = None
    house_volume: float | None = None
    house_stable_growth: float | None = None
    house_win_rate: float | None = None
    house_markets_count: int
    house_avg_entry_price: float | None = None
    house_avg_exit_price: float | None = None
    house_avg_buy_price: float | None = None
    house_avg_sell_price: float | None = None
    synced_at: datetime

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump(exclude={"proxy_wallet"})
