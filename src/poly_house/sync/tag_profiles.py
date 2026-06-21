from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from pymongo import UpdateOne

from poly_house.db import Database
from poly_house.models import TagProfile
from poly_house.models.activity import (
    Activity,
    MarketActivity,
    build_market_activities,
    metrics_from_market_activities,
)

logger = structlog.get_logger()

BULK_SIZE = 100


async def load_condition_tag_ids(db: Database) -> dict[str, list[int]]:
    cursor = db.markets.find(
        {
            "condition_id": {"$ne": None},
            "tag_ids.0": {"$exists": True},
            "closed": True,
        },
        projection={"condition_id": 1, "tag_ids": 1},
    )
    docs = await cursor.to_list(length=None)
    return {doc["condition_id"]: doc["tag_ids"] for doc in docs}


def group_market_activities_by_tag(
    market_activities: list[MarketActivity],
    condition_tags: dict[str, list[int]],
) -> dict[int, list[MarketActivity]]:
    by_tag: dict[int, list[MarketActivity]] = {}
    for market_activity in market_activities:
        tag_ids = condition_tags.get(market_activity.condition_id)
        if not tag_ids:
            continue
        for tag_id in tag_ids:
            by_tag.setdefault(tag_id, []).append(market_activity)
    return by_tag


def build_tag_profile(
    wallet: str,
    tag_id: int,
    market_activities: list[MarketActivity],
) -> TagProfile:
    (
        win_rate,
        markets_count,
        pnl,
        volume,
        pnl_growth,
        pnl_stability,
    ) = metrics_from_market_activities(market_activities)

    return TagProfile(
        proxy_wallet=wallet,
        tag_id=tag_id,
        markets_count=markets_count,
        pnl=pnl,
        volume=volume,
        pnl_growth=pnl_growth,
        pnl_stability=pnl_stability,
        win_rate=win_rate,
    )


async def _flush_writes(db: Database, ops: list[UpdateOne]) -> None:
    if ops:
        await db.tag_profiles.bulk_write(ops)


async def update_tag_profiles_for_wallets(
    db: Database,
    wallets: list[str],
    condition_tags: dict[str, list[int]],
    *,
    stop: asyncio.Event | None = None,
) -> tuple[int, int]:
    """Upsert tag profiles for given wallets; returns (holders processed, tag_profiles written)."""
    if not wallets or not condition_tags:
        return 0, 0

    total_profiles = 0
    total_tag_profiles = 0
    ops: list[UpdateOne] = []
    condition_ids = list(condition_tags)

    for proxy_wallet in wallets:
        if stop is not None and stop.is_set():
            break

        activity_docs = await db.activities.find(
            {
                "proxy_wallet": proxy_wallet,
                "condition_id": {"$in": condition_ids},
            }
        ).to_list(length=None)
        activities = [Activity.model_validate(doc) for doc in activity_docs]
        market_activities = build_market_activities(activities)

        by_tag = group_market_activities_by_tag(market_activities, condition_tags)
        for tag_id, tag_market_activities in by_tag.items():
            profile = build_tag_profile(proxy_wallet, tag_id, tag_market_activities)
            ops.append(
                UpdateOne(
                    {"proxy_wallet": proxy_wallet, "tag_id": tag_id},
                    {"$set": profile.to_doc()},
                    upsert=True,
                )
            )
            total_tag_profiles += 1

            if len(ops) >= BULK_SIZE:
                await _flush_writes(db, ops)
                ops.clear()

        total_profiles += 1
        logger.info(
            "tag_profiles_synced",
            wallet=proxy_wallet,
            tag_profiles=len(by_tag),
            market_activities=len(market_activities),
        )

    await _flush_writes(db, ops)
    return total_profiles, total_tag_profiles


async def update_tag_profiles(
    db: Database,
    *,
    wallet: str | None = None,
    limit: int | None = None,
    stop: asyncio.Event | None = None,
) -> tuple[int, int]:
    """Upsert tag profiles for profiles; returns (wallets, tag_profiles written)."""
    condition_tags = await load_condition_tag_ids(db)
    if not condition_tags:
        logger.info("tag_profiles_sync_skip", reason="no_markets_with_tags")
        return 0, 0

    query: dict[str, Any] = {}
    if wallet is not None:
        query["_id"] = wallet

    cursor = db.profiles.find(query, projection={"_id": 1})
    if limit is not None:
        cursor = cursor.limit(limit)
    profiles = await cursor.to_list(length=None)

    logger.info(
        "tag_profiles_sync_start",
        profiles=len(profiles),
        wallet=wallet,
        limit=limit,
    )

    started = time.monotonic()
    wallets = [profile["_id"] for profile in profiles]
    total_profiles, total_tag_profiles = await update_tag_profiles_for_wallets(
        db, wallets, condition_tags, stop=stop
    )

    logger.info(
        "tag_profiles_sync_done",
        profiles=total_profiles,
        tag_profiles=total_tag_profiles,
        interrupted=stop is not None and stop.is_set(),
        seconds=round(time.monotonic() - started, 1),
    )
    return total_profiles, total_tag_profiles
