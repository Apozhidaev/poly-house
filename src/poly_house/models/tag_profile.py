from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class TagProfile(BaseModel):
    proxy_wallet: str
    tag_id: int
    markets_count: int
    positions_count: int
    pnl: float | None = None
    volume: float | None = None
    pnl_growth: float | None = None
    pnl_stability: float | None = None
    win_rate: float | None = None

    @property
    def stable_growth(self) -> float | None:
        if self.pnl_growth is None or self.pnl_stability is None:
            return None
        return self.pnl_stability if self.pnl_growth > 0 else 0.0

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()
