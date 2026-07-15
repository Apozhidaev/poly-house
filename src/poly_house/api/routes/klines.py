from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from poly_house.api.deps import Db

router = APIRouter(prefix="/klines", tags=["klines"])


def _kline_to_candle(kline: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": kline["time"],
        "open": kline["max_bid"],
        "high": kline["min_ask"],
        "low": kline["min_bid"],
        "close": kline["min_ask"],
    }


@router.get("")
async def list_klines(
    db: Db,
    condition_id: str,
    outcome_index: int,
) -> dict[str, Any]:
    cursor = (
        db.klines.find(
            {"condition_id": condition_id, "outcome_index": outcome_index},
            projection={"_id": 0},
        )
        .sort("time", 1)
    )
    items = await cursor.to_list(length=None)
    return {"items": items}


@router.get("/candles")
async def list_kline_candles(
    db: Db,
    condition_id: str,
    outcome_index: int,
) -> list[dict[str, Any]]:
    cursor = (
        db.klines.find(
            {"condition_id": condition_id, "outcome_index": outcome_index},
            projection={"_id": 0, "time": 1, "min_bid": 1, "max_bid": 1, "min_ask": 1},
        )
        .sort("time", 1)
    )
    klines = await cursor.to_list(length=None)
    return [_kline_to_candle(kline) for kline in klines]
