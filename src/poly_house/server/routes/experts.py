from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from poly_house.api.holder_boost import HolderBoostClient
from poly_house.config import Settings
from poly_house.models.expert import Expert
from poly_house.server.deps import Db

router = APIRouter(prefix="/experts", tags=["experts"])


@router.get("/{tag_id}")
async def list_experts(tag_id: int, db: Db) -> dict[str, Any]:
    settings = Settings()
    async with HolderBoostClient(settings) as client:
        features = await client.features(tag_id)

    if features is None:
        raise HTTPException(
            status_code=502, detail="Holder boost features unavailable"
        )
    if not features:
        return {"items": []}

    wallets = list(dict.fromkeys(item["feature"] for item in features))
    users, profiles, home_profiles = await _load_docs(db, wallets)
    user_map = {doc["_id"]: doc for doc in users}
    profile_map = {doc["_id"]: doc for doc in profiles}
    home_profile_map = {doc["_id"]: doc for doc in home_profiles}

    experts: list[Expert] = []
    for item in features:
        wallet = item["feature"]
        importance = item["importance"]
        if importance is None:
            continue
        user = user_map.get(wallet)
        if user is None:
            continue
        profile = profile_map.get(wallet)
        home_profile = home_profile_map.get(wallet)
        experts.append(
            Expert(
                proxy_wallet=wallet,
                importance=importance,
                name=user.get("name") or "",
                profile_image=user.get("profile_image") or "",
                pnl=profile.get("pnl") if profile else None,
                total_balance=profile.get("total_balance") if profile else None,
                home_pnl=home_profile.get("pnl") if home_profile else None,
                home_win_rate=home_profile.get("win_rate") if home_profile else None,
                home_markets_count=(
                    home_profile.get("markets_count") if home_profile else None
                ),
            )
        )

    experts.sort(key=lambda expert: expert.importance, reverse=True)
    return {"items": [expert.model_dump() for expert in experts]}


async def _load_docs(
    db: Db, wallets: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    users = await db.users.find(
        {"_id": {"$in": wallets}},
        projection={"_id": 1, "name": 1, "profile_image": 1},
    ).to_list(length=None)
    profiles = await db.profiles.find(
        {"_id": {"$in": wallets}},
        projection={"_id": 1, "pnl": 1, "total_balance": 1},
    ).to_list(length=None)
    home_profiles = await db.home_profiles.find(
        {"_id": {"$in": wallets}},
        projection={"_id": 1, "pnl": 1, "win_rate": 1, "markets_count": 1},
    ).to_list(length=None)
    return users, profiles, home_profiles
