from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class TagProfile(BaseModel):
    proxy_wallet: str
    tag_id: int
    markets_count: int
    pnl: float | None = None
    volume: float | None = None
    pnl_growth: float | None = None
    pnl_stability: float | None = None
    win_rate: float | None = None
    avg_entry_price: float | None = None
    avg_exit_price: float | None = None
    avg_buy_price: float | None = None
    avg_sell_price: float | None = None

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()
