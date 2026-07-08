from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from poly_house.models.ts import TimeSeriesPoint
from poly_house.utils.metrics import fit_trend_by_days
from poly_house.utils.time import DAY_SEC, day_timestamp


class ActivityType(StrEnum):
    TRADE = "TRADE"
    SPLIT = "SPLIT"
    MERGE = "MERGE"
    REDEEM = "REDEEM"
    CONVERSION = "CONVERSION"


MARKET_ACTIVITY_TYPES: tuple[ActivityType, ...] = (
    ActivityType.TRADE,
    ActivityType.SPLIT,
    ActivityType.MERGE,
    ActivityType.REDEEM,
)

ALL_ACTIVITY_TYPES: tuple[ActivityType, ...] = MARKET_ACTIVITY_TYPES + (
    ActivityType.CONVERSION,
)

ACTIVITY_OVERFLOW_TYPE = "ACTIVITY_OVERFLOW"
ACTIVITY_OVERFLOW_TIMESTAMP = 4102444800  # 2100-01-01 UTC — watermark sentinel
ACTIVITY_OVERFLOW_CONDITION_ID = "0x0000000000000000000000000000000000000000"
ACTIVITY_OVERFLOW_OUTCOME_INDEX = 999


def activity_overflow_marker(*, proxy_wallet: str, event_slug: str) -> dict[str, Any]:
    """Synthetic activity when event activity cannot be fully fetched."""
    return {
        "proxyWallet": proxy_wallet,
        "timestamp": ACTIVITY_OVERFLOW_TIMESTAMP,
        "conditionId": ACTIVITY_OVERFLOW_CONDITION_ID,
        "type": ACTIVITY_OVERFLOW_TYPE,
        "size": 0,
        "usdcSize": 0,
        "transactionHash": "",
        "price": 0,
        "asset": "",
        "side": "",
        "outcomeIndex": ACTIVITY_OVERFLOW_OUTCOME_INDEX,
        "title": "",
        "slug": "",
        "icon": "",
        "eventSlug": event_slug,
        "outcome": "",
        "name": "",
        "pseudonym": "",
        "bio": "",
        "profileImage": "",
        "profileImageOptimized": "",
    }


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
    title: str = ""
    group_item_title: str = ""
    asset: str = ""
    outcome: str = ""


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
            group_item_title=market.get("group_item_title", ""),
            asset=(
                clob_token_ids[outcome_index]
                if outcome_index < len(clob_token_ids)
                else ""
            ),
            outcome=outcomes[outcome_index] if outcome_index < len(outcomes) else "",
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


class HolderDistribution(BaseModel):
    proxy_wallet: str
    distribution: dict[int, int]


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


def _compute_outcome_hold_days(
    sorted_activities: list[Activity],
    outcome_index: int,
    closed_day_ts: int,
    min_usdc: float,
) -> set[int]:
    hold_usdc = 0.0
    result: set[int] = set()
    last_hold_day: int | None = None
    for activity in sorted_activities:
        match activity.type:
            case "SPLIT":
                hold_usdc += activity.usdc_size
            case "MERGE":
                hold_usdc -= activity.usdc_size
            case "TRADE":
                if activity.outcome_index == outcome_index:
                    match activity.side:
                        case "SELL":
                            hold_usdc -= activity.usdc_size
                        case "BUY":
                            hold_usdc += activity.usdc_size
            case "REDEEM":
                hold_usdc = 0.0

        day_ts = day_timestamp(activity.timestamp)
        hold_day = None if hold_usdc < min_usdc else day_ts

        if hold_day is not None:
            result.add(hold_day)

        if last_hold_day is not None:
            result.update(range(last_hold_day + DAY_SEC, day_ts + DAY_SEC, DAY_SEC))

        last_hold_day = hold_day

    if last_hold_day is not None:
        if closed_day_ts > last_hold_day:
            result.update(
                range(last_hold_day + DAY_SEC, closed_day_ts + DAY_SEC, DAY_SEC)
            )

    return result


def _compute_holder_distribution(
    activities: list[Activity],
    closed_day_ts: int,
    min_usdc: float,
) -> dict[int, int]:
    side_by_day: dict[int, int] = {}
    sorted_activities = sorted(activities, key=lambda x: x.timestamp)
    set_0 = _compute_outcome_hold_days(sorted_activities, 0, closed_day_ts, min_usdc)
    set_1 = _compute_outcome_hold_days(sorted_activities, 1, closed_day_ts, min_usdc)
    for day in set_0.union(set_1):
        if day > closed_day_ts:
            continue
        side_0 = day in set_0
        side_1 = day in set_1
        if side_0 and not side_1:
            side_by_day[day] = 0
        elif side_1 and not side_0:
            side_by_day[day] = 1

    return {
        ((closed_day_ts - day) // DAY_SEC): side for day, side in side_by_day.items()
    }


def build_holder_distributions(
    activities: list[Activity],
    closed_day_ts: int,
    *,
    min_usdc: float = 1,
    skip_wallets: list[str] | None = None,
) -> tuple[list[HolderDistribution], int]:
    by_holder: dict[str, list[Activity]] = {}
    for activity in activities:
        by_holder.setdefault(activity.proxy_wallet, []).append(activity)
    result: list[HolderDistribution] = []
    max_days_left = -1
    skip = skip_wallets or []
    for proxy_wallet, group in by_holder.items():
        if proxy_wallet in skip:
            continue
        distribution = _compute_holder_distribution(group, closed_day_ts, min_usdc)
        if distribution:
            max_days_left = max(max_days_left, max(distribution.keys()))
            result.append(
                HolderDistribution(proxy_wallet=proxy_wallet, distribution=distribution)
            )
    return result, max_days_left
