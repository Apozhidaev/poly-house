from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class HomeProfile(BaseModel):
    """User profile metrics computed from synced activities."""

    proxy_wallet: str
    pnl: float | None = None
    volume: float | None = None
    pnl_growth: float | None = None
    pnl_stability: float | None = None
    win_rate: float | None = None
    markets_count: int
    win_holder_count: int
    lose_holder_count: int
    synced_at: datetime

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump(exclude={"proxy_wallet"})
