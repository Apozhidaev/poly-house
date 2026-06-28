from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from poly_house.models import Activity
from poly_house.models.activity import build_event_activities, build_market_activities
from poly_house.models.market import load_resolved_outcomes
from poly_house.server.deps import Db, Page

router = APIRouter(prefix="/activity", tags=["activity"])


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


@router.get("/user")
async def list_user_activity(
    db: Db,
    page: Page,
    user: str,
) -> dict[str, Any]:
    query: dict[str, Any] = {"proxy_wallet": user}
    cursor = (
        db.activities.find(query, {"_id": 0})
        .sort("timestamp", -1)
        .skip(page.offset)
        .limit(page.limit)
    )
    return {"items": await cursor.to_list(page.limit)}


@router.get("/not-relevant")
async def list_not_relevant_activity(db: Db, page: Page) -> dict[str, Any]:
    market_docs = await db.markets.find(
        {"relevant": False},
        projection={"condition_id": 1},
    ).to_list(length=None)
    condition_ids = [doc["condition_id"] for doc in market_docs]
    if not condition_ids:
        return {"items": []}

    cursor = (
        db.activities.find(
            {"condition_id": {"$in": condition_ids}},
            {"_id": 0},
        )
        .sort("timestamp", -1)
        .skip(page.offset)
        .limit(page.limit)
    )
    return {"items": await cursor.to_list(page.limit)}


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
    event_docs = await db.events.find(
        {"slug": {"$in": event_slugs}},
        projection={"slug": 1, "title": 1, "icon": 1},
    ).to_list(length=None)
    event_map = {doc["slug"]: doc for doc in event_docs}
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
            "icon": event_map[ea.slug]["icon"],
        }
        for ea in page_event_activities
    ]

    return {"items": page_items, "total": len(home_event_activities)}
