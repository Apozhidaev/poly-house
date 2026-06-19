from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, computed_field
from pydantic.alias_generators import to_camel


class Position(BaseModel):
    model_config = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )

    proxy_wallet: str
    condition_id: str
    timestamp: int
    total_bought: float
    avg_price: float
    realized_pnl: float
    outcome_index: int
    end_date: str

    @computed_field
    @property
    def total_cost(self) -> float:
        return self.total_bought * self.avg_price

    @computed_field
    @property
    def realized_delta(self) -> float:
        return (
            self.realized_pnl / self.total_bought
        )  # realized_delta = close_price - open_price

    @computed_field
    @property
    def exit_price(self) -> float:
        return min(1, max(0, self.avg_price + self.realized_delta))

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()
