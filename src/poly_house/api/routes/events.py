from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from poly_house.api.deps import Db, Page

router = APIRouter(prefix="/events", tags=["events"])


async def _attach_markets(db: Db, events: list[dict[str, Any]]) -> None:
    market_ids: list[int] = []
    for event in events:
        market_ids.extend(event.get("market_ids") or [])
    if not market_ids:
        for event in events:
            event["markets"] = []
        return

    docs = await db.markets.find({"_id": {"$in": market_ids}}).to_list(length=None)
    by_id = {doc["_id"]: doc for doc in docs}
    for event in events:
        event["markets"] = [
            by_id[mid] for mid in (event.get("market_ids") or []) if mid in by_id
        ]


@router.get("")
async def list_events(
    db: Db,
    page: Page,
    closed: bool | None = None,
    tag_id: Annotated[list[int] | None, Query()] = None,
    slug: str | None = None,
    include_market: bool = False,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if closed is not None:
        query["closed"] = closed
    if tag_id:
        query["tag_ids"] = {"$in": tag_id}
    if slug:
        query["slug"] = slug
    cursor = db.events.find(query).skip(page.offset).limit(page.limit)
    items = await cursor.to_list(page.limit)
    if include_market:
        await _attach_markets(db, items)
    total = await db.events.count_documents(query)
    return {"items": items, "total": total}


@router.get("/{event_id}")
async def get_event(event_id: int, db: Db) -> dict[str, Any]:
    doc = await db.events.find_one({"_id": event_id})
    if doc is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return doc
