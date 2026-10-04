from __future__ import annotations

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
