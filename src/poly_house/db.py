from __future__ import annotations

from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from poly_house.config import Settings

# Тип документа MongoDB и параметризованные им алиасы pymongo-классов:
# в строгом режиме basedpyright generic-классы без параметра дают
# reportMissingTypeArgument и каскад "partially unknown".
Doc = dict[str, Any]
Database = AsyncDatabase[Doc]


def create_client(settings: Settings) -> AsyncMongoClient[Doc]:
    return AsyncMongoClient(settings.mongo_uri, tz_aware=True)


async def ensure_indexes(db: Database) -> None:
    await db.events.create_index("slug")
    await db.events.create_index("tag_ids")

    await db.markets.create_index("condition_id", unique=True)
    await db.markets.create_index("event_id")
    await db.markets.create_index("slug")
    await db.markets.create_index("tag_ids")
    await db.markets.create_index("synced_at")
    await db.markets.create_index([("closed", 1), ("deadline", 1)])

    await db.tags.create_index("slug")

    await db.trades.create_index([("condition_id", 1), ("timestamp", 1)])
    await db.trades.create_index("proxy_wallet")
    await db.trades.create_index("timestamp")

    await db.activities.create_index([("proxy_wallet", 1), ("timestamp", -1)])
    await db.activities.create_index("condition_id")
    await db.activities.create_index([("proxy_wallet", 1), ("event_slug", 1)])

    await db.activity_caches.create_index(
        [("proxy_wallet", 1), ("event_slug", 1)], unique=True
    )
    await db.activity_caches.create_index([("proxy_wallet", 1), ("timestamp", -1)])
    await db.activity_caches.create_index([("activity_synced", 1), ("proxy_wallet", 1)])

    await db.tag_profiles.create_index(
        [("proxy_wallet", 1), ("tag_id", 1)], unique=True
    )
    await db.tag_profiles.create_index("tag_id")

    await db.home_profiles.create_index("synced_at")

    await db.profiles.create_index("synced_at")

    await db.klines.create_index(
        [("condition_id", 1), ("outcome_index", 1), ("time", 1)],
        unique=True,
    )
