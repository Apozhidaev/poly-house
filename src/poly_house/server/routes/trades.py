from __future__ import annotations

from math import sqrt
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


def _wilson_ci(p_hat: float, n: int, *, z: float = 1.96) -> tuple[float, float]:
    denom = 1 + z**2 / n
    center = (p_hat + z**2 / (2 * n)) / denom
    margin = z * sqrt(p_hat * (1 - p_hat) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


async def _min_max_price_items(
    db: Db,
    *,
    condition_id: str,
    outcome_index: int,
    side: str,
) -> list[dict[str, Any]]:
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
    return [
        {
            "timestamp": row["timestamp"],
            "price": row.get("price"),
            "min_price": min_price,
            "max_price": max_price,
        }
        for row, (min_price, max_price) in zip(rows, suffixes, strict=True)
    ]


def _aggregate_stop_prices(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[float, list[dict[str, Any]]] = {}
    for item in items:
        price = item.get("price")
        min_price = item.get("min_price")
        max_price = item.get("max_price")
        if price is None or min_price is None or max_price is None:
            continue
        groups.setdefault(round(price, 2), []).append(item)

    result: list[dict[str, Any]] = []
    for price in sorted(groups):
        group = groups[price]
        count = len(group)
        min_mean = sum(item["min_price"] for item in group) / count
        max_mean = sum(item["max_price"] for item in group) / count
        min_lo, min_hi = _wilson_ci(min_mean, count)
        max_lo, max_hi = _wilson_ci(max_mean, count)
        result.append(
            {
                "price": price,
                "count": count,
                "min_price": min_mean,
                "min_price_ci_low": min_lo,
                "min_price_ci_high": min_hi,
                "max_price": max_mean,
                "max_price_ci_low": max_lo,
                "max_price_ci_high": max_hi,
            }
        )
    return result


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
    return {
        "items": await _min_max_price_items(
            db,
            condition_id=condition_id,
            outcome_index=outcome_index,
            side=side,
        )
    }


@router.get("/stop-prices")
async def stop_prices(
    db: Db,
    condition_id: str,
    outcome_index: int,
    side: str,
) -> dict[str, Any]:
    """Средние min/max по округлённой цене входа с 95% Wilson CI."""
    items = await _min_max_price_items(
        db,
        condition_id=condition_id,
        outcome_index=outcome_index,
        side=side,
    )
    return {"items": _aggregate_stop_prices(items)}


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
