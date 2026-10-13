from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from poly_house.api.deps import Db, Page

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("")
async def list_tags(
    db: Db,
    page: Page,
    slug: str | None = None,
    has_open_events: bool | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if slug:
        query["slug"] = slug
    if has_open_events is True:
        query["open_events_count"] = {"$gt": 0}
    elif has_open_events is False:
        query["open_events_count"] = {"$eq": 0}
    cursor = db.tags.find(query).skip(page.offset).limit(page.limit)
    return {"items": await cursor.to_list(page.limit)}


@router.get("/{tag_id}")
async def get_tag(tag_id: int, db: Db) -> dict[str, Any]:
    doc = await db.tags.find_one({"_id": tag_id})
    if doc is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return doc
