from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from poly_house.api.deps import Db, Page

router = APIRouter(prefix="/series", tags=["series"])


@router.get("")
async def list_series(
    db: Db,
    page: Page,
    slug: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if slug:
        query["slug"] = slug
    cursor = db.series.find(query).skip(page.offset).limit(page.limit)
    return {"items": await cursor.to_list(page.limit)}


@router.get("/{series_id}")
async def get_series(series_id: int, db: Db) -> dict[str, Any]:
    doc = await db.series.find_one({"_id": series_id})
    if doc is None:
        raise HTTPException(status_code=404, detail="Series not found")
    return doc
