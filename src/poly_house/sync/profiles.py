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
    Activity,
    EquitySnapshot,
    LeaderboardStats,
    Profile,
    PnlTimeSeries,
)
from poly_house.models.activity import (
    build_market_activities,
    win_rate_from_market_activities,
)
from poly_house.utils.math import fit_trend_by_days

logger = structlog.get_logger()

BULK_SIZE = 100
PROFILE_SYNC_MIN_INTERVAL = timedelta(days=1)


async def wallets_needing_profile_sync(db: Database, wallets: list[str]) -> list[str]:
    if not wallets:
        return []
    cutoff = datetime.now(UTC) - PROFILE_SYNC_MIN_INTERVAL
    cursor = db.profiles.find(
        {"_id": {"$in": wallets}, "synced_at": {"$gte": cutoff}},
        projection={"_id": 1},
    )
    recent = {doc["_id"] for doc in await cursor.to_list(length=None)}
    return [wallet for wallet in wallets if wallet not in recent]


async def _load_profiles_needing_sync(
    db: Database,
    *,
    wallet: str | None = None,
    limit: int | None = None,
) -> dict[str, str]:
    cutoff = datetime.now(UTC) - PROFILE_SYNC_MIN_INTERVAL
    query: dict[str, Any] = {
        "$or": [
            {"synced_at": {"$lt": cutoff}},
            {"synced_at": {"$exists": False}},
        ]
    }
    if wallet is not None:
        query = {"_id": wallet, **query}
    cursor = db.profiles.find(query, projection={"_id": 1, "name": 1})
    if limit is not None:
        cursor = cursor.limit(limit)
    docs = await cursor.to_list(length=None)
    return {doc["_id"]: doc.get("name") or "" for doc in docs}


def build_profile(
    *,
    wallet: str,
    name: str,
    stats: LeaderboardStats | None,
    equity: EquitySnapshot | None,
    ts: PnlTimeSeries | None,
    win_rate: float | None = None,
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
        name=name,
        pnl=pnl,
        volume=volume,
        pnl_growth=pnl_growth,
        pnl_stability=pnl_stability,
        positions_value=positions_value,
        cash_balance=cash_balance,
        total_balance=total_balance,
        win_rate=win_rate,
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
    win_rates = await load_win_rates(db, list(wallets.keys()))
    synced_at = datetime.now(UTC)

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
            win_rate=win_rates.get(wallet),
            synced_at=synced_at,
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


async def sync_profiles_for_wallets(
    db: Database,
    data: DataClient,
    pnl: PnlClient,
    settings: Settings,
    wallets: dict[str, str],
    *,
    stop: asyncio.Event | None = None,
) -> None:
    """Sync profiles for given wallets."""
    started = time.monotonic()
    if not wallets:
        logger.info("profiles_sync_skip", reason="no_wallets")
        return

    activities = await sync_activities_for_wallets(
        db, data, settings, list(wallets.keys()), stop=stop
    )
    profiles = await upsert_profiles_for_wallets(db, data, pnl, wallets, stop=stop)
    logger.info(
        "profiles_sync_complete",
        profiles=profiles,
        activities=activities,
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
    """Sync stale profiles (synced_at older than 24h)."""
    wallets_to_sync = await _load_profiles_needing_sync(db, wallet=wallet, limit=limit)

    logger.info(
        "profiles_sync_start",
        profiles=len(wallets_to_sync),
        wallet=wallet,
        limit=limit,
    )

    await sync_profiles_for_wallets(db, data, pnl, settings, wallets_to_sync, stop=stop)


async def load_win_rates(db: Database, wallets: list[str]) -> dict[str, float]:
    if not wallets:
        return {}
    docs = await db.activities.find({"proxy_wallet": {"$in": wallets}}).to_list(
        length=None
    )
    by_wallet: dict[str, list[Activity]] = {}
    for doc in docs:
        activity = Activity.model_validate(doc)
        by_wallet.setdefault(activity.proxy_wallet, []).append(activity)
    return {
        wallet: rate
        for wallet, wallet_activities in by_wallet.items()
        if (
            rate := win_rate_from_market_activities(
                build_market_activities(wallet_activities)
            )
        )
        is not None
    }


async def _get_activity_watermark(db: Database, proxy_wallet: str) -> int | None:
    doc = await db.activities.find_one(
        {"proxy_wallet": proxy_wallet},
        sort=[("timestamp", -1)],
        projection={"timestamp": 1},
    )
    return doc["timestamp"] if doc else None


async def _fetch_new_activities(
    data: DataClient,
    settings: Settings,
    user: str,
    watermark: int | None,
) -> list[dict[str, Any]]:
    """Пагинация /activity (новые первыми) до watermark или max_offset."""
    min_timestamp = watermark if watermark is not None else -1
    pages: list[list[dict[str, Any]]] = []
    offset = 0
    while offset <= settings.activity_max_offset:
        raw = await data.activity_page(
            user=user,
            limit=settings.activity_page_limit,
            offset=offset,
        )
        if not raw:
            break
        fresh = [p for p in raw if p["timestamp"] > min_timestamp]
        pages.append(fresh)
        if any(p["timestamp"] <= min_timestamp for p in raw):
            break
        if len(raw) < settings.activity_page_limit:
            break
        offset += settings.activity_page_limit
    return [p for page in reversed(pages) for p in reversed(page)]


async def _write_activities(db: Database, raw_activities: list[dict[str, Any]]) -> int:
    if not raw_activities:
        return 0
    docs = [Activity.model_validate(raw).to_doc() for raw in raw_activities]
    await db.activities.insert_many(docs)
    return len(docs)


async def sync_activities_for_wallets(
    db: Database,
    data: DataClient,
    settings: Settings,
    wallets: list[str],
    *,
    stop: asyncio.Event | None = None,
) -> int:
    """Догружает activity для списка wallets; returns number of activities written."""
    total_activities = 0
    for proxy_wallet in wallets:
        if stop is not None and stop.is_set():
            break
        watermark = await _get_activity_watermark(db, proxy_wallet)
        activities = await _fetch_new_activities(
            data, settings, proxy_wallet, watermark
        )
        if activities:
            total_activities += await _write_activities(db, activities)
        logger.info(
            "holder_activities_synced",
            wallet=proxy_wallet,
            activities=len(activities),
            watermark=watermark,
        )
    return total_activities
