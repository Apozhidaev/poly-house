from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from poly_house.api.deps import Db, Page
from poly_house.utils.time import DAY_SEC

router = APIRouter(prefix="/trades", tags=["trades"])


def _per_day(value: float, days: int) -> float | None:
    return value / days if days > 0 else None


def _side_outcome(side: str, outcome_index: int) -> dict[str, Any]:
    return {
        "$and": [
            {"$eq": ["$side", side]},
            {"$eq": ["$outcome_index", outcome_index]},
        ]
    }


def _sum_cond(predicate: dict[str, Any], then: Any) -> dict[str, Any]:
    return {"$sum": {"$cond": [predicate, then, 0]}}


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
    rows: list[dict[str, Any]], *, outcome_index: int = 0
) -> list[dict[str, Any]]:
    normalized = [_normalize_trade_to_outcome(row, outcome_index) for row in rows]
    by_timestamp: dict[int, float] = {}
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
        by_timestamp[trade["timestamp"]] = mid
    return [
        {"timestamp": timestamp, "price": price}
        for timestamp, price in sorted(by_timestamp.items())
    ]


@router.get("/market-stats")
async def market_stats(db: Db, condition_id: str) -> dict[str, Any]:
    """Counts/size/volume and per-day averages for trades on a market (span first..last trade)."""
    buy0 = _side_outcome("BUY", 0)
    buy1 = _side_outcome("BUY", 1)
    sell0 = _side_outcome("SELL", 0)
    sell1 = _side_outcome("SELL", 1)
    size_price = {"$multiply": ["$size", "$price"]}
    pipeline: list[dict[str, Any]] = [
        {"$match": {"condition_id": condition_id}},
        {
            "$group": {
                "_id": None,
                "trades_count": {"$sum": 1},
                "min_ts": {"$min": "$timestamp"},
                "max_ts": {"$max": "$timestamp"},
                "total_size": {"$sum": "$size"},
                "total_volume": {"$sum": size_price},
                "buy_0_count": _sum_cond(buy0, 1),
                "buy_1_count": _sum_cond(buy1, 1),
                "sell_0_count": _sum_cond(sell0, 1),
                "sell_1_count": _sum_cond(sell1, 1),
                "buy_0_size": _sum_cond(buy0, "$size"),
                "buy_1_size": _sum_cond(buy1, "$size"),
                "sell_0_size": _sum_cond(sell0, "$size"),
                "sell_1_size": _sum_cond(sell1, "$size"),
                "buy_0_volume": _sum_cond(buy0, size_price),
                "buy_1_volume": _sum_cond(buy1, size_price),
                "sell_0_volume": _sum_cond(sell0, size_price),
                "sell_1_volume": _sum_cond(sell1, size_price),
            }
        },
    ]
    cursor = await db.trades.aggregate(pipeline)
    rows = await cursor.to_list(length=1)
    if not rows:
        return {
            "condition_id": condition_id,
            "days": 0,
            "trades_count": 0,
            "trades_per_day": None,
            "buy_count": [0, 0],
            "buy_per_day": [None, None],
            "sell_count": [0, 0],
            "sell_per_day": [None, None],
            "total_size": 0.0,
            "total_volume": 0.0,
            "size_per_day": None,
            "volume_per_day": None,
            "buy_size": [0.0, 0.0],
            "sell_size": [0.0, 0.0],
            "buy_size_per_day": [None, None],
            "sell_size_per_day": [None, None],
            "buy_volume": [0.0, 0.0],
            "sell_volume": [0.0, 0.0],
            "buy_volume_per_day": [None, None],
            "sell_volume_per_day": [None, None],
        }

    row = rows[0]
    days = int((row["max_ts"] - row["min_ts"]) // DAY_SEC) + 1
    trades_count = int(row["trades_count"])
    total_size = float(row["total_size"])
    total_volume = float(row["total_volume"])
    buy_count = [int(row["buy_0_count"]), int(row["buy_1_count"])]
    sell_count = [int(row["sell_0_count"]), int(row["sell_1_count"])]
    buy_size = [float(row["buy_0_size"]), float(row["buy_1_size"])]
    sell_size = [float(row["sell_0_size"]), float(row["sell_1_size"])]
    buy_volume = [float(row["buy_0_volume"]), float(row["buy_1_volume"])]
    sell_volume = [float(row["sell_0_volume"]), float(row["sell_1_volume"])]
    return {
        "condition_id": condition_id,
        "days": days,
        "trades_count": trades_count,
        "trades_per_day": _per_day(trades_count, days),
        "buy_count": buy_count,
        "buy_per_day": [_per_day(count, days) for count in buy_count],
        "sell_count": sell_count,
        "sell_per_day": [_per_day(count, days) for count in sell_count],
        "total_size": total_size,
        "total_volume": total_volume,
        "size_per_day": _per_day(total_size, days),
        "volume_per_day": _per_day(total_volume, days),
        "buy_size": buy_size,
        "sell_size": sell_size,
        "buy_size_per_day": [_per_day(value, days) for value in buy_size],
        "sell_size_per_day": [_per_day(value, days) for value in sell_size],
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "buy_volume_per_day": [_per_day(value, days) for value in buy_volume],
        "sell_volume_per_day": [_per_day(value, days) for value in sell_volume],
    }


@router.get("/mid-prices")
async def mid_prices(
    db: Db, condition_id: str, outcome_index: int = 0
) -> dict[str, Any]:
    """Mid price по трейдам, нормализованным к outcome_index: (current + prev opposite side) / 2."""
    cursor = db.trades.find(
        {"condition_id": condition_id},
        {"timestamp": 1, "price": 1, "side": 1, "outcome_index": 1, "_id": 0},
    ).sort("timestamp", 1)
    rows = await cursor.to_list(length=None)
    return {"items": _mid_prices(rows, outcome_index=outcome_index)}


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
