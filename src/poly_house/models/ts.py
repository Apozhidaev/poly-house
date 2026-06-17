"""Generic time series models."""

from datetime import datetime

from pydantic import BaseModel


class TimeSeriesPoint(BaseModel):
    """Single point in a time series (timestamp, value). Reusable for PnL, metrics, etc."""

    timestamp: datetime
    value: float
