from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from poly_house.server.deps import Db
from poly_house.utils.tag_metrics import (
    aggregate_wallet_tag_metrics,
    load_tag_profile_tag_metrics,
    load_tag_weights,
    wallet_has_tag_metrics,
)

router = APIRouter(prefix="/profile-summary", tags=["profile-summary"])

TagIds = Annotated[list[int], Query(min_length=1)]


class ProfileSummaryBatchRequest(BaseModel):
    proxy_wallets: list[str]
    tag_ids: list[int] = Field(min_length=1)


async def _aggregate_for_wallets(
    db: Db, wallets: list[str], tag_ids: list[int]
) -> list[dict[str, Any]]:
    if not wallets:
        return []
    profiles = await db.profiles.find({"_id": {"$in": wallets}}).to_list(length=None)
    profile_map = {profile["_id"]: profile for profile in profiles}
    tag_weights = await load_tag_weights(db, tag_ids)
    (
        win_rates,
        pnl_growths,
        pnl_stabilities,
        pnls,
        volumes,
        markets_counts,
        win_holder_counts,
        lose_holder_counts,
        hit_rates,
        error_rates,
    ) = await load_tag_profile_tag_metrics(db, wallets, tag_ids)
    metric_maps = (win_rates, pnl_growths, pnl_stabilities, pnls, volumes)
    items: list[dict[str, Any]] = []
    for profile in profiles:
        if wallet_has_tag_metrics(profile["_id"], tag_ids, *metric_maps):
            aggregate = aggregate_wallet_tag_metrics(
                profile["_id"],
                tag_ids,
                tag_weights,
                win_rates,
                pnl_growths,
                pnl_stabilities,
                pnls,
                volumes,
                markets_counts,
                win_holder_counts,
                lose_holder_counts,
                hit_rates,
                error_rates,
            )
            items.append(
                {
                    "proxy_wallet": profile["_id"],
                    "pnl": profile.get("pnl"),
                    "total_balance": profile.get("total_balance"),
                    "tag_pnl": aggregate.pnl,
                }
            )
        else:
            items.append(
                {
                    "proxy_wallet": profile["_id"],
                    "pnl": profile.get("pnl"),
                    "total_balance": profile.get("total_balance"),
                    "tag_pnl": None,
                }
            )
    return items


@router.post("/batch")
async def batch_profile_summaries(
    body: ProfileSummaryBatchRequest, db: Db
) -> dict[str, Any]:
    if not body.proxy_wallets:
        return {"items": []}
    items = await _aggregate_for_wallets(db, body.proxy_wallets, body.tag_ids)
    return {"items": items}


@router.get("/{proxy_wallet}")
async def get_profile_summary(
    proxy_wallet: str, db: Db, tag_id: TagIds
) -> dict[str, Any]:
    items = await _aggregate_for_wallets(db, [proxy_wallet], tag_id)
    if not items:
        raise HTTPException(status_code=404, detail="Profile summary not found")
    return items[0]
