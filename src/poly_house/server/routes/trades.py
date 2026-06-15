from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from poly_house.server.deps import Db, Page

router = APIRouter(prefix="/trades", tags=["trades"])


def _suffix_min_max_prices(
    rows: list[dict[str, Any]],
) -> list[tuple[float | None, float | None]]:
    suffix_min: list[float | None] = [None] * len(rows)
    suffix_max: list[float | None] = [None] * len(rows)
    running_min: float | None = None
    running_max: float | None = None
    for i in range(len(rows) - 1, -1, -1):
        suffix_min[i] = running_min
        suffix_max[i] = running_max
        price = rows[i].get("price")
        if price is not None:
            running_min = price if running_min is None else min(running_min, price)
            running_max = price if running_max is None else max(running_max, price)
    return list(zip(suffix_min, suffix_max, strict=True))


@router.get("/take-prices")
async def trade_prices_by_user(
    db: Db,
    condition_id: str,
    outcome_index: int,
    side: str,
) -> dict[str, Any]:
    """Уникальные цены на пользователя (2 знака), собранные в один список."""
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
    ]
    cursor = await db.trades.aggregate(pipeline)
    rows = await cursor.to_list(length=None)
    items = [price for row in rows for price in row["prices"]]
    return {"items": items}


@router.get("/min-max-prices")
async def min_max_prices(
    db: Db,
    condition_id: str,
    outcome_index: int,
    side: str,
) -> dict[str, Any]:
    """Цена каждого трейда и min/max цен последующих (те же фильтры, все пользователи)."""
    cursor = db.trades.find(
        {
            "condition_id": condition_id,
            "outcome_index": outcome_index,
            "side": side.upper(),
        },
        {"timestamp": 1, "price": 1, "_id": 0},
    ).sort("timestamp", 1)
    rows = await cursor.to_list(length=None)
    suffixes = _suffix_min_max_prices(rows)
    items = [
        {
            "timestamp": row["timestamp"],
            "price": row.get("price"),
            "min_price": min_price,
            "max_price": max_price,
        }
        for row, (min_price, max_price) in zip(rows, suffixes, strict=True)
    ]
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
