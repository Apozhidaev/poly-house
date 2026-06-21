from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from poly_house.models.ts import TimeSeriesPoint
from poly_house.utils.math import fit_trend_by_days


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
    entry_price: float | None = None
    exit_price: float | None = None
    avg_buy_price: float | None = None
    avg_sell_price: float | None = None


class ActivityMetrics(BaseModel):
    win_rate: float | None = None
    markets_count: int = 0
    pnl: float | None = None
    volume: float | None = None
    pnl_growth: float | None = None
    pnl_stability: float | None = None
    avg_entry_price: float | None = None
    avg_exit_price: float | None = None
    avg_buy_price: float | None = None
    avg_sell_price: float | None = None


def _mean_non_null(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def build_market_activity(activities: list[Activity]) -> MarketActivity | None:
    if not activities:
        return None
    if any(a.is_combo for a in activities):
        return None

    sorted_activities = sorted(activities, key=lambda x: x.timestamp)

    pnl = 0.0
    volume = 0.0
    buy_size = 0.0
    buy_usdc = 0.0
    sell_size = 0.0
    sell_usdc = 0.0
    buy_prices: list[float] = []
    sell_prices: list[float] = []
    for activity in sorted_activities:
        volume += activity.usdc_size
        match activity.type:
            case "SPLIT":
                pnl -= activity.usdc_size
            case "MERGE":
                pnl += activity.usdc_size
            case "CONVERSION":
                pnl -= activity.usdc_size
            case "TRADE":
                match activity.side:
                    case "SELL":
                        sell_size += activity.size
                        sell_usdc += activity.usdc_size
                        pnl += activity.usdc_size
                        sell_prices.append(activity.price)
                    case "BUY":
                        buy_size += activity.size
                        buy_usdc += activity.usdc_size
                        pnl -= activity.usdc_size
                        buy_prices.append(activity.price)
            case "REDEEM":
                pnl += activity.usdc_size

    last_activity = sorted_activities[-1]
    entry_price = buy_usdc / buy_size if buy_size > 0 else None
    exit_price = sell_usdc / sell_size if sell_size > 0 else None
    avg_buy_price = sum(buy_prices) / len(buy_prices) if buy_prices else None
    avg_sell_price = sum(sell_prices) / len(sell_prices) if sell_prices else None

    return MarketActivity(
        condition_id=last_activity.condition_id,
        proxy_wallet=last_activity.proxy_wallet,
        pnl=pnl,
        volume=volume,
        timestamp=last_activity.timestamp,
        entry_price=entry_price,
        exit_price=exit_price,
        avg_buy_price=avg_buy_price,
        avg_sell_price=avg_sell_price,
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
        avg_entry_price=_mean_non_null([ma.entry_price for ma in market_activities]),
        avg_exit_price=_mean_non_null([ma.exit_price for ma in market_activities]),
        avg_buy_price=_mean_non_null([ma.avg_buy_price for ma in market_activities]),
        avg_sell_price=_mean_non_null([ma.avg_sell_price for ma in market_activities]),
    )
