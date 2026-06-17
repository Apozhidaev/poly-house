from pydantic import BaseModel

from .ts import TimeSeriesPoint


class PnlTimeSeries(BaseModel):
    """Full PnL time series for a user."""

    wallet_address: str
    data: list[TimeSeriesPoint]
