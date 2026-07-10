from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from poly_house.db import Doc
from poly_house.models.activity import (
    Activity,
    HoldDistribution,
    build_event_activities,
    build_hold_distribution,
    build_market_activities,
    build_user_activity_item,
)
from poly_house.models.market import load_resolved_outcomes
from poly_house.server.deps import Db, Page
from poly_house.utils import day_timestamp

router = APIRouter(prefix="/activity", tags=["activity"])


async def _load_markets_by_condition_id(
    db: Db, condition_ids: list[str]
) -> dict[str, dict[str, Any]]:
    if not condition_ids:
        return {}
    docs = await db.markets.find(
        {"condition_id": {"$in": condition_ids}},
        projection={
            "condition_id": 1,
            "question": 1,
            "group_item_title": 1,
            "clob_token_ids": 1,
            "outcomes": 1,
        },
    ).to_list(length=None)
    return {doc["condition_id"]: doc for doc in docs}


async def _load_events_by_slug(
    db: Db, event_slugs: list[str]
) -> dict[str, dict[str, Any]]:
    if not event_slugs:
        return {}
    docs: list[Doc] = await db.events.find(
        {"slug": {"$in": event_slugs}, "closed": True},
        projection={"slug": 1, "title": 1, "icon": 1},
    ).to_list(length=None)
    return {doc["slug"]: doc for doc in docs}


def _build_activity_query(
    *,
    condition_id: str | None,
    outcome_index: int | None,
    user: str | None,
    activity_type: str | None,
    side: str | None,
    from_ts: int | None,
    to_ts: int | None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if condition_id:
        query["condition_id"] = condition_id
    if outcome_index is not None:
        query["outcome_index"] = outcome_index
    if user:
        query["proxy_wallet"] = user
    if activity_type:
        query["type"] = activity_type.upper()
    if side:
        query["side"] = side.upper()
    timestamp: dict[str, int] = {}
    if from_ts is not None:
        timestamp["$gte"] = from_ts
    if to_ts is not None:
        timestamp["$lte"] = to_ts
    if timestamp:
        query["timestamp"] = timestamp
    return query


@router.get("")
async def list_activity(
    db: Db,
    page: Page,
    condition_id: str | None = None,
    outcome_index: int | None = None,
    user: str | None = None,
    activity_type: Annotated[str | None, Query(alias="type")] = None,
    side: str | None = None,
    from_ts: Annotated[int | None, Query(alias="from")] = None,
    to_ts: Annotated[int | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    query = _build_activity_query(
        condition_id=condition_id,
        outcome_index=outcome_index,
        user=user,
        activity_type=activity_type,
        side=side,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    cursor = (
        db.activities.find(query, {"_id": 0})
        .sort("timestamp", -1)
        .skip(page.offset)
        .limit(page.limit)
    )
    return {"items": await cursor.to_list(page.limit)}


@router.get("/user-event")
async def list_user_event_activity(
    db: Db, user: str, event_slug: str
) -> dict[str, Any]:
    query: dict[str, Any] = {"proxy_wallet": user, "event_slug": event_slug}
    cursor = db.activities.find(query, {"_id": 0}).sort("timestamp", -1)
    docs = await cursor.to_list(None)
    activities = [Activity.model_validate(doc) for doc in docs]
    condition_ids = list({activity.condition_id for activity in activities})
    markets = await _load_markets_by_condition_id(db, condition_ids)
    items = [
        build_user_activity_item(activity, markets.get(activity.condition_id))
        for activity in activities
    ]
    return {"items": [item.model_dump() for item in items]}


@router.get("/by-market")
async def list_market_activity(
    db: Db,
    page: Page,
    user: str,
    condition_id: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {"proxy_wallet": user}
    if condition_id:
        query["condition_id"] = condition_id
    docs = await db.activities.find(query).to_list(length=None)
    activities = [Activity.model_validate(doc) for doc in docs]
    condition_ids = list({activity.condition_id for activity in activities})
    resolved_outcomes = await load_resolved_outcomes(db, condition_ids)
    market_activities = build_market_activities(activities, resolved_outcomes)
    market_activities.sort(key=lambda ma: ma.timestamp, reverse=True)
    page_items = market_activities[page.offset : page.offset + page.limit]
    return {
        "items": [ma.model_dump() for ma in page_items],
        "total": len(market_activities),
    }


@router.get("/by-event")
async def list_event_activity(
    db: Db,
    page: Page,
    user: str,
    event_slug: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {"proxy_wallet": user}
    if event_slug:
        query["event_slug"] = event_slug
    docs = await db.activities.find(query).to_list(length=None)
    activities = [Activity.model_validate(doc) for doc in docs]
    event_activities = build_event_activities(activities)
    event_slugs = list({ea.slug for ea in event_activities})
    event_map = await _load_events_by_slug(db, event_slugs)
    home_event_activities = [
        ea for ea in event_activities if event_map.get(ea.slug) is not None
    ]
    home_event_activities.sort(key=lambda ea: ea.timestamp, reverse=True)
    page_event_activities = home_event_activities[
        page.offset : page.offset + page.limit
    ]
    page_items = [
        {
            **ea.model_dump(),
            "title": event_map[ea.slug]["title"],
            "icon": event_map[ea.slug].get("icon"),
        }
        for ea in page_event_activities
    ]

    return {"items": page_items, "total": len(home_event_activities)}


async def _load_top_hit_wallets(
    db: Db, wallets: list[str], tag_id: int, limit: int
) -> list[str]:
    if not wallets:
        return []
    docs = (
        await db.tag_profiles.find(
            {
                "proxy_wallet": {"$in": wallets},
                "tag_id": tag_id,
                "pnl": {"$gt": 0},
                "hit_rate": {"$ne": None},
            },
            projection={"proxy_wallet": 1},
        )
        .sort("hit_rate", -1)
        .limit(limit)
        .to_list(None)
    )
    return [doc["proxy_wallet"] for doc in docs]


@router.get("/holders")
async def get_holders(
    db: Db,
    condition_id: str,
    tag_id: int,
    limit: int = 200,
) -> dict[str, Any]:
    market = await db.markets.find_one({"condition_id": condition_id})
    if market is None:
        raise HTTPException(status_code=404, detail="Market not found")

    wallets = await db.activities.distinct(
        "proxy_wallet", {"condition_id": condition_id}
    )
    if len(wallets) > limit:
        wallets = await _load_top_hit_wallets(db, wallets, tag_id, limit)
    return {
        "condition_id": condition_id,
        "proxy_wallets": wallets,
    }


@router.get("/hold-distribution")
async def get_hold_distribution(
    db: Db,
    condition_id: str,
    proxy_wallet: str,
) -> dict[str, Any]:
    market = await db.markets.find_one({"condition_id": condition_id})
    if market is None:
        raise HTTPException(status_code=404, detail="Market not found")

    docs = await db.activities.find(
        {"condition_id": condition_id, "proxy_wallet": proxy_wallet}, {"_id": 0}
    ).to_list(None)
    deadline = market.get("deadline")
    if docs and deadline:
        activities = [Activity.model_validate(doc) for doc in docs]
        closed_timestamp = int(deadline.timestamp())
        closed_day_ts = day_timestamp(closed_timestamp)
        holder_distribution = build_hold_distribution(
            proxy_wallet, activities, closed_day_ts
        )
        if holder_distribution:
            return holder_distribution.model_dump()

    return HoldDistribution(proxy_wallet=proxy_wallet).model_dump()
