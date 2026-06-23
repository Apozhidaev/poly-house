from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from poly_house.models.ts import TimeSeriesPoint
from poly_house.utils.metrics import fit_trend_by_days


class Activity(BaseModel):
    model_config = ConfigDict(
        extra="ignore", alias_generator=to_camel, populate_by_name=True
    )

    proxy_wallet: str
    condition_id: str
    event_slug: str
    timestamp: int
    type: str
    side: str = ""
    size: float
    usdc_size: float
    price: float
    outcome_index: int
    transaction_hash: str
    is_combo: bool = False

    def to_doc(self) -> dict[str, Any]:
        return self.model_dump()


class MarketActivity(BaseModel):
    condition_id: str
    proxy_wallet: str
    pnl: float
    volume: float
    timestamp: int


class ActivityMetrics(BaseModel):
    win_rate: float | None = None
    pnl: float | None = None
    volume: float | None = None
    pnl_growth: float | None = None
    pnl_stability: float | None = None
    markets_count: int = 0


def compute_pnl(activities: list[Activity]) -> float:
    pnl = 0.0
    for activity in activities:
        match activity.type:
            case "SPLIT":
                pnl -= activity.usdc_size
            case "MERGE":
                pnl += activity.usdc_size
            case "TRADE":
                match activity.side:
                    case "SELL":
                        pnl += activity.usdc_size
                    case "BUY":
                        pnl -= activity.usdc_size
            case "REDEEM":
                pnl += activity.usdc_size

    return pnl


def compute_volume(activities: list[Activity]) -> float:
    volume = 0.0
    for activity in activities:
        volume += activity.usdc_size

    return volume


def build_market_activity(activities: list[Activity]) -> MarketActivity | None:
    if not activities:
        return None
    if any(a.is_combo for a in activities):
        return None

    sorted_activities = sorted(activities, key=lambda x: x.timestamp)
    last_activity = sorted_activities[-1]

    return MarketActivity(
        condition_id=last_activity.condition_id,
        proxy_wallet=last_activity.proxy_wallet,
        pnl=compute_pnl(sorted_activities),
        volume=compute_volume(sorted_activities),
        timestamp=last_activity.timestamp,
    )


def build_market_activities(activities: list[Activity]) -> list[MarketActivity]:
    by_condition: dict[str, list[Activity]] = {}
    for activity in activities:
        by_condition.setdefault(activity.condition_id, []).append(activity)
    return [
        ma
        for group in by_condition.values()
        if (ma := build_market_activity(group)) is not None
    ]


def metrics_from_market_activities(
    market_activities: list[MarketActivity],
) -> ActivityMetrics:
    if not market_activities:
        return ActivityMetrics()
    markets_count = len(market_activities)
    win_count = sum(1 for ma in market_activities if ma.pnl > 0)
    points = [
        TimeSeriesPoint(
            timestamp=datetime.fromtimestamp(ma.timestamp, UTC),
            value=ma.pnl,
        )
        for ma in market_activities
    ]
    if len(points) >= 2:
        _, slope, r2 = fit_trend_by_days(points, use_cumulative=True)
        pnl_growth = slope
        pnl_stability = r2
    else:
        pnl_growth = None
        pnl_stability = None
    return ActivityMetrics(
        win_rate=win_count / markets_count,
        markets_count=markets_count,
        pnl=sum(ma.pnl for ma in market_activities),
        volume=sum(ma.volume for ma in market_activities),
        pnl_growth=pnl_growth,
        pnl_stability=pnl_stability,
    )
