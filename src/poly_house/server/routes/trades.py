from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

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
        result.append(
            {
                "price": price,
                "count": count,
                "min_mean": min_mean,
                "max_mean": max_mean,
                "min_price": min(item["min_price"] for item in group),
                "max_price": max(item["max_price"] for item in group),
            }
        )
    return result


def _build_trades_query(
    *,
    condition_id: str | None,
    outcome_index: int | None,
    user: str | None,
    side: str | None,
    from_ts: int | None,
    to_ts: int | None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if condition_id:
        query["condition_id"] = condition_id
    if outcome_index is not None:
        query["outcome_index"] = outcome_index
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
    return query


def _normalize_trade_to_outcome(
    doc: dict[str, Any], target_outcome_index: int
) -> dict[str, Any]:
    result = dict(doc)
    if result.get("outcome_index") != target_outcome_index:
        side = result.get("side")
        if side == "BUY":
            result["side"] = "SELL"
        elif side == "SELL":
            result["side"] = "BUY"
        price = result.get("price")
        if price is not None:
            result["price"] = 1 - price
    result.pop("outcome", None)
    result.pop("outcome_index", None)
    return result


def _mid_prices(
    rows: list[dict[str, Any]], *, target_outcome_index: int = 0
) -> list[dict[str, Any]]:
    normalized = [_normalize_trade_to_outcome(row, target_outcome_index) for row in rows]
    items: list[dict[str, Any]] = []
    last_buy: float | None = None
    last_sell: float | None = None
    for trade in normalized:
        price = trade.get("price")
        if price is None:
            continue
        side = trade.get("side")
        if side == "BUY":
            prev_opposite = last_sell
            last_buy = price
        elif side == "SELL":
            prev_opposite = last_buy
            last_sell = price
        else:
            raise HTTPException(status_code=500, detail=f"unexpected trade side: {side!r}")
        mid = (price + prev_opposite) / 2 if prev_opposite is not None else price
        items.append({"timestamp": trade["timestamp"], "price": mid})
    return items


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
    """Средние min/max по округлённой цене входа."""
    items = await _min_max_price_items(
        db,
        condition_id=condition_id,
        outcome_index=outcome_index,
        side=side,
    )
    return {"items": _aggregate_stop_prices(items)}


@router.get("/mid-prices")
async def mid_prices(db: Db, condition_id: str) -> dict[str, Any]:
    """Mid price по нормализованным к outcome 0 трейдам: (current + prev opposite side) / 2."""
    cursor = db.trades.find(
        {"condition_id": condition_id},
        {"timestamp": 1, "price": 1, "side": 1, "outcome_index": 1, "_id": 0},
    ).sort("timestamp", 1)
    rows = await cursor.to_list(length=None)
    return {"items": _mid_prices(rows)}


@router.get("/to-outcome")
async def trades_to_outcome(
    db: Db,
    page: Page,
    target_outcome_index: int = 0,
    condition_id: str | None = None,
    user: str | None = None,
    side: str | None = None,
    from_ts: Annotated[int | None, Query(alias="from")] = None,
    to_ts: Annotated[int | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    """Трейды, приведённые к target_outcome_index: инверсия side/price для другого исхода."""
    if target_outcome_index not in (0, 1):
        raise HTTPException(
            status_code=422, detail="target_outcome_index must be 0 or 1"
        )
    query = _build_trades_query(
        condition_id=condition_id,
        outcome_index=None,
        user=user,
        side=side,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    cursor = (
        db.trades.find(query, {"_id": 0})
        .sort("timestamp", -1)
        .skip(page.offset)
        .limit(page.limit)
    )
    rows = await cursor.to_list(page.limit)
    return {
        "items": [
            _normalize_trade_to_outcome(row, target_outcome_index) for row in rows
        ]
    }


@router.get("")
async def list_trades(
    db: Db,
    page: Page,
    condition_id: str | None = None,
    outcome_index: int | None = None,
    user: str | None = None,
    side: str | None = None,
    from_ts: Annotated[int | None, Query(alias="from")] = None,
    to_ts: Annotated[int | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    query = _build_trades_query(
        condition_id=condition_id,
        outcome_index=outcome_index,
        user=user,
        side=side,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    cursor = (
        db.trades.find(query, {"_id": 0})
        .sort("timestamp", -1)
        .skip(page.offset)
        .limit(page.limit)
    )
    return {"items": await cursor.to_list(page.limit)}
