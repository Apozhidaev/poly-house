from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from poly_house.db import Database

logger = structlog.get_logger()

MIN_ACTIVITY_COUNT = 3500


@dataclass
class ActivityOverflowRow:
    proxy_wallet: str
    event_slug: str
    count: int


@dataclass
class DataFixResult:
    rows: list[ActivityOverflowRow]
    activities_deleted: int
    activity_caches_deleted: int


async def list_activity_overflows(db: Database) -> list[ActivityOverflowRow]:
    pipeline: list[dict[str, Any]] = [
        {
            "$group": {
                "_id": {"proxy_wallet": "$proxy_wallet", "event_slug": "$event_slug"},
                "count": {"$sum": 1},
            }
        },
        {"$match": {"count": {"$gte": MIN_ACTIVITY_COUNT}}},
        {"$sort": {"count": -1, "_id.proxy_wallet": 1, "_id.event_slug": 1}},
    ]
    cursor = await db.activities.aggregate(pipeline)
    docs = await cursor.to_list(length=None)
    return [
        ActivityOverflowRow(
            proxy_wallet=doc["_id"]["proxy_wallet"],
            event_slug=doc["_id"]["event_slug"],
            count=doc["count"],
        )
        for doc in docs
    ]


def print_activity_overflows(rows: list[ActivityOverflowRow]) -> None:
    for row in rows:
        print(f"{row.count}\t{row.proxy_wallet}\t{row.event_slug}")
    print(f"total\t{len(rows)}")


async def apply_activity_overflow_fix(
    db: Database, rows: list[ActivityOverflowRow]
) -> tuple[int, int]:
    activities_deleted = 0
    for row in rows:
        result = await db.activities.delete_many(
            {"proxy_wallet": row.proxy_wallet, "event_slug": row.event_slug}
        )
        activities_deleted += result.deleted_count

    wallets = list({row.proxy_wallet for row in rows})
    cache_result = await db.activity_caches.delete_many(
        {"proxy_wallet": {"$in": wallets}}
    )
    return activities_deleted, cache_result.deleted_count


async def run_data_fix(db: Database, *, apply: bool) -> DataFixResult:
    rows = await list_activity_overflows(db)
    print_activity_overflows(rows)
    if not apply or not rows:
        return DataFixResult(rows, 0, 0)

    activities_deleted, activity_caches_deleted = await apply_activity_overflow_fix(
        db, rows
    )
    logger.info(
        "data_fix_applied",
        pairs=len(rows),
        wallets=len({row.proxy_wallet for row in rows}),
        activities_deleted=activities_deleted,
        activity_caches_deleted=activity_caches_deleted,
    )
    return DataFixResult(rows, activities_deleted, activity_caches_deleted)
