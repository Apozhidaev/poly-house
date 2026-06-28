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
