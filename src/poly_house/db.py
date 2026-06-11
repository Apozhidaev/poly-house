from __future__ import annotations

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from poly_house.config import Settings


def create_client(settings: Settings) -> AsyncMongoClient:
    # tz_aware: updatedAt из базы должен сравниваться с aware-датами watermark'а
    return AsyncMongoClient(settings.mongo_uri, tz_aware=True)


async def ensure_indexes(db: AsyncDatabase) -> None:
    await db.events.create_index("slug")
    await db.events.create_index("closedTime")

    await db.markets.create_index("conditionId", unique=True, sparse=True)
    await db.markets.create_index("eventId")
    await db.markets.create_index("slug")
    await db.markets.create_index("tagIds")
    await db.markets.create_index("updatedAt")

    await db.tags.create_index("slug")

    await db.trades.create_index([("conditionId", 1), ("timestamp", 1)])
    await db.trades.create_index("proxyWallet")
    await db.trades.create_index("timestamp")
