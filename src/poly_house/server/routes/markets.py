from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from poly_house.server.deps import Db, Page

router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("")
async def list_markets(
    db: Db,
    page: Page,
    closed: bool | None = None,
    event_id: int | None = None,
    tag_id: int | None = None,
    slug: str | None = None,
    condition_id: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if closed is not None:
        query["closed"] = closed
    if event_id:
        query["event_id"] = event_id
    if tag_id:
        query["tag_ids"] = tag_id
    if slug:
        query["slug"] = slug
    if condition_id:
        query["condition_id"] = condition_id
    cursor = db.markets.find(query).skip(page.offset).limit(page.limit)
    return {"items": await cursor.to_list(page.limit)}


@router.get("/{market_id}")
async def get_market(market_id: int, db: Db) -> dict[str, Any]:
    doc = await db.markets.find_one({"_id": market_id})
    if doc is None:
        raise HTTPException(status_code=404, detail="Market not found")
    return doc
