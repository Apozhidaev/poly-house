from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from poly_house.models.tag_profile import TagProfileAggregate
from poly_house.server.deps import Db, Page
from poly_house.utils.tag_metrics import (
    aggregate_wallet_tag_metrics,
    load_tag_profile_tag_metrics,
    load_tag_weights,
    wallet_has_tag_metrics,
)

router = APIRouter(prefix="/tag-profile", tags=["tag-profile"])

TagIds = Annotated[list[int], Query(min_length=1)]


class TagProfileBatchRequest(BaseModel):
    proxy_wallets: list[str]
    tag_ids: list[int] = Field(min_length=1)


async def _load_page_wallets(db: Db, tag_ids: list[int], page: Page) -> list[str]:
    pipeline: list[dict[str, Any]] = [
        {"$match": {"tag_id": {"$in": tag_ids}}},
        {"$group": {"_id": "$proxy_wallet"}},
        {"$sort": {"_id": 1}},
        {"$skip": page.offset},
        {"$limit": page.limit},
    ]
    cursor = await db.tag_profiles.aggregate(pipeline)
    docs = await cursor.to_list(length=page.limit)
    return [doc["_id"] for doc in docs]


async def _aggregate_for_wallets(
    db: Db, wallets: list[str], tag_ids: list[int]
) -> list[TagProfileAggregate]:
    if not wallets:
        return []
    tag_weights = await load_tag_weights(db, tag_ids)
    win_rates, pnl_growths, pnl_stabilities, pnls, volumes, markets_counts, win_holder_counts, lose_holder_counts, strict_win_counts = (
        await load_tag_profile_tag_metrics(db, wallets, tag_ids)
    )
    metric_maps = (win_rates, pnl_growths, pnl_stabilities, pnls, volumes)
    items: list[TagProfileAggregate] = []
    for wallet in wallets:
        if not wallet_has_tag_metrics(wallet, tag_ids, *metric_maps):
            continue
        items.append(
            aggregate_wallet_tag_metrics(
                wallet,
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
                strict_win_counts,
            )
        )
    return items


@router.get("")
async def list_tag_profiles(
    db: Db, page: Page, tag_id: TagIds
) -> dict[str, Any]:
    wallets = await _load_page_wallets(db, tag_id, page)
    items = await _aggregate_for_wallets(db, wallets, tag_id)
    return {"items": [item.model_dump() for item in items]}


@router.post("/batch")
async def batch_tag_profiles(
    body: TagProfileBatchRequest, db: Db
) -> dict[str, Any]:
    if not body.proxy_wallets:
        return {"items": []}
    items = await _aggregate_for_wallets(db, body.proxy_wallets, body.tag_ids)
    return {"items": [item.model_dump() for item in items]}


@router.get("/{proxy_wallet}")
async def get_tag_profile(
    proxy_wallet: str, db: Db, tag_id: TagIds
) -> dict[str, Any]:
    items = await _aggregate_for_wallets(db, [proxy_wallet], tag_id)
    if not items:
        raise HTTPException(status_code=404, detail="Tag profile not found")
    return items[0].model_dump()
