from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class BestPrice(BaseModel):
    condition_id: str
    outcome_index: int
    timestamp: int
    best_bid: float
    best_ask: float
    synced_at: datetime

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()
