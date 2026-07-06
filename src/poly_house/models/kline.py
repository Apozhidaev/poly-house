from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Kline(BaseModel):
    condition_id: str
    outcome_index: int
    time: int
    min_bid: float
    max_bid: float
    min_ask: float
    max_ask: float

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()
