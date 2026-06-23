from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class TagProfile(BaseModel):
    proxy_wallet: str
    name: str
    tag_id: int
    pnl: float | None = None
    volume: float | None = None
    pnl_growth: float | None = None
    pnl_stability: float | None = None
    win_rate: float | None = None
    markets_count: int

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()


class TagProfileAggregate(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True
    )

    proxy_wallet: str
    win_rate: float | None = None
    pnl_growth: float | None = None
    pnl_stability: float | None = None
    pnl: float | None = None
    volume: float | None = None
