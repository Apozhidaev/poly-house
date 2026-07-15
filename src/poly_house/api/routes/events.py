from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from poly_house.api.deps import Db, Page

router = APIRouter(prefix="/events", tags=["events"])


@router.get("")
async def list_events(
    db: Db,
    page: Page,
    closed: bool | None = None,
    tag_id: int | None = None,
    slug: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if closed is not None:
        query["closed"] = closed
    if tag_id:
        query["tag_ids"] = tag_id
    if slug:
        query["slug"] = slug
    cursor = db.events.find(query).skip(page.offset).limit(page.limit)
    return {"items": await cursor.to_list(page.limit)}


@router.get("/{event_id}")
async def get_event(event_id: int, db: Db) -> dict[str, Any]:
    doc = await db.events.find_one({"_id": event_id})
    if doc is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return doc
