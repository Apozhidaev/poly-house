from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from poly_house.clients.holder_boost import HolderBoostClient
from poly_house.config import Settings
from poly_house.models.expert import Expert
from poly_house.api.deps import Db

router = APIRouter(prefix="/experts", tags=["experts"])


@router.get("/{tag_id}")
async def list_experts(tag_id: int, db: Db) -> dict[str, Any]:
    settings = Settings()
    async with HolderBoostClient(settings) as client:
        features = await client.features(tag_id)

    if features is None:
        raise HTTPException(status_code=502, detail="Holder boost features unavailable")
    if not features:
        return {"items": []}

    wallets = list(dict.fromkeys(item["feature"] for item in features))
    users, profiles, tag_profiles = await _load_docs(db, wallets, tag_id)
    user_map = {doc["_id"]: doc for doc in users}
    profile_map = {doc["_id"]: doc for doc in profiles}
    tag_profile_map = {doc["proxy_wallet"]: doc for doc in tag_profiles}

    experts: list[Expert] = []
    for item in features:
        wallet = item["feature"]
        importance = item["importance"]
        if importance <= 0:
            continue
        user = user_map.get(wallet)
        if user is None:
            continue
        profile = profile_map.get(wallet)
        tag_profile = tag_profile_map.get(wallet)
        experts.append(
            Expert(
                proxy_wallet=wallet,
                importance=importance,
                name=user.get("name") or "",
                profile_image=user.get("profile_image") or "",
                pnl=profile.get("pnl") if profile else None,
                total_balance=profile.get("total_balance") if profile else None,
                tag_pnl=tag_profile.get("pnl") if tag_profile else None,
                tag_win_rate=tag_profile.get("win_rate") if tag_profile else None,
                tag_markets_count=(
                    tag_profile.get("markets_count") if tag_profile else None
                ),
            )
        )

    experts.sort(key=lambda expert: expert.importance, reverse=True)
    return {"items": [expert.model_dump() for expert in experts]}


async def _load_docs(
    db: Db, wallets: list[str], tag_id: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    users = await db.users.find(
        {"_id": {"$in": wallets}},
        projection={"_id": 1, "name": 1, "profile_image": 1},
    ).to_list(length=None)
    profiles = await db.profiles.find(
        {"_id": {"$in": wallets}},
        projection={"_id": 1, "pnl": 1, "total_balance": 1},
    ).to_list(length=None)
    tag_profiles = await db.tag_profiles.find(
        {"proxy_wallet": {"$in": wallets}, "tag_id": tag_id},
        projection={"proxy_wallet": 1, "pnl": 1, "win_rate": 1, "markets_count": 1},
    ).to_list(length=None)
    return users, profiles, tag_profiles
