from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Profile(BaseModel):
    """User profile metrics materialized from Polymarket API."""

    proxy_wallet: str
    name: str
    pnl: float | None = None
    volume: float | None = None
    pnl_growth: float | None = None
    pnl_stability: float | None = None
    positions_value: float | None = None
    cash_balance: float | None = None
    total_balance: float | None = None
    win_rate: float | None = None

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump(exclude={"proxy_wallet"})
