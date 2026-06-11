from __future__ import annotations

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from poly_house.config import Settings


def create_client(settings: Settings) -> AsyncMongoClient:
    return AsyncMongoClient(settings.mongo_uri, tz_aware=True)


async def ensure_indexes(db: AsyncDatabase) -> None:
    await db.events.create_index("slug")
    await db.events.create_index("tag_ids")

    await db.markets.create_index("condition_id", unique=True, sparse=True)
    await db.markets.create_index("event_id")
    await db.markets.create_index("slug")
    await db.markets.create_index("tag_ids")

    await db.tags.create_index("slug")

    await db.trades.create_index([("condition_id", 1), ("timestamp", 1)])
    await db.trades.create_index("proxy_wallet")
    await db.trades.create_index("timestamp")
