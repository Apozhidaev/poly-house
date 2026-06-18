from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class SnapshotHolder(BaseModel):
    model_config = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )

    proxy_wallet: str
    amount: float
    roi: float | None = None
    stable_growth: float | None = None
    positions_value: float | None = None
    cash_balance: float | None = None
    total_balance: float | None = None
    tag_win_rate: float | None = None
    tag_stable_growth: float | None = None
    tag_roi: float | None = None


class Snapshot(BaseModel):
    model_config = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )

    market_id: int
    condition_id: str
    captured_at: datetime
    min_order_size: float
    tick_size: float
    best_bid: float | None
    best_ask: float | None
    last_trade_price: float | None
    top_holders: list[list[SnapshotHolder]]

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()
