from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from pymongo import UpdateOne

from poly_house.api.data import DataClient
from poly_house.config import Settings
from poly_house.db import Database
from poly_house.models import Position

logger = structlog.get_logger()

BULK_SIZE = 100


def win_rate_from_positions(positions: list[dict[str, Any]]) -> float | None:
    if not positions:
        return None
    win_count = sum(1 for p in positions if p["realized_pnl"] > 0)
    return win_count / len(positions)


async def load_win_rates(db: Database, wallets: list[str]) -> dict[str, float]:
    if not wallets:
        return {}
    cursor = await db.positions.aggregate(
        [
            {"$match": {"proxy_wallet": {"$in": wallets}}},
            {
                "$group": {
                    "_id": "$proxy_wallet",
                    "positions_count": {"$sum": 1},
                    "win_positions_count": {
                        "$sum": {"$cond": [{"$gt": ["$realized_pnl", 0]}, 1, 0]}
                    },
                }
            },
        ]
    )
    docs = await cursor.to_list(length=None)
    return {
        doc["_id"]: doc["win_positions_count"] / doc["positions_count"]
        for doc in docs
        if doc["positions_count"] > 0
    }


async def upsert_profile_win_rates(db: Database, wallets: list[str]) -> int:
    win_rates = await load_win_rates(db, wallets)
    if not win_rates:
        return 0
    ops = [
        UpdateOne(
            {"_id": wallet},
            {"$set": {"win_rate": rate}},
            upsert=True,
        )
        for wallet, rate in win_rates.items()
    ]
    for i in range(0, len(ops), BULK_SIZE):
        await db.profiles.bulk_write(ops[i : i + BULK_SIZE])
    return len(ops)


async def _get_holder_watermark(db: Database, proxy_wallet: str) -> int | None:
    doc = await db.positions.find_one(
        {"proxy_wallet": proxy_wallet},
        sort=[("timestamp", -1)],
        projection={"timestamp": 1},
    )
    return doc["timestamp"] if doc else None


def _first_run_cutoff_timestamp(settings: Settings) -> int:
    dt = datetime.now(UTC) - timedelta(days=settings.positions_closed_in_days)
    return int(dt.timestamp())


async def _fetch_new_positions(
    data: DataClient,
    settings: Settings,
    user: str,
    watermark: int | None,
) -> list[dict[str, Any]]:
    """Пагинация /closed-positions (новые первыми) до watermark или max_offset."""
    min_timestamp = (
        watermark if watermark is not None else _first_run_cutoff_timestamp(settings) - 1
    )
    pages: list[list[dict[str, Any]]] = []
    offset = 0
    while offset <= settings.closed_positions_max_offset:
        raw = await data.closed_positions_page(
            user=user,
            limit=settings.closed_positions_page_limit,
            offset=offset,
        )
        if not raw:
            break
        fresh = [p for p in raw if p["timestamp"] > min_timestamp]
        pages.append(fresh)
        if len(fresh) < len(raw):
            break
        if len(raw) < settings.closed_positions_page_limit:
            break
        offset += settings.closed_positions_page_limit
    return [p for page in reversed(pages) for p in reversed(page)]


async def _write_positions(db: Database, raw_positions: list[dict[str, Any]]) -> int:
    if not raw_positions:
        return 0
    docs = [Position.model_validate(raw).to_doc() for raw in raw_positions]
    await db.positions.insert_many(docs)
    return len(docs)


async def sync_positions_for_wallets(
    db: Database,
    data: DataClient,
    settings: Settings,
    wallets: list[str],
    *,
    stop: asyncio.Event | None = None,
) -> tuple[int, int]:
    """Догружает closed positions для списка wallets; returns (holders, positions)."""
    total_holders = 0
    total_positions = 0
    wallets_with_new: list[str] = []
    for proxy_wallet in wallets:
        if stop is not None and stop.is_set():
            break
        watermark = await _get_holder_watermark(db, proxy_wallet)
        positions = await _fetch_new_positions(data, settings, proxy_wallet, watermark)
        if positions:
            total_positions += await _write_positions(db, positions)
            wallets_with_new.append(proxy_wallet)
        total_holders += 1
        logger.info(
            "holder_positions_synced",
            wallet=proxy_wallet,
            positions=len(positions),
            watermark=watermark,
        )
    if wallets_with_new:
        metrics_written = await upsert_profile_win_rates(db, wallets_with_new)
        logger.info(
            "profile_win_rates_updated",
            wallets=len(wallets_with_new),
            profiles=metrics_written,
        )
    return total_holders, total_positions


async def sync_holder_positions(
    db: Database,
    data: DataClient,
    settings: Settings,
    *,
    wallet: str | None = None,
    limit: int | None = None,
    stop: asyncio.Event | None = None,
) -> tuple[int, int]:
    """Догружает closed positions для holders; returns (holders, positions)."""
    query: dict[str, Any] = {}
    if wallet is not None:
        query["_id"] = wallet

    cursor = db.holders.find(query, projection={"_id": 1})
    if limit is not None:
        cursor = cursor.limit(limit)
    holders = await cursor.to_list(length=None)

    logger.info(
        "holder_positions_sync_start",
        holders=len(holders),
        wallet=wallet,
        limit=limit,
    )

    started = time.monotonic()
    wallets = [holder["_id"] for holder in holders]
    total_holders, total_positions = await sync_positions_for_wallets(
        db, data, settings, wallets, stop=stop
    )

    logger.info(
        "holder_positions_sync_done",
        holders=total_holders,
        positions=total_positions,
        interrupted=stop is not None and stop.is_set(),
        seconds=round(time.monotonic() - started, 1),
    )
    return total_holders, total_positions
