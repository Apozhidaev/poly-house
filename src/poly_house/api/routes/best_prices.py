from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from poly_house.api.deps import Db

router = APIRouter(prefix="/best-prices", tags=["best-prices"])


@router.get("")
async def list_best_prices(
    db: Db,
    condition_id: str,
    outcome_index: int,
) -> dict[str, Any]:
    cursor = (
        db.best_prices.find(
            {"condition_id": condition_id, "outcome_index": outcome_index},
            projection={"_id": 0},
        )
        .sort("timestamp", 1)
    )
    items = await cursor.to_list(length=None)
    return {"items": items}


@router.get("/avg-price")
async def avg_price(
    db: Db,
    condition_id: str,
    outcome_index: int,
    window_sec: int,
) -> dict[str, Any]:
    cutoff_ms = int(datetime.now(UTC).timestamp() * 1000) - window_sec * 1000
    pipeline: list[dict[str, Any]] = [
        {
            "$match": {
                "condition_id": condition_id,
                "outcome_index": outcome_index,
                "timestamp": {"$gte": cutoff_ms},
            }
        },
        {
            "$group": {
                "_id": None,
                "avg_bid": {"$avg": "$best_bid"},
                "avg_ask": {"$avg": "$best_ask"},
            }
        },
    ]
    cursor = await db.best_prices.aggregate(pipeline)
    rows = await cursor.to_list(length=1)
    if not rows:
        return {
            "condition_id": condition_id,
            "outcome_index": outcome_index,
            "window_sec": window_sec,
            "avg_bid": None,
            "avg_ask": None,
            "avg_spread": None,
        }
    row = rows[0]
    avg_bid = row["avg_bid"]
    avg_ask = row["avg_ask"]
    return {
        "condition_id": condition_id,
        "outcome_index": outcome_index,
        "window_sec": window_sec,
        "avg_bid": avg_bid,
        "avg_ask": avg_ask,
        "avg_spread": avg_ask - avg_bid,
    }
