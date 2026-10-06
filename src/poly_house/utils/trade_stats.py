from __future__ import annotations

from typing import Any

from poly_house.db import Database
from poly_house.utils.time import DAY_SEC


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


def _empty_market_trade_stats(condition_id: str) -> dict[str, Any]:
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


async def compute_market_trade_stats(
    db: Database, condition_id: str
) -> dict[str, Any]:
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
        return _empty_market_trade_stats(condition_id)

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


async def load_latest_best_prices(
    db: Database, condition_ids: list[str]
) -> dict[str, tuple[list[float | None], list[float | None]]]:
    """Per condition_id: (best_bids[2], best_asks[2]) from newest synced_at per outcome."""
    if not condition_ids:
        return {}
    pipeline: list[dict[str, Any]] = [
        {"$match": {"condition_id": {"$in": condition_ids}}},
        {"$sort": {"synced_at": -1}},
        {
            "$group": {
                "_id": {
                    "condition_id": "$condition_id",
                    "outcome_index": "$outcome_index",
                },
                "best_bid": {"$first": "$best_bid"},
                "best_ask": {"$first": "$best_ask"},
            }
        },
    ]
    cursor = await db.best_prices.aggregate(pipeline)
    rows = await cursor.to_list(length=None)
    result: dict[str, tuple[list[float | None], list[float | None]]] = {
        cid: ([None, None], [None, None]) for cid in condition_ids
    }
    for row in rows:
        key = row["_id"]
        condition_id = key["condition_id"]
        outcome_index = int(key["outcome_index"])
        if condition_id not in result or outcome_index not in (0, 1):
            continue
        bids, asks = result[condition_id]
        bids[outcome_index] = float(row["best_bid"])
        asks[outcome_index] = float(row["best_ask"])
    return result
