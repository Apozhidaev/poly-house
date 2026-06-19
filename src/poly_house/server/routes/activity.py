from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from poly_house.models import Activity
from poly_house.models.activity import build_market_activities
from poly_house.server.deps import Db, Page

router = APIRouter(prefix="/activity", tags=["activity"])
market_activity_router = APIRouter(prefix="/market-activity", tags=["market-activity"])


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


@market_activity_router.get("")
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
    market_activities = build_market_activities(activities)
    market_activities.sort(key=lambda ma: ma.timestamp, reverse=True)
    page_items = market_activities[page.offset : page.offset + page.limit]
    return {"items": [ma.model_dump() for ma in page_items]}
