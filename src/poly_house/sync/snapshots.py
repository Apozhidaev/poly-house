from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from pymongo import UpdateOne

from poly_house.api.clob import ClobClient
from poly_house.api.data import DataClient
from poly_house.api.pnl import PnlClient
from poly_house.config import Settings
from poly_house.db import Database
from poly_house.models import Holder, Snapshot, SnapshotHolder
from poly_house.sync.positions import sync_positions_for_wallets
from poly_house.sync.profiles import upsert_profiles_for_holders
from poly_house.sync.tag_profiles import (
    load_condition_tag_ids,
    update_tag_profiles_for_wallets,
)

logger = structlog.get_logger()

OPEN_MARKETS_QUERY = {
    "closed": False,
    "accepting_orders": True,
    "clob_token_ids.0": {"$exists": True},
    "clob_token_ids.1": {"$exists": True},
    "condition_id": {"$ne": None},
}
OPEN_MARKETS_PROJECTION = {"_id": 1, "condition_id": 1, "clob_token_ids": 1, "tag_ids": 1}

PROFILE_METRIC_FIELDS = (
    "pnl",
    "volume",
    "pnl_growth",
    "pnl_stability",
    "positions_value",
    "cash_balance",
    "total_balance",
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


def compute_roi(pnl: float | None, volume: float | None) -> float | None:
    if pnl is None or volume is None or volume == 0.0:
        return None
    return pnl / volume


def compute_stable_growth(
        pnl_growth: float | None,
        pnl_stability: float | None,
    ) -> float | None:
        if pnl_growth is None or pnl_stability is None:
            return None
        return pnl_stability if pnl_growth > 0 else 0.0


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


def compute_tag_roi(
    wallet: str,
    tag_ids: list[int],
    tag_weights: dict[int, float],
    pnls: dict[tuple[str, int], float],
    volumes: dict[tuple[str, int], float],
) -> float | None:
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
        return None
    return weighted_pnl / weighted_volume


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
    profiles: dict[str, dict[str, float | None]] | None = None,
    *,
    market_tag_ids: list[int] | None = None,
    tag_weights: dict[int, float] | None = None,
    tag_win_rates: dict[tuple[str, int], float] | None = None,
    tag_stable_growths: dict[tuple[str, int], float] | None = None,
    tag_pnls: dict[tuple[str, int], float] | None = None,
    tag_volumes: dict[tuple[str, int], float] | None = None,
    position_win_rates: dict[str, float | None] | None = None,
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
    position_win_rate_map = position_win_rates or {}
    for entry in raw:
        outcome_index = token_to_outcome.get(entry["token"])
        if outcome_index is None:
            continue
        for holder in entry["holders"]:
            if not holder.get("displayUsernamePublic"):
                continue
            wallet = holder["proxyWallet"]
            metrics = profile_map.get(wallet, {})
            result[outcome_index].append(
                SnapshotHolder(
                    proxy_wallet=wallet,
                    amount=holder["amount"],
                    win_rate=position_win_rate_map.get(wallet),
                    roi=compute_roi(metrics.get("pnl"), metrics.get("volume")),
                    stable_growth=compute_stable_growth(
                        metrics.get("pnl_growth"),
                        metrics.get("pnl_stability"),
                    ),
                    positions_value=metrics.get("positions_value"),
                    cash_balance=metrics.get("cash_balance"),
                    total_balance=metrics.get("total_balance"),
                    tag_win_rate=compute_tag_win_rate(
                        wallet, tag_ids, weights, win_rates
                    ),
                    tag_stable_growth=compute_tag_stable_growth(
                        wallet, tag_ids, weights, stable_growths
                    ),
                    tag_roi=compute_tag_roi(
                        wallet, tag_ids, weights, pnls, tag_volume_map
                    ),
                )
            )
    return result


async def load_profile_metrics(
    db: Database, wallets: list[str]
) -> dict[str, dict[str, float | None]]:
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


async def load_position_win_rates(
    db: Database, wallets: list[str]
) -> dict[str, float | None]:
    if not wallets:
        return {}
    cursor = db.position_metrics.find(
        {"_id": {"$in": wallets}},
        projection={"_id": 1, "win_rate": 1},
    )
    docs = await cursor.to_list(length=None)
    return {doc["_id"]: doc.get("win_rate") for doc in docs}


def collect_holders(holders_raw: list[dict[str, Any]]) -> dict[str, Holder]:
    holders: dict[str, Holder] = {}
    for entry in holders_raw:
        for raw in entry.get("holders") or []:
            if not raw.get("displayUsernamePublic"):
                continue
            holder = Holder.from_payload(raw)
            holders[holder.proxy_wallet] = holder
    return holders


async def upsert_holders(db: Database, holders: dict[str, Holder]) -> None:
    if not holders:
        return
    await db.holders.bulk_write(
        [
            UpdateOne(
                {"_id": h.proxy_wallet},
                {"$set": h.to_doc()},
                upsert=True,
            )
            for h in holders.values()
        ]
    )


def snapshot_from_book_and_holders(
    market: dict[str, Any],
    book: dict[str, Any],
    holders_raw: list[dict[str, Any]],
    *,
    captured_at: datetime,
    profiles: dict[str, dict[str, float | None]] | None = None,
    market_tag_ids: list[int] | None = None,
    tag_weights: dict[int, float] | None = None,
    tag_win_rates: dict[tuple[str, int], float] | None = None,
    tag_stable_growths: dict[tuple[str, int], float] | None = None,
    tag_pnls: dict[tuple[str, int], float] | None = None,
    tag_volumes: dict[tuple[str, int], float] | None = None,
    position_win_rates: dict[str, float | None] | None = None,
) -> Snapshot:
    last_trade = book.get("last_trade_price")
    return Snapshot(
        market_id=market["_id"],
        condition_id=market["condition_id"],
        captured_at=captured_at,
        min_order_size=float(book["min_order_size"]),
        tick_size=float(book["tick_size"]),
        best_bid=_book_top_price(book.get("bids")),
        best_ask=_book_top_price(book.get("asks")),
        last_trade_price=_book_float(last_trade),
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
            position_win_rates=position_win_rates,
        ),
    )


async def sync_snapshots(
    db: Database,
    data: DataClient,
    clob: ClobClient,
    pnl: PnlClient,
    settings: Settings,
    *,
    stop: asyncio.Event | None = None,
) -> tuple[int, int]:
    """Снимки ордербука + holders для открытых маркетов; returns (written, skipped)."""
    logger.info("snapshots_sync_start")
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
            existing = await db.holders.find(
                {"_id": {"$in": list(holders.keys())}},
                projection={"_id": 1},
            ).to_list(length=None)
            existing_ids = {doc["_id"] for doc in existing}
            new_holders = {
                wallet: holder
                for wallet, holder in holders.items()
                if wallet not in existing_ids
            }
        else:
            new_holders = {}
        await upsert_holders(db, holders)
        if new_holders:
            await upsert_profiles_for_holders(db, data, pnl, new_holders, stop=stop)
            await sync_positions_for_wallets(
                db, data, settings, list(new_holders.keys()), stop=stop
            )
            if condition_tags:
                await update_tag_profiles_for_wallets(
                    db,
                    list(new_holders.keys()),
                    condition_tags,
                    stop=stop,
                )
        profiles = await load_profile_metrics(db, list(holders.keys()))
        position_win_rates = await load_position_win_rates(
            db, list(holders.keys())
        )
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
        snapshot = snapshot_from_book_and_holders(
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
            position_win_rates=position_win_rates,
        )
        await db.snapshots.insert_one(snapshot.to_doc())
        written += 1
        logger.info(
            "snapshot_inserted",
            market_id=market["_id"],
            condition_id=market["condition_id"],
            best_bid=snapshot.best_bid,
            best_ask=snapshot.best_ask,
        )

    logger.info("snapshots_sync_done", written=written, skipped=skipped, markets=len(markets))
    return written, skipped
