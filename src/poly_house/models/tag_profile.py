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

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()
