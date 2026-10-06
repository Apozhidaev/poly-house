from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from poly_house.api.deps import Db, Page

router = APIRouter(prefix="/events-view", tags=["events-view"])


@router.get("")
async def list_events_view(db: Db, page: Page) -> dict[str, Any]:
    cursor = db.events_view.find({}).skip(page.offset).limit(page.limit)
    items = await cursor.to_list(page.limit)
    total = await db.events_view.count_documents({})
    return {"items": items, "total": total}
