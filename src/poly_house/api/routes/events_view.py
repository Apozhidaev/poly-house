from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from poly_house.api.deps import Db, Page

router = APIRouter(prefix="/events-view", tags=["events-view"])


@router.get("")
async def list_events_view(
    db: Db,
    page: Page,
    tag_id: Annotated[list[int] | None, Query()] = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if tag_id:
        query["tag_ids"] = {"$in": tag_id}
    cursor = db.events_view.find(query).skip(page.offset).limit(page.limit)
    items = await cursor.to_list(page.limit)
    total = await db.events_view.count_documents(query)
    return {"items": items, "total": total}
