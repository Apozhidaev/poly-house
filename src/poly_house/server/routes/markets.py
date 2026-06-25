from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from poly_house.models.deadline import (
    deadline_from_end_date,
    deadline_from_group_item_title,
    deadlines_from_market_raws,
)
from poly_house.server.deps import Db, Page
from poly_house.sync.sync_state import parse_iso

router = APIRouter(prefix="/markets", tags=["markets"])


class CheckHomeMarketsRequest(BaseModel):
    condition_ids: list[str]
    tag_ids: list[int] | None = None


async def _resolve_market_deadline(db: Db, market: dict[str, Any]) -> datetime | None:
    deadline = deadline_from_group_item_title(market)
    if deadline is not None:
        if deadline.year is not None:
            return deadline.to_date()
        event_id = market.get("event_id")
        if event_id is not None:
            event = await db.events.find_one({"_id": event_id})
            if event is None:
                raise HTTPException(status_code=500, detail=f"Event {event_id} not found")
            start_date = event.get("start_date")
            if not start_date:
                raise HTTPException(
                    status_code=500, detail=f"Event {event_id} has no start_date"
                )
            event_markets = await db.markets.find({"event_id": event_id}).to_list(
                length=None
            )
            deadlines = deadlines_from_market_raws(
                event_markets, parse_iso(start_date)
            )
            for item in deadlines:
                if item.market_id == market["_id"]:
                    return item.to_date()
    return deadline_from_end_date(market)


@router.get("")
async def list_markets(
    db: Db,
    page: Page,
    closed: bool | None = None,
    neg_risk: bool | None = None,
    event_id: int | None = None,
    tag_ids: list[int] | None = None,
    slug: str | None = None,
    condition_id: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if closed is not None:
        query["closed"] = closed
    if neg_risk is not None:
        query["neg_risk"] = neg_risk
    if event_id:
        query["event_id"] = event_id
    if tag_ids:
        query["tag_ids"] = {"$in": tag_ids}
    if slug:
        query["slug"] = slug
    if condition_id:
        query["condition_id"] = condition_id
    cursor = db.markets.find(query).skip(page.offset).limit(page.limit)
    return {"items": await cursor.to_list(page.limit)}


@router.post("/check-home")
async def check_home_markets(body: CheckHomeMarketsRequest, db: Db) -> dict[str, Any]:
    if not body.condition_ids:
        return {"condition_ids": []}

    query: dict[str, Any] = {"condition_id": {"$in": body.condition_ids}}
    if body.tag_ids:
        query["tag_ids"] = {"$in": body.tag_ids}

    docs = await db.markets.find(query, projection={"condition_id": 1}).to_list(
        length=None
    )
    found = {doc["condition_id"] for doc in docs if doc.get("condition_id")}
    return {
        "condition_ids": [cid for cid in body.condition_ids if cid in found],
    }


@router.get("/deadline/{market_id}")
async def get_market_deadline(market_id: int, db: Db) -> datetime | None:
    market = await db.markets.find_one({"_id": market_id})
    if market is None:
        raise HTTPException(status_code=404, detail="Market not found")
    return await _resolve_market_deadline(db, market)


@router.get("/{market_id}")
async def get_market(market_id: int, db: Db) -> dict[str, Any]:
    doc = await db.markets.find_one({"_id": market_id})
    if doc is None:
        raise HTTPException(status_code=404, detail="Market not found")
    return doc
