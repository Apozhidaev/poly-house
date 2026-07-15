from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from poly_house.api.deps import Db, Page

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileBatchRequest(BaseModel):
    proxy_wallets: list[str]


@router.get("")
async def list_profiles(db: Db, page: Page) -> dict[str, Any]:
    cursor = db.profiles.find().skip(page.offset).limit(page.limit)
    return {"items": await cursor.to_list(page.limit)}


@router.post("/batch")
async def batch_profiles(body: ProfileBatchRequest, db: Db) -> dict[str, Any]:
    if not body.proxy_wallets:
        return {"items": []}
    docs = await db.profiles.find({"_id": {"$in": body.proxy_wallets}}).to_list(
        length=None
    )
    return {"items": docs}


@router.get("/{proxy_wallet}")
async def get_profile(proxy_wallet: str, db: Db) -> dict[str, Any]:
    doc = await db.profiles.find_one({"_id": proxy_wallet})
    if doc is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return doc
