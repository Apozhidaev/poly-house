from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from poly_house.api.deps import Db, Page

router = APIRouter(prefix="/home-profile", tags=["home-profile"])


class HomeProfileBatchRequest(BaseModel):
    proxy_wallets: list[str]


@router.get("")
async def list_home_profiles(db: Db, page: Page) -> dict[str, Any]:
    cursor = db.home_profiles.find().skip(page.offset).limit(page.limit)
    return {"items": await cursor.to_list(page.limit)}


@router.post("/batch")
async def batch_home_profiles(body: HomeProfileBatchRequest, db: Db) -> dict[str, Any]:
    if not body.proxy_wallets:
        return {"items": []}
    docs = await db.home_profiles.find({"_id": {"$in": body.proxy_wallets}}).to_list(
        length=None
    )
    return {"items": docs}


@router.get("/{proxy_wallet}")
async def get_home_profile(proxy_wallet: str, db: Db) -> dict[str, Any]:
    doc = await db.home_profiles.find_one({"_id": proxy_wallet})
    if doc is None:
        raise HTTPException(status_code=404, detail="Home profile not found")
    return doc
