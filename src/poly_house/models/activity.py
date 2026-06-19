from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Activity(BaseModel):
    model_config = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )

    proxy_wallet: str
    condition_id: str
    timestamp: int
    type: str
    side: str = ""
    size: float
    usdc_size: float
    price: float
    outcome_index: int

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()


class MarketActivity(BaseModel):
    condition_id: str
    proxy_wallet: str
    pnl: float
    volume: float
    timestamp: int


def build_market_activity(activities: list[Activity]) -> MarketActivity:
    if not activities:
        raise ValueError("activities must not be empty")

    sorted_activities = sorted(activities, key=lambda x: x.timestamp)

    pnl = 0.0
    volume = 0.0
    for activity in sorted_activities:
        volume += activity.usdc_size
        if activity.type == "TRADE":
            if activity.side == "SELL":
                pnl += activity.usdc_size
            elif activity.side == "BUY":
                pnl -= activity.usdc_size

    last_activity = sorted_activities[-1]

    return MarketActivity(
        condition_id=last_activity.condition_id,
        proxy_wallet=last_activity.proxy_wallet,
        pnl=pnl,
        volume=volume,
        timestamp=last_activity.timestamp,
    )
