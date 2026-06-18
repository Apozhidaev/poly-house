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
from poly_house.models import (
    EquitySnapshot,
    Holder,
    LeaderboardStats,
    Profile,
    PnlTimeSeries,
)
from poly_house.utils.math import fit_trend_by_days

logger = structlog.get_logger()

BULK_SIZE = 100


def build_profile(
    *,
    wallet: str,
    name: str,
    stats: LeaderboardStats | None,
    equity: EquitySnapshot | None,
    ts: PnlTimeSeries | None,
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
        name=name,
        pnl=pnl,
        volume=volume,
        pnl_growth=pnl_growth,
        pnl_stability=pnl_stability,
        positions_value=positions_value,
        cash_balance=cash_balance,
        total_balance=total_balance,
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


async def _flush_writes(db: Database, ops: list[UpdateOne]) -> None:
    if ops:
        await db.profiles.bulk_write(ops)


async def upsert_profiles_for_wallets(
    db: Database,
    data: DataClient,
    pnl: PnlClient,
    wallets: dict[str, str],
    *,
    stop: asyncio.Event | None = None,
) -> int:
    """Upsert profiles for wallet->name mapping; returns number of profiles written."""
    ops: list[UpdateOne] = []
    processed = 0

    for wallet, name in wallets.items():
        if stop is not None and stop.is_set():
            break

        stats, equity, ts = await _fetch_user_metrics(data, pnl, wallet)
        profile = build_profile(
            wallet=wallet,
            name=name,
            stats=stats,
            equity=equity,
            ts=ts,
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
            await _flush_writes(db, ops)
            ops.clear()

        logger.info("profile_synced", wallet=wallet)

    await _flush_writes(db, ops)
    return processed


async def upsert_profiles_for_holders(
    db: Database,
    data: DataClient,
    pnl: PnlClient,
    holders: dict[str, Holder],
    *,
    stop: asyncio.Event | None = None,
) -> int:
    """Upsert profiles for holders using name from Holder."""
    wallets = {h.proxy_wallet: h.name for h in holders.values()}
    return await upsert_profiles_for_wallets(
        db, data, pnl, wallets, stop=stop
    )


async def sync_user_profiles(
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
        "user_profiles_sync_start",
        users=len(users),
        wallet=wallet,
        limit=limit,
    )

    started = time.monotonic()
    wallets = {user["_id"]: user.get("name") or "" for user in users}
    processed = await upsert_profiles_for_wallets(
        db, data, pnl, wallets, stop=stop
    )

    logger.info(
        "user_profiles_sync_done",
        processed=processed,
        interrupted=stop is not None and stop.is_set(),
        seconds=round(time.monotonic() - started, 1),
    )
    return processed


async def sync_holder_profiles(
    db: Database,
    data: DataClient,
    pnl: PnlClient,
    _settings: Settings,
    *,
    wallet: str | None = None,
    limit: int | None = None,
    stop: asyncio.Event | None = None,
) -> int:
    """Upsert profiles for holders from MongoDB; returns number of profiles written."""
    query: dict[str, Any] = {}
    if wallet is not None:
        query["_id"] = wallet

    cursor = db.holders.find(query, projection={"_id": 1, "name": 1})
    if limit is not None:
        cursor = cursor.limit(limit)
    holder_docs = await cursor.to_list(length=None)

    logger.info(
        "holder_profiles_sync_start",
        holders=len(holder_docs),
        wallet=wallet,
        limit=limit,
    )

    started = time.monotonic()
    wallets = {doc["_id"]: doc.get("name") or "" for doc in holder_docs}
    processed = await upsert_profiles_for_wallets(
        db, data, pnl, wallets, stop=stop
    )

    logger.info(
        "holder_profiles_sync_done",
        processed=processed,
        interrupted=stop is not None and stop.is_set(),
        seconds=round(time.monotonic() - started, 1),
    )
    return processed
