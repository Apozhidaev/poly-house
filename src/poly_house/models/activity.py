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


class UserActivityItem(Activity):
    title: str | None = None
    group_item_title: str | None = None
    asset: str | None = None
    outcome: str | None = None


def build_user_activity_item(
    activity: Activity, market: dict[str, Any] | None
) -> UserActivityItem:
    kwargs = activity.model_dump()
    if market is not None:
        clob_token_ids = market.get("clob_token_ids") or []
        outcomes = market.get("outcomes") or []
        outcome_index = activity.outcome_index
        kwargs.update(
            title=market["question"],
            group_item_title=market.get("group_item_title"),
            asset=clob_token_ids[outcome_index]
            if outcome_index < len(clob_token_ids)
            else None,
            outcome=outcomes[outcome_index]
            if outcome_index < len(outcomes)
            else None,
        )
    return UserActivityItem(**kwargs)


class MarketActivity(BaseModel):
    condition_id: str
    proxy_wallet: str
    pnl: float
    volume: float
    timestamp: int
    win_holder: bool
    lose_holder: bool


class EventActivity(BaseModel):
    slug: str
    proxy_wallet: str
    pnl: float
    volume: float
    timestamp: int
    markets_count: int


class ActivityMetrics(BaseModel):
    win_rate: float | None = None
    pnl: float | None = None
    volume: float | None = None
    pnl_growth: float | None = None
    pnl_stability: float | None = None
    markets_count: int = 0
    win_holder_count: int = 0
    lose_holder_count: int = 0


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


def is_outcome_holder(activities: list[Activity], outcome_index: int) -> bool:
    for activity in activities:
        match activity.type:
            case "SPLIT":
                return True
            case "MERGE":
                return True
            case "TRADE":
                if activity.outcome_index == outcome_index:
                    return True

    return False


def build_market_activity(
    activities: list[Activity], resolved_outcome_index: int
) -> MarketActivity | None:
    if not activities:
        return None
    if any(a.is_combo or a.type == "CONVERSION" for a in activities):
        return None

    win_holder = is_outcome_holder(activities, resolved_outcome_index)
    lose_holder = is_outcome_holder(activities, 1 - resolved_outcome_index)

    first_activity = activities[0]
    return MarketActivity(
        condition_id=first_activity.condition_id,
        proxy_wallet=first_activity.proxy_wallet,
        pnl=compute_pnl(activities),
        volume=compute_volume(activities),
        timestamp=max(a.timestamp for a in activities),
        win_holder=win_holder,
        lose_holder=lose_holder,
    )


def build_market_activities(
    activities: list[Activity], resolved_outcomes: dict[str, int]
) -> list[MarketActivity]:
    by_condition: dict[str, list[Activity]] = {}
    for activity in activities:
        by_condition.setdefault(activity.condition_id, []).append(activity)
    result: list[MarketActivity] = []
    for condition_id, group in by_condition.items():
        resolved_outcome_index = resolved_outcomes.get(condition_id)
        if resolved_outcome_index is None:
            continue
        if ma := build_market_activity(group, resolved_outcome_index):
            result.append(ma)
    return result


def metrics_from_market_activities(
    market_activities: list[MarketActivity],
) -> ActivityMetrics:
    if not market_activities:
        return ActivityMetrics()
    markets_count = len(market_activities)
    win_holder_count = sum(
        1 for ma in market_activities if ma.win_holder and not ma.lose_holder
    )
    lose_holder_count = sum(
        1 for ma in market_activities if ma.lose_holder and not ma.win_holder
    )
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
        win_holder_count=win_holder_count,
        lose_holder_count=lose_holder_count,
    )


def build_event_activity(activities: list[Activity]) -> EventActivity | None:
    if not activities:
        return None
    if any(a.is_combo for a in activities):
        return None

    market_activities = [a for a in activities if a.type != "CONVERSION"]
    if not market_activities:
        return None

    first_activity = activities[0]
    return EventActivity(
        slug=first_activity.event_slug,
        proxy_wallet=first_activity.proxy_wallet,
        pnl=compute_pnl(market_activities),
        volume=compute_volume(market_activities),
        timestamp=max(a.timestamp for a in activities),
        markets_count=len({a.condition_id for a in market_activities}),
    )


def build_event_activities(activities: list[Activity]) -> list[EventActivity]:
    by_event: dict[str, list[Activity]] = {}
    for activity in activities:
        by_event.setdefault(activity.event_slug, []).append(activity)
    result: list[EventActivity] = []
    for event_slug, group in by_event.items():
        if ea := build_event_activity(group):
            result.append(ea)
    return result
