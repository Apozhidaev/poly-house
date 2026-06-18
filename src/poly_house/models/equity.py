from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class EquitySnapshot(BaseModel):
    """Row from accounting snapshot equity.csv."""

    cash_balance: float
    positions_value: float
    equity: float
    valuation_time: datetime
