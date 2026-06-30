from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from poly_house.server.deps import Db, Page

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
async def list_users(db: Db, page: Page) -> dict[str, Any]:
    cursor = db.users.find().skip(page.offset).limit(page.limit)
    return {"items": await cursor.to_list(page.limit)}


@router.get("/{proxy_wallet}")
async def get_user(proxy_wallet: str, db: Db) -> dict[str, Any]:
    doc = await db.users.find_one({"_id": proxy_wallet})
    if doc is None:
        raise HTTPException(status_code=404, detail="User not found")
    return doc
