from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from poly_house.server.deps import Db, Page

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("/take-prices")
async def trade_prices_by_user(
    db: Db,
    condition_id: str,
    outcome_index: int,
    side: str,
) -> dict[str, Any]:
    """Уникальные цены сделок (округлённые до 2 знаков) по каждому proxy_wallet."""
    pipeline: list[dict[str, Any]] = [
        {
            "$match": {
                "condition_id": condition_id,
                "outcome_index": outcome_index,
                "side": side.upper(),
                "price": {"$ne": None},
            }
        },
        {
            "$group": {
                "_id": "$proxy_wallet",
                "prices": {"$addToSet": {"$round": ["$price", 2]}},
            }
        },
        {"$project": {"_id": 0, "proxy_wallet": "$_id", "prices": 1}},
    ]
    cursor = await db.trades.aggregate(pipeline)
    items = await cursor.to_list(length=None)
    return {"items": items}


@router.get("")
async def list_trades(
    db: Db,
    page: Page,
    condition_id: str | None = None,
    user: str | None = None,
    side: str | None = None,
    from_ts: Annotated[int | None, Query(alias="from")] = None,
    to_ts: Annotated[int | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if condition_id:
        query["condition_id"] = condition_id
    if user:
        query["proxy_wallet"] = user
    if side:
        query["side"] = side.upper()
    timestamp: dict[str, int] = {}
    if from_ts is not None:
        timestamp["$gte"] = from_ts
    if to_ts is not None:
        timestamp["$lte"] = to_ts
    if timestamp:
        query["timestamp"] = timestamp
    cursor = (
        db.trades.find(query, {"_id": 0})
        .sort("timestamp", -1)
        .skip(page.offset)
        .limit(page.limit)
    )
    return {"items": await cursor.to_list(page.limit)}
