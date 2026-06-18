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

    @staticmethod
    def stable_growth_value(
        pnl_growth: float | None,
        pnl_stability: float | None,
    ) -> float | None:
        if pnl_growth is None or pnl_stability is None:
            return None
        return pnl_stability if pnl_growth > 0 else 0.0

    @property
    def stable_growth(self) -> float | None:
        return self.stable_growth_value(self.pnl_growth, self.pnl_stability)

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()
