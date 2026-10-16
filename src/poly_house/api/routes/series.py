from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from poly_house.api.deps import Db, Page

router = APIRouter(prefix="/series", tags=["series"])


@router.get("")
async def list_series(
    db: Db,
    page: Page,
    slug: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if slug:
        query["slug"] = slug
    cursor = db.series.find(query).skip(page.offset).limit(page.limit)
    return {"items": await cursor.to_list(page.limit)}


@router.get("/{series_id}/market-stats")
async def get_series_market_stats(series_id: int, db: Db) -> dict[str, Any]:
    series = await db.series.find_one({"_id": series_id})
    if series is None:
        raise HTTPException(status_code=404, detail="Series not found")

    pipeline: list[dict[str, Any]] = [
        {
            "$match": {
                "series_ids": series_id,
                "resolved_outcome_index": {"$ne": None},
            }
        },
        {
            "$group": {
                "_id": None,
                "resolved_count": {"$sum": 1},
                "outcome_0": {
                    "$sum": {
                        "$cond": [{"$eq": ["$resolved_outcome_index", 0]}, 1, 0]
                    }
                },
                "outcome_1": {
                    "$sum": {
                        "$cond": [{"$eq": ["$resolved_outcome_index", 1]}, 1, 0]
                    }
                },
            }
        },
    ]
    cursor = await db.markets.aggregate(pipeline)
    docs = await cursor.to_list(length=1)
    if not docs:
        return {
            "resolved_count": 0,
            "resolved_outcomes": [0, 0],
            "win_rates": [None, None],
        }

    row = docs[0]
    resolved_count = int(row["resolved_count"])
    outcome_0 = int(row["outcome_0"])
    outcome_1 = int(row["outcome_1"])
    return {
        "resolved_count": resolved_count,
        "resolved_outcomes": [outcome_0, outcome_1],
        "win_rates": (
            [None, None]
            if resolved_count == 0
            else [outcome_0 / resolved_count, outcome_1 / resolved_count]
        ),
    }


@router.get("/{series_id}")
async def get_series(series_id: int, db: Db) -> dict[str, Any]:
    doc = await db.series.find_one({"_id": series_id})
    if doc is None:
        raise HTTPException(status_code=404, detail="Series not found")
    return doc
