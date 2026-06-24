from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
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
    HomeProfile,
    LeaderboardStats,
    Profile,
    PnlTimeSeries,
)
from poly_house.models.activity import (
    build_market_activities,
    load_resolved_outcomes,
    metrics_from_market_activities,
)
from poly_house.utils.metrics import fit_trend_by_days

logger = structlog.get_logger()

BULK_SIZE = 100


@dataclass(frozen=True)
class WalletIdentity:
    name: str
    bio: str = ""
    profile_image: str = ""


async def _load_stored_identities(
    db: Database, wallets: list[str]
) -> dict[str, WalletIdentity]:
    if not wallets:
        return {}
    identities: dict[str, WalletIdentity] = {}
    profile_docs = await db.profiles.find(
        {"_id": {"$in": wallets}},
        projection={"bio": 1, "profile_image": 1},
    ).to_list(length=None)
    for doc in profile_docs:
        identities[doc["_id"]] = WalletIdentity(
            bio=doc.get("bio") or "",
            profile_image=doc.get("profile_image") or "",
            name="",
        )
    user_docs = await db.users.find(
        {"_id": {"$in": wallets}},
        projection={"bio": 1, "profile_image": 1},
    ).to_list(length=None)
    for doc in user_docs:
        identities[doc["_id"]] = WalletIdentity(
            bio=doc.get("bio") or "",
            profile_image=doc.get("profile_image") or "",
            name="",
        )
    return identities


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


async def wallets_needing_home_profile_sync(
    db: Database, settings: Settings, wallets: list[str]
) -> list[str]:
    if not wallets:
        return []
    cutoff = datetime.now(UTC) - timedelta(
        hours=settings.home_profile_sync_interval_hours
    )
    cursor = db.home_profiles.find(
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
) -> dict[str, str]:
    cutoff = datetime.now(UTC) - timedelta(days=settings.profile_sync_interval_days)
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


async def _load_home_profiles_needing_sync(
    db: Database,
    settings: Settings,
    *,
    wallet: str | None = None,
    limit: int | None = None,
) -> dict[str, str]:
    cutoff = datetime.now(UTC) - timedelta(
        hours=settings.home_profile_sync_interval_hours
    )
    query: dict[str, Any] = {
        "$or": [
            {"synced_at": {"$lt": cutoff}},
            {"synced_at": {"$exists": False}},
        ]
    }
    if wallet is not None:
        query = {"_id": wallet, **query}
    cursor = db.home_profiles.find(query, projection={"_id": 1, "name": 1})
    if limit is not None:
        cursor = cursor.limit(limit)
    docs = await cursor.to_list(length=None)
    return {doc["_id"]: doc.get("name") or "" for doc in docs}


def build_profile(
    wallet: str,
    name: str,
    stats: LeaderboardStats | None,
    equity: EquitySnapshot | None,
    ts: PnlTimeSeries | None,
    synced_at: datetime,
    *,
    bio: str = "",
    profile_image: str = "",
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
        bio=bio,
        profile_image=profile_image,
        pnl=pnl,
        volume=volume,
        pnl_growth=pnl_growth,
        pnl_stability=pnl_stability,
        positions_value=positions_value,
        cash_balance=cash_balance,
        total_balance=total_balance,
        synced_at=synced_at,
    )


def build_home_profile(
    *,
    wallet: str,
    name: str,
    metrics: ActivityMetrics | None,
    synced_at: datetime,
) -> HomeProfile:
    return HomeProfile(
        proxy_wallet=wallet,
        name=name,
        pnl=metrics.pnl if metrics else None,
        volume=metrics.volume if metrics else None,
        pnl_growth=metrics.pnl_growth if metrics else None,
        pnl_stability=metrics.pnl_stability if metrics else None,
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


async def _flush_home_profile_writes(db: Database, ops: list[UpdateOne]) -> None:
    if ops:
        await db.home_profiles.bulk_write(ops)


async def upsert_profiles_for_wallets(
    db: Database,
    data: DataClient,
    pnl: PnlClient,
    wallets: dict[str, str],
    *,
    identities: dict[str, WalletIdentity] | None = None,
    stop: asyncio.Event | None = None,
) -> int:
    """Upsert Polymarket profiles; returns number of profiles written."""
    ops: list[UpdateOne] = []
    processed = 0
    stored_identities = await _load_stored_identities(db, list(wallets.keys()))

    for wallet, name in wallets.items():
        if stop is not None and stop.is_set():
            break

        identity = (identities or {}).get(wallet) or stored_identities.get(wallet)
        bio = identity.bio if identity else ""
        profile_image = identity.profile_image if identity else ""

        stats, equity, ts = await _fetch_user_metrics(data, pnl, wallet)
        profile = build_profile(
            wallet=wallet,
            name=name,
            stats=stats,
            equity=equity,
            ts=ts,
            synced_at=datetime.now(UTC),
            bio=bio,
            profile_image=profile_image,
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


async def upsert_home_profiles_for_wallets(
    db: Database,
    wallets: dict[str, str],
    *,
    stop: asyncio.Event | None = None,
) -> int:
    """Upsert home profiles from activities; returns number of home profiles written."""
    if not wallets:
        return 0

    ops: list[UpdateOne] = []
    processed = 0
    home_metrics = await load_home_metrics(db, list(wallets.keys()))

    for wallet, name in wallets.items():
        if stop is not None and stop.is_set():
            break

        metrics = home_metrics.get(wallet)
        home_profile = build_home_profile(
            wallet=wallet,
            name=name,
            metrics=metrics,
            synced_at=datetime.now(UTC),
        )
        ops.append(
            UpdateOne(
                {"_id": wallet},
                {"$set": home_profile.to_doc()},
                upsert=True,
            )
        )
        processed += 1

        if len(ops) >= BULK_SIZE:
            await _flush_home_profile_writes(db, ops)
            ops.clear()

        logger.info("home_profile_synced", wallet=wallet)

    await _flush_home_profile_writes(db, ops)
    return processed


async def load_closed_condition_ids(db: Database) -> list[str]:
    cursor = db.markets.find(
        {"condition_id": {"$ne": None}, "closed": True},
        projection={"condition_id": 1},
    )
    docs = await cursor.to_list(length=None)
    return [doc["condition_id"] for doc in docs]


async def load_home_metrics(
    db: Database, wallets: list[str]
) -> dict[str, ActivityMetrics]:
    if not wallets:
        return {}
    condition_ids = await load_closed_condition_ids(db)
    if not condition_ids:
        return {}
    resolved_outcomes = await load_resolved_outcomes(db, condition_ids)
    docs = await db.activities.find(
        {
            "proxy_wallet": {"$in": wallets},
            "condition_id": {"$in": condition_ids},
        }
    ).to_list(length=None)
    by_wallet: dict[str, list[Activity]] = {}
    for doc in docs:
        activity = Activity.model_validate(doc)
        by_wallet.setdefault(activity.proxy_wallet, []).append(activity)
    metrics: dict[str, ActivityMetrics] = {}
    for wallet, wallet_activities in by_wallet.items():
        activity_metrics = metrics_from_market_activities(
            build_market_activities(wallet_activities, resolved_outcomes)
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
                "event_slug": doc["event_slug"],
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
        try:
            entries = await _fetch_activity_cache_entries(
                data, settings, proxy_wallet, watermark
            )
        except RuntimeError as exc:
            logger.warning(
                "profile_activity_cache_sync_failed",
                wallet=proxy_wallet,
                error=str(exc),
            )
            continue
        if entries:
            total_entries += await _write_activity_cache(db, entries)
        logger.info(
            "profile_activity_cache_synced",
            wallet=proxy_wallet,
            activity_cache=len(entries),
            watermark=watermark,
        )
    return total_entries


async def _load_event_slug_to_id(db: Database) -> dict[str, int]:
    docs = await db.events.find(
        {"slug": {"$exists": True, "$nin": [None, ""]}},
        projection={"_id": 1, "slug": 1},
    ).to_list(length=None)
    return {doc["slug"]: doc["_id"] for doc in docs}


async def _load_activity_cache_pending_sync(
    db: Database, wallets: list[str]
) -> list[ActivityCache]:
    if not wallets:
        return []
    event_slugs = await db.events.distinct(
        "slug", {"slug": {"$exists": True, "$nin": [None, ""]}}
    )
    if not event_slugs:
        return []
    docs = await db.activity_caches.find(
        {
            "proxy_wallet": {"$in": wallets},
            "activity_synced": False,
            "event_slug": {"$in": event_slugs},
        }
    ).to_list(length=None)
    return [ActivityCache.model_validate(doc) for doc in docs]


async def _get_activity_watermark(
    db: Database, proxy_wallet: str, event_slug: str
) -> int | None:
    doc = await db.activities.find_one(
        {"proxy_wallet": proxy_wallet, "event_slug": event_slug},
        sort=[("timestamp", -1)],
        projection={"timestamp": 1},
    )
    return doc["timestamp"] if doc else None


async def _fetch_new_activities(
    data: DataClient,
    settings: Settings,
    user: str,
    event_id: int,
    watermark: int | None,
) -> list[dict[str, Any]]:
    """Пагинация /activity по event (новые первыми) до watermark или max_offset."""
    min_timestamp = watermark if watermark is not None else -1
    pages: list[list[dict[str, Any]]] = []
    offset = 0
    while offset <= settings.activity_max_offset:
        raw = await data.activity_page(
            user=user,
            limit=settings.activity_page_limit,
            offset=offset,
            event_id=event_id,
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
    db: Database, proxy_wallet: str, event_slug: str
) -> None:
    await db.activity_caches.update_one(
        {"proxy_wallet": proxy_wallet, "event_slug": event_slug},
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
    slug_to_event_id = await _load_event_slug_to_id(db)
    total_activities = 0
    for entry in pending:
        if stop is not None and stop.is_set():
            break
        event_id = slug_to_event_id.get(entry.event_slug)
        if event_id is None:
            continue
        watermark = await _get_activity_watermark(
            db, entry.proxy_wallet, entry.event_slug
        )
        activities = await _fetch_new_activities(
            data,
            settings,
            entry.proxy_wallet,
            event_id,
            watermark,
        )
        if activities:
            total_activities += await _write_activities(db, activities)
        await _mark_activity_cache_synced(
            db, entry.proxy_wallet, entry.event_slug
        )
        logger.info(
            "activity_cache_activities_synced",
            wallet=entry.proxy_wallet,
            event_slug=entry.event_slug,
            event_id=event_id,
            activities=len(activities),
            watermark=watermark,
        )
    return total_activities


async def sync_home_profiles_for_wallets(
    db: Database,
    data: DataClient,
    settings: Settings,
    wallets: dict[str, str],
    *,
    stop: asyncio.Event | None = None,
) -> None:
    """Sync home profiles for given wallets (activity cache → activities → home_profiles)."""
    started = time.monotonic()
    if not wallets:
        logger.info("home_profiles_sync_skip", reason="no_wallets")
        return

    activity_cache = 0
    activities = 0
    home_profiles = 0

    wallet_list = list(wallets.keys())

    activity_cache = await sync_activity_cache_for_wallets(
        db, data, settings, wallet_list, stop=stop
    )
    activities = await sync_activities_for_wallets(
        db, data, settings, wallet_list, stop=stop
    )
    home_profiles = await upsert_home_profiles_for_wallets(
        db, wallets, stop=stop
    )

    logger.info(
        "home_profiles_sync_complete",
        home_profiles=home_profiles,
        activity_cache=activity_cache,
        activities=activities,
        interrupted=stop is not None and stop.is_set(),
        seconds=round(time.monotonic() - started, 1),
    )


async def sync_profiles_for_wallets(
    db: Database,
    data: DataClient,
    pnl: PnlClient,
    settings: Settings,
    wallets: dict[str, str],
    *,
    identities: dict[str, WalletIdentity] | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Sync Polymarket profiles for given wallets."""
    started = time.monotonic()
    if not wallets:
        logger.info("profiles_sync_skip", reason="no_wallets")
        return

    profiles = await upsert_profiles_for_wallets(
        db, data, pnl, wallets, identities=identities, stop=stop
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


async def sync_home_profiles(
    db: Database,
    data: DataClient,
    settings: Settings,
    *,
    wallet: str | None = None,
    limit: int | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Sync stale home profiles."""
    wallets_to_sync = await _load_home_profiles_needing_sync(
        db, settings, wallet=wallet, limit=limit
    )

    logger.info(
        "home_profiles_sync_start",
        wallets=len(wallets_to_sync),
        wallet=wallet,
        limit=limit,
    )

    await sync_home_profiles_for_wallets(
        db, data, settings, wallets_to_sync, stop=stop
    )
