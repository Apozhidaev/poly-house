from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from pymongo import UpdateOne

from poly_house.api.data import DataClient
from poly_house.api.pnl import PnlClient
from poly_house.config import Settings
from poly_house.db import Database
from poly_house.models import LeaderboardStats, Profile, PnlTimeSeries
from poly_house.utils.math import fit_trend_by_days

logger = structlog.get_logger()

BULK_SIZE = 100


def build_profile(
    *,
    wallet: str,
    name: str,
    stats: LeaderboardStats | None,
    positions_value: float | None,
    ts: PnlTimeSeries | None,
) -> Profile:
    pnl = stats.pnl if stats else None
    volume = stats.vol if stats else None

    if ts and ts.data:
        _, slope, r2 = fit_trend_by_days(ts.data, use_cumulative=False)
        values = [p.value for p in ts.data]
        pnl_slope = slope
        pnl_r_squared = r2
        pnl_min_value = min(values)
        pnl_max_value = max(values)
    else:
        pnl_slope = None
        pnl_r_squared = None
        pnl_min_value = None
        pnl_max_value = None

    return Profile(
        proxy_wallet=wallet,
        name=name,
        pnl=pnl,
        volume=volume,
        pnl_slope=pnl_slope,
        pnl_r_squared=pnl_r_squared,
        pnl_min_value=pnl_min_value,
        pnl_max_value=pnl_max_value,
        positions_value=positions_value,
    )


async def _fetch_user_metrics(
    data: DataClient,
    pnl: PnlClient,
    wallet: str,
) -> tuple[LeaderboardStats | None, float | None, PnlTimeSeries | None]:
    stats, positions_value, ts = await asyncio.gather(
        data.leaderboard_stats(user=wallet),
        data.positions_value(user=wallet),
        pnl.pnl_time_series(user=wallet),
    )
    return stats, positions_value, ts


async def _flush_writes(db: Database, ops: list[UpdateOne]) -> None:
    if ops:
        await db.profiles.bulk_write(ops)


async def sync_profiles(
    db: Database,
    data: DataClient,
    pnl: PnlClient,
    _settings: Settings,
    *,
    wallet: str | None = None,
    limit: int | None = None,
    stop: asyncio.Event | None = None,
) -> int:
    """Upsert profiles for users from MongoDB; returns number of profiles written."""
    query: dict[str, Any] = {}
    if wallet is not None:
        query["_id"] = wallet

    cursor = db.users.find(query, projection={"_id": 1, "name": 1})
    if limit is not None:
        cursor = cursor.limit(limit)
    users = await cursor.to_list(length=None)

    logger.info(
        "profiles_sync_start",
        users=len(users),
        wallet=wallet,
        limit=limit,
    )

    started = time.monotonic()
    ops: list[UpdateOne] = []
    processed = 0

    for user in users:
        if stop is not None and stop.is_set():
            break

        user_wallet = user["_id"]
        name = user.get("name") or ""

        stats, positions_value, ts = await _fetch_user_metrics(data, pnl, user_wallet)
        profile = build_profile(
            wallet=user_wallet,
            name=name,
            stats=stats,
            positions_value=positions_value,
            ts=ts,
        )
        ops.append(
            UpdateOne(
                {"_id": user_wallet},
                {"$set": profile.to_doc()},
                upsert=True,
            )
        )
        processed += 1

        if len(ops) >= BULK_SIZE:
            await _flush_writes(db, ops)
            ops.clear()

        logger.info("profile_synced", wallet=user_wallet)

    await _flush_writes(db, ops)

    logger.info(
        "profiles_sync_done",
        processed=processed,
        interrupted=stop is not None and stop.is_set(),
        seconds=round(time.monotonic() - started, 1),
    )
    return processed
