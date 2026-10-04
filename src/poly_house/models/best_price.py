from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class BestPrice(BaseModel):
    condition_id: str
    outcome_index: int
    timestamp: int
    best_bid: float
    best_ask: float

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()
