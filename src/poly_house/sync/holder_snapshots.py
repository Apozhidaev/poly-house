from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog

from poly_house.api.clob import ClobClient
from poly_house.api.data import DataClient
from poly_house.api.pnl import PnlClient
from poly_house.config import Settings
from poly_house.db import Database
from poly_house.models import HolderSnapshot, SnapshotHolder
from poly_house.sync.profiles import (
    sync_profiles_for_wallets,
    wallets_needing_profile_sync,
)
from poly_house.sync.tag_profiles import (
    load_condition_tag_ids,
    update_tag_profiles_for_wallets,
)
from poly_house.utils.math import compute_stable_growth

logger = structlog.get_logger()

OPEN_MARKETS_QUERY = {
    "closed": False,
    "accepting_orders": True,
    "clob_token_ids.0": {"$exists": True},
    "clob_token_ids.1": {"$exists": True},
    "condition_id": {"$ne": None},
}
OPEN_MARKETS_PROJECTION = {
    "_id": 1,
    "condition_id": 1,
    "slug": 1,
    "clob_token_ids": 1,
    "tag_ids": 1,
}

PROFILE_METRIC_FIELDS = (
    "pnl",
    "volume",
    "stable_growth",
    "total_balance",
    "house_pnl",
    "house_volume",
    "house_stable_growth",
    "house_win_rate",
    "house_markets_count",
)
PROFILE_METRICS_PROJECTION = {"_id": 1, **{f: 1 for f in PROFILE_METRIC_FIELDS}}


def _book_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _book_top_price(levels: list[dict[str, Any]] | None) -> float | None:
    if not levels:
        return None
    return _book_float(levels[-1].get("price"))


def compute_tag_weighted_average(
    wallet: str,
    tag_ids: list[int],
    tag_weights: dict[int, float],
    values: dict[tuple[str, int], float],
) -> float | None:
    weighted_sum = 0.0
    weight_sum = 0.0
    for tag_id in tag_ids:
        value = values.get((wallet, tag_id))
        if value is None:
            continue
        weight = tag_weights.get(tag_id, 0.0)
        if weight == 0.0:
            continue
        weighted_sum += value * weight
        weight_sum += weight
    if weight_sum == 0.0:
        return None
    return weighted_sum / weight_sum


def compute_tag_win_rate(
    wallet: str,
    tag_ids: list[int],
    tag_weights: dict[int, float],
    win_rates: dict[tuple[str, int], float],
) -> float | None:
    return compute_tag_weighted_average(wallet, tag_ids, tag_weights, win_rates)


def compute_tag_stable_growth(
    wallet: str,
    tag_ids: list[int],
    tag_weights: dict[int, float],
    stable_growths: dict[tuple[str, int], float],
) -> float | None:
    return compute_tag_weighted_average(
        wallet, tag_ids, tag_weights, stable_growths
    )


def compute_tag_pnl_volume(
    wallet: str,
    tag_ids: list[int],
    tag_weights: dict[int, float],
    pnls: dict[tuple[str, int], float],
    volumes: dict[tuple[str, int], float],
) -> tuple[float | None, float | None]:
    weighted_pnl = 0.0
    weighted_volume = 0.0
    for tag_id in tag_ids:
        key = (wallet, tag_id)
        pnl = pnls.get(key)
        volume = volumes.get(key)
        if pnl is None or volume is None:
            continue
        weight = tag_weights.get(tag_id, 0.0)
        if weight == 0.0:
            continue
        weighted_pnl += pnl * weight
        weighted_volume += volume * weight
    if weighted_volume == 0.0:
        return None, None
    return weighted_pnl, weighted_volume


async def load_tag_weights(db: Database, tag_ids: list[int]) -> dict[int, float]:
    if not tag_ids:
        return {}
    cursor = db.tags.find(
        {"_id": {"$in": tag_ids}},
        projection={"_id": 1, "markets_count": 1},
    )
    docs = await cursor.to_list(length=None)
    weights: dict[int, float] = {}
    for doc in docs:
        markets_count = doc.get("markets_count", 0)
        weights[doc["_id"]] = 1 / markets_count if markets_count > 0 else 0.0
    return weights


async def load_tag_profile_tag_metrics(
    db: Database,
    wallets: list[str],
    tag_ids: list[int],
) -> tuple[
    dict[tuple[str, int], float],
    dict[tuple[str, int], float],
    dict[tuple[str, int], float],
    dict[tuple[str, int], float],
]:
    if not wallets or not tag_ids:
        return {}, {}, {}, {}
    cursor = db.tag_profiles.find(
        {
            "proxy_wallet": {"$in": wallets},
            "tag_id": {"$in": tag_ids},
        },
        projection={
            "proxy_wallet": 1,
            "tag_id": 1,
            "win_rate": 1,
            "pnl": 1,
            "volume": 1,
            "pnl_growth": 1,
            "pnl_stability": 1,
        },
    )
    docs = await cursor.to_list(length=None)
    win_rates: dict[tuple[str, int], float] = {}
    stable_growths: dict[tuple[str, int], float] = {}
    pnls: dict[tuple[str, int], float] = {}
    volumes: dict[tuple[str, int], float] = {}
    for doc in docs:
        key = (doc["proxy_wallet"], doc["tag_id"])
        win_rate = doc.get("win_rate")
        if win_rate is not None:
            win_rates[key] = win_rate
        pnl = doc.get("pnl")
        if pnl is not None:
            pnls[key] = pnl
        volume = doc.get("volume")
        if volume is not None:
            volumes[key] = volume
        stable_growth = compute_stable_growth(
            doc.get("pnl_growth"),
            doc.get("pnl_stability"),
        )
        if stable_growth is not None:
            stable_growths[key] = stable_growth
    return win_rates, stable_growths, pnls, volumes


def parse_top_holders(
    raw: list[dict[str, Any]],
    clob_token_ids: list[str],
    profiles: dict[str, dict[str, float | int | None]] | None = None,
    *,
    market_tag_ids: list[int] | None = None,
    tag_weights: dict[int, float] | None = None,
    tag_win_rates: dict[tuple[str, int], float] | None = None,
    tag_stable_growths: dict[tuple[str, int], float] | None = None,
    tag_pnls: dict[tuple[str, int], float] | None = None,
    tag_volumes: dict[tuple[str, int], float] | None = None,
) -> list[list[SnapshotHolder]]:
    token_to_outcome = {token: i for i, token in enumerate(clob_token_ids)}
    result: list[list[SnapshotHolder]] = [[], []]
    profile_map = profiles or {}
    tag_ids = market_tag_ids or []
    weights = tag_weights or {}
    win_rates = tag_win_rates or {}
    stable_growths = tag_stable_growths or {}
    pnls = tag_pnls or {}
    tag_volume_map = tag_volumes or {}
    for entry in raw:
        outcome_index = token_to_outcome.get(entry["token"])
        if outcome_index is None:
            continue
        for holder in entry["holders"]:
            if not holder.get("displayUsernamePublic"):
                continue
            wallet = holder["proxyWallet"]
            metrics = profile_map.get(wallet, {})
            tag_pnl, tag_volume = compute_tag_pnl_volume(
                wallet, tag_ids, weights, pnls, tag_volume_map
            )
            result[outcome_index].append(
                SnapshotHolder(
                    proxy_wallet=wallet,
                    name=holder["name"],
                    amount=holder["amount"],
                    pnl=metrics.get("pnl"),
                    volume=metrics.get("volume"),
                    stable_growth=metrics.get("stable_growth"),
                    total_balance=metrics.get("total_balance"),
                    house_pnl=metrics.get("house_pnl"),
                    house_volume=metrics.get("house_volume"),
                    house_stable_growth=metrics.get("house_stable_growth"),
                    house_win_rate=metrics.get("house_win_rate"),
                    house_markets_count=int(metrics.get("house_markets_count") or 0),
                    tag_win_rate=compute_tag_win_rate(
                        wallet, tag_ids, weights, win_rates
                    ),
                    tag_stable_growth=compute_tag_stable_growth(
                        wallet, tag_ids, weights, stable_growths
                    ),
                    tag_pnl=tag_pnl,
                    tag_volume=tag_volume,
                )
            )
    return result


async def load_profile_metrics(
    db: Database, wallets: list[str]
) -> dict[str, dict[str, float | int | None]]:
    if not wallets:
        return {}
    cursor = db.profiles.find(
        {"_id": {"$in": wallets}},
        projection=PROFILE_METRICS_PROJECTION,
    )
    docs = await cursor.to_list(length=None)
    return {
        doc["_id"]: {field: doc.get(field) for field in PROFILE_METRIC_FIELDS}
        for doc in docs
    }


def collect_holders(holders_raw: list[dict[str, Any]]) -> dict[str, str]:
    holders: dict[str, str] = {}
    for entry in holders_raw:
        for raw in entry.get("holders") or []:
            if not raw.get("displayUsernamePublic"):
                continue
            holders[raw["proxyWallet"]] = raw["name"]
    return holders


def holder_snapshot_from_book_and_holders(
    market: dict[str, Any],
    book: dict[str, Any],
    holders_raw: list[dict[str, Any]],
    *,
    captured_at: datetime,
    profiles: dict[str, dict[str, float | int | None]] | None = None,
    market_tag_ids: list[int] | None = None,
    tag_weights: dict[int, float] | None = None,
    tag_win_rates: dict[tuple[str, int], float] | None = None,
    tag_stable_growths: dict[tuple[str, int], float] | None = None,
    tag_pnls: dict[tuple[str, int], float] | None = None,
    tag_volumes: dict[tuple[str, int], float] | None = None,
) -> HolderSnapshot:
    return HolderSnapshot(
        market_id=market["_id"],
        market_slug=market["slug"],
        condition_id=market["condition_id"],
        captured_at=captured_at,
        min_order_size=float(book["min_order_size"]),
        tick_size=float(book["tick_size"]),
        best_bid=_book_top_price(book.get("bids")),
        best_ask=_book_top_price(book.get("asks")),
        top_holders=parse_top_holders(
            holders_raw,
            market["clob_token_ids"],
            profiles=profiles,
            market_tag_ids=market_tag_ids,
            tag_weights=tag_weights,
            tag_win_rates=tag_win_rates,
            tag_stable_growths=tag_stable_growths,
            tag_pnls=tag_pnls,
            tag_volumes=tag_volumes,
        ),
    )


async def sync_holder_snapshots(
    db: Database,
    data: DataClient,
    clob: ClobClient,
    pnl: PnlClient,
    settings: Settings,
    *,
    stop: asyncio.Event | None = None,
) -> tuple[int, int]:
    """Снимки ордербука + holders для открытых маркетов; returns (written, skipped)."""
    logger.info("holder_snapshots_sync_start")
    cursor = db.markets.find(OPEN_MARKETS_QUERY, OPEN_MARKETS_PROJECTION)
    markets = await cursor.to_list(length=None)
    condition_tags = await load_condition_tag_ids(db)

    written = 0
    skipped = 0
    captured_at = datetime.now(UTC)
    for market in markets:
        if stop is not None and stop.is_set():
            break
        book = await clob.order_book(market["clob_token_ids"][0])
        if book is None:
            skipped += 1
            continue
        holders_raw = await data.top_holders(
            condition_id=market["condition_id"],
            limit=settings.snapshot_holders_limit,
        )
        if holders_raw is None:
            skipped += 1
            continue
        holders = collect_holders(holders_raw)
       
        if holders:
            to_sync = await wallets_needing_profile_sync(
                db, list(holders.keys())
            )
            if to_sync:
                holders_to_sync = {
                    wallet: holders[wallet] for wallet in to_sync
                }
                await sync_profiles_for_wallets(
                    db, data, pnl, settings, holders_to_sync, stop=stop
                )
            if condition_tags:
                await update_tag_profiles_for_wallets(
                    db,
                    list(holders.keys()),
                    condition_tags,
                    stop=stop,
                )
        profiles = await load_profile_metrics(db, list(holders.keys()))
        market_tag_ids = market.get("tag_ids") or []
        if market_tag_ids and holders:
            tag_weights = await load_tag_weights(db, market_tag_ids)
            tag_win_rates, tag_stable_growths, tag_pnls, tag_volumes = (
                await load_tag_profile_tag_metrics(
                    db, list(holders.keys()), market_tag_ids
                )
            )
        else:
            tag_weights = {}
            tag_win_rates = {}
            tag_stable_growths = {}
            tag_pnls = {}
            tag_volumes = {}
        holder_snapshot = holder_snapshot_from_book_and_holders(
            market,
            book,
            holders_raw,
            captured_at=captured_at,
            profiles=profiles,
            market_tag_ids=market_tag_ids,
            tag_weights=tag_weights,
            tag_win_rates=tag_win_rates,
            tag_stable_growths=tag_stable_growths,
            tag_pnls=tag_pnls,
            tag_volumes=tag_volumes,
        )
        await db.holder_snapshots.insert_one(holder_snapshot.to_doc())
        written += 1
        logger.info(
            "holder_snapshot_inserted",
            market_id=market["_id"],
            condition_id=market["condition_id"],
            best_bid=holder_snapshot.best_bid,
            best_ask=holder_snapshot.best_ask,
        )

    logger.info(
        "holder_snapshots_sync_done",
        written=written,
        skipped=skipped,
        markets=len(markets),
    )
    return written, skipped
