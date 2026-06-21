from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from pymongo import ReplaceOne, UpdateOne

from poly_house.api.data import DataClient
from poly_house.api.pnl import PnlClient
from poly_house.config import Settings
from poly_house.db import Database
from poly_house.models import (
    Activity,
    ActivityCache,
    ActivityMetrics,
    EquitySnapshot,
    HouseProfile,
    LeaderboardStats,
    Profile,
    PnlTimeSeries,
)
from poly_house.models.activity import build_market_activities, metrics_from_market_activities
from poly_house.utils.math import compute_stable_growth, fit_trend_by_days

logger = structlog.get_logger()

BULK_SIZE = 100
PROFILE_SYNC_MIN_INTERVAL = timedelta(days=1)
HOUSE_PROFILE_SYNC_MIN_INTERVAL = timedelta(hours=6)


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


async def wallets_needing_house_profile_sync(
    db: Database, wallets: list[str]
) -> list[str]:
    if not wallets:
        return []
    cutoff = datetime.now(UTC) - HOUSE_PROFILE_SYNC_MIN_INTERVAL
    cursor = db.house_profiles.find(
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


async def _load_house_profiles_needing_sync(
    db: Database,
    *,
    wallet: str | None = None,
    limit: int | None = None,
) -> dict[str, str]:
    cutoff = datetime.now(UTC) - HOUSE_PROFILE_SYNC_MIN_INTERVAL
    query: dict[str, Any] = {
        "$or": [
            {"synced_at": {"$lt": cutoff}},
            {"synced_at": {"$exists": False}},
        ]
    }
    if wallet is not None:
        query = {"_id": wallet, **query}
    cursor = db.house_profiles.find(query, projection={"_id": 1, "name": 1})
    if limit is not None:
        cursor = cursor.limit(limit)
    docs = await cursor.to_list(length=None)
    return {doc["_id"]: doc.get("name") or "" for doc in docs}


async def _load_wallets_needing_sync(
    db: Database,
    *,
    wallet: str | None = None,
    limit: int | None = None,
) -> dict[str, str]:
    profile_wallets = await _load_profiles_needing_sync(
        db, wallet=wallet, limit=limit
    )
    house_wallets = await _load_house_profiles_needing_sync(
        db, wallet=wallet, limit=limit
    )
    return {**house_wallets, **profile_wallets}


def build_profile(
    *,
    wallet: str,
    name: str,
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
        stable_growth = compute_stable_growth(slope, r2)
    else:
        stable_growth = None

    return Profile(
        proxy_wallet=wallet,
        name=name,
        pnl=pnl,
        volume=volume,
        stable_growth=stable_growth,
        positions_value=positions_value,
        cash_balance=cash_balance,
        total_balance=total_balance,
        synced_at=synced_at,
    )


def build_house_profile(
    *,
    wallet: str,
    name: str,
    metrics: ActivityMetrics | None,
    synced_at: datetime,
) -> HouseProfile:
    return HouseProfile(
        proxy_wallet=wallet,
        name=name,
        pnl=metrics.pnl if metrics else None,
        volume=metrics.volume if metrics else None,
        stable_growth=(
            compute_stable_growth(metrics.pnl_growth, metrics.pnl_stability)
            if metrics
            else None
        ),
        win_rate=metrics.win_rate if metrics else None,
        markets_count=metrics.markets_count if metrics else 0,
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


async def _flush_house_profile_writes(db: Database, ops: list[UpdateOne]) -> None:
    if ops:
        await db.house_profiles.bulk_write(ops)


async def upsert_profiles_for_wallets(
    db: Database,
    data: DataClient,
    pnl: PnlClient,
    wallets: dict[str, str],
    *,
    stop: asyncio.Event | None = None,
) -> int:
    """Upsert Polymarket profiles; returns number of profiles written."""
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


async def upsert_house_profiles_for_wallets(
    db: Database,
    wallets: dict[str, str],
    *,
    stop: asyncio.Event | None = None,
) -> int:
    """Upsert house profiles from activities; returns number of house profiles written."""
    if not wallets:
        return 0

    ops: list[UpdateOne] = []
    processed = 0
    house_metrics = await load_house_metrics(db, list(wallets.keys()))

    for wallet, name in wallets.items():
        if stop is not None and stop.is_set():
            break

        metrics = house_metrics.get(wallet)
        house_profile = build_house_profile(
            wallet=wallet,
            name=name,
            metrics=metrics,
            synced_at=datetime.now(UTC),
        )
        ops.append(
            UpdateOne(
                {"_id": wallet},
                {"$set": house_profile.to_doc()},
                upsert=True,
            )
        )
        processed += 1

        if len(ops) >= BULK_SIZE:
            await _flush_house_profile_writes(db, ops)
            ops.clear()

        logger.info("house_profile_synced", wallet=wallet)

    await _flush_house_profile_writes(db, ops)
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
    """Sync profiles and/or house profiles for given wallets."""
    started = time.monotonic()
    if not wallets:
        logger.info("profiles_sync_skip", reason="no_wallets")
        return

    wallet_list = list(wallets.keys())
    house_stale = set(await wallets_needing_house_profile_sync(db, wallet_list))
    profile_stale = set(await wallets_needing_profile_sync(db, wallet_list))

    house_wallets = {w: wallets[w] for w in house_stale}
    profile_wallets = {w: wallets[w] for w in profile_stale}

    activity_cache = 0
    activities = 0
    house_profiles = 0
    profiles = 0

    if house_wallets:
        activity_cache = await sync_activity_cache_for_wallets(
            db, data, settings, list(house_wallets.keys()), stop=stop
        )
        activities = await sync_activities_for_wallets(
            db, data, settings, list(house_wallets.keys()), stop=stop
        )
        house_profiles = await upsert_house_profiles_for_wallets(
            db, house_wallets, stop=stop
        )

    if profile_wallets:
        profiles = await upsert_profiles_for_wallets(
            db, data, pnl, profile_wallets, stop=stop
        )

    logger.info(
        "profiles_sync_complete",
        profiles=profiles,
        house_profiles=house_profiles,
        activity_cache=activity_cache,
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
    """Sync stale profiles and/or house profiles."""
    wallets_to_sync = await _load_wallets_needing_sync(
        db, wallet=wallet, limit=limit
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


async def load_house_metrics(
    db: Database, wallets: list[str]
) -> dict[str, ActivityMetrics]:
    if not wallets:
        return {}
    docs = await db.activities.find({"proxy_wallet": {"$in": wallets}}).to_list(
        length=None
    )
    by_wallet: dict[str, list[Activity]] = {}
    for doc in docs:
        activity = Activity.model_validate(doc)
        by_wallet.setdefault(activity.proxy_wallet, []).append(activity)
    metrics: dict[str, ActivityMetrics] = {}
    for wallet, wallet_activities in by_wallet.items():
        activity_metrics = metrics_from_market_activities(
            build_market_activities(wallet_activities)
        )
        if (
            activity_metrics.win_rate is not None
            and activity_metrics.pnl is not None
            and activity_metrics.volume is not None
        ):
            metrics[wallet] = activity_metrics
    return metrics


async def _get_activity_cache_watermark(db: Database, proxy_wallet: str) -> int | None:
    doc = await db.activity_caches.find_one(
        {"proxy_wallet": proxy_wallet},
        sort=[("timestamp", -1)],
        projection={"timestamp": 1},
    )
    return doc["timestamp"] if doc else None


def _activity_cache_min_timestamp(settings: Settings, watermark: int | None) -> int:
    if watermark is not None:
        return watermark
    cutoff = datetime.now(UTC) - timedelta(days=settings.activity_cache_in_days)
    return int(cutoff.timestamp())


async def _fetch_activity_cache_entries(
    data: DataClient,
    settings: Settings,
    user: str,
    watermark: int | None,
) -> list[dict[str, Any]]:
    """Пагинация /closed-positions (новые первыми) до watermark или max_offset."""
    min_timestamp = _activity_cache_min_timestamp(settings, watermark)
    pages: list[list[dict[str, Any]]] = []
    offset = 0
    while offset <= settings.activity_cache_max_offset:
        raw = await data.closed_positions_page(
            user=user,
            limit=settings.activity_cache_page_limit,
            offset=offset,
        )
        if not raw:
            break
        fresh = [p for p in raw if p["timestamp"] > min_timestamp]
        pages.append(fresh)
        if any(p["timestamp"] <= min_timestamp for p in raw):
            break
        if len(raw) < settings.activity_cache_page_limit:
            break
        offset += settings.activity_cache_page_limit
    return [p for page in reversed(pages) for p in reversed(page)]


async def _write_activity_cache(
    db: Database, raw_entries: list[dict[str, Any]]
) -> int:
    if not raw_entries:
        return 0
    ops = [
        ReplaceOne(
            {
                "proxy_wallet": doc["proxy_wallet"],
                "condition_id": doc["condition_id"],
            },
            doc,
            upsert=True,
        )
        for doc in (ActivityCache.model_validate(raw).to_doc() for raw in raw_entries)
    ]
    await db.activity_caches.bulk_write(ops)
    return len(ops)


async def sync_activity_cache_for_wallets(
    db: Database,
    data: DataClient,
    settings: Settings,
    wallets: list[str],
    *,
    stop: asyncio.Event | None = None,
) -> int:
    """Догружает activity cache из /closed-positions; returns number of entries written."""
    total_entries = 0
    for proxy_wallet in wallets:
        if stop is not None and stop.is_set():
            break
        watermark = await _get_activity_cache_watermark(db, proxy_wallet)
        entries = await _fetch_activity_cache_entries(
            data, settings, proxy_wallet, watermark
        )
        if entries:
            total_entries += await _write_activity_cache(db, entries)
        logger.info(
            "profile_activity_cache_synced",
            wallet=proxy_wallet,
            activity_cache=len(entries),
            watermark=watermark,
        )
    return total_entries


async def _load_activity_cache_pending_sync(
    db: Database, wallets: list[str]
) -> list[ActivityCache]:
    if not wallets:
        return []
    market_ids = await db.markets.distinct("condition_id")
    if not market_ids:
        return []
    docs = await db.activity_caches.find(
        {
            "proxy_wallet": {"$in": wallets},
            "activity_synced": False,
            "condition_id": {"$in": market_ids},
        }
    ).to_list(length=None)
    return [ActivityCache.model_validate(doc) for doc in docs]


async def _get_activity_watermark(
    db: Database, proxy_wallet: str, condition_id: str
) -> int | None:
    doc = await db.activities.find_one(
        {"proxy_wallet": proxy_wallet, "condition_id": condition_id},
        sort=[("timestamp", -1)],
        projection={"timestamp": 1},
    )
    return doc["timestamp"] if doc else None


async def _fetch_new_activities(
    data: DataClient,
    settings: Settings,
    user: str,
    condition_id: str,
    watermark: int | None,
) -> list[dict[str, Any]]:
    """Пагинация /activity по market (новые первыми) до watermark или max_offset."""
    min_timestamp = watermark if watermark is not None else -1
    pages: list[list[dict[str, Any]]] = []
    offset = 0
    while offset <= settings.activity_max_offset:
        raw = await data.activity_page(
            user=user,
            limit=settings.activity_page_limit,
            offset=offset,
            market=condition_id,
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


async def _mark_activity_cache_synced(
    db: Database, proxy_wallet: str, condition_id: str
) -> None:
    await db.activity_caches.update_one(
        {"proxy_wallet": proxy_wallet, "condition_id": condition_id},
        {"$set": {"activity_synced": True}},
    )


async def sync_activities_for_wallets(
    db: Database,
    data: DataClient,
    settings: Settings,
    wallets: list[str],
    *,
    stop: asyncio.Event | None = None,
) -> int:
    """Догружает activity для pending activity cache entries."""
    pending = await _load_activity_cache_pending_sync(db, wallets)
    total_activities = 0
    for entry in pending:
        if stop is not None and stop.is_set():
            break
        watermark = await _get_activity_watermark(
            db, entry.proxy_wallet, entry.condition_id
        )
        activities = await _fetch_new_activities(
            data,
            settings,
            entry.proxy_wallet,
            entry.condition_id,
            watermark,
        )
        if activities:
            total_activities += await _write_activities(db, activities)
        await _mark_activity_cache_synced(
            db, entry.proxy_wallet, entry.condition_id
        )
        logger.info(
            "activity_cache_activities_synced",
            wallet=entry.proxy_wallet,
            condition_id=entry.condition_id,
            activities=len(activities),
            watermark=watermark,
        )
    return total_activities
