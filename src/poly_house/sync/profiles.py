from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from pymongo import UpdateOne

from poly_house.api.data import DataClient
from poly_house.api.pnl import PnlClient
from poly_house.config import Settings
from poly_house.db import Database
from poly_house.models import (
    EquitySnapshot,
    LeaderboardStats,
    Profile,
    PnlTimeSeries,
)
from poly_house.utils.metrics import fit_trend_by_days

logger = structlog.get_logger()

BULK_SIZE = 100


async def wallets_needing_profile_sync(
    db: Database, settings: Settings, wallets: list[str]
) -> list[str]:
    if not wallets:
        return []
    cutoff = datetime.now(UTC) - timedelta(days=settings.profile_sync_interval_days)
    cursor = db.profiles.find(
        {"_id": {"$in": wallets}, "synced_at": {"$gte": cutoff}},
        projection={"_id": 1},
    )
    recent = {doc["_id"] for doc in await cursor.to_list(length=None)}
    return [wallet for wallet in wallets if wallet not in recent]


async def _load_profiles_needing_sync(
    db: Database,
    settings: Settings,
    *,
    wallet: str | None = None,
    limit: int | None = None,
) -> list[str]:
    cutoff = datetime.now(UTC) - timedelta(days=settings.profile_sync_interval_days)
    query: dict[str, Any] = {
        "$or": [
            {"synced_at": {"$lt": cutoff}},
            {"synced_at": {"$exists": False}},
        ]
    }
    if wallet is not None:
        query = {"_id": wallet, **query}
    cursor = db.profiles.find(query, projection={"_id": 1})
    if limit is not None:
        cursor = cursor.limit(limit)
    docs = await cursor.to_list(length=None)
    return [doc["_id"] for doc in docs]


def build_profile(
    wallet: str,
    stats: LeaderboardStats | None,
    equity: EquitySnapshot | None,
    ts: PnlTimeSeries | None,
    synced_at: datetime,
) -> Profile:
    pnl = stats.pnl if stats else None
    volume = stats.vol if stats else None

    if equity:
        positions_value = equity.positions_value
        cash_balance = equity.cash_balance
        total_balance = equity.equity
    else:
        positions_value = None
        cash_balance = None
        total_balance = None

    if ts and ts.data:
        _, slope, r2 = fit_trend_by_days(ts.data, use_cumulative=False)
        pnl_growth = slope
        pnl_stability = r2
    else:
        pnl_growth = None
        pnl_stability = None

    return Profile(
        proxy_wallet=wallet,
        pnl=pnl,
        volume=volume,
        pnl_growth=pnl_growth,
        pnl_stability=pnl_stability,
        positions_value=positions_value,
        cash_balance=cash_balance,
        total_balance=total_balance,
        synced_at=synced_at,
    )


async def _fetch_user_metrics(
    data: DataClient,
    pnl: PnlClient,
    wallet: str,
) -> tuple[LeaderboardStats | None, EquitySnapshot | None, PnlTimeSeries | None]:
    stats, equity_rows, ts = await asyncio.gather(
        data.leaderboard_stats(user=wallet),
        data.accounting_equity(user=wallet),
        pnl.pnl_time_series(user=wallet),
    )
    equity = (
        max(equity_rows, key=lambda row: row.valuation_time) if equity_rows else None
    )
    return stats, equity, ts


async def _flush_profile_writes(db: Database, ops: list[UpdateOne]) -> None:
    if ops:
        await db.profiles.bulk_write(ops)


async def upsert_profiles_for_wallets(
    db: Database,
    data: DataClient,
    pnl: PnlClient,
    wallets: list[str],
    *,
    stop: asyncio.Event | None = None,
) -> int:
    """Upsert Polymarket profiles; returns number of profiles written."""
    ops: list[UpdateOne] = []
    processed = 0

    for wallet in wallets:
        if stop is not None and stop.is_set():
            break

        stats, equity, ts = await _fetch_user_metrics(data, pnl, wallet)
        profile = build_profile(
            wallet=wallet,
            stats=stats,
            equity=equity,
            ts=ts,
            synced_at=datetime.now(UTC),
        )
        ops.append(
            UpdateOne(
                {"_id": wallet},
                {"$set": profile.to_doc()},
                upsert=True,
            )
        )
        processed += 1

        if len(ops) >= BULK_SIZE:
            await _flush_profile_writes(db, ops)
            ops.clear()

        logger.info("profile_synced", wallet=wallet)

    await _flush_profile_writes(db, ops)
    return processed


async def sync_profiles_for_wallets(
    db: Database,
    data: DataClient,
    pnl: PnlClient,
    settings: Settings,
    wallets: list[str],
    *,
    stop: asyncio.Event | None = None,
) -> None:
    """Sync Polymarket profiles for given wallets."""
    started = time.monotonic()
    if not wallets:
        logger.info("profiles_sync_skip", reason="no_wallets")
        return

    profiles = await upsert_profiles_for_wallets(
        db, data, pnl, wallets, stop=stop
    )

    logger.info(
        "profiles_sync_complete",
        profiles=profiles,
        interrupted=stop is not None and stop.is_set(),
        seconds=round(time.monotonic() - started, 1),
    )


async def sync_profiles(
    db: Database,
    data: DataClient,
    pnl: PnlClient,
    settings: Settings,
    *,
    wallet: str | None = None,
    limit: int | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Sync stale Polymarket profiles."""
    wallets_to_sync = await _load_profiles_needing_sync(
        db, settings, wallet=wallet, limit=limit
    )

    logger.info(
        "profiles_sync_start",
        wallets=len(wallets_to_sync),
        wallet=wallet,
        limit=limit,
    )

    await sync_profiles_for_wallets(
        db, data, pnl, settings, wallets_to_sync, stop=stop
    )
